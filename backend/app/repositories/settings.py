"""SQLite persistence for last-used LLM provider/model + API keys.

Stored in the app database at ``DATABASE_URL`` (``matters.db``), in the three
tables of :mod:`app.models.llm_config`. Those tables are self-bootstrapped with
``checkfirst=True`` on first use, so LLM settings keep working before migrations
run -- which is why this store never depended on alembic.

The legacy ``<STORAGE_DIR>/llm_settings.json`` is an **import source only**: it is
read exactly once, while ``llm_active_settings.json_imported_at`` is still NULL,
and is never written again (``get_settings_path`` survives as that import path).
The merge policy and the public dict shape below are unchanged from the file store
this replaced, so all 14 call sites compile and behave identically.

Reads degrade to :func:`_defaults` when the database is missing, locked, or
corrupt -- the same silent-defaults contract the JSON store had for a missing or
unparseable file. Writes raise, and :func:`api.v1.chat` already logs and carries
on when they do. The one loud failure is an in-memory ``DATABASE_URL``, which
:mod:`app.repositories.settings_engine` refuses by name.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.domain.privacy import EXTERNAL_PROVIDER_IDS, LOCAL_PROVIDERS
from app.repositories import settings_db
from app.repositories.settings_db import (
    ImportedSettings,
    ProviderCredential,
    SettingsPatch,
    StoredSettings,
)

SUPPORTED_KEY_PROVIDERS: tuple[str, ...] = EXTERNAL_PROVIDER_IDS

# base_urls may name a local provider (ollama), so its vocabulary is wider than
# SUPPORTED_KEY_PROVIDERS. The credentials table has a foreign key to
# llm_providers.name, so an unknown name cannot be stored at all -- which is why
# both write paths filter to this vocabulary, the legacy import included.
_KNOWN_PROVIDER_NAMES: frozenset[str] = frozenset(SUPPORTED_KEY_PROVIDERS) | LOCAL_PROVIDERS


def get_settings_path() -> Path:
    """Resolve the legacy JSON settings file path (the import source).

    Respects ``FASL_STORAGE_DIR`` then ``STORAGE_DIR`` env, falling back to
    the configured ``settings.STORAGE_DIR`` and finally ``./storage``.
    """
    storage_dir = os.getenv("FASL_STORAGE_DIR") or os.getenv("STORAGE_DIR")
    if storage_dir is None:
        try:
            from app.config import settings

            storage_dir = settings.STORAGE_DIR
        except Exception:
            storage_dir = "./storage"
    return Path(storage_dir) / "llm_settings.json"


def _defaults() -> dict[str, Any]:
    return {
        "provider": None,
        "model": None,
        "base_urls": {},
        "api_keys": {p: "" for p in SUPPORTED_KEY_PROVIDERS},
    }


def _normalize_loaded(data: Any) -> dict[str, Any]:
    """Merge raw JSON into the canonical shape; tolerate corrupt input."""
    base = _defaults()
    if not isinstance(data, dict):
        return base
    provider = data.get("provider")
    base["provider"] = provider if isinstance(provider, str) and provider else None
    model = data.get("model")
    base["model"] = model if isinstance(model, str) and model else None
    raw_keys = data.get("api_keys")
    if isinstance(raw_keys, dict):
        for p in SUPPORTED_KEY_PROVIDERS:
            val = raw_keys.get(p)
            base["api_keys"][p] = val if isinstance(val, str) else ""
    raw_urls = data.get("base_urls")
    if isinstance(raw_urls, dict):
        for k, v in raw_urls.items():
            if isinstance(k, str) and isinstance(v, str):
                base["base_urls"][k.strip().lower()] = v.strip()
    return base


def _load_legacy_json() -> dict[str, Any]:
    """Read + normalise the legacy JSON file; defaults when missing or corrupt.

    This is the boundary where untrusted file content becomes typed values, so
    ``_normalize_loaded`` does the narrowing and no caller re-validates.
    """
    try:
        raw = get_settings_path().read_text(encoding="utf-8")
    except OSError:
        return _defaults()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return _defaults()
    return _normalize_loaded(data)


def _import_payload() -> ImportedSettings:
    """Parse the legacy file into the import payload (empty when unusable).

    Names are filtered to the same vocabularies ``save_llm_settings`` uses.
    """
    legacy = _load_legacy_json()
    keys = {n: v for n, v in legacy["api_keys"].items() if n in SUPPORTED_KEY_PROVIDERS and v}
    urls = {n: v for n, v in legacy["base_urls"].items() if n in _KNOWN_PROVIDER_NAMES and v}
    return ImportedSettings(
        provider=legacy["provider"], model=legacy["model"], api_keys=keys, base_urls=urls
    )


def _read_state() -> StoredSettings:
    """Current database state, running the one-shot legacy import when due."""
    return settings_db.read_settings(_import_payload)


def _state_to_dict(state: StoredSettings) -> dict[str, Any]:
    """Project stored rows onto the public dict shape."""
    credentials = state.credentials
    return {
        "provider": state.provider,
        "model": state.model,
        "base_urls": {n: c.base_url for n, c in credentials.items() if c.base_url},
        "api_keys": {
            name: credentials[name].api_key if name in credentials else ""
            for name in state.external_providers
        },
    }


def load_llm_settings() -> dict[str, Any]:
    """Load persisted settings; return defaults when the database is unusable."""
    return _state_to_dict(_read_state())


def save_llm_settings(
    provider: str | None = None,
    model: str | None = None,
    api_keys: dict[str, str | None] | None = None,
    base_urls: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Merge the given fields into the stored settings and persist them.

    ``api_keys`` values of ``None`` or ``""`` delete the stored key. Returns the
    merged settings dict.
    """
    current = load_llm_settings()
    stored_keys: dict[str, str | None] = {}
    stored_urls: dict[str, str | None] = {}
    if provider is not None:
        current["provider"] = provider
    if model is not None:
        current["model"] = model
    if api_keys is not None:
        for key, value in api_keys.items():
            name = key.strip().lower()
            if name not in SUPPORTED_KEY_PROVIDERS:
                continue
            if value is None or value == "":
                current["api_keys"][name] = ""
                stored_keys[name] = ""
            else:
                current["api_keys"][name] = value
                stored_keys[name] = value
    if base_urls is not None:
        for key, value in base_urls.items():
            name = key.strip().lower()
            if name not in _KNOWN_PROVIDER_NAMES:
                continue
            if value is None or not value.strip():
                current["base_urls"].pop(name, None)
                stored_urls[name] = None
            else:
                current["base_urls"][name] = value.strip()
                stored_urls[name] = value.strip()
    settings_db.save_settings(
        SettingsPatch(
            provider=provider,
            model=model,
            api_keys=stored_keys,
            base_urls=stored_urls,
        )
    )
    return current


def get_base_url(provider: str) -> str | None:
    """Return the stored custom base URL for a provider, or None."""
    value = load_llm_settings()["base_urls"].get(provider.strip().lower())
    return value if isinstance(value, str) and value.strip() else None


def base_urls() -> dict[str, str | None]:
    """Map of provider -> custom base URL."""
    return load_llm_settings()["base_urls"]


def get_api_key(provider: str) -> str:
    """Return the stored key for a provider, or ``""`` when absent."""
    value = load_llm_settings()["api_keys"].get(provider.strip().lower())
    return value if isinstance(value, str) else ""


def has_api_key(provider: str) -> bool:
    """True when a non-empty key is stored for the provider."""
    return bool(get_api_key(provider))


def mask_key(value: str | None) -> str | None:
    """Mask a key for display: ``None``/empty → ``None``, else last 4 chars."""
    if not value:
        return None
    tail = value[-4:] if len(value) >= 4 else value
    return f"sk-...{tail}"


def keys_status() -> dict[str, bool]:
    """Map of provider → whether a key is stored (external providers only)."""
    state = _read_state()
    return {
        name: bool(state.credentials[name].api_key) if name in state.credentials else False
        for name in state.external_providers
    }


def masked_keys() -> dict[str, str | None]:
    """Map of provider → masked key (or ``None`` when absent)."""
    state = _read_state()
    out: dict[str, str | None] = {}
    for name in state.external_providers:
        credential: ProviderCredential | None = state.credentials.get(name)
        out[name] = mask_key(credential.api_key) if credential else None
    return out
