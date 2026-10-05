"""LLM settings persistence: SQLite store + settings router + chat memory.

The store is the SQLite one, so the fixture points **both** inputs at one tmp
directory: ``DATABASE_URL`` (where settings live) and ``FASL_STORAGE_DIR`` (the
legacy JSON that is imported exactly once). ``settings_engine._reset_engine_cache``
on both sides of every test is what keeps two tmp databases from sharing an
engine. API keys in here are test doubles; no assertion prints one.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.repositories import settings as settings_store
from app.repositories import settings_engine


@pytest.fixture()
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect the store's database and its one-shot import source into tmp_path."""
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FASL_STORAGE_DIR", str(storage))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'matters.db'}")
    settings_engine._reset_engine_cache()
    yield tmp_path
    settings_engine._reset_engine_cache()


def _marker(db_path: Path) -> str | None:
    """Read the one-shot import marker with raw sqlite3 (no ORM in the way)."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT json_imported_at FROM llm_active_settings").fetchone()
    return None if row is None else row[0]


def _write_legacy(directory: Path, payload: dict[str, Any]) -> Path:
    path = directory / "llm_settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_save_load_roundtrip(isolated_store: Path) -> None:
    # Given: an empty store.
    # When: a provider, a model, and one key are saved.
    # Then: a fresh load answers all three, and only that provider has a key.
    settings_store.save_llm_settings(
        provider="openai", model="gpt-4o", api_keys={"openai": "sk-test-1234abcd"}
    )
    loaded = settings_store.load_llm_settings()
    assert loaded["provider"] == "openai"
    assert loaded["model"] == "gpt-4o"
    assert loaded["api_keys"]["openai"] == "sk-test-1234abcd"
    assert settings_store.get_api_key("openai") == "sk-test-1234abcd"
    assert settings_store.has_api_key("openai") is True
    assert settings_store.has_api_key("groq") is False


def test_database_created_with_0600(isolated_store: Path) -> None:
    # Given: a store whose database file does not exist yet.
    # When: the first save creates it.
    # Then: the file is owner-only -- it holds API keys, like the JSON file was.
    settings_store.save_llm_settings(provider="ollama", model="llama3.2")
    database = isolated_store / "matters.db"
    assert database.exists()
    assert oct(os.stat(database).st_mode & 0o777) == "0o600"


def test_empty_string_deletes_key(isolated_store: Path) -> None:
    # Given: a stored key.
    # When: it is cleared with "" and again with None, and set in between.
    # Then: an empty or None value always leaves no key behind.
    settings_store.save_llm_settings(api_keys={"groq": "gsk-secret"})
    assert settings_store.has_api_key("groq") is True
    settings_store.save_llm_settings(api_keys={"groq": ""})
    assert settings_store.has_api_key("groq") is False
    settings_store.save_llm_settings(api_keys={"groq": "gsk-again"})
    settings_store.save_llm_settings(api_keys={"groq": None})
    assert settings_store.has_api_key("groq") is False


def test_masked_status(isolated_store: Path) -> None:
    # Given: one stored key.
    # When: the masked views are built from llm_providers.kind='external'.
    # Then: only that provider reports True / a mask, and the raw key never leaks.
    settings_store.save_llm_settings(api_keys={"openai": "sk-test-1234abcd"})
    status = settings_store.keys_status()
    assert status["openai"] is True
    assert status["groq"] is False
    masked = settings_store.masked_keys()
    assert masked["openai"] == "sk-...abcd"
    assert masked["groq"] is None
    assert "sk-test-1234abcd" not in json.dumps(masked)


def test_missing_database_returns_defaults(isolated_store: Path) -> None:
    # Given: a database that has never been written.
    # When: settings are loaded.
    # Then: defaults, not an error -- the contract a missing JSON file had.
    loaded = settings_store.load_llm_settings()
    assert loaded["provider"] is None
    assert loaded["model"] is None


def test_corrupt_legacy_json_returns_defaults_and_stamps_marker(isolated_store: Path) -> None:
    # Given: an unparseable legacy JSON file.
    # When: settings are loaded twice.
    # Then: both loads answer defaults and the one-shot marker is stamped, so the
    # broken file is never retried.
    path = _write_legacy(isolated_store / "storage", {})
    path.write_text("{not valid json", encoding="utf-8")
    assert settings_store.load_llm_settings()["provider"] is None
    assert _marker(isolated_store / "matters.db") is not None
    assert settings_store.has_api_key("openai") is False
    assert settings_store.load_llm_settings()["provider"] is None


def test_non_dict_legacy_json_returns_defaults(isolated_store: Path) -> None:
    # Given: a legacy JSON file holding a list, not an object.
    # When: settings are loaded.
    # Then: defaults, and the marker is still stamped.
    _write_legacy(isolated_store / "storage", {})
    (isolated_store / "storage" / "llm_settings.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert settings_store.load_llm_settings()["model"] is None
    assert _marker(isolated_store / "matters.db") is not None


def test_legacy_json_imported_once_and_never_rewritten(isolated_store: Path) -> None:
    # Given: a legacy JSON holding a provider, a model, and a key.
    path = _write_legacy(
        isolated_store / "storage",
        {"provider": "openrouter", "model": "legacy/model", "api_keys": {"groq": "gsk-legacy"}},
    )
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    # When: the first load imports it, then the key is cleared and read again.
    assert settings_store.load_llm_settings()["provider"] == "openrouter"
    assert settings_store.get_api_key("groq") == "gsk-legacy"
    settings_store.save_llm_settings(api_keys={"groq": ""})
    # Then: the clear sticks (the marker blocks a second import) and the file is
    # untouched on disk -- bytes and mtime both.
    assert settings_store.has_api_key("groq") is False
    assert settings_store.load_llm_settings()["provider"] == "openrouter"
    assert _marker(isolated_store / "matters.db") is not None
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_legacy_json_with_unknown_base_url_name_still_imports_once(isolated_store: Path) -> None:
    # Given: a legacy JSON holding representable settings AND a base_urls entry for
    # a provider that does not exist -- the file is user-owned by design, so a
    # hand-edit, a fork, or a downgraded build can put any name in it.
    path = _write_legacy(
        isolated_store / "storage",
        {
            "provider": "openrouter",
            "model": "legacy/model",
            "api_keys": {"groq": "gsk-legacy"},
            "base_urls": {
                "groq": "https://api.groq.com/openai/v1",
                "skynet": "http://typo.example",
            },
        },
    )
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    # When: settings are loaded twice.
    first = settings_store.load_llm_settings()
    second = settings_store.load_llm_settings()
    # Then: the representable settings are imported and readable, the junk name is
    # skipped instead of failing the whole import, and the marker is stamped -- so
    # the second load is a plain read, not a retried doomed import.
    assert first["provider"] == "openrouter"
    assert first["model"] == "legacy/model"
    assert settings_store.get_api_key("groq") == "gsk-legacy"
    assert settings_store.has_api_key("groq") is True
    assert settings_store.base_urls() == {"groq": "https://api.groq.com/openai/v1"}
    assert "skynet" not in settings_store.base_urls()
    assert _marker(isolated_store / "matters.db") is not None
    assert second == first
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_permanent_import_failure_stamps_marker_and_never_retries(isolated_store: Path) -> None:
    # Given: a legacy JSON naming a provider whose registry row is gone. The
    # credentials FK is live, so this payload is *permanently* unstorable -- no
    # future read can make it succeed.
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    path = _write_legacy(
        isolated_store / "storage",
        {
            "provider": "openrouter",
            "model": "legacy/model",
            "api_keys": {"groq": "gsk-legacy-0002"},
            "base_urls": {"groq": "https://api.groq.com/openai/v1"},
        },
    )
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    database = isolated_store / "matters.db"
    settings_engine.engine()  # create the tables + registry without importing yet
    with sqlite3.connect(database) as conn:
        conn.execute("DELETE FROM llm_providers WHERE name = 'groq'")
    # Then: writing that provider's credential really raises IntegrityError (the FK
    # is live and the class is the one the stamp branch is written for), so this is
    # the permanent failure mode and not an environment hiccup.
    with pytest.raises(IntegrityError):
        with settings_engine.engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO llm_provider_credentials (provider, api_key)"
                    " VALUES (:provider, :api_key)"
                ),
                {"provider": "groq", "api_key": "x"},
            )
    # When: settings are loaded twice.
    first = settings_store.load_llm_settings()
    second = settings_store.load_llm_settings()
    # Then: no exception, and the documented tradeoff -- the payload is DROPPED (a
    # rolled-back transaction cannot keep half of it, and a pre-existing row would
    # survive the COALESCE) while the marker IS consumed, so the file is retired
    # instead of wedging every later read.
    assert first["provider"] is None
    assert first["model"] is None
    assert settings_store.has_api_key("groq") is False
    assert _marker(database) is not None
    assert second == first
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM llm_provider_credentials").fetchone()[0] == 0
    # And the registry heals for whoever runs next: a fresh engine re-seeds it.
    settings_engine._reset_engine_cache()
    settings_engine.engine()
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM llm_providers").fetchone()[0] == 6


def test_transient_import_failure_leaves_marker_unset_and_imports_later(
    isolated_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a healthy database whose import claim statement is poisoned, which is
    # what a locked database looks like from inside the transaction.
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.repositories import settings_db

    path = _write_legacy(
        isolated_store / "storage",
        {
            "provider": "openrouter",
            "model": "legacy/model",
            "api_keys": {"groq": "gsk-legacy-0002"},
        },
    )
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    database = isolated_store / "matters.db"
    settings_engine.engine()
    healthy = settings_db._SQL_CLAIM_IMPORT
    poisoned = text("SELECT no_such_column FROM llm_active_settings")
    monkeypatch.setattr(settings_db, "_SQL_CLAIM_IMPORT", poisoned)
    # Then: the injected fault is really an OperationalError -- the transient
    # class, not the permanent one the test above covers.
    with pytest.raises(OperationalError):
        with settings_engine.engine().connect() as conn:
            conn.execute(poisoned)
    # When: settings are loaded with that fault in place.
    degraded = settings_store.load_llm_settings()
    # Then: the read degrades instead of raising, and the marker is NOT spent --
    # a locked database is not the file's fault, so the import stays pending.
    assert degraded["provider"] is None
    assert _marker(database) is None
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    # When: the database is usable again.
    monkeypatch.setattr(settings_db, "_SQL_CLAIM_IMPORT", healthy)
    recovered = settings_store.load_llm_settings()
    # Then: the very next read imports the file and stamps the marker -- the
    # failure cost a retry, not the user's settings.
    assert recovered["provider"] == "openrouter"
    assert recovered["model"] == "legacy/model"
    assert settings_store.has_api_key("groq") is True
    assert _marker(database) is not None


def test_engine_cache_isolates_two_databases(
    isolated_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: one store written into database A.
    settings_store.save_llm_settings(provider="openai", model="gpt-4o")
    # When: DATABASE_URL is repointed at database B, then back at A.
    other = isolated_store / "other.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{other}")
    # Then: B answers defaults while A keeps its row -- the cache is keyed by URL.
    assert settings_store.load_llm_settings()["provider"] is None
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{isolated_store / 'matters.db'}")
    assert settings_store.load_llm_settings()["provider"] == "openai"
    assert settings_store.load_llm_settings()["model"] == "gpt-4o"


def test_memory_database_url_is_refused(
    isolated_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an in-memory DATABASE_URL.
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    # When: the store is read.
    # Then: a RuntimeError naming the reason -- every connection would be a
    # different, empty database, so settings would silently vanish.
    with pytest.raises(RuntimeError, match="in-memory"):
        settings_store.load_llm_settings()
    with pytest.raises(RuntimeError, match="in-memory"):
        settings_store.save_llm_settings(provider="openai")


def test_database_deleted_mid_run_degrades_to_defaults(isolated_store: Path) -> None:
    # Given: a populated store whose database file then disappears.
    settings_store.save_llm_settings(provider="openai", model="gpt-4o")
    (isolated_store / "matters.db").unlink()
    settings_engine._reset_engine_cache()
    # When: settings are read again.
    # Then: defaults, not an exception -- a fresh process on a lost database sees
    # the same thing.
    loaded = settings_store.load_llm_settings()
    assert loaded["provider"] is None
    assert loaded["model"] is None


def test_concurrent_reads_never_report_a_locked_database(isolated_store: Path) -> None:
    # Given: a populated store.
    settings_store.save_llm_settings(
        provider="groq", model="llama-3.3-70b-versatile", api_keys={"groq": "gsk-concurrent"}
    )
    failures: list[str] = []

    def _read() -> None:
        try:
            for _ in range(25):
                settings_store.load_llm_settings()
                settings_store.keys_status()
                settings_store.masked_keys()
        except Exception as exc:  # noqa: BLE001 - the assertion reports the message
            failures.append(f"{type(exc).__name__}: {exc}")

    # When: eight threads read (and one writes) the same database at once.
    threads = [threading.Thread(target=_read) for _ in range(7)]
    threads.append(threading.Thread(target=lambda: settings_store.save_llm_settings(model="m2")))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    # Then: nobody is still running and no thread saw "database is locked" --
    # busy_timeout + a short BEGIN IMMEDIATE is what buys that.
    assert [t.name for t in threads if t.is_alive()] == []
    assert failures == []
    assert "locked" not in " ".join(failures)


def test_get_put_llm_settings(isolated_store: Path) -> None:
    # Given: the settings router.
    client = TestClient(app)
    # When: the current settings are read.
    resp = client.get("/api/v1/settings/llm")
    # Then: the documented response shape is there.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "current_provider" in body
    assert "current_model" in body
    assert "privacy_mode" in body
    assert "keys_status" in body
    assert "masked_keys" in body

    # When: a provider, model, and key are written.
    put = client.put(
        "/api/v1/settings/llm",
        json={
            "provider": "openai",
            "model": "gpt-4o",
            "api_keys": {"openai": "sk-live-9999wxyz"},
        },
    )
    # Then: the response echoes them, masks the key, and never echoes it raw.
    assert put.status_code == 200, put.text
    data = put.json()
    assert data["current_provider"] == "openai"
    assert data["current_model"] == "gpt-4o"
    assert data["keys_status"]["openai"] is True
    assert data["masked_keys"]["openai"] == "sk-...wxyz"
    assert "sk-live-9999wxyz" not in put.text

    get2 = client.get("/api/v1/settings/llm")
    assert get2.json()["current_provider"] == "openai"
    assert get2.json()["current_model"] == "gpt-4o"


def test_put_rejects_unknown_provider(isolated_store: Path) -> None:
    # Given: an unknown provider name.
    client = TestClient(app)
    # When: it is PUT.
    resp = client.put("/api/v1/settings/llm", json={"provider": "skynet"})
    # Then: 422 -- rejected at the boundary, never stored.
    assert resp.status_code == 422


def test_put_rejects_bad_model(isolated_store: Path) -> None:
    # Given: a model with whitespace and an empty model.
    client = TestClient(app)
    # When: each is PUT.
    resp = client.put("/api/v1/settings/llm", json={"model": "has space"})
    resp2 = client.put("/api/v1/settings/llm", json={"model": ""})
    # Then: both are 422.
    assert resp.status_code == 422
    assert resp2.status_code == 422


def test_put_rejects_unknown_key_provider(isolated_store: Path) -> None:
    # Given: an api_keys entry for a provider outside the external registry.
    client = TestClient(app)
    # When: it is PUT.
    resp = client.put("/api/v1/settings/llm", json={"api_keys": {"skynet": "x"}})
    # Then: 422.
    assert resp.status_code == 422


def test_agent_injects_stored_keys(isolated_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a key stored in the database and no matching env vars.
    settings_store.save_llm_settings(api_keys={"openai": "sk-inject-abcd"})
    for var in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from app.infrastructure.llm import agent as agent_mod

    seen: dict[str, Any] = {}

    class _FakeAgent:
        def __init__(self, model_string: str) -> None:
            seen["model"] = model_string
            self.model_name = model_string

    monkeypatch.setattr("pydantic_ai.Agent", _FakeAgent)
    # When: an agent is built for that provider.
    agent = agent_mod.get_agent(provider="openai", model="gpt-4o")
    # Then: the stored key is injected into the environment.
    assert agent.model_name == "openai:gpt-4o"
    assert os.environ.get("OPENAI_API_KEY") == "sk-inject-abcd"


def test_agent_does_not_override_existing_env(
    isolated_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: both a stored key and an env key for the same provider.
    settings_store.save_llm_settings(api_keys={"groq": "gsk-stored"})
    monkeypatch.setenv("GROQ_API_KEY", "gsk-env")
    from app.infrastructure.llm import agent as agent_mod

    class _FakeAgent:
        def __init__(self, model_string: str) -> None:
            self.model_name = model_string

    monkeypatch.setattr("pydantic_ai.Agent", _FakeAgent)
    # When: an agent is built.
    agent_mod.get_agent(provider="groq", model="llama-3.3-70b-versatile")
    # Then: env wins -- the precedence get_agent documents.
    assert os.environ.get("GROQ_API_KEY") == "gsk-env"


def test_agent_injects_google_both_vars(
    isolated_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a stored google key and neither env var set.
    settings_store.save_llm_settings(api_keys={"google": "gem-key-1234"})
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from app.infrastructure.llm import agent as agent_mod

    class _FakeAgent:
        def __init__(self, model_string: str) -> None:
            self.model_name = model_string

    monkeypatch.setattr("pydantic_ai.Agent", _FakeAgent)
    # When: an agent is built for google.
    agent_mod.get_agent(provider="google", model="gemini-2.0-flash")
    # Then: both spellings receive the stored key.
    assert os.environ.get("GEMINI_API_KEY") == "gem-key-1234"
    assert os.environ.get("GOOGLE_API_KEY") == "gem-key-1234"


def test_chat_remembers_last_used(isolated_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/v1/chat persists provider/model server-side (store update)."""
    import anyio

    from app.api import deps as deps_mod
    from app.models import Base, Matter
    from app.models.base import get_db

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    anyio.run(_init)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _seed() -> None:
        async with factory() as sess:
            sess.add(
                Matter(
                    id=7,
                    title="Synthetic matter",
                    matter_type="labor",
                    jurisdiction="casablanca",
                    language="ar",
                )
            )
            await sess.commit()

    anyio.run(_seed)

    async def _override_db():
        async with factory() as sess:
            yield sess

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setenv("MATTER_PRIVACY_MODE", "strict")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.2")

    class _FakeStream:
        def __init__(self, agent: Any, prompt: str) -> None:
            self._agent = agent
            self._prompt = prompt

        async def __aenter__(self) -> _FakeStream:
            self._agent.calls.append(self._prompt)
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def stream_text(self, delta: bool = True):  # type: ignore[no-untyped-def]
            yield "hello there"

    class _FakeAgent:
        model_name = "fake:model"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def run_stream(self, prompt: str) -> _FakeStream:
            return _FakeStream(self, prompt)

    class _FakeEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    class _FakeStore:
        def hybrid_query(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr(deps_mod, "get_store", lambda: _FakeStore())
    monkeypatch.setattr(deps_mod, "get_embedder", lambda: _FakeEmbedder())
    from app.infrastructure import llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_agent", lambda **kwargs: _FakeAgent())

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/chat",
                json={
                    "matter_id": 7,
                    "content": "hello",
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                },
            )
            assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()
        anyio.run(engine.dispose)

    # Then: the chat provider/model landed in the settings store.
    stored = settings_store.load_llm_settings()
    assert stored["provider"] == "openai"
    assert stored["model"] == "gpt-4o-mini"


def test_put_and_get_llm_settings_base_urls(isolated_store: Path) -> None:
    # Given: a base-URL override PUT for a provider with no API key.
    client = TestClient(app)
    resp = client.put(
        "/api/v1/settings/llm",
        json={
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "base_urls": {
                "groq": "https://api.groq.com/openai/v1",
            },
        },
    )
    # Then: both the PUT response and a later GET report it.
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_provider"] == "groq"
    assert data["current_model"] == "llama-3.3-70b-versatile"
    assert data["base_urls"]["groq"] == "https://api.groq.com/openai/v1"

    get_resp = client.get("/api/v1/settings/llm")
    assert get_resp.status_code == 200
    assert get_resp.json()["base_urls"]["groq"] == "https://api.groq.com/openai/v1"
    assert settings_store.get_base_url("groq") == "https://api.groq.com/openai/v1"


def test_clearing_a_base_url_removes_it(isolated_store: Path) -> None:
    # Given: two stored base URLs.
    settings_store.save_llm_settings(
        base_urls={
            "groq": "https://api.groq.com/openai/v1",
            "ollama": "http://localhost:11434/v1",
        }
    )
    # When: one is cleared with an empty value.
    settings_store.save_llm_settings(base_urls={"groq": ""})
    # Then: only that one is gone; the other survives the merge.
    assert settings_store.get_base_url("groq") is None
    assert settings_store.get_base_url("ollama") == "http://localhost:11434/v1"
    assert "groq" not in settings_store.base_urls()


def test_a_cleared_row_never_resurrects_the_legacy_file(isolated_store: Path) -> None:
    # Given: a legacy JSON imported once, then a deliberate clear of the provider.
    _write_legacy(isolated_store / "storage", {"provider": "openrouter", "model": "legacy/model"})
    assert settings_store.load_llm_settings()["provider"] == "openrouter"
    settings_store.save_llm_settings(api_keys={"openrouter": "sk-cleared"})
    settings_store.save_llm_settings(api_keys={"openrouter": ""})
    # When: settings are read again after the marker row is re-created empty by a
    # save that clears the last key.
    settings_store.save_llm_settings(model="gpt-4o")
    loaded = settings_store.load_llm_settings()
    # Then: the marker is still set, so nothing comes back from the file.
    assert _marker(isolated_store / "matters.db") is not None
    assert loaded["model"] == "gpt-4o"
    assert settings_store.has_api_key("openrouter") is False
