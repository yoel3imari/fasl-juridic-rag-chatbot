"""Matter-evidence indexer boundary (Qdrant ``matter_evidence`` only).

The authority collection is never referenced here: the module owns a single
collection constant and every payload carries matter_id + document_id + page
+ span + faithful_ref so citations resolve original spans later.

Point shape lives in app.search.schemas (single source of truth); this module
re-exports the names the ingestion pipeline already uses.
"""

from __future__ import annotations

from typing import Protocol

from app.search.schemas import EVIDENCE_COLLECTION, EvidencePoint

__all__ = [
    "EVIDENCE_COLLECTION",
    "EvidenceIndexer",
    "EvidencePoint",
    "QdrantEvidenceIndexer",
    "get_indexer",
]


class EvidenceIndexer(Protocol):
    """Index target for matter sections; fakes implement this in tests."""

    collection: str

    async def index(self, points: list[EvidencePoint]) -> int:
        """Store points; return the count stored."""
        ...


class QdrantEvidenceIndexer:
    """Real Qdrant adapter; delegates to the shared hybrid store."""

    collection: str = EVIDENCE_COLLECTION

    def __init__(self, url: str | None = None, local_path: str | None = None) -> None:
        from app.search.store import QdrantStore

        self._store = QdrantStore(url=url, local_path=local_path)

    async def index(self, points: list[dict]) -> int:
        """Embed payloads into matter_evidence; raise on transport failure."""
        if not points:
            return 0
        from anyio import to_thread

        return await to_thread.run_sync(lambda: self._store.upsert_evidence(points))


def get_indexer() -> EvidenceIndexer:
    """Factory seam: tests override this with an in-memory fake."""
    return QdrantEvidenceIndexer()  # type: ignore[return-value]
