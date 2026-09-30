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

from tests.test_dual_rag import client, db_session_factory

__all__ = ["client", "db_session_factory"]
