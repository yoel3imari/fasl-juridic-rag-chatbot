"""Contract (characterization) tests for the `chat_rag` SSE stream.

Why this file exists
--------------------
Plan todo 5. `app/api/v1/chat.py` has **four** near-identical SSE generators
(`_gen_failed`, `_gen_direct_agent`, `_gen_empty`, `_gen`), and they do NOT emit
the same frame sequence. A test that only asserts the documented contract
("citations -> token* -> done") would stay green while todo 31 collapses the
four generators into one builder and drops, duplicates or reorders a `status`
frame. These tests therefore pin, per path:

  * the **ordered list of frame `type` values** (not a subsequence),
  * the **full key set of the `done` frame**,
  * the mid-stream `error` frame shape and position,
  * the number of `status` frames and their `stage` values.

Frame order is a frozen contract (plan: "No change to the SSE event order").
Regenerating any of these expectations is itself a contract change and must go
through the plan, not through editing this file.

All fixtures are SYNTHETIC test data — never real legal text.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import chat as chat_mod
from app.domain.search.schemas import AUTHORITY_COLLECTION, EVIDENCE_COLLECTION
from app.main import app
from app.models import Base, Matter
from app.models.base import get_db

MATTER_ID = 7

STATUS = "status"
CITATIONS = "citations"
TOKEN = "token"
DONE = "done"
ERROR = "error"

# The four contractual frame sequences. `*` is expanded by _types() so the
# token count stays asserted while the token text itself is not frozen here.
EXPECTED_FAILED_NO_ROUNDS: list[str] = [STATUS, CITATIONS, ERROR]
EXPECTED_FAILED_AFTER_ROUNDS: list[str] = [STATUS, STATUS, CITATIONS, ERROR]
EXPECTED_DIRECT_AGENT: list[str] = [STATUS, CITATIONS, TOKEN, TOKEN, DONE]
EXPECTED_EMPTY: list[str] = [STATUS, STATUS, CITATIONS, TOKEN, TOKEN, DONE]
EXPECTED_FULL: list[str] = [STATUS, STATUS, STATUS, CITATIONS, TOKEN, TOKEN, DONE]


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def _matter_hit() -> dict[str, Any]:
    return {
        "payload": {
            "matter_id": MATTER_ID,
            "document_id": 11,
            "version_no": 1,
            "doc_type": "contract",
            "page": 2,
            "span": [3, 9],
            "faithful_ref": "syn-sec-1",
            "text": "SYNTHETIC matter clause about annual leave (test data only).",
        },
        "relevance": 0.9,
    }


def _authority_hit() -> dict[str, Any]:
    return {
        "payload": {
            "source": "SYNTHETIC-TEST-LAW",
            "version": "v0-test",
            "edition": "ar-general",
            "pub_date": "2024-01-01",
            "doc_date": "2024-01-01",
            "jurisdiction": "casablanca",
            "language": "ar",
            "article_or_section": "SYN-Art-1 (synthetic, not real law)",
            "text": "SYNTHETIC authority passage (test data only).",
        },
        "relevance": 0.8,
    }


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class _FakeStore:
    def __init__(self, matter_hits: list[dict], authority_hits: list[dict]) -> None:
        self.matter_hits = matter_hits
        self.authority_hits = authority_hits

    def hybrid_query(
        self,
        collection: str,
        dense: list[float],
        sparse_text: str,
        limit: int,
        matter_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if collection == EVIDENCE_COLLECTION:
            return self.matter_hits[:limit]
        assert collection == AUTHORITY_COLLECTION
        return self.authority_hits[:limit]


class _FakeStream:
    def __init__(self, agent: _ScriptedAgent, prompt: str, chunks: list[str]) -> None:
        self._agent = agent
        self._prompt = prompt
        self._chunks = chunks

    async def __aenter__(self) -> _FakeStream:
        self._agent.calls.append(self._prompt)
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def stream_text(self, delta: bool = True):  # type: ignore[no-untyped-def]
        if self._agent.raise_on_call is not None and len(self._agent.calls) == (
            self._agent.raise_on_call
        ):
            raise self._agent.exc
        for chunk in self._chunks:
            yield chunk


class _ScriptedAgent:
    """ReAct agent double: round N yields ``script[min(N, len-1)]``."""

    model_name = "fake:model"

    def __init__(
        self,
        script: list[list[str]] | None = None,
        *,
        raise_on_call: int | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.script = script if script is not None else [["plain answer"]]
        self.raise_on_call = raise_on_call
        self.exc = exc or RuntimeError("synthetic provider outage")

    def run_stream(self, prompt: str) -> _FakeStream:
        idx = min(len(self.calls), len(self.script) - 1)
        return _FakeStream(self, prompt, self.script[idx])


def _envelope(tool: str = "search_both", query: str = "conge annuel") -> str:
    return json.dumps({"tool": tool, "query": query})


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def db_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    import anyio

    anyio.run(_init)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    anyio.run(engine.dispose)


@pytest.fixture()
def client(db_session_factory, monkeypatch: pytest.MonkeyPatch):
    async def _seed() -> None:
        async with db_session_factory() as sess:
            sess.add(
                Matter(
                    id=MATTER_ID,
                    title="Synthetic matter",
                    matter_type="labor",
                    jurisdiction="casablanca",
                    language="ar",
                )
            )
            await sess.commit()

    import anyio

    anyio.run(_seed)

    async def _override_db():
        async with db_session_factory() as sess:
            yield sess

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setenv("MATTER_PRIVACY_MODE", "strict")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.2")
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    store: _FakeStore,
    agent: _ScriptedAgent,
) -> None:
    from app.infrastructure import llm as llm_mod
    from app.services import search as svc_mod

    svc_mod.clear_search_cache()
    monkeypatch.setattr(chat_mod, "get_store", lambda: store)
    monkeypatch.setattr(chat_mod, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(llm_mod, "get_agent", lambda **kwargs: agent)


def _events(resp: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:") :]))
    return out


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def _post(client: TestClient, **body: Any) -> Any:
    return client.post("/api/v1/chat", json={"matter_id": MATTER_ID, **body})


# --------------------------------------------------------------------------- #
# path 1 — _gen_failed: provider unreachable before any tool round
# --------------------------------------------------------------------------- #
def test_failed_path_no_tool_rounds_frame_sequence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider down on the first decision round: 1 status, citations, error.

    `_gen_failed` emits NO `done` frame — that asymmetry is the contract.
    """
    agent = _ScriptedAgent(raise_on_call=1)
    _wire(monkeypatch, _FakeStore([], []), agent)
    resp = _post(client, content="conge annuel")
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    assert _types(events) == EXPECTED_FAILED_NO_ROUNDS


def test_failed_path_status_stages_and_error_frame(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error frame is `provider_unreachable` and carries conversation_id."""
    agent = _ScriptedAgent(raise_on_call=1)
    _wire(monkeypatch, _FakeStore([], []), agent)
    resp = _post(client, content="conge annuel")
    events = _events(resp)

    statuses = [e for e in events if e["type"] == STATUS]
    assert [s["stage"] for s in statuses] == ["classifying"]

    err = next(e for e in events if e["type"] == ERROR)
    assert err["code"] == "provider_unreachable"
    assert "synthetic provider outage" in err["detail"]
    assert isinstance(err["conversation_id"], int)
    # no done frame on this path
    assert DONE not in _types(events)


# --------------------------------------------------------------------------- #
# path 2 — _gen_failed after at least one tool round (2 status frames)
# --------------------------------------------------------------------------- #
def test_failed_path_after_tool_rounds_frame_sequence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool round ran (returning nothing) then the provider died.

    `if tool_rounds:` at chat.py:434 is what adds the second status frame, so
    the count of status frames here is a distinct contractual value. The
    retrieval must come back empty: `_gen_failed` is only reached when
    `not matter_raw and not auth_raw` (chat.py:424), so a tool round that found
    citations would fall through to the grounded path instead.
    """
    agent = _ScriptedAgent(
        script=[[_envelope()], []],
        raise_on_call=2,
    )
    _wire(monkeypatch, _FakeStore([], []), agent)
    resp = _post(client, content="conge annuel")
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    assert _types(events) == EXPECTED_FAILED_AFTER_ROUNDS
    assert [s["stage"] for s in events if s["type"] == STATUS] == [
        "classifying",
        "searching",
    ]
    assert DONE not in _types(events)


# --------------------------------------------------------------------------- #
# path 3 — _gen_direct_agent: zero tool calls
# --------------------------------------------------------------------------- #
def test_direct_agent_frame_sequence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct answer with no retrieval: 1 status, citations, 2 tokens, done."""
    agent = _ScriptedAgent(script=[["Bonjour, comment puis-je vous aider?"]])
    _wire(monkeypatch, _FakeStore([], []), agent)
    resp = _post(client, content="bonjour")
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    assert _types(events) == EXPECTED_DIRECT_AGENT
    assert [s["stage"] for s in events if s["type"] == STATUS] == ["classifying"]


def test_direct_agent_done_payload_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`done` on this path is exactly {type, not_found, conversation_id}."""
    agent = _ScriptedAgent(script=[["Bonjour"]])
    _wire(monkeypatch, _FakeStore([], []), agent)
    resp = _post(client, content="bonjour")
    done = next(e for e in _events(resp) if e["type"] == DONE)
    assert set(done) == {"type", "not_found", "conversation_id"}
    assert done["not_found"] is False


# --------------------------------------------------------------------------- #
# path 4 — _gen_empty: retrieval ran but rerank produced nothing
# --------------------------------------------------------------------------- #
def test_empty_retrieval_frame_sequence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool round that returns zero hits: 2 status, citations, 2 tokens, done."""
    agent = _ScriptedAgent(script=[[_envelope()], ["ignored"]])
    _wire(monkeypatch, _FakeStore([], []), agent)
    resp = _post(client, content="conge annuel")
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    assert _types(events) == EXPECTED_EMPTY
    assert [s["stage"] for s in events if s["type"] == STATUS] == [
        "classifying",
        "searching",
    ]


def test_empty_retrieval_done_payload_not_found_true(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`not_found` is True only on this path."""
    from app.domain.prompts import PROVISIONAL_NOT_FOUND

    agent = _ScriptedAgent(script=[[_envelope()], ["ignored"]])
    _wire(monkeypatch, _FakeStore([], []), agent)
    resp = _post(client, content="conge annuel")
    events = _events(resp)
    done = next(e for e in events if e["type"] == DONE)
    assert set(done) == {"type", "not_found", "conversation_id"}
    assert done["not_found"] is True
    tokens = [e for e in events if e["type"] == TOKEN]
    assert "".join(t["text"] for t in tokens) == PROVISIONAL_NOT_FOUND


# --------------------------------------------------------------------------- #
# path 5 — _gen: the full grounded path (3 status frames, one of them thinking)
# --------------------------------------------------------------------------- #
def test_full_grounded_frame_sequence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The supported path: 3 status frames (classifying, searching, thinking)."""
    agent = _ScriptedAgent(script=[[_envelope()], ["FACT: synthetic. ", "RULE: synthetic."]])
    _wire(monkeypatch, _FakeStore([_matter_hit()], [_authority_hit()]), agent)
    resp = _post(client, content="conge annuel")
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    assert _types(events) == EXPECTED_FULL
    assert [s["stage"] for s in events if s["type"] == STATUS] == [
        "classifying",
        "searching",
        "thinking",
    ]


def test_full_grounded_done_payload_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _ScriptedAgent(script=[[_envelope()], ["ok"]])
    _wire(monkeypatch, _FakeStore([_matter_hit()], [_authority_hit()]), agent)
    resp = _post(client, content="conge annuel")
    done = next(e for e in _events(resp) if e["type"] == DONE)
    assert set(done) == {"type", "not_found", "conversation_id"}
    assert done["not_found"] is False


def test_full_grounded_midstream_error_stops_without_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider dying inside `run_stream`: error frame, NO done frame.

    This is the `except` at chat.py:570-592 — it commits the partial text and
    returns early, so the sequence ends at `error` and the frame count differs
    from every happy path. The scripted rounds are: 1 = retrieval envelope,
    2 = decision answer, 3 = the grounded `run_stream` whose mid-stream failure
    is under test, so the raise is armed on call 3.
    """
    agent = _ScriptedAgent(
        script=[[_envelope()], ["grounded answer"]],
        raise_on_call=3,
    )
    _wire(monkeypatch, _FakeStore([_matter_hit()], [_authority_hit()]), agent)
    resp = _post(client, content="conge annuel")
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    assert _types(events) == [STATUS, STATUS, STATUS, CITATIONS, ERROR]
    err = events[-1]
    assert err["code"] == "provider_unreachable"
    assert isinstance(err["conversation_id"], int)


# --------------------------------------------------------------------------- #
# cross-path invariants
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "script", "store"),
    [
        ("direct", [["hi"]], "empty"),
        ("empty", [[_envelope()], ["x"]], "empty"),
        ("full", [[_envelope()], ["x"]], "hits"),
    ],
)
def test_citations_precede_every_token_on_happy_paths(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    script: list[list[str]],
    store: str,
) -> None:
    """No token frame may precede the citations frame on any path."""
    hits = [_matter_hit()] if store == "hits" else []
    agent = _ScriptedAgent(script=script)
    _wire(monkeypatch, _FakeStore(hits, [_authority_hit()] if store == "hits" else []), agent)
    resp = _post(client, content="conge annuel")
    assert resp.status_code == 200, resp.text
    types = _types(_events(resp))
    assert CITATIONS in types, label
    assert types.index(CITATIONS) < types.index(TOKEN), (label, types)


# --------------------------------------------------------------------------- #
# prompt-text contract (plan todo 9)
# --------------------------------------------------------------------------- #
# The plan requires the emitted prompt strings to stay byte-identical across
# the move to app/domain/prompts.py. Nothing pinned that text, so a one-word
# edit passed the suite silently. These tests are the machine check: any change
# to a prompt literal now fails here.
EXPECTED_GUARDRAILS = (
    "Answer ONLY from the provided matter + authority context below. "
    "Mark gaps explicitly where the context is silent. "
    "If the answer is not found in the context, reply with the provisional "
    '"I don\'t know" statement. '
    "Never guarantee legal outcomes. "
    "Never compute deadlines from dates."
)

EXPECTED_PROVISIONAL_NOT_FOUND = (
    "I don't know — the provided matter and authority context contains "
    "no relevant passage for this question. This is provisional, not legal "
    "advice: outcomes are never guaranteed, and deadlines cannot be computed "
    "from dates alone."
)


def test_guardrails_text_is_byte_pinned() -> None:
    """The mandatory guardrail wording is a frozen contract."""
    from app.domain.prompts import GUARDRAILS

    assert GUARDRAILS == EXPECTED_GUARDRAILS


def test_provisional_not_found_text_is_byte_pinned() -> None:
    """The provisional no-answer text is frozen, em dash included."""
    from app.domain.prompts import PROVISIONAL_NOT_FOUND

    assert PROVISIONAL_NOT_FOUND == EXPECTED_PROVISIONAL_NOT_FOUND
    assert "—" in PROVISIONAL_NOT_FOUND


def _prompt_hits() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        [
            {
                "document_id": 11,
                "version_no": 1,
                "page": 2,
                "span": [3, 9],
                "doc_type": "contract",
                "faithful_ref": "syn-sec-1",
                "text": "SYNTHETIC matter clause.",
            }
        ],
        [
            {
                "source": "SYN-LAW",
                "version": "v0-test",
                "edition": "ar-general",
                "pub_date": "2024-01-01",
                "doc_date": "2024-01-01",
                "language": "ar",
                "article_or_section": "SYN-Art-1",
                "text": "SYNTHETIC authority passage.",
            }
        ],
    )


def test_assemble_prompt_is_byte_pinned() -> None:
    """The fully-populated prompt is frozen byte-for-byte."""
    from app.domain.prompts import assemble_prompt

    matter, authority = _prompt_hits()
    expected = (
        "You are a Moroccan legal research assistant. Structure every answer as:\n"
        "FACT, then RULE, then APPLICATION, then CONCLUSION.\n\n"
        f"Guardrails (mandatory): {EXPECTED_GUARDRAILS}\n\n"
        "Question: conge annuel\n\n"
        "[matter evidence]\n"
        "[matter: doc 11 p.2 ¶[3, 9]] (contract, syn-sec-1)\n"
        "SYNTHETIC matter clause.\n\n"
        "[legal authorities]\n"
        "[authority: SYN-LAW v0-test SYN-Art-1 (ar-general)]\n"
        "SYNTHETIC authority passage.\n"
    )
    assert assemble_prompt("conge annuel", matter, authority) == expected


@pytest.mark.parametrize(
    ("label", "matter", "authority"),
    [
        ("matter only", "yes", "no"),
        ("authority only", "no", "yes"),
        ("neither", "no", "no"),
    ],
)
def test_assemble_prompt_empty_block_marker_is_pinned(
    label: str, matter: str, authority: str
) -> None:
    """A domain with no hits renders the literal `(none)` marker."""
    from app.domain.prompts import assemble_prompt

    m, a = _prompt_hits()
    matter_hits = m if matter == "yes" else []
    authority_hits = a if authority == "yes" else []
    prompt = assemble_prompt("q", matter_hits, authority_hits)
    assert prompt.count("(none)") == (matter == "no") + (authority == "no"), label
    assert prompt.startswith(
        "You are a Moroccan legal research assistant. Structure every answer as:\n"
    )
    assert "Question: q\n\n" in prompt


def test_citation_key_sets_are_byte_pinned() -> None:
    """The single-domain key sets are the machine-readable citation contract."""
    from app.domain.citations import AUTHORITY_CITATION_KEYS, MATTER_CITATION_KEYS

    assert MATTER_CITATION_KEYS == frozenset(
        {
            "domain",
            "document_id",
            "version_no",
            "doc_type",
            "page",
            "span",
            "faithful_ref",
        }
    )
    assert AUTHORITY_CITATION_KEYS == frozenset(
        {
            "domain",
            "source",
            "version",
            "edition",
            "pub_date",
            "doc_date",
            "language",
            "article_or_section",
        }
    )


def test_citation_objects_are_single_domain() -> None:
    """A citation never mixes matter and authority fields."""
    from app.domain.citations import build_citations

    matter, authority = _prompt_hits()
    cites = build_citations(matter, authority)
    assert [c["domain"] for c in cites] == ["matter", "authority"]
    for cite in cites:
        other = "authority" if cite["domain"] == "matter" else "matter"
        assert not any(f"{other}_" in k or k == other for k in cite), cite
    assert cites[0]["span"] == [3, 9]
