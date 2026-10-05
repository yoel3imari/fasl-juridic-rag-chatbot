"""Shared chat fixtures for suites that do not define their own.

The chat harness lives in ``tests/test_dual_rag.py`` (in-memory SQLite
engine, seeded ``MATTER_ID`` matter, and the app dependency overrides). It is
re-exported here so a suite can take ``client`` as a plain fixture parameter
without importing the test module itself — importing it would make every
``client``/``db_session_factory`` test parameter look like an F811
redefinition of the imported name.

Suites that define their own ``client`` fixture are unaffected: pytest
resolves a test module's own fixtures before the conftest ones, so the local
definition still wins.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.repositories import settings_engine
from tests.test_dual_rag import client, db_session_factory

__all__ = ["client", "db_session_factory"]


@pytest.fixture(autouse=True)
def _isolate_settings_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Keep ``app.repositories.settings`` off the developer's real database.

    The store resolves ``DATABASE_URL``, which in a test process is
    ``sqlite+aiosqlite:///./data/matters.db`` — the *live* development database.
    Any suite that calls ``save_llm_settings`` without redirecting it would
    write its test doubles (fake API keys included) into that file, so every test
    gets a session-wide tmp database instead and the engine cache is dropped on
    both sides of the switch.

    A suite that needs its own database sets ``DATABASE_URL`` itself and wins:
    autouse fixtures are set up before the module's own fixtures of the same
    scope, and ``monkeypatch`` restores whatever this fixture left behind.
    """
    previous = os.environ.get("DATABASE_URL")
    database = tmp_path_factory.mktemp("settings-store") / "matters.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    settings_engine._reset_engine_cache()
    yield database
    settings_engine._reset_engine_cache()
    if previous is None:
        del os.environ["DATABASE_URL"]
    else:
        os.environ["DATABASE_URL"] = previous
