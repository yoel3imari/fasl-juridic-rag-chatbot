"""Qdrant owner for both collections: hybrid dense + lexical sparse with RRF.

Local-mode choice (documented): ``QDRANT_LOCAL_PATH=":memory:"`` (tests) or a
file path (single-node prod without a server) uses the SAME qdrant-client code
path as remote ``QDRANT_URL`` — only the client constructor differs. Local
mode runs exact brute-force search instead of HNSW; acceptable for a
single-user local app, and ranking behavior (RRF over dense+lexical) is
identical.

Point IDs: pipeline string keys (``"{doc}:{section}"``) are mapped with
deterministic UUID5 — Qdrant only accepts int/UUID ids. The mapping is
internal; citations resolve from payload (document_id/page/span,
source/version/article), never from the point id.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from app.search import sparse as sparse_mod
from app.search.schemas import AUTHORITY_COLLECTION, EVIDENCE_COLLECTION

DENSE_NAME: str = "dense"
SPARSE_NAME: str = "lexical"

# Local-mode clients hold their own state (notably ":memory:"), so one
# shared instance per path is REQUIRED — a fresh client per call would see
# an empty database. Remote-URL clients stay short-lived per call.
_LOCAL_CLIENTS: dict[str, Any] = {}
_LOCAL_LOCK = threading.Lock()


def _shared_local_client(path: str):
    from qdrant_client import QdrantClient

    with _LOCAL_LOCK:
        client = _LOCAL_CLIENTS.get(path)
        if client is None:
            client = QdrantClient(path=path)
            _LOCAL_CLIENTS[path] = client
        return client


def _point_id(raw: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


class QdrantStore:
    """Thin Qdrant adapter; one short-lived client per call (thread-safe)."""

    def __init__(
        self,
        url: str | None = None,
        local_path: str | None = None,
        dim: int | None = None,
    ) -> None:
        from app.config import settings

        self.url = (url or settings.QDRANT_URL).rstrip("/")
        if local_path is not None:
            self.local_path = local_path or None
        else:
            self.local_path = settings.QDRANT_LOCAL_PATH or None
        self.dim = dim or settings.EMBEDDING_DIM

    def _client(self):
        from qdrant_client import QdrantClient

        if self.local_path:
            return _shared_local_client(self.local_path)
        return QdrantClient(url=self.url, timeout=10)

    def _close(self, client) -> None:
        if not self.local_path:
            client.close()

    def ensure_collections(self, dim: int | None = None) -> None:
        from qdrant_client.models import (
            Distance,
            SparseVectorParams,
            VectorParams,
        )

        size = dim or self.dim
        client = self._client()
        try:
            for name in (EVIDENCE_COLLECTION, AUTHORITY_COLLECTION):
                if not client.collection_exists(name):
                    client.create_collection(
                        collection_name=name,
                        vectors_config={
                            DENSE_NAME: VectorParams(
                                size=size, distance=Distance.COSINE
                            )
                        },
                        sparse_vectors_config={SPARSE_NAME: SparseVectorParams()},
                    )
        finally:
            self._close(client)

    def upsert_evidence(self, points: Sequence[Any]) -> int:
        """Store matter points with dense + lexical vectors; return count."""
        return self._upsert(EVIDENCE_COLLECTION, points, _evidence_payload)

    def upsert_authorities(self, points: Sequence[Any]) -> int:
        """Store authority points with dense + lexical vectors; return count."""
        return self._upsert(AUTHORITY_COLLECTION, points, _authority_payload)

    def _upsert(
        self, collection: str, points: Sequence[Any], payload_of: Callable[[Any], dict]
    ) -> int:
        from qdrant_client.models import PointStruct

        if not points:
            return 0
        self.ensure_collections(dim=len(points[0]["vector"]))
        structs = [
            PointStruct(
                id=_point_id(str(p["id"])),
                vector={
                    DENSE_NAME: p["vector"],
                    SPARSE_NAME: sparse_mod.sparse_vector(p.get("text", "")),
                },
                payload=payload_of(p),
            )
            for p in points
        ]
        client = self._client()
        try:
            client.upsert(collection_name=collection, points=structs)
        finally:
            self._close(client)
        return len(structs)

    def hybrid_query(
        self,
        collection: str,
        dense: list[float],
        sparse_text: str,
        limit: int,
        matter_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Dense + lexical queries (both pre-filtered) fused with RRF.

        Returns [{payload, relevance}] — ``relevance`` is a fusion score,
        NEVER an authority rank; callers must not label it as one.
        """
        from qdrant_client.hybrid.fusion import reciprocal_rank_fusion
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        query_filter = None
        if matter_id is not None:
            query_filter = Filter(
                must=[
                    FieldCondition(key="matter_id", match=MatchValue(value=matter_id))
                ]
            )
        client = self._client()
        try:
            dense_hits = client.query_points(
                collection,
                query=dense,
                using=DENSE_NAME,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            ).points
            sparse_hits = client.query_points(
                collection,
                query=sparse_mod.sparse_vector(sparse_text),
                using=SPARSE_NAME,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            ).points
        finally:
            self._close(client)
        fused = reciprocal_rank_fusion([dense_hits, sparse_hits], limit=limit)
        return [
            {"payload": dict(p.payload or {}), "relevance": round(float(p.score), 6)}
            for p in fused
        ]


def _evidence_payload(p: dict) -> dict:
    return {
        "matter_id": p["matter_id"],
        "document_id": p["document_id"],
        "version_no": p["version_no"],
        "doc_type": p["doc_type"],
        "page": p["page"],
        "span": list(p["span"]),
        "faithful_ref": p["faithful_ref"],
        "text": p.get("text", ""),
    }


def _authority_payload(p: dict) -> dict:
    return {
        "source": p["source"],
        "version": p["version"],
        "edition": p["edition"],
        "pub_date": p.get("pub_date"),
        "doc_date": p.get("doc_date"),
        "language": p.get("language", ""),
        "article_or_section": p["article_or_section"],
        "text": p.get("text", ""),
    }
