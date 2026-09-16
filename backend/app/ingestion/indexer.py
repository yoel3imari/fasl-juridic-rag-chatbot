"""Matter-evidence indexer boundary (Qdrant ``matter_evidence`` only).

The authority collection is never referenced here: the module owns a single
collection constant and every payload carries matter_id + document_id + page
+ span + faithful_ref so citations resolve original spans later.
"""

from __future__ import annotations

from typing import Protocol
from typing import TypedDict

EVIDENCE_COLLECTION: str = "matter_evidence"


class EvidencePoint(TypedDict):
    """Qdrant point for one section: dense vector + citation-resolving payload."""

    id: str
    vector: list[float]
    matter_id: int
    document_id: int
    version_no: int
    doc_type: str
    page: int
    span: list[int]
    faithful_ref: str
    text: str


class EvidenceIndexer(Protocol):
    """Index target for matter sections; fakes implement this in tests."""

    collection: str

    async def index(self, points: list[EvidencePoint]) -> int:
        """Store points; return the count stored."""
        ...


class QdrantEvidenceIndexer:
    """Real Qdrant adapter over HTTP; creates the evidence collection lazily."""

    collection: str = EVIDENCE_COLLECTION

    def __init__(self, url: str | None = None) -> None:
        from app.config import settings

        self.url = (url or settings.QDRANT_URL).rstrip("/")

    async def index(self, points: list[dict]) -> int:
        """Embed payloads into matter_evidence; raise on transport failure."""
        if not points:
            return 0
        from anyio import to_thread

        return await to_thread.run_sync(lambda: self._index_sync(points))

    def _index_sync(self, points: list[dict]) -> int:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams

        dim = len(points[0]["vector"])
        with QdrantClient(url=self.url, timeout=10) as client:
            if not client.collection_exists(self.collection):
                client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
            structs = [
                PointStruct(
                    id=p["id"],
                    vector=p["vector"],
                    payload={k: v for k, v in p.items() if k not in {"id", "vector"}},
                )
                for p in points
            ]
            client.upsert(collection_name=self.collection, points=structs)
        return len(structs)


def get_indexer() -> EvidenceIndexer:
    """Factory seam: tests override this with an in-memory fake."""
    return QdrantEvidenceIndexer()  # type: ignore[return-value]
