"""End-to-end proof: LLM config survives a process boundary, and the legacy JSON dies.

``tests/test_llm_settings.py`` proves what the store does *inside* one process.
This file proves the two things only a fresh reader can prove:

* what ``PUT /api/v1/settings/llm`` wrote is still there after **every cached
  engine has been disposed** and a brand-new ``TestClient`` has booted against
  the same ``DATABASE_URL`` -- the in-process stand-in for a restart. The real
  kill/restart across two OS processes is the live receipt in
  ``.omo/evidence/sqlite-config-persistence/05/``; a value that only lives in a
  warm engine would pass every other suite and fail there.
* the legacy ``llm_settings.json`` is byte-identical afterwards (bytes *and*
  mtime), and a key the user cleared does not come back from that file.

Every claim is checked against the database with a **separate** ``sqlite3``
connection, not only against the HTTP body: a 200 carrying a plausible body is
exactly the misleading-success shape this file refuses to accept. API keys here
are test doubles and no assertion prints one.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories import settings_engine

_PROOF_KEY = "gsk-persist-0001"
_LEGACY_KEY = "gsk-legacy-0002"
_PUT_BODY: dict[str, Any] = {
    "provider": "groq",
    "model": "restart-proof",
    "api_keys": {"groq": _PROOF_KEY},
}


@pytest.fixture()
def restartable_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point both store inputs at one tmp dir: the database and the import source."""
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FASL_STORAGE_DIR", str(storage))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'matters.db'}")
    settings_engine._reset_engine_cache()
    yield tmp_path
    settings_engine._reset_engine_cache()


def _seed_legacy_json(root: Path) -> Path:
    """Write the legacy file the one-shot import reads; return its path."""
    path = root / "storage" / "llm_settings.json"
    path.write_text(
        json.dumps(
            {
                "provider": "openrouter",
                "model": "legacy/model",
                "api_keys": {"groq": _LEGACY_KEY},
            }
        ),
        encoding="utf-8",
    )
    return path


def _active_row(database: Path) -> tuple[str | None, str | None, str | None]:
    """Read the singleton row through a raw sqlite3 connection: no ORM in the way."""
    with sqlite3.connect(database) as conn:
        row = conn.execute(
            "SELECT provider, model, json_imported_at FROM llm_active_settings"
        ).fetchone()
    return (None, None, None) if row is None else row


def _boot_and_get() -> dict[str, Any]:
    """Boot a brand-new client (lifespan included) and GET the LLM settings."""
    with TestClient(app) as client:
        resp = client.get("/api/v1/settings/llm")
        assert resp.status_code == 200, resp.text
        return dict(resp.json())


def test_a_put_survives_disposing_every_cached_engine(restartable_store: Path) -> None:
    # Given: a legacy JSON file, and a client whose first read imports it.
    legacy = _seed_legacy_json(restartable_store)
    database = restartable_store / "matters.db"
    with TestClient(app) as client:
        assert client.get("/api/v1/settings/llm").json()["current_provider"] == "openrouter"
        before = (legacy.read_bytes(), legacy.stat().st_mtime_ns)
        # When: a new provider, model and key are written over the imported ones.
        put = client.put("/api/v1/settings/llm", json=_PUT_BODY)
    # Then: the row on disk holds the new values, the response masks the key, and
    # the JSON file is untouched -- bytes AND mtime, so a 200 is not accepted as
    # proof that nothing was rewritten.
    assert put.status_code == 200, put.text
    assert put.json()["current_provider"] == "groq"
    assert put.json()["masked_keys"]["groq"] == "sk-...0001"
    assert _PROOF_KEY not in put.text
    assert _active_row(database)[:2] == ("groq", "restart-proof")
    assert (legacy.read_bytes(), legacy.stat().st_mtime_ns) == before

    # And the engine that answered that PUT really is cached, so the dispose
    # below is load-bearing rather than a no-op over an empty cache.
    assert settings_engine._ENGINES != {}

    # When: every cached engine is disposed and a brand-new client boots.
    settings_engine._reset_engine_cache()
    assert settings_engine._ENGINES == {}
    after = _boot_and_get()

    # Then: the PUT is still there after the boundary, and the raw row agrees.
    assert after["current_provider"] == "groq"
    assert after["current_model"] == "restart-proof"
    assert after["masked_keys"]["groq"] == "sk-...0001"
    assert _PROOF_KEY not in json.dumps(after)
    assert _active_row(database)[:2] == ("groq", "restart-proof")


def test_a_reopened_client_reads_the_file_not_a_cached_engine(restartable_store: Path) -> None:
    # Given: a store written and read once, so this process holds a warm engine.
    _seed_legacy_json(restartable_store)
    database = restartable_store / "matters.db"
    with TestClient(app) as client:
        client.get("/api/v1/settings/llm")
        client.put("/api/v1/settings/llm", json={"provider": "groq", "model": "first-value"})
    assert settings_engine._ENGINES != {}
    assert _boot_and_get()["current_model"] == "first-value"

    # When: another process commits a different model with its own sqlite3
    # connection, while this process keeps that warm engine for the same URL.
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE llm_active_settings SET model = 'out-of-band-value'")

    # Then: the next client reports the committed value, and the row agrees --
    # a warm engine is not a snapshot, so the body cannot be stale state.
    warm = _boot_and_get()
    assert warm["current_model"] == "out-of-band-value"
    assert _active_row(database)[1] == "out-of-band-value"

    # And after disposing the engines the answer is byte-for-byte the same, so
    # no process-local copy was involved at any point.
    settings_engine._reset_engine_cache()
    assert settings_engine._ENGINES == {}
    assert _boot_and_get() == warm


def test_a_cleared_key_survives_a_restart_and_the_json_stays_byte_identical(
    restartable_store: Path,
) -> None:
    # Given: a legacy JSON imported once, then a key the user clears.
    legacy = _seed_legacy_json(restartable_store)
    database = restartable_store / "matters.db"
    with TestClient(app) as client:
        first = client.get("/api/v1/settings/llm").json()
        assert first["masked_keys"]["groq"] == "sk-...0002"
        before = (legacy.read_bytes(), legacy.stat().st_mtime_ns)
        cleared = client.put("/api/v1/settings/llm", json={"api_keys": {"groq": ""}})
    assert cleared.status_code == 200, cleared.text

    # When: every cached engine is disposed and a brand-new client boots.
    settings_engine._reset_engine_cache()
    reloaded = _boot_and_get()

    # Then: the clear sticks across the boundary -- the one-shot marker blocks a
    # re-import, so the file is a backup and not a source any more.
    assert reloaded["keys_status"]["groq"] is False
    assert reloaded["masked_keys"]["groq"] is None
    assert reloaded["current_provider"] == "openrouter"
    assert _LEGACY_KEY not in json.dumps(reloaded)
    assert _active_row(database)[2] is not None
    assert (legacy.read_bytes(), legacy.stat().st_mtime_ns) == before
