"""Matter-privacy guard + provider-agnostic LLM layer (TDD spec for task 6).

Red-first: this module imports app.llm, which does not exist yet.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app

MATTER_TEXT = (
    "[matter: contract p.2 \u00b64] The employee was dismissed without notice."
)


def _strict_settings(monkeypatch: pytest.MonkeyPatch, provider: str = "openai") -> None:
    monkeypatch.setenv("MATTER_PRIVACY_MODE", "strict")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")


class _RecordingAgent:
    """Fake pydantic-ai agent: records prompts instead of hitting HTTP."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.model_name = "fake:model"

    def run_stream(self, prompt: str) -> Any:
        self.calls.append(prompt)
        raise AssertionError("must use 'async with' protocol in production code path")


def test_strict_mode_blocks_external_with_matter_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict mode: matter evidence must never reach an external provider."""
    _strict_settings(monkeypatch, provider="openai")
    from app import llm as llm_mod

    fake = _RecordingAgent()
    with pytest.raises(llm_mod.PrivacyViolationError) as exc_info:
        llm_mod.check_privacy(
            MATTER_TEXT,
            provider="openai",
            privacy_mode="strict",
            consent=False,
        )
    assert "403" in str(exc_info.value) or "redact" in str(exc_info.value).lower()
    # Negative proof: nothing bearing matter text was handed to any transport.
    assert fake.calls == []


def test_strict_mode_blocks_system_role_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adversarial: matter text smuggled via the system role is also blocked."""
    _strict_settings(monkeypatch, provider="groq")
    from app import llm as llm_mod

    with pytest.raises(llm_mod.PrivacyViolationError):
        llm_mod.check_privacy(
            "summarize the case",
            provider="groq",
            privacy_mode="strict",
            system=MATTER_TEXT,
        )


def test_local_provider_always_allowed() -> None:
    from app import llm as llm_mod

    # Must not raise, even with matter evidence and no consent.
    llm_mod.check_privacy(MATTER_TEXT, provider="ollama", privacy_mode="strict")


def test_non_strict_requires_explicit_consent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app import llm as llm_mod

    with pytest.raises(llm_mod.ConsentRequiredError):
        llm_mod.check_privacy(
            MATTER_TEXT, provider="openai", privacy_mode="non-strict", consent=False
        )
    with caplog.at_level(logging.WARNING):
        llm_mod.check_privacy(
            MATTER_TEXT, provider="openai", privacy_mode="non-strict", consent=True
        )
    assert any("privacy" in rec.message.lower() for rec in caplog.records)


def test_invalid_provider_model_raises_config_error() -> None:
    from app import llm as llm_mod

    with pytest.raises(llm_mod.InvalidModelError):
        llm_mod.get_agent(provider="skynet", model="t-800")
    with pytest.raises(llm_mod.InvalidModelError):
        llm_mod.get_agent(provider="openai", model="")


def test_get_agent_builds_model_string(monkeypatch: pytest.MonkeyPatch) -> None:
    _strict_settings(monkeypatch, provider="ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.2")
    from app import llm as llm_mod

    agent = llm_mod.get_agent()
    assert "llama3.2" in agent.model_name or "ollama" in agent.model_name


def test_ws_malformed_payload_yields_structured_error() -> None:
    client = TestClient(app)
    with client.websocket_connect("/api/v1/chat/stream") as ws:
        ws.send_text("this is not json{{{")
        raw = ws.receive_text()
        frame = json.loads(raw)
        assert frame["type"] == "error"
        assert frame["code"] == "invalid_payload"


def test_ws_strict_violation_structured_error_no_external_send(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _strict_settings(monkeypatch, provider="openai")
    client = TestClient(app)
    with caplog.at_level(logging.INFO):
        with client.websocket_connect("/api/v1/chat/stream") as ws:
            ws.send_text(json.dumps({"matter_id": 1, "content": MATTER_TEXT}))
            frame = json.loads(ws.receive_text())
    assert frame["type"] == "error"
    assert frame["code"] == "privacy_violation"
    # Misleading-success check: nothing may claim matter text went external.
    assert not any(
        "sent to external provider" in rec.message.lower() for rec in caplog.records
    )


def test_ws_provider_unreachable_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import llm as llm_mod

    class _BoomAgent:
        model_name = "openai:gpt-4o-mini"

        class _Ctx:
            async def __aenter__(self) -> Any:
                raise ConnectionError("provider down")

            async def __aexit__(self, *args: Any) -> bool:
                return False

        def run_stream(self, prompt: str) -> Any:
            return self._Ctx()

    monkeypatch.setenv("MATTER_PRIVACY_MODE", "strict")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(llm_mod, "get_agent", lambda **kwargs: _BoomAgent())
    client = TestClient(app)
    with client.websocket_connect("/api/v1/chat/stream") as ws:
        ws.send_text(json.dumps({"matter_id": 1, "content": "What is Article 237?"}))
        frame = json.loads(ws.receive_text())
    assert frame["type"] == "error"
    assert frame["code"] == "provider_unreachable"
