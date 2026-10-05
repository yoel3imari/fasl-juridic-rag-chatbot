"""The sync SQLite engine behind the LLM settings store: URL, pragmas, bootstrap.

Split out of :mod:`app.repositories.settings_db` so each module owns one thing:
this one answers "which database, which connection, which schema", the store
answers "what do the rows mean".

Constraints it exists to satisfy:

* **Separate from the async engine.** ``app.models.base`` builds the process-wide
  *async* engine at import time. The store keeps the sync surface its 14 call
  sites compile against, so it owns this engine and never touches that one.
* **One engine per resolved URL.** Two tmp databases in one pytest process must
  not share a connection or leak into each other; ``_reset_engine_cache`` is the
  test seam that guarantees it.
* **Pragmas on connect.** ``busy_timeout`` absorbs contention (there is no WAL:
  switching ``journal_mode`` inside an alembic transaction is unsafe), and
  ``foreign_keys=ON`` is what makes ``ON DELETE CASCADE`` on
  ``llm_provider_credentials.provider`` do anything at all in SQLite (measured).
* **BEGIN IMMEDIATE for writers.** ``isolation_level=None`` hands transaction
  control to SQLAlchemy so the ``begin`` hook can pick the flavour: writers take
  the write lock up front instead of failing to upgrade a deferred read
  transaction, which is SQLite's un-retryable ``database is locked``.
* **Self-bootstrap.** The three LLM tables are created with ``checkfirst=True``
  and the provider registry is seeded with ``INSERT OR IGNORE`` on first use, so
  LLM settings work before alembic has ever run. Alembic 0007 is
  inspector-guarded, so the two DDL paths converge instead of colliding.
* **0600 on the file.** The database now holds API keys, so the file is tightened
  to owner-only on first use, best-effort (a read-only mount must not break
  reads).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection, make_url

from app.domain.privacy import EXTERNAL_PROVIDER_IDS, LOCAL_PROVIDERS
from app.models.base import Base
from app.models.llm_config import (
    LLMActiveSettings,
    LLMProvider,
    LLMProviderCredential,
)

_BUSY_TIMEOUT_S: Final = 10
_BUSY_TIMEOUT_MS: Final = _BUSY_TIMEOUT_S * 1000

# Provider order matters: llm_provider_credentials has a foreign key to
# llm_providers.name, so the referenced table is always created first.
_LLM_MODELS: Final = (LLMProvider, LLMProviderCredential, LLMActiveSettings)
_REGISTRY_SEED: Final[tuple[tuple[str, str], ...]] = tuple(
    [(name, "external") for name in EXTERNAL_PROVIDER_IDS]
    + [(name, "local") for name in sorted(LOCAL_PROVIDERS)]
)
_SQL_SEED_PROVIDER: Final = text(
    "INSERT OR IGNORE INTO llm_providers (name, kind) VALUES (:name, :kind)"
)
_ENGINES: dict[str, Engine] = {}


def _resolve_database_url() -> str:
    """Resolve the sync URL from ``DATABASE_URL`` (env first, then app config).

    The async driver segment is stripped (``sqlite+aiosqlite:///x.db`` ->
    ``sqlite:///x.db``) because this store brings its own sync engine. An
    in-memory database is refused by name: every connection would open a
    different, empty database, so settings would silently vanish between reads.
    """
    raw = os.getenv("DATABASE_URL")
    if not raw:
        from app.config import settings

        raw = settings.DATABASE_URL
    scheme, separator, rest = raw.partition("://")
    resolved = f"{scheme.split('+', 1)[0]}://{rest}" if separator else raw
    if make_url(resolved).database in (None, "", ":memory:"):
        raise RuntimeError(
            "the LLM settings store refuses an in-memory SQLite database "
            f"(resolved DATABASE_URL={resolved!r}): each connection would open a "
            "separate empty database, so stored settings would disappear between "
            "reads. Point DATABASE_URL at a file, e.g. sqlite:///./data/matters.db."
        )
    return resolved


def _configure(engine: Engine) -> None:
    """Take over transaction control and pin the pragmas the store depends on."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        # isolation_level=None stops pysqlite from emitting its own BEGIN, so the
        # "begin" hook below decides which flavour each transaction gets.
        dbapi_connection.isolation_level = None
        dbapi_connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Connection) -> None:
        immediate = bool(conn.get_execution_options().get("begin_immediate"))
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


def _restrict_permissions(engine: Engine) -> None:
    """Best-effort 0600 on the database file: it holds API keys."""
    database = engine.url.database
    if not database:
        return
    try:
        os.chmod(database, 0o600)
    except OSError:
        pass


def _bootstrap(engine: Engine) -> None:
    """Create the three LLM tables and seed the registry; idempotent."""
    with engine.execution_options(begin_immediate=True).begin() as conn:
        for model in _LLM_MODELS:
            # Base.metadata is the ORM's own view of the tables, so the store
            # bootstraps exactly the schema the models declare.
            Base.metadata.tables[model.__tablename__].create(bind=conn, checkfirst=True)
        for name, kind in _REGISTRY_SEED:
            conn.execute(_SQL_SEED_PROVIDER, {"name": name, "kind": kind})
    _restrict_permissions(engine)


def engine() -> Engine:
    """Return the cached, self-bootstrapped sync engine for the resolved URL."""
    url = _resolve_database_url()
    cached = _ENGINES.get(url)
    if cached is not None:
        return cached
    database = make_url(url).database or ""
    if database:
        try:
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # the connect below reports the real problem
    fresh = create_engine(url, connect_args={"timeout": _BUSY_TIMEOUT_S})
    _configure(fresh)
    try:
        _bootstrap(fresh)
    except BaseException:
        # Never cache an engine whose bootstrap failed: the next call must retry.
        fresh.dispose()
        raise
    _ENGINES[url] = fresh
    return fresh


def _reset_engine_cache() -> None:
    """Drop every cached engine (test seam: isolates one tmp DB from the next)."""
    for cached in _ENGINES.values():
        cached.dispose()
    _ENGINES.clear()
