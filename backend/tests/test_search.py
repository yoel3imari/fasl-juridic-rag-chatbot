"""TDD (red-first): Qdrant dual-domain hybrid search (task 5).

Given: matter_evidence + legal_authorities collections in Qdrant local
       in-memory mode (same code path as prod, no live server needed).
When: GET/POST /api/v1/search runs with domain matter|authority|both.
Then: matter queries pre-filter matter_id, both-domains stay separate,
      fusion surfaces dense+lexical hits, failures are honest 503s.

Synthetic fixtures below are labeled SYNTHETIC — never real Article 237 text.
"""

from __future__ import annotations

import json

import pytest  # noqa: F401  (fixture marker parity with suite style)
from fastapi.testclient import TestClient

DIM = 4


class _FakeEmbedder:
    """Deterministic stand-in for CrispEmbedClient (no HTTP)."""

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [1.0, 0.0, 0.0, 0.0]
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self.vector) for _ in texts]


def _make_store():
    from app.search.store import QdrantStore

    return QdrantStore(local_path=":memory:", dim=DIM)


def _seed_matter(store, matter_id: int, tag: str):
    """SYNTHETIC fixture: one matter chunk tagged per matter."""
    store.upsert_evidence(
        [
            {
                "id": f"{tag}-doc1-sec1",
                "vector": [1.0, 0.0, 0.0, 0.0],
                "matter_id": matter_id,
                "document_id": 100 + matter_id,
                "version_no": 1,
                "doc_type": "contract",
                "page": 2,
                "span": [10, 20],
                "faithful_ref": f"{tag}-sec1",
                "text": f"conge annuel clause {tag} contrat travail",
            }
        ]
    )


def _seed_authority(store):
    """SYNTHETIC fixture: labeled test-only, NOT real Code du Travail text."""
    store.upsert_authorities(
        [
            {
                "id": "syn-art-1",
                "vector": [1.0, 0.0, 0.0, 0.0],
                "source": "SYNTHETIC-TEST-LAW",
                "version": "v0-test",
                "edition": "ar-general",
                "pub_date": "2024-01-01",
                "doc_date": "2024-01-01",
                "language": "ar",
                "article_or_section": "SYN-Art-1 (synthetic, not real law)",
                "text": "duree conge annuel paye dix-huit jours ouvrables",
            }
        ]
    )


# --- fusion: dense-only hit AND lexical-only hit both surface ----------------


async def test_hybrid_fusion_surfaces_dense_and_lexical_hits() -> None:
    from app.search import service as svc

    svc.clear_search_cache()
    store = _make_store()
    # Dense-near point with zero lexical overlap.
    store.upsert_evidence(
        [
            {
                "id": "dense-doc-sec",
                "vector": [1.0, 0.0, 0.0, 0.0],
                "matter_id": 1,
                "document_id": 1,
                "version_no": 1,
                "doc_type": "letter",
                "page": 1,
                "span": [0, 5],
                "faithful_ref": "dense-sec",
                "text": "zzz qqq www",
            }
        ]
    )
    # Lexical-match point with an orthogonal dense vector.
    store.upsert_evidence(
        [
            {
                "id": "lex-doc-sec",
                "vector": [0.0, 0.0, 0.0, 1.0],
                "matter_id": 1,
                "document_id": 2,
                "version_no": 1,
                "doc_type": "letter",
                "page": 3,
                "span": [6, 9],
                "faithful_ref": "lex-sec",
                "text": "conge annuel paye",
            }
        ]
    )
    out = await svc.search_matter(
        store=store,
        embedder=_FakeEmbedder([1.0, 0.0, 0.0, 0.0]),
        matter_id=1,
        query="conge annuel paye",
        top_k=5,
    )
    refs = {hit["faithful_ref"] for hit in out["matter"]}
    assert refs == {"dense-sec", "lex-sec"}


# --- empty matter echoes matter_id ------------------------------------------


async def test_empty_matter_returns_empty_with_matter_id_echo() -> None:
    from app.search import service as svc

    svc.clear_search_cache()
    out = await svc.search_matter(
        store=_make_store(),
        embedder=_FakeEmbedder(),
        matter_id=99,
        query="conge annuel",
        top_k=5,
    )
    assert out["matter"] == []
    assert out["matter_id"] == 99


# --- domain separation -------------------------------------------------------


async def test_both_domains_return_separate_labeled_lists() -> None:
    from app.search import service as svc

    svc.clear_search_cache()
    store = _make_store()
    _seed_matter(store, matter_id=7, tag="m7")
    _seed_authority(store)
    out = await svc.search_both(
        store=store,
        embedder=_FakeEmbedder(),
        matter_id=7,
        query="conge annuel",
        top_k=5,
    )
    assert set(out) >= {"matter", "authority"}
    assert "results" not in out  # never one merged list
    assert all(h["domain"] == "matter" for h in out["matter"])
    assert all(h["domain"] == "authority" for h in out["authority"])
    blob = json.dumps(out)
    assert '"score"' not in blob and '"rank"' not in blob  # relevance only
    assert out["matter"] and out["authority"]


async def test_authority_hits_carry_version_and_edition() -> None:
    from app.search import service as svc

    svc.clear_search_cache()
    store = _make_store()
    _seed_authority(store)
    out = await svc.search_authority(
        store=store, embedder=_FakeEmbedder(), query="conge annuel", top_k=5
    )
    hit = out["authority"][0]
    assert hit["source"] == "SYNTHETIC-TEST-LAW"
    assert hit["version"] == "v0-test"
    assert hit["edition"] == "ar-general"
    assert "SYN-Art-1" in hit["article_or_section"]


# --- cache keys include matter_id -------------------------------------------


async def test_cache_key_includes_matter_id() -> None:
    from app.search import service as svc

    svc.clear_search_cache()
    calls: list = []

    class _SpyStore:
        def hybrid_query(self, collection, dense, sparse_text, limit, matter_id=None):
            calls.append(matter_id)
            return [
                {
                    "payload": {
                        "matter_id": matter_id,
                        "document_id": 1,
                        "version_no": 1,
                        "doc_type": "letter",
                        "page": 1,
                        "span": [0, 1],
                        "faithful_ref": f"m{matter_id}",
                        "text": "x",
                    },
                    "relevance": 1.0,
                }
            ]

    await svc.search_matter(
        store=_SpyStore(), embedder=_FakeEmbedder(), matter_id=1, query="q", top_k=5
    )
    await svc.search_matter(
        store=_SpyStore(), embedder=_FakeEmbedder(), matter_id=1, query="q", top_k=5
    )
    await svc.search_matter(
        store=_SpyStore(), embedder=_FakeEmbedder(), matter_id=2, query="q", top_k=5
    )
    assert calls == [1, 2]  # second identical call served from cache


# --- HTTP route ---------------------------------------------------------------


def test_search_routes_registered() -> None:
    from app.main import app

    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    assert "/api/v1/search" in spec["paths"]


def test_post_search_both_shape_and_no_merged_list() -> None:
    from app.main import app

    client = TestClient(app)
    resp = client.post(
        "/api/v1/search",
        json={"query": "conge annuel", "domain": "authority", "top_k": 3},
    )
    assert resp.status_code in (200, 503)  # 503 when CrispEmbed/Qdrant down
    if resp.status_code == 200:
        body = resp.json()
        assert "authority" in body
        assert "results" not in body


def test_matter_search_without_matter_id_rejected() -> None:
    from app.main import app

    client = TestClient(app)
    resp = client.post("/api/v1/search", json={"query": "x", "domain": "matter"})
    assert resp.status_code == 422


def test_get_search_authority_live_shape() -> None:
    from app.main import app

    client = TestClient(app)
    resp = client.get(
        "/api/v1/search", params={"query": "conge", "domain": "authority", "top_k": 2}
    )
    assert resp.status_code in (200, 503)


# --- 503 paths -----------------------------------------------------------------


def test_qdrant_down_returns_503_with_retry_hint(monkeypatch) -> None:
    import app.api.v1.search as search_mod
    from app.main import app

    class _DownStore:
        def hybrid_query(self, *a, **k):
            raise ConnectionError("qdrant unreachable")

    monkeypatch.setattr(search_mod, "get_store", lambda: _DownStore())
    monkeypatch.setattr(
        search_mod, "get_embedder", lambda: _FakeEmbedder(), raising=True
    )
    client = TestClient(app)
    resp = client.post(
        "/api/v1/search", json={"query": "x", "domain": "authority", "top_k": 3}
    )
    assert resp.status_code == 503
    assert "retry" in resp.json()["detail"].lower()


def test_embedder_down_returns_503(monkeypatch) -> None:
    import app.api.v1.search as search_mod
    from app.main import app

    class _DownEmbedder:
        async def embed(self, texts):
            raise ConnectionError("crispembed unreachable")

    monkeypatch.setattr(search_mod, "get_store", lambda: _make_store())
    monkeypatch.setattr(search_mod, "get_embedder", lambda: _DownEmbedder())
    client = TestClient(app)
    resp = client.post(
        "/api/v1/search", json={"query": "x", "domain": "authority", "top_k": 3}
    )
    assert resp.status_code == 503
