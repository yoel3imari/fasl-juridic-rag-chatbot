"""Hybrid retrieval per domain with matter isolation + matter-aware cache.

- Matter queries ALWAYS pre-filter matter_id (enforced in the store filter,
  not by post-hoc dropping — cross-matter points never leave Qdrant).
- Cache keys ALWAYS include matter_id for matter-involving searches.
- Response items carry ``domain: matter|authority`` (task 7 privacy guard
  depends on these exact field names) and ``relevance`` (fusion score,
  never an authority rank).
"""

from __future__ import annotations

from typing import Any, Protocol

from app.search.schemas import AUTHORITY_COLLECTION, EVIDENCE_COLLECTION

_CACHE: dict[tuple, dict] = {}


class SearchUnavailableError(Exception):
    """Qdrant unreachable — router maps to 503 with a retry hint."""


class EmbeddingUnavailableError(Exception):
    """CrispEmbed unreachable — router maps to 503."""


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class Store(Protocol):
    def hybrid_query(
        self,
        collection: str,
        dense: list[float],
        sparse_text: str,
        limit: int,
        matter_id: int | None = None,
    ) -> list[dict[str, Any]]: ...


def clear_search_cache() -> None:
    """Test seam: drop all cached search responses."""
    _CACHE.clear()


async def _embed_vector(embedder: Embedder, query: str) -> list[float]:
    try:
        vectors = await embedder.embed([query])
    except Exception as exc:
        raise EmbeddingUnavailableError(
            f"embedding service unavailable (CrispEmbed): {exc}"
        ) from exc
    if not vectors:
        raise EmbeddingUnavailableError("embedding service returned no vectors")
    return list(vectors[0])


def _run_query(
    store: Store,
    collection: str,
    dense: list[float],
    query: str,
    limit: int,
    matter_id: int | None,
) -> list[dict[str, Any]]:
    try:
        return store.hybrid_query(
            collection,
            dense=dense,
            sparse_text=query,
            limit=limit,
            matter_id=matter_id,
        )
    except (SearchUnavailableError, EmbeddingUnavailableError):
        raise
    except Exception as exc:
        raise SearchUnavailableError(
            f"vector store unavailable (Qdrant): {exc} — retry shortly"
        ) from exc


def _matter_item(hit: dict[str, Any]) -> dict[str, Any]:
    p = hit["payload"]
    return {
        "domain": "matter",
        "matter_id": p["matter_id"],
        "document_id": p["document_id"],
        "version_no": p["version_no"],
        "doc_type": p["doc_type"],
        "page": p["page"],
        "span": list(p["span"]),
        "faithful_ref": p["faithful_ref"],
        "text": p.get("text", ""),
        "relevance": hit["relevance"],
    }


def _authority_item(hit: dict[str, Any]) -> dict[str, Any]:
    p = hit["payload"]
    return {
        "domain": "authority",
        "source": p["source"],
        "version": p["version"],
        "edition": p["edition"],
        "pub_date": p.get("pub_date"),
        "doc_date": p.get("doc_date"),
        "language": p.get("language", ""),
        "article_or_section": p["article_or_section"],
        "text": p.get("text", ""),
        "relevance": hit["relevance"],
    }


async def search_matter(
    *,
    store: Store,
    embedder: Embedder,
    matter_id: int,
    query: str,
    top_k: int = 5,
) -> dict[str, Any]:
    """Hybrid search over matter_evidence, pre-filtered to one matter."""
    key = ("matter", matter_id, query, top_k)
    if key in _CACHE:
        return _CACHE[key]
    dense = await _embed_vector(embedder, query)
    hits = _run_query(store, EVIDENCE_COLLECTION, dense, query, top_k, matter_id)
    out: dict[str, Any] = {
        "matter": [_matter_item(h) for h in hits],
        "matter_id": matter_id,
    }
    _CACHE[key] = out
    return out


async def search_authority(
    *, store: Store, embedder: Embedder, query: str, top_k: int = 5
) -> dict[str, Any]:
    """Hybrid search over legal_authorities (no matter scope)."""
    key = ("authority", None, query, top_k)
    if key in _CACHE:
        return _CACHE[key]
    dense = await _embed_vector(embedder, query)
    hits = _run_query(store, AUTHORITY_COLLECTION, dense, query, top_k, None)
    out: dict[str, Any] = {"authority": [_authority_item(h) for h in hits]}
    _CACHE[key] = out
    return out


async def search_both(
    *,
    store: Store,
    embedder: Embedder,
    matter_id: int,
    query: str,
    top_k: int = 5,
) -> dict[str, Any]:
    """Both domains, SEPARATE labeled lists — never one merged list."""
    key = ("both", matter_id, query, top_k)
    if key in _CACHE:
        return _CACHE[key]
    dense = await _embed_vector(embedder, query)
    matter_hits = _run_query(store, EVIDENCE_COLLECTION, dense, query, top_k, matter_id)
    auth_hits = _run_query(store, AUTHORITY_COLLECTION, dense, query, top_k, None)
    out: dict[str, Any] = {
        "matter": [_matter_item(h) for h in matter_hits],
        "authority": [_authority_item(h) for h in auth_hits],
        "matter_id": matter_id,
    }
    _CACHE[key] = out
    return out
