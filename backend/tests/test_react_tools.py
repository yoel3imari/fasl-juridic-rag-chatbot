"""Unit tests for the ReAct chat-tooling helpers and the SSE token batcher.

Covers the plan's robustness/testing items that are pure functions or
service-level behavior, so they run without the full HTTP harness:

* ``parse_tool_call`` edge cases (direct JSON, envelope embedded in prose or a
  JSON array, malformed JSON, missing/blank/unknown fields).
* ``execute_tool_call``'s ``search_both`` composition (item 2): the ``both``
  result equals the two single-domain tools and issues the same store calls,
  with matter isolation preserved when ``matter_id`` is None.
* ``estimate_tokens`` / ``cap_prior_summary`` (item 3): the chars/4 proxy and
  the decision-prompt context cap.
* ``_batched_tokens`` (item 4): the frozen two-frame contract for short text
  plus multi-batch streaming for long text.
* the ``GET /api/v1/chat/metrics`` snapshot (item 7).

All fixtures below are SYNTHETIC test data — never real legal text.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api.v1.chat import _batched_tokens
from app.domain.search.schemas import AUTHORITY_COLLECTION, EVIDENCE_COLLECTION
from app.infrastructure.observability.metrics import metrics
from app.services import chat as react_tools
from app.services import search as svc_mod
from tests.test_dual_rag import (
    MATTER_ID,
    _FakeEmbedder,
    _FakeStore,
    _retrieval_agent,
    _wire,
)


def _envelope(tool: str = "search_both", query: str = "conge annuel") -> str:
    return json.dumps({"tool": tool, "query": query})


# --------------------------------------------------------------------------- #
# item 1 — parse_tool_call robustness
# --------------------------------------------------------------------------- #
def test_parse_tool_call_direct_envelope() -> None:
    assert react_tools.parse_tool_call(_envelope()) == {
        "tool": "search_both",
        "query": "conge annuel",
    }


def test_parse_tool_call_envelope_embedded_in_prose() -> None:
    """Models that wrap the envelope in a sentence are still understood."""
    text = f"Sure, let me look that up. {_envelope('search_matter', 'leave')} One moment."
    assert react_tools.parse_tool_call(text) == {"tool": "search_matter", "query": "leave"}


def test_parse_tool_call_envelope_wrapped_in_json_array() -> None:
    """A JSON array around the envelope (direct parse is a list) still parses."""
    text = json.dumps([{"tool": "search_both", "query": "arr"}])
    assert react_tools.parse_tool_call(text) == {"tool": "search_both", "query": "arr"}


def test_parse_tool_call_envelope_nested_under_a_key() -> None:
    """A top-level dict without a tool key falls through to the inner block."""
    text = json.dumps({"wrapper": {"tool": "search_authority", "query": "nested"}})
    assert react_tools.parse_tool_call(text) == {"tool": "search_authority", "query": "nested"}


def test_parse_tool_call_survives_doubled_braces() -> None:
    """Extra surrounding braces are not valid JSON but must not hide the block."""
    text = '{{"tool": "search_both", "query": "braced"}}'
    assert react_tools.parse_tool_call(text) == {"tool": "search_both", "query": "braced"}


def test_parse_tool_call_trims_query_whitespace() -> None:
    assert react_tools.parse_tool_call(_envelope(query="  spaced  ")) == {
        "tool": "search_both",
        "query": "spaced",
    }


def test_parse_tool_call_rejects_incomplete_or_unknown_fields() -> None:
    assert react_tools.parse_tool_call(json.dumps({"tool": "search_both"})) is None
    assert react_tools.parse_tool_call(_envelope(query="   ")) is None
    assert react_tools.parse_tool_call(json.dumps({"tool": "nope", "query": "x"})) is None
    assert react_tools.parse_tool_call(json.dumps({"query": "x"})) is None


def test_parse_tool_call_rejects_non_dict_and_malformed_json() -> None:
    assert react_tools.parse_tool_call('"just a string"') is None
    assert react_tools.parse_tool_call("[1, 2, 3]") is None
    assert react_tools.parse_tool_call("not json at all {{{") is None
    assert react_tools.parse_tool_call('{"tool": "search_both", "query": 7}') is None


def test_parse_tool_call_empty_input_is_a_direct_answer() -> None:
    assert react_tools.parse_tool_call("") is None
    assert react_tools.parse_tool_call("   \n  ") is None


def test_parse_tool_call_accepts_intent_envelope() -> None:
    """The out_of_scope intent verdict is a valid (non-retrieval) envelope."""
    assert react_tools.parse_tool_call(_envelope("out_of_scope", "pizza")) == {
        "tool": "out_of_scope",
        "query": "pizza",
    }


def test_is_valid_tool_call_shared_rule_set() -> None:
    assert react_tools._is_valid_tool_call({"tool": "search_both", "query": "q"}) == {
        "tool": "search_both",
        "query": "q",
    }
    assert react_tools._is_valid_tool_call({"tool": "search_both", "query": ""}) is None
    assert react_tools._is_valid_tool_call(["not", "a", "dict"]) is None


# --------------------------------------------------------------------------- #
# item 2 — search_both composes the single-domain tools
# --------------------------------------------------------------------------- #
class _RecordingStore(_FakeStore):
    """test_dual_rag's fake store plus a complete call log."""

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


def test_search_both_issues_one_call_per_domain() -> None:
    """``both`` = evidence (matter-scoped) + authority, one store call each."""
    svc_mod.clear_search_cache()
    store = _RecordingStore()
    ctx = react_tools.ToolContext(store=store, embedder=_FakeEmbedder(), matter_id=MATTER_ID)

    matter, auth = anyio.run(react_tools.execute_tool_call, ctx, "search_both", "conge annuel")

    assert len(matter) == 1 and len(auth) == 1
    assert store.all_calls == [
        (EVIDENCE_COLLECTION, MATTER_ID),
        (AUTHORITY_COLLECTION, None),
    ]


def test_search_both_equals_the_two_single_domain_tools() -> None:
    """The composed result is exactly the two tools' results, domain for domain."""
    svc_mod.clear_search_cache()
    both_ctx = react_tools.ToolContext(
        store=_RecordingStore(), embedder=_FakeEmbedder(), matter_id=MATTER_ID
    )
    both_matter, both_auth = anyio.run(
        react_tools.execute_tool_call, both_ctx, "search_both", "conge annuel"
    )

    svc_mod.clear_search_cache()
    matter_ctx = react_tools.ToolContext(
        store=_RecordingStore(), embedder=_FakeEmbedder(), matter_id=MATTER_ID
    )
    matter_only, _ = anyio.run(
        react_tools.execute_tool_call, matter_ctx, "search_matter", "conge annuel"
    )

    svc_mod.clear_search_cache()
    auth_ctx = react_tools.ToolContext(
        store=_RecordingStore(), embedder=_FakeEmbedder(), matter_id=MATTER_ID
    )
    _, auth_only = anyio.run(
        react_tools.execute_tool_call, auth_ctx, "search_authority", "conge annuel"
    )

    assert both_matter == matter_only
    assert both_auth == auth_only


def test_search_both_without_matter_never_touches_evidence() -> None:
    """Matter isolation holds through the composed path: authority only."""
    svc_mod.clear_search_cache()
    store = _RecordingStore()
    ctx = react_tools.ToolContext(store=store, embedder=_FakeEmbedder(), matter_id=None)

    matter, auth = anyio.run(react_tools.execute_tool_call, ctx, "search_both", "conge annuel")

    assert matter == []
    assert len(auth) == 1
    assert store.all_calls == [(AUTHORITY_COLLECTION, None)]


def test_execute_tool_call_rejects_non_retrieval_tool() -> None:
    svc_mod.clear_search_cache()
    ctx = react_tools.ToolContext(
        store=_RecordingStore(), embedder=_FakeEmbedder(), matter_id=MATTER_ID
    )
    with pytest.raises(ValueError, match="not a retrieval tool"):
        anyio.run(react_tools.execute_tool_call, ctx, "out_of_scope", "x")


# --------------------------------------------------------------------------- #
# item 3 — context-window estimate + prior-summary cap
# --------------------------------------------------------------------------- #
def test_estimate_tokens_is_chars_over_four() -> None:
    assert react_tools.estimate_tokens("abcd") == 1
    assert react_tools.estimate_tokens("a" * 400) == 100


def test_estimate_tokens_handles_empty_and_none() -> None:
    assert react_tools.estimate_tokens("") == 0
    assert react_tools.estimate_tokens(None) == 0


def test_cap_prior_summary_leaves_short_summary_untouched() -> None:
    summary = react_tools.summarize_hits([{"text": "short"}], [])
    assert react_tools.cap_prior_summary(summary, 10_000) == summary


def test_cap_prior_summary_truncates_and_keeps_the_counts_line() -> None:
    summary = react_tools.summarize_hits(
        [{"text": "M" * 250}, {"text": "M2" * 125}],
        [{"text": "A" * 250}],
    )
    capped = react_tools.cap_prior_summary(summary, 120)
    assert len(capped) <= 120
    assert capped.startswith("matter passages: 2, authority passages: 1.")
    assert "Excerpts:" in capped
    assert capped.endswith("…")


def test_cap_prior_summary_passthrough_for_empty_and_nonpositive_budget() -> None:
    assert react_tools.cap_prior_summary(None) is None
    assert react_tools.cap_prior_summary("") == ""
    assert react_tools.cap_prior_summary("keep me", 0) == "keep me"


# --------------------------------------------------------------------------- #
# item 4 — SSE token batching preserves the frozen two-frame contract
# --------------------------------------------------------------------------- #
async def _collect(text: str, *, batch_chars: int = 600) -> list[dict[str, Any]]:
    return [frame async for frame in _batched_tokens(text, batch_chars=batch_chars)]


def test_batched_tokens_short_text_is_the_two_half_frames() -> None:
    text = "Bonjour, comment puis-je vous aider?"
    frames = anyio.run(_collect, text)
    assert [f["type"] for f in frames] == ["token", "token"]
    assert "".join(f["text"] for f in frames) == text
    half = len(text) // 2
    assert frames[0]["text"] == text[:half]
    assert frames[1]["text"] == text[half:]


async def test_batched_tokens_medium_text_still_two_frames() -> None:
    """Texts up to 2*batch_chars keep the frozen count (the static replies)."""
    frames = await _collect("x" * 1000, batch_chars=600)
    assert len(frames) == 2


async def test_batched_tokens_long_text_streams_in_batches() -> None:
    text = "y" * 3000
    frames = await _collect(text, batch_chars=600)
    assert len(frames) > 2
    assert "".join(f["text"] for f in frames) == text
    assert all(len(f["text"]) <= 600 for f in frames)


def test_batched_tokens_empty_text_yields_one_empty_frame() -> None:
    assert anyio.run(_collect, "") == [{"type": "token", "text": ""}]


async def test_batched_tokens_nonpositive_batch_chars_falls_back() -> None:
    text = "z" * 2000
    frames = await _collect(text, batch_chars=0)
    assert "".join(f["text"] for f in frames) == text


# --------------------------------------------------------------------------- #
# item 6 — chat tuning defaults are declared and documented
# --------------------------------------------------------------------------- #
def test_chat_tuning_settings_defaults() -> None:
    from app.config import Settings
    from app.domain.prompts import OUT_OF_SCOPE_REPLY_BY_LANG

    fields = Settings.model_fields
    assert fields["REACT_PRIOR_SUMMARY_MAX_CHARS"].default == 1200
    assert fields["STREAM_BATCH_CHARS"].default == 600
    # The batch threshold must stay above the longest static reply (the frozen
    # two-frame contract depends on it); assert the headroom explicitly.
    longest_static = max(len(v) for v in OUT_OF_SCOPE_REPLY_BY_LANG.values())
    assert fields["STREAM_BATCH_CHARS"].default >= 2 * longest_static


# --------------------------------------------------------------------------- #
# item 7 — metrics snapshot endpoint
# --------------------------------------------------------------------------- #
def test_metrics_endpoint_reports_pipeline_counters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    metrics.reset()
    _wire(monkeypatch, _FakeStore(), _FakeEmbedder(), _retrieval_agent())

    resp = client.post("/api/v1/chat", json={"matter_id": MATTER_ID, "content": "conge annuel"})
    assert resp.status_code == 200, resp.text

    snapshot = client.get("/api/v1/chat/metrics").json()
    assert set(snapshot) == {"counters", "timers"}
    assert snapshot["counters"]["chat.tool_round_dist.1"] == 1
    assert snapshot["counters"]["chat.retrieval.rounds"] == 1
    assert snapshot["counters"]["chat.drafting.ok"] == 1
    assert snapshot["timers"]["chat.retrieval.seconds"]["count"] == 1
    assert snapshot["timers"]["chat.drafting.seconds"]["count"] == 1
