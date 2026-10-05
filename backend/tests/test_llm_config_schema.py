"""Parity between alembic 0007 and the ORM tables in ``app.models.llm_config``.

Two builders must produce interchangeable schemas:

* DB-A -- ``alembic upgrade head`` (the production path), which also seeds the
  provider registry.
* DB-B -- ``Table.create(bind, checkfirst=True)``, the self-bootstrap the settings
  store uses so LLM settings work before migrations have ever run.

The columns are read with raw ``PRAGMA table_info`` over stdlib sqlite3 so the
assertion sees the file the migrations produced, not SQLAlchemy's reflection of
its own models. The seeded registry is pinned against ``KNOWN_PROVIDERS`` and the
``kind`` column against ``SUPPORTED_KEY_PROVIDERS`` -- ``keys_status()`` /
``masked_keys()`` filter ``WHERE kind='external'``, so ``kind`` is read live, not
decoration.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.domain.privacy import LOCAL_PROVIDERS
from app.infrastructure.llm.agent import KNOWN_PROVIDERS
from app.models.llm_config import (
    LLMActiveSettings,
    LLMProvider,
    LLMProviderCredential,
)
from app.repositories.settings import SUPPORTED_KEY_PROVIDERS

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LLM_TABLES = ("llm_providers", "llm_provider_credentials", "llm_active_settings")
ORM_TABLES = (
    LLMProvider.__table__,
    LLMProviderCredential.__table__,
    LLMActiveSettings.__table__,
)


def _alembic(*args: str, db_path: Path) -> subprocess.CompletedProcess[str]:
    """Run alembic from ``backend/`` against ``db_path`` (alembic.ini paths are relative)."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _create_with_orm(db_path: Path) -> None:
    """Build DB-B: the store's self-bootstrap path, idempotent by construction."""
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            for table in ORM_TABLES:
                table.create(bind=conn, checkfirst=True)
    finally:
        engine.dispose()


def _column_names(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _seeded_kinds(db_path: Path) -> dict[str, str]:
    with sqlite3.connect(db_path) as conn:
        return dict(conn.execute("SELECT name, kind FROM llm_providers"))


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """DB-A: created by alembic alone, 0001..0007."""
    db_path = tmp_path_factory.mktemp("alembic-built") / "matters.db"
    result = _alembic("upgrade", "head", db_path=db_path)
    assert result.returncode == 0, result.stderr
    return db_path


def test_migration_seeds_exactly_the_known_providers(migrated_db: Path) -> None:
    # Given: a database migrated by alembic alone.
    # When: the provider registry is read.
    # Then: it holds exactly KNOWN_PROVIDERS, nothing extra, nothing missing.
    assert set(_seeded_kinds(migrated_db)) == set(KNOWN_PROVIDERS)


def test_external_kind_subset_equals_supported_key_providers(migrated_db: Path) -> None:
    # Given: the same registry.
    # When: rows are split by the kind column.
    # Then: kind='external' is exactly SUPPORTED_KEY_PROVIDERS and kind='local' is
    # exactly LOCAL_PROVIDERS -- the split keys_status()/masked_keys() filter on.
    kinds = _seeded_kinds(migrated_db)
    assert {name for name, kind in kinds.items() if kind == "external"} == set(
        SUPPORTED_KEY_PROVIDERS
    )
    assert {name for name, kind in kinds.items() if kind == "local"} == set(LOCAL_PROVIDERS)


@pytest.mark.parametrize("table", LLM_TABLES)
def test_migration_and_orm_build_identical_columns(
    migrated_db: Path, tmp_path: Path, table: str
) -> None:
    # Given: DB-A from alembic and DB-B from the ORM self-bootstrap.
    # When: PRAGMA table_info is read for one LLM-config table from each.
    # Then: the column-name sets are identical.
    orm_db = tmp_path / "orm-built.db"
    _create_with_orm(orm_db)
    assert _column_names(orm_db, table) == _column_names(migrated_db, table)


def test_migration_rerun_on_already_migrated_db_exits_zero(tmp_path: Path) -> None:
    # Given: a database already at head.
    # When: alembic upgrade head runs a second time.
    # Then: exit 0, no "already exists" -- re-running is safe.
    db_path = tmp_path / "rerun.db"
    assert _alembic("upgrade", "head", db_path=db_path).returncode == 0
    second = _alembic("upgrade", "head", db_path=db_path)
    assert second.returncode == 0, second.stderr
    assert "already exists" not in (second.stderr + second.stdout).lower()


def test_migration_upgrades_over_orm_bootstrapped_tables(tmp_path: Path) -> None:
    # Given: tables already created by the store self-bootstrap and stamped at 0006
    # (the dev who ran the app before migrating).
    # When: alembic upgrades to head.
    # Then: exit 0 -- create_table is inspector-guarded -- and the registry is still
    # seeded, because seeding is not owned by the create_table guard.
    db_path = tmp_path / "bootstrapped.db"
    _create_with_orm(db_path)
    stamped = _alembic("stamp", "0006_conversation_matter_optional", db_path=db_path)
    assert stamped.returncode == 0, stamped.stderr
    upgraded = _alembic("upgrade", "head", db_path=db_path)
    assert upgraded.returncode == 0, upgraded.stderr
    assert set(_seeded_kinds(db_path)) == set(KNOWN_PROVIDERS)


def test_active_settings_starts_empty_after_migration(migrated_db: Path) -> None:
    # Given: a freshly migrated database.
    # When: the active-settings singleton is counted.
    # Then: 0 rows -- 0007 seeds the registry only; the store writes the row lazily,
    # so a first read before any write is not a "missing row" error.
    with sqlite3.connect(migrated_db) as conn:
        assert conn.execute("SELECT count(*) FROM llm_active_settings").fetchone()[0] == 0


def test_active_settings_rejects_any_row_but_id_one(migrated_db: Path) -> None:
    # Given: the migrated singleton table.
    # When: a row with id=2 is inserted.
    # Then: the CHECK(id=1) constraint rejects it.
    with sqlite3.connect(migrated_db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO llm_active_settings (id, updated_at) VALUES (2, 'now')")


def test_orm_bootstrap_is_idempotent(tmp_path: Path) -> None:
    # Given: a database whose LLM tables the store already created.
    # When: the self-bootstrap runs a second time.
    # Then: no error and the columns are still exactly the ones the models declare --
    # checkfirst=True is what makes the store safe before migrations have run.
    db_path = tmp_path / "bootstrap-twice.db"
    _create_with_orm(db_path)
    _create_with_orm(db_path)
    for table in ORM_TABLES:
        assert _column_names(db_path, table.name) == set(table.c.keys())
