"""POST /api/v1/settings/llm/test: authenticated probe + key precedence.

Split out of ``test_llm_settings.py`` because the connection probe has its own
contract: the request must *authenticate* against the provider (an unauthenticated
``GET /models`` answers 200 on OpenRouter, which is how a dead key used to report
"Connected successfully"), and the key it uses must follow the same precedence as
``get_agent`` -- ``body.api_key > env > stored``.

Key sources are asserted by LABEL (``typed`` / ``env`` / ``stored`` / ``none``);
no test here asserts on a raw key value.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories import settings as settings_store

GROQ_BASE = "https://api.groq.com/openai/v1"
CHAT_URL = f"{GROQ_BASE}/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


@pytest.fixture()
def isolated_storage(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FASL_STORAGE_DIR", str(tmp_path))
    yield tmp_path


@pytest.fixture()
def probe_request() -> dict[str, Any]:
    """Sink filled in by the respx side effect with machine artifacts of the probe.

    Keyed by ``authorization`` (source-precedence evidence) and ``body``
    (probe-shape evidence). Left empty when no probe request was recorded.
    """
    return {}


def _record_and_reply(
    sink: dict[str, Any], status_code: int
) -> Callable[[httpx.Request], httpx.Response]:
    def _handler(request: httpx.Request) -> httpx.Response:
        sink["authorization"] = request.headers.get("authorization")
        sink["body"] = json.loads(request.content)
        return httpx.Response(status_code, json={"choices": []})

    return _handler


def test_post_llm_test_connection_invalid_provider(isolated_storage) -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/settings/llm/test",
        json={"provider": "invalid_provider", "model": "any-model"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "Unknown provider" in data["message"]


def test_post_llm_test_connection_mocked(isolated_storage, respx_mock, probe_request) -> None:
    respx_mock.post(CHAT_URL).mock(side_effect=_record_and_reply(probe_request, 200))
    client = TestClient(app)
    resp = client.post(
        "/api/v1/settings/llm/test",
        json={
            "provider": "groq",
            "model": GROQ_MODEL,
            "base_url": GROQ_BASE,
            "api_key": "gsk_test123",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "Connected successfully" in data["message"]
    assert data["latency_ms"] is not None


def test_probe_posts_authenticated_chat_completions(
    isolated_storage, respx_mock, probe_request
) -> None:
    """The probe must target POST /chat/completions with a Bearer header."""
    route = respx_mock.post(CHAT_URL).mock(side_effect=_record_and_reply(probe_request, 200))
    client = TestClient(app)

    resp = client.post(
        "/api/v1/settings/llm/test",
        json={
            "provider": "groq",
            "model": GROQ_MODEL,
            "base_url": GROQ_BASE,
            "api_key": "gsk_test123",
        },
    )

    assert resp.status_code == 200
    assert route.called, "probe must not decide success from an unauthenticated GET /models"
    assert probe_request["authorization"] == "Bearer gsk_test123"
    assert probe_request["body"] == {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }


def test_dead_key_reports_authentication_failure(isolated_storage, respx_mock) -> None:
    """Defect A: a rejected key must report failure, not "Connected successfully"."""
    respx_mock.post(CHAT_URL).respond(
        status_code=401, json={"error": {"message": "User not found"}}
    )
    client = TestClient(app)

    resp = client.post(
        "/api/v1/settings/llm/test",
        json={
            "provider": "groq",
            "model": GROQ_MODEL,
            "base_url": GROQ_BASE,
            "api_key": "gsk-dead-beef-0000",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "Authentication failed" in data["message"]
    assert "typed" in data["message"]
    assert "gsk-dead-beef-0000" not in data["message"]


def test_env_key_wins_over_stored_key(
    isolated_storage, respx_mock, probe_request, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect B: env must outrank the stored key, matching get_agent."""
    settings_store.save_llm_settings(api_keys={"groq": "gsk-stored-8888"})
    monkeypatch.setenv("GROQ_API_KEY", "gsk-env-7777")
    respx_mock.post(CHAT_URL).mock(side_effect=_record_and_reply(probe_request, 200))
    client = TestClient(app)

    resp = client.post(
        "/api/v1/settings/llm/test",
        json={"provider": "groq", "model": GROQ_MODEL, "base_url": GROQ_BASE},
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert probe_request["authorization"] == "Bearer gsk-env-7777"


def test_stored_key_is_used_when_no_env_key(
    isolated_storage, respx_mock, probe_request, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    settings_store.save_llm_settings(api_keys={"groq": "gsk-stored-8888"})
    respx_mock.post(CHAT_URL).mock(side_effect=_record_and_reply(probe_request, 200))
    client = TestClient(app)

    resp = client.post(
        "/api/v1/settings/llm/test",
        json={"provider": "groq", "model": GROQ_MODEL, "base_url": GROQ_BASE},
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert probe_request["authorization"] == "Bearer gsk-stored-8888"


def test_rate_limited_response_counts_as_connected(isolated_storage, respx_mock) -> None:
    """429 proves the key authenticated, so it is a success carrying a note."""
    respx_mock.post(CHAT_URL).respond(
        status_code=429, json={"error": {"message": "Rate limit reached"}}
    )
    client = TestClient(app)

    resp = client.post(
        "/api/v1/settings/llm/test",
        json={
            "provider": "groq",
            "model": GROQ_MODEL,
            "base_url": GROQ_BASE,
            "api_key": "gsk_test123",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "rate limit" in data["message"].lower()
    assert data["latency_ms"] is not None


def test_forbidden_without_any_key_names_no_key_source(
    isolated_storage, respx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    respx_mock.post(CHAT_URL).respond(status_code=403, json={"error": {"message": "Forbidden"}})
    client = TestClient(app)

    resp = client.post(
        "/api/v1/settings/llm/test",
        json={"provider": "groq", "model": GROQ_MODEL, "base_url": GROQ_BASE},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "Authentication failed" in data["message"]
    assert "none" in data["message"]


def test_server_error_reports_status_and_body(isolated_storage, respx_mock) -> None:
    respx_mock.post(CHAT_URL).respond(status_code=500, text="upstream exploded: " + "x" * 400)
    client = TestClient(app)

    resp = client.post(
        "/api/v1/settings/llm/test",
        json={
            "provider": "groq",
            "model": GROQ_MODEL,
            "base_url": GROQ_BASE,
            "api_key": "gsk_test123",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "500" in data["message"]
    assert "upstream exploded" in data["message"]
    assert len(data["message"]) <= 260, "upstream body must be truncated to ~200 chars"


def test_ollama_probe_stays_on_api_tags(isolated_storage, respx_mock) -> None:
    """Ollama keeps its keyless local probe; anything else would 404 on respx."""
    tags = respx_mock.get("http://localhost:11434/api/tags").respond(
        status_code=200, json={"models": []}
    )
    client = TestClient(app)

    resp = client.post(
        "/api/v1/settings/llm/test",
        json={"provider": "ollama", "model": "llama3.2"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "Ollama" in data["message"]
    assert tags.called
