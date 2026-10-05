"""Row I/O for the LLM settings store: the three ``llm_*`` tables as one value.

Owns exactly one thing -- what the rows in :mod:`app.models.llm_config` mean.
How the database is reached lives in :mod:`app.repositories.settings_engine`; the
public shape and the merge policy live in :mod:`app.repositories.settings`.

The rules this module implements:

* **One-shot legacy import.** While ``llm_active_settings.json_imported_at`` is
  NULL the caller's payload is upserted and the marker claimed in the same write
  transaction, so a later "clear settings" can never resurrect
  ``<STORAGE_DIR>/llm_settings.json``. An empty payload still stamps the marker:
  a missing or corrupt file is never retried. The file itself is only ever read.
* **Partial upserts.** A key write and a base-URL write are separate statements
  because one save may carry both for the same provider, and each must leave the
  other column alone.
* **Explicit values, never server defaults.** ``api_key`` is always bound: only
  the alembic-created table has ``DEFAULT ''``, so an insert relying on it would
  raise NOT NULL on a store-bootstrapped database.
* **Reads degrade, writes raise.** A missing, locked, or corrupt database answers
  :data:`EMPTY_STATE` -- the same silent-defaults contract the JSON store had for
  an unreadable file. Save failures propagate to the caller, which logs and
  carries on.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.domain.privacy import EXTERNAL_PROVIDER_IDS
from app.models.llm_config import (
    LLMActiveSettings,
    LLMProvider,
    LLMProviderCredential,
)
from app.repositories.settings_engine import engine

_EXTERNAL_KIND: Final = "external"

_SQL_SAVE_ACTIVE: Final = text(
    """
    INSERT INTO llm_active_settings (id, provider, model, updated_at)
    VALUES (1, :provider, :model, CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO UPDATE SET
        provider = COALESCE(excluded.provider, llm_active_settings.provider),
        model = COALESCE(excluded.model, llm_active_settings.model),
        updated_at = CURRENT_TIMESTAMP
    """
)
_SQL_SAVE_KEY: Final = text(
    """
    INSERT INTO llm_provider_credentials (provider, api_key, created_at, updated_at)
    VALUES (:provider, :api_key, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT(provider) DO UPDATE SET
        api_key = excluded.api_key, updated_at = CURRENT_TIMESTAMP
    """
)
_SQL_SAVE_URL: Final = text(
    """
    INSERT INTO llm_provider_credentials (provider, api_key, base_url, created_at, updated_at)
    VALUES (:provider, '', :base_url, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT(provider) DO UPDATE SET
        base_url = excluded.base_url, updated_at = CURRENT_TIMESTAMP
    """
)
_SQL_CLAIM_IMPORT: Final = text(
    """
    UPDATE llm_active_settings
    SET json_imported_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
    WHERE id = 1 AND json_imported_at IS NULL
    """
)
_READ_ACTIVE: Final = select(
    LLMActiveSettings.provider,
    LLMActiveSettings.model,
    LLMActiveSettings.json_imported_at,
)
_READ_CREDENTIALS: Final = select(
    LLMProviderCredential.provider,
    LLMProviderCredential.api_key,
    LLMProviderCredential.base_url,
)
_READ_EXTERNAL: Final = select(LLMProvider.name).where(LLMProvider.kind == _EXTERNAL_KIND)


@dataclass(frozen=True, slots=True)
class ProviderCredential:
    """One provider's stored key and base-URL override.

    ``api_key`` is never ``None``: the column is NOT NULL and the legacy store
    answered ``""`` for a cleared key. ``base_url`` is ``None`` when unset.
    """

    api_key: str
    base_url: str | None


@dataclass(frozen=True, slots=True)
class StoredSettings:
    """Snapshot of the three LLM tables as one immutable value."""

    provider: str | None
    model: str | None
    json_imported: bool
    credentials: Mapping[str, ProviderCredential]
    external_providers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImportedSettings:
    """Payload of the one-shot legacy-JSON import (already normalised)."""

    provider: str | None = None
    model: str | None = None
    api_keys: Mapping[str, str] = field(default_factory=dict)
    base_urls: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SettingsPatch:
    """One save request.

    ``None`` on ``provider``/``model`` means "leave unchanged"; a key mapped to
    ``None`` or ``""`` clears it; a URL mapped to ``None`` or blank clears it.
    """

    provider: str | None = None
    model: str | None = None
    api_keys: Mapping[str, str | None] = field(default_factory=dict)
    base_urls: Mapping[str, str | None] = field(default_factory=dict)


# The degraded answer: no provider/model and no keys, but the external provider
# set still comes from the domain registry, so keys_status() keeps answering for
# all five providers exactly as it did when the file could not be read.
EMPTY_STATE: Final = StoredSettings(
    provider=None,
    model=None,
    json_imported=False,
    credentials={},
    external_providers=EXTERNAL_PROVIDER_IDS,
)


def _read(conn: Connection) -> StoredSettings:
    """Project the three tables onto one immutable snapshot."""
    active = conn.execute(_READ_ACTIVE).one_or_none()
    credentials = {
        name: ProviderCredential(api_key=key or "", base_url=base_url)
        for name, key, base_url in conn.execute(_READ_CREDENTIALS)
    }
    external = tuple(conn.execute(_READ_EXTERNAL).scalars())
    return StoredSettings(
        provider=active.provider if active else None,
        model=active.model if active else None,
        json_imported=active.json_imported_at is not None if active else False,
        credentials=credentials,
        external_providers=external,
    )


def _import(conn: Connection, payload: ImportedSettings) -> StoredSettings:
    """Claim the one-shot marker, upsert the payload, return the new snapshot.

    A URL-only provider gets an ``''`` key so the credential row can hold the
    ``base_url`` on its own.
    """
    conn.execute(_SQL_SAVE_ACTIVE, {"provider": payload.provider, "model": payload.model})
    if conn.execute(_SQL_CLAIM_IMPORT).rowcount == 0:
        return _read(conn)  # another writer claimed the import first
    for name, key in payload.api_keys.items():
        conn.execute(_SQL_SAVE_KEY, {"provider": name, "api_key": key})
    for name, base_url in payload.base_urls.items():
        conn.execute(_SQL_SAVE_URL, {"provider": name, "base_url": base_url})
    return _read(conn)


def read_settings(legacy_source: Callable[[], ImportedSettings]) -> StoredSettings:
    """Read the stored settings, importing the legacy JSON once when due.

    ``legacy_source`` is called at most once per database, and only while
    ``json_imported_at`` is still NULL.
    """
    try:
        with engine().connect() as conn:
            state = _read(conn)
        if state.json_imported:
            return state
        payload = legacy_source()
        with engine().execution_options(begin_immediate=True).begin() as conn:
            return _import(conn, payload)
    except (SQLAlchemyError, OSError):
        return EMPTY_STATE


def save_settings(patch: SettingsPatch) -> None:
    """Merge ``patch`` into the tables in one short ``BEGIN IMMEDIATE`` transaction.

    Raises on failure; only reads degrade silently.
    """
    with engine().execution_options(begin_immediate=True).begin() as conn:
        conn.execute(_SQL_SAVE_ACTIVE, {"provider": patch.provider, "model": patch.model})
        for name, key in patch.api_keys.items():
            conn.execute(_SQL_SAVE_KEY, {"provider": name, "api_key": key or ""})
        for name, base_url in patch.base_urls.items():
            conn.execute(_SQL_SAVE_URL, {"provider": name, "base_url": base_url})
