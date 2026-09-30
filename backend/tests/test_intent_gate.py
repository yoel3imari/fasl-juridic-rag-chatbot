"""Intent-gate suite: matter-optional chat + the Moroccan-legal scope gate.

Given: `POST /api/v1/chat` accepts an omitted `matter_id`, and every message
       passes an LLM intent gate before any retrieval.
When: the classifier says the message is not about Moroccan law.
Then: the stream refuses with `OUT_OF_SCOPE_REPLY` and performs ZERO
      retrieval — and the `matter_id=None` scope can never reach the
      `matter_evidence` store (the cross-matter leak guard).

Fixtures and fakes are reused from `tests/test_dual_rag` (the shared chat
harness: `client`, `db_session_factory`, `_FakeStore`, `_FakeEmbedder`,
`_FakeAgent`, `_wire`, `_events`) so no parallel harness is introduced.

All fixtures below are SYNTHETIC test data — never real legal text.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.domain.intent import (
    IN_SCOPE,
    OUT_OF_SCOPE,
    is_out_of_scope,
    parse_intent_label,
)
from app.domain.prompts import OUT_OF_SCOPE_REPLY
from app.domain.search.schemas import AUTHORITY_COLLECTION, EVIDENCE_COLLECTION
from app.services import chat as react_tools
from app.services import search as svc_mod
from tests.test_dual_rag import (
    MATTER_ID,
    _events,
    _FakeAgent,
    _FakeEmbedder,
    _FakeStore,
    _retrieval_agent,
    _wire,
)


# --------------------------------------------------------------------------- #
# recording store — test_dual_rag's store plus a complete call log, so
# "zero store calls" is asserted rather than inferred from an empty result.
# --------------------------------------------------------------------------- #
class _RecordingStore(_FakeStore):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.all_calls: list[tuple[str, int | None]] = []

    def hybrid_query(
        self,
        collection: str,
        dense: list[float],
        sparse_text: str,
        limit: int,
        matter_id: int | None = None,
    ) -> list[dict[str, Any]]:
        self.all_calls.append((collection, matter_id))
        return super().hybrid_query(collection, dense, sparse_text, limit, matter_id)


def _envelope(tool: str, query: str = "conge annuel") -> str:
    return json.dumps({"tool": tool, "query": query})


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


# --------------------------------------------------------------------------- #
# parse_intent_label — fail-open is the security-critical direction
# --------------------------------------------------------------------------- #
def test_parse_intent_label_exact_labels() -> None:
    assert parse_intent_label("in_scope") == IN_SCOPE
    assert parse_intent_label("out_of_scope") == OUT_OF_SCOPE


def test_parse_intent_label_case_and_whitespace_tolerant() -> None:
    assert parse_intent_label("  OUT_of_Scope \n") == OUT_OF_SCOPE
    assert parse_intent_label("\tIn_Scope   ") == IN_SCOPE


def test_parse_intent_label_empty_and_garbage_fail_open() -> None:
    """Empty / whitespace / nonsense must never become a refusal."""
    assert parse_intent_label("") == IN_SCOPE
    assert parse_intent_label("     ") == IN_SCOPE
    assert parse_intent_label("banana sandwich 42") == IN_SCOPE
    assert parse_intent_label("lorem ipsum dolor") == IN_SCOPE
    assert is_out_of_scope("") is False


def test_parse_intent_label_both_labels_fails_open_to_in_scope() -> None:
    """A mixed verdict must route IN_SCOPE — refusing a legal question is worse."""
    assert parse_intent_label("OUT_OF_SCOPE IN_SCOPE") == IN_SCOPE
    assert parse_intent_label("IN_SCOPE ... actually OUT_OF_SCOPE") == IN_SCOPE
    assert is_out_of_scope("OUT_OF_SCOPE IN_SCOPE") is False


# --------------------------------------------------------------------------- #
# execute_tool_call — matter_id=None must never reach the evidence store
# --------------------------------------------------------------------------- #
def test_search_matter_with_no_scope_makes_zero_store_calls() -> None:
    """The cross-matter leak guard: no scope → no evidence query at all."""
    svc_mod.clear_search_cache()
    store = _RecordingStore()
    embedder = _FakeEmbedder()
    ctx = react_tools.ToolContext(store=store, embedder=embedder, matter_id=None)

    matter, auth = anyio.run(react_tools.execute_tool_call, ctx, "search_matter", "conge annuel")

    assert matter == []
    assert auth == []
    assert store.all_calls == []
    assert embedder.calls == []


def test_search_both_with_no_scope_is_authority_only() -> None:
    svc_mod.clear_search_cache()
    store = _RecordingStore()
    embedder = _FakeEmbedder()
    ctx = react_tools.ToolContext(store=store, embedder=embedder, matter_id=None)

    matter, auth = anyio.run(react_tools.execute_tool_call, ctx, "search_both", "conge annuel")

    assert matter == []
    assert len(auth) == 1
    assert EVIDENCE_COLLECTION not in [c for c, _ in store.all_calls]
    assert store.all_calls == [(AUTHORITY_COLLECTION, None)]


def test_search_authority_still_works_without_a_matter() -> None:
    svc_mod.clear_search_cache()
    store = _RecordingStore()
    embedder = _FakeEmbedder()
    ctx = react_tools.ToolContext(store=store, embedder=embedder, matter_id=None)

    matter, auth = anyio.run(react_tools.execute_tool_call, ctx, "search_authority", "conge annuel")

    assert matter == []
    assert len(auth) == 1
    assert store.all_calls == [(AUTHORITY_COLLECTION, None)]


def test_search_matter_service_rejects_missing_matter_id() -> None:
    """The service-level guard raises BEFORE any embedder or store call."""
    svc_mod.clear_search_cache()
    store = _RecordingStore()
    embedder = _FakeEmbedder()

    async def _call() -> None:
        await svc_mod.search_matter(
            store=store,
            embedder=embedder,
            matter_id=None,
            query="conge annuel",
        )

    with pytest.raises(ValueError, match="matter_id is required"):
        anyio.run(_call)
    assert store.all_calls == []
    assert embedder.calls == []


# --------------------------------------------------------------------------- #
# HTTP — a matterless conversation persists and reopens matterless
# --------------------------------------------------------------------------- #
def test_matterless_chat_persists_and_reopens_matterless(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    embedder = _FakeEmbedder()
    _wire(monkeypatch, store, embedder, _retrieval_agent())

    resp = client.post("/api/v1/chat", json={"content": "can I quit during annual leave?"})
    assert resp.status_code == 200, resp.text
    done = next(e for e in _events(resp) if e["type"] == "done")
    conv_id = done["conversation_id"]

    detail = client.get(f"/api/v1/conversations/{conv_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["matter_id"] is None
    # the evidence half was never attempted
    assert store.seen_matter_ids == []

    # Reopening without a matter keeps it matterless (None == None passes).
    resp2 = client.post(
        "/api/v1/chat", json={"conversation_id": conv_id, "content": "annual leave"}
    )
    assert resp2.status_code == 200, resp2.text
    assert _events(resp2)[-1]["type"] == "done"

    detail2 = client.get(f"/api/v1/conversations/{conv_id}")
    assert detail2.status_code == 200
    assert detail2.json()["matter_id"] is None


# --------------------------------------------------------------------------- #
# HTTP — ownership is a (conversation_id, matter_id) pair
# --------------------------------------------------------------------------- #
def test_ownership_pairing_contract(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """None==None is admitted; a mismatch across a matter boundary is 404."""
    store = _FakeStore()
    embedder = _FakeEmbedder()
    _wire(monkeypatch, store, embedder, _retrieval_agent())

    matterless = client.post("/api/v1/chat", json={"content": "hello there"})
    assert matterless.status_code == 200, matterless.text
    matterless_id = next(e for e in _events(matterless) if e["type"] == "done")["conversation_id"]

    scoped = client.post("/api/v1/chat", json={"matter_id": MATTER_ID, "content": "conge"})
    assert scoped.status_code == 200, scoped.text
    scoped_id = next(e for e in _events(scoped) if e["type"] == "done")["conversation_id"]

    # matterless conversation adopted by a matter -> 404
    r1 = client.post(
        "/api/v1/chat",
        json={"conversation_id": matterless_id, "matter_id": MATTER_ID, "content": "x"},
    )
    assert r1.status_code == 404, r1.text
    assert r1.json()["detail"] == "conversation not found in this matter"

    # matter conversation reopened without a matter -> 404
    r2 = client.post("/api/v1/chat", json={"conversation_id": scoped_id, "content": "x"})
    assert r2.status_code == 404, r2.text
    assert r2.json()["detail"] == "conversation not found in this matter"

    # matterless + matterless -> 200
    r3 = client.post("/api/v1/chat", json={"conversation_id": matterless_id, "content": "x"})
    assert r3.status_code == 200, r3.text
    assert _events(r3)[-1]["type"] == "done"


# --------------------------------------------------------------------------- #
# HTTP — the intent gate refuses, and the prose bypass hole is closed
# --------------------------------------------------------------------------- #
def test_out_of_scope_envelope_on_round_one_refuses_without_any_tool(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _FakeStore()
    embedder = _FakeEmbedder()
    agent = _FakeAgent(script=[[_envelope("out_of_scope", "pizza recipe")]])
    _wire(monkeypatch, store, embedder, agent)

    resp = client.post("/api/v1/chat", json={"matter_id": MATTER_ID, "content": "pizza recipe"})
    assert resp.status_code == 200, resp.text
    events = _events(resp)

    assert _types(events) == ["status", "citations", "token", "token", "done"]
    done = next(e for e in events if e["type"] == "done")
    assert set(done) == {"type", "not_found", "conversation_id", "out_of_scope"}
    assert done["out_of_scope"] is True
    tokens = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert tokens == OUT_OF_SCOPE_REPLY
    # no tool execution: exactly one round, no embed, no store query
    assert len(agent.calls) == 1
    assert embedder.calls == []
    assert store.seen_matter_ids == []


def test_round_one_prose_classified_out_of_scope_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bypass hole: prose that never emits an envelope still gets gated."""
    store = _FakeStore()
    embedder = _FakeEmbedder()
    agent = _FakeAgent(script=[["Sure, here is a pasta recipe."], ["OUT_OF_SCOPE"]])
    _wire(monkeypatch, store, embedder, agent)

    resp = client.post(
        "/api/v1/chat", json={"matter_id": MATTER_ID, "content": "how to cook pasta"}
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp)

    done = next(e for e in events if e["type"] == "done")
    assert done["out_of_scope"] is True
    tokens = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert tokens == OUT_OF_SCOPE_REPLY
    assert len(agent.calls) == 2, "decision round + enforcement classification"
    assert embedder.calls == []
    assert store.seen_matter_ids == []


def test_greeting_prose_with_in_scope_classification_answers_directly(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Greeting carve-out: IN_SCOPE verdict keeps the direct answer intact."""
    store = _FakeStore(matter_hits=[], authority_hits=[])
    embedder = _FakeEmbedder()
    agent = _FakeAgent(script=[["Hello! I am FASL, your legal assistant."], ["IN_SCOPE"]])
    _wire(monkeypatch, store, embedder, agent)

    resp = client.post("/api/v1/chat", json={"matter_id": MATTER_ID, "content": "salam"})
    assert resp.status_code == 200, resp.text
    events = _events(resp)

    done = next(e for e in events if e["type"] == "done")
    assert "out_of_scope" not in done, "in-scope answers must not carry the refusal key"
    assert done["not_found"] is False
    tokens = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert "fasl" in tokens.lower()
    assert len(agent.calls) == 2, "decision round + enforcement classification"
    assert embedder.calls == []
    assert store.seen_matter_ids == []
