"""LLM settings persistence: file store + settings router + chat memory."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import settings_store
from app.main import app


@pytest.fixture()
def isolated_storage(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FASL_STORAGE_DIR", str(tmp_path))
    yield tmp_path


def test_save_load_roundtrip(isolated_storage) -> None:
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


def test_file_created_with_0600(isolated_storage) -> None:
    settings_store.save_llm_settings(provider="ollama", model="llama3.2")
    path = settings_store.get_settings_path()
    assert path.exists()
    mode = oct(os.stat(path).st_mode & 0o777)
    assert mode == "0o600"


def test_empty_string_deletes_key(isolated_storage) -> None:
    settings_store.save_llm_settings(api_keys={"groq": "gsk-secret"})
    assert settings_store.has_api_key("groq") is True
    settings_store.save_llm_settings(api_keys={"groq": ""})
    assert settings_store.has_api_key("groq") is False
    settings_store.save_llm_settings(api_keys={"groq": "gsk-again"})
    settings_store.save_llm_settings(api_keys={"groq": None})
    assert settings_store.has_api_key("groq") is False


def test_masked_status(isolated_storage) -> None:
    settings_store.save_llm_settings(api_keys={"openai": "sk-test-1234abcd"})
    status = settings_store.keys_status()
    assert status["openai"] is True
    assert status["groq"] is False
    masked = settings_store.masked_keys()
    assert masked["openai"] == "sk-...abcd"
    assert masked["groq"] is None
    assert "sk-test-1234abcd" not in json.dumps(masked)


def test_corrupt_json_returns_defaults(isolated_storage) -> None:
    path = settings_store.get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    loaded = settings_store.load_llm_settings()
    assert loaded["provider"] is None
    assert loaded["model"] is None
    assert settings_store.has_api_key("openai") is False


def test_missing_file_returns_defaults(isolated_storage) -> None:
    loaded = settings_store.load_llm_settings()
    assert loaded["provider"] is None
    assert loaded["model"] is None


def test_get_put_llm_settings(isolated_storage) -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/settings/llm")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "current_provider" in body
    assert "current_model" in body
    assert "privacy_mode" in body
    assert "keys_status" in body
    assert "masked_keys" in body

    put = client.put(
        "/api/v1/settings/llm",
        json={
            "provider": "openai",
            "model": "gpt-4o",
            "api_keys": {"openai": "sk-live-9999wxyz"},
        },
    )
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


def test_put_rejects_unknown_provider(isolated_storage) -> None:
    client = TestClient(app)
    resp = client.put("/api/v1/settings/llm", json={"provider": "skynet"})
    assert resp.status_code == 422


def test_put_rejects_bad_model(isolated_storage) -> None:
    client = TestClient(app)
    resp = client.put("/api/v1/settings/llm", json={"model": "has space"})
    assert resp.status_code == 422
    resp2 = client.put("/api/v1/settings/llm", json={"model": ""})
    assert resp2.status_code == 422


def test_put_rejects_unknown_key_provider(isolated_storage) -> None:
    client = TestClient(app)
    resp = client.put("/api/v1/settings/llm", json={"api_keys": {"skynet": "x"}})
    assert resp.status_code == 422


def test_agent_injects_stored_keys(
    isolated_storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_store.save_llm_settings(api_keys={"openai": "sk-inject-abcd"})
    for var in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from app.llm import agent as agent_mod

    seen: dict[str, Any] = {}

    class _FakeAgent:
        def __init__(self, model_string: str) -> None:
            seen["model"] = model_string
            self.model_name = model_string

    monkeypatch.setattr("pydantic_ai.Agent", _FakeAgent)
    agent = agent_mod.get_agent(provider="openai", model="gpt-4o")
    assert agent.model_name == "openai:gpt-4o"
    assert os.environ.get("OPENAI_API_KEY") == "sk-inject-abcd"


def test_agent_does_not_override_existing_env(
    isolated_storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_store.save_llm_settings(api_keys={"groq": "gsk-stored"})
    monkeypatch.setenv("GROQ_API_KEY", "gsk-env")
    from app.llm import agent as agent_mod

    class _FakeAgent:
        def __init__(self, model_string: str) -> None:
            self.model_name = model_string

    monkeypatch.setattr("pydantic_ai.Agent", _FakeAgent)
    agent_mod.get_agent(provider="groq", model="llama-3.3-70b-versatile")
    assert os.environ.get("GROQ_API_KEY") == "gsk-env"


def test_agent_injects_google_both_vars(
    isolated_storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_store.save_llm_settings(api_keys={"google": "gem-key-1234"})
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from app.llm import agent as agent_mod

    class _FakeAgent:
        def __init__(self, model_string: str) -> None:
            self.model_name = model_string

    monkeypatch.setattr("pydantic_ai.Agent", _FakeAgent)
    agent_mod.get_agent(provider="google", model="gemini-2.0-flash")
    assert os.environ.get("GEMINI_API_KEY") == "gem-key-1234"
    assert os.environ.get("GOOGLE_API_KEY") == "gem-key-1234"


def test_chat_remembers_last_used(
    isolated_storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/v1/chat persists provider/model server-side (store update)."""
    import anyio

    from app.api.v1 import chat as chat_mod
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

    monkeypatch.setattr(chat_mod, "get_store", lambda: _FakeStore())
    monkeypatch.setattr(chat_mod, "get_embedder", lambda: _FakeEmbedder())
    from app import llm as llm_mod

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

    stored = settings_store.load_llm_settings()
    assert stored["provider"] == "openai"
    assert stored["model"] == "gpt-4o-mini"
