"""TDD (red-first): dual-domain RAG query endpoint (task 7).

Given: POST /api/v1/chat embeds the query, retrieves matter (top_k=30,
       matter_id filter) + authority (top_k=30) separately, reranks each
       domain, applies MMR to authority, and streams SSE citations → tokens
       → done with per-claim single-domain citations.
When: the endpoint runs against fake-wire embedder/store seams.
Then: citations carry mandatory domain labels, citations precede tokens,
      empty context yields provisional not-found (never hallucination),
      store failure is 500, embedding failure is 503, and Message rows
      persist citations_json matching the emitted event.

All fixtures below are SYNTHETIC test data — never real legal text.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import chat as chat_mod
from app.main import app
from app.models import Base, Conversation, Matter, Message
from app.models.base import get_db
from app.search.schemas import AUTHORITY_COLLECTION, EVIDENCE_COLLECTION

MATTER_ID = 7


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


def _authority_hit(
    article: str = "SYN-Art-1 (synthetic, not real law)",
) -> dict[str, Any]:
    return {
        "payload": {
            "source": "SYNTHETIC-TEST-LAW",
            "version": "v0-test",
            "edition": "ar-general",
            "pub_date": "2024-01-01",
            "doc_date": "2024-01-01",
            "language": "ar",
            "article_or_section": article,
            "text": f"SYNTHETIC authority passage {article} (test data only).",
        },
        "relevance": 0.8,
    }


class _FakeEmbedder:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.exc is not None:
            raise self.exc
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class _FakeStore:
    """In-memory stand-in for QdrantStore.hybrid_query."""

    def __init__(
        self,
        matter_hits: list[dict] | None = None,
        authority_hits: list[dict] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.matter_hits = matter_hits if matter_hits is not None else [_matter_hit()]
        self.authority_hits = (
            authority_hits if authority_hits is not None else [_authority_hit()]
        )
        self.exc = exc
        self.seen_matter_ids: list[int | None] = []

    def hybrid_query(
        self,
        collection: str,
        dense: list[float],
        sparse_text: str,
        limit: int,
        matter_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if self.exc is not None:
            raise self.exc
        if collection == EVIDENCE_COLLECTION:
            self.seen_matter_ids.append(matter_id)
            assert matter_id == MATTER_ID, "matter retrieval must filter matter_id"
            return self.matter_hits[:limit]
        assert collection == AUTHORITY_COLLECTION
        return self.authority_hits[:limit]


class _FakeStream:
    def __init__(self, agent: _FakeAgent, prompt: str, chunks: list[str]) -> None:
        self._agent = agent
        self._prompt = prompt
        self._chunks = chunks

    async def __aenter__(self) -> _FakeStream:
        self._agent.calls.append(self._prompt)
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def stream_text(self, delta: bool = True):  # type: ignore[no-untyped-def]
        for chunk in self._chunks:
            yield chunk


class _FakeAgent:
    model_name = "fake:model"

    def __init__(self, chunks: list[str] | None = None) -> None:
        self.calls: list[str] = []
        self.chunks = (
            chunks if chunks is not None else ["FACT: synthetic. RULE: synthetic."]
        )

    def run_stream(self, prompt: str) -> _FakeStream:
        return _FakeStream(self, prompt, self.chunks)


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
    embedder: _FakeEmbedder,
    agent: _FakeAgent,
) -> None:
    from app.search import service as svc_mod

    svc_mod.clear_search_cache()
    monkeypatch.setattr(chat_mod, "get_store", lambda: store)
    monkeypatch.setattr(chat_mod, "get_embedder", lambda: embedder)
    from app import llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_agent", lambda **kwargs: agent)


def _events(resp: Any) -> list[dict[str, Any]]:
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:") :]))
    return out


def test_claims_carry_domain_labels(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db_session_factory
) -> None:
    """Every citation is single-domain with a mandatory domain label."""
    from app.rag import assemble as asm_mod

    assert asm_mod  # rag assembly module exists
    store = _FakeStore()
    agent = _FakeAgent()
    _wire(monkeypatch, store, _FakeEmbedder(), agent)
    resp = client.post(
        "/api/v1/chat", json={"matter_id": MATTER_ID, "content": "conge annuel"}
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    cites = next(e for e in events if e["type"] == "citations")["citations"]
    assert len(cites) == 2
    domains = {c["domain"] for c in cites}
    assert domains == {"matter", "authority"}
    matter_keys = {
        "domain",
        "document_id",
        "version_no",
        "doc_type",
        "page",
        "span",
        "faithful_ref",
    }
    authority_keys = {
        "domain",
        "source",
        "version",
        "edition",
        "pub_date",
        "doc_date",
        "language",
        "article_or_section",
    }
    for cite in cites:
        assert cite["domain"] in ("matter", "authority")
        if cite["domain"] == "matter":
            assert set(cite) <= matter_keys, f"mixed-domain citation: {cite}"
            assert cite["document_id"] == 11 and cite["page"] == 2
        else:
            assert set(cite) <= authority_keys, f"mixed-domain citation: {cite}"
            assert cite["edition"] == "ar-general"


def test_citations_before_tokens(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The citations event MUST precede the first token event in the stream."""
    _wire(monkeypatch, _FakeStore(), _FakeEmbedder(), _FakeAgent())
    resp = client.post(
        "/api/v1/chat", json={"matter_id": MATTER_ID, "content": "conge annuel"}
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "citations"
    assert "token" in kinds[1:]
    assert kinds[-1] == "done"


def test_provisional_not_found_on_unrelated_query(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero hits in BOTH domains → provisional not-found + flag, no LLM call."""
    store = _FakeStore(matter_hits=[], authority_hits=[])
    agent = _FakeAgent()
    _wire(monkeypatch, store, _FakeEmbedder(), agent)
    resp = client.post(
        "/api/v1/chat", json={"matter_id": MATTER_ID, "content": "unrelated zebras"}
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    assert events[0]["type"] == "citations" and events[0]["citations"] == []
    done = next(e for e in events if e["type"] == "done")
    assert done.get("not_found") is True
    body = " ".join(e.get("text", "") for e in events if e["type"] == "token")
    assert "don't know" in body.lower()
    assert agent.calls == [], "provider must not be called on empty context"


def test_empty_context_never_hallucinates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty matter + empty authority → no invented articles, sections, or spans."""
    store = _FakeStore(matter_hits=[], authority_hits=[])
    _wire(monkeypatch, store, _FakeEmbedder(), _FakeAgent())
    resp = client.post(
        "/api/v1/chat", json={"matter_id": MATTER_ID, "content": "anything at all"}
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    body = " ".join(e.get("text", "") for e in events if e["type"] == "token")
    for invented in ("SYN-Art", "Article 237", "document_id", "p.2"):
        assert invented not in body, f"hallucinated content: {invented}"


def test_qdrant_error_returns_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Store failure → 500 with a message (plan acceptance)."""
    store = _FakeStore(exc=RuntimeError("qdrant boom"))
    _wire(monkeypatch, store, _FakeEmbedder(), _FakeAgent())
    resp = client.post(
        "/api/v1/chat", json={"matter_id": MATTER_ID, "content": "conge annuel"}
    )
    assert resp.status_code == 500
    assert "qdrant boom" in resp.text.lower() or "store" in resp.text.lower()


def test_embedding_failure_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CrispEmbed failure → 503."""
    _wire(
        monkeypatch,
        _FakeStore(),
        _FakeEmbedder(exc=RuntimeError("crispembed down")),
        _FakeAgent(),
    )
    resp = client.post(
        "/api/v1/chat", json={"matter_id": MATTER_ID, "content": "conge annuel"}
    )
    assert resp.status_code == 503


def test_messages_persisted_with_matching_citations(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db_session_factory
) -> None:
    """Assistant Message row persists role + content + citations_json of the event."""
    _wire(
        monkeypatch, _FakeStore(), _FakeEmbedder(), _FakeAgent(chunks=["hello world"])
    )
    resp = client.post(
        "/api/v1/chat", json={"matter_id": MATTER_ID, "content": "conge annuel"}
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp)
    emitted = next(e for e in events if e["type"] == "citations")["citations"]

    async def _read():
        async with db_session_factory() as sess:
            convs = (await sess.execute(select(Conversation))).scalars().all()
            assert len(convs) == 1 and convs[0].matter_id == MATTER_ID
            msgs = (
                (await sess.execute(select(Message).order_by(Message.id)))
                .scalars()
                .all()
            )
            roles = [m.role for m in msgs]
            assert roles == ["user", "assistant"]
            assert msgs[1].content == "hello world"
            assert msgs[1].citations_json == emitted

    import anyio

    anyio.run(_read)


def test_strict_privacy_blocks_external_with_matter_evidence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict mode + external provider + matter hits → 403 before any provider call."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    agent = _FakeAgent()
    _wire(monkeypatch, _FakeStore(), _FakeEmbedder(), agent)
    resp = client.post(
        "/api/v1/chat", json={"matter_id": MATTER_ID, "content": "conge annuel"}
    )
    assert resp.status_code == 403
    assert agent.calls == []
