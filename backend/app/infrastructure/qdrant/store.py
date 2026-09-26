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
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.domain.search.schemas import AUTHORITY_COLLECTION, EVIDENCE_COLLECTION
from app.infrastructure.qdrant import sparse as sparse_mod

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


@dataclass(frozen=True)
class IndexBatchResult:
    """Verified outcome of one bulk authority index call (T16 drives this).

    ``verified_ids`` holds ONLY point ids proven present via a live read
    after the final verification pass — the caller (T16 ledger driver) must
    mark exactly these rows ``indexed`` and leave everything else pending.
    Never claim indexed from the ``wait=false`` async response.
    """

    verified_ids: list[str] = field(default_factory=list)
    expected: int = 0
    count_before: int = 0
    count_after: int = 0
    batches: int = 0
    spot_checked: int = 0


class IndexReconciliationError(RuntimeError):
    """Loud failure: live-read reconciliation did not prove every point.

    Carries ``verified_ids`` (safe to mark indexed) and ``missing_ids``
    (must stay pending) so the caller never marks unverified points.
    """

    def __init__(
        self,
        message: str,
        *,
        verified_ids: Sequence[str] = (),
        missing_ids: Sequence[str] = (),
        expected: int = 0,
        actual: int = 0,
    ) -> None:
        super().__init__(message)
        self.verified_ids: list[str] = list(verified_ids)
        self.missing_ids: list[str] = list(missing_ids)
        self.expected = expected
        self.actual = actual


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

    def count_points(self, collection: str) -> int:
        """Live-read point count; 0 when the collection does not exist yet."""
        client = self._client()
        try:
            if not client.collection_exists(collection):
                return 0
            return client.count(collection_name=collection, exact=True).count
        finally:
            self._close(client)

    def _upsert_batch(self, client, collection: str, structs, *, wait: bool) -> None:
        """One batch write seam (override in tests to simulate a lost write)."""
        client.upsert(collection_name=collection, points=structs, wait=wait)

    def index_authorities_batched(
        self,
        points: Sequence[Any],
        *,
        batch_points: int | None = None,
        stable_attempts: int = 60,
        stable_poll_seconds: float = 0.1,
    ) -> IndexBatchResult:
        """Bulk-index authority chunks with count reconciliation.

        Throughput path: ``wait=false`` batch upserts of at most
        ``LIBRARY_INDEX_BATCH_POINTS`` points. Verification path (never the
        async response): poll-until-stable live ``retrieve`` of every
        expected point id, then a live ``count`` plus a ``scroll``
        spot-check of the todo-3 payload shape
        (``page``/``hierarchy``/``chunk_id``).

        Raises:
            ValueError: any vector whose length != this store's dim
                (defaults to ``settings.EMBEDDING_DIM``); raised BEFORE any
                write so a bad batch never partially lands.
            IndexReconciliationError: live reads did not prove every point;
                ``verified_ids`` on the error are the only ids safe to mark.
        """
        from qdrant_client.models import PointStruct

        from app.config import settings

        pts = list(points)
        per_batch = batch_points or int(settings.LIBRARY_INDEX_BATCH_POINTS)
        if per_batch <= 0:
            raise ValueError(f"batch_points must be > 0, got {per_batch}")
        count_before = self.count_points(AUTHORITY_COLLECTION)
        if not pts:
            return IndexBatchResult(
                verified_ids=[],
                expected=0,
                count_before=count_before,
                count_after=count_before,
                batches=0,
                spot_checked=0,
            )
        for p in pts:
            got = len(p["vector"])
            if got != self.dim:
                raise ValueError(
                    f"dim mismatch for point {p.get('id')!r}: "
                    f"len(vector)={got} != EMBEDDING_DIM({self.dim})"
                )
        self.ensure_collections(dim=self.dim)

        raw_ids = [str(p["id"]) for p in pts]
        structs = [
            PointStruct(
                id=_point_id(raw),
                vector={
                    DENSE_NAME: p["vector"],
                    SPARSE_NAME: sparse_mod.sparse_vector(p.get("text", "")),
                },
                payload=_authority_payload(p),
            )
            for raw, p in zip(raw_ids, pts)
        ]
        client = self._client()
        try:
            batches = 0
            for start in range(0, len(structs), per_batch):
                self._upsert_batch(
                    client,
                    AUTHORITY_COLLECTION,
                    structs[start : start + per_batch],
                    wait=False,
                )
                batches += 1
            verified = self._await_stable(
                client,
                [_point_id(raw) for raw in raw_ids],
                attempts=stable_attempts,
                poll_seconds=stable_poll_seconds,
            )
            missing = [raw for raw in raw_ids if _point_id(raw) not in verified]
            if missing:
                raise IndexReconciliationError(
                    f"reconciliation failed: {len(missing)}/{len(raw_ids)} "
                    f"points missing from live read (unverified stay pending)",
                    verified_ids=[raw for raw in raw_ids if _point_id(raw) in verified],
                    missing_ids=missing,
                    expected=len(raw_ids),
                    actual=len(verified),
                )
            count_after = client.count(
                collection_name=AUTHORITY_COLLECTION, exact=True
            ).count
            spot_checked = self._scroll_spot_check(
                client, [_point_id(raw) for raw in raw_ids]
            )
        finally:
            self._close(client)
        return IndexBatchResult(
            verified_ids=raw_ids,
            expected=len(raw_ids),
            count_before=count_before,
            count_after=count_after,
            batches=batches,
            spot_checked=spot_checked,
        )

    def _await_stable(
        self, client, uuid_ids: list[str], *, attempts: int, poll_seconds: float
    ) -> set[str]:
        """Poll-until-stable live read: return the subset of ids retrievable.

        ``wait=false`` is throughput-only; this live ``retrieve`` loop is the
        verification pass — reconciliation never touches the async response.
        """
        wanted = set(uuid_ids)
        for _ in range(max(1, attempts)):
            found = {
                str(r.id)
                for r in client.retrieve(
                    collection_name=AUTHORITY_COLLECTION,
                    ids=uuid_ids,
                    with_payload=False,
                )
            }
            if wanted <= found:
                return found
            time.sleep(poll_seconds)
        return {
            str(r.id)
            for r in client.retrieve(
                collection_name=AUTHORITY_COLLECTION,
                ids=uuid_ids,
                with_payload=False,
            )
        }

    def _scroll_spot_check(self, client, uuid_ids: list[str]) -> int:
        """``scroll`` a sample of our points; assert the todo-3 payload shape.

        Every sampled record must carry ``page:int``,
        ``hierarchy:dict`` and a non-empty ``chunk_id:str``. Raises
        ``IndexReconciliationError`` on shape violation (loud, no claim).
        """
        from qdrant_client.models import Filter, HasIdCondition

        sample = uuid_ids[: min(5, len(uuid_ids))]
        records, _ = client.scroll(
            collection_name=AUTHORITY_COLLECTION,
            scroll_filter=Filter(must=[HasIdCondition(has_id=sample)]),
            limit=len(sample),
            with_payload=True,
        )
        by_id = {str(r.id): dict(r.payload or {}) for r in records}
        for uid in sample:
            payload = by_id.get(uid)
            if payload is None:
                raise IndexReconciliationError(
                    f"scroll spot-check: point {uid} absent from live read",
                    verified_ids=[],
                    missing_ids=[uid],
                    expected=len(uuid_ids),
                    actual=len(by_id),
                )
            if (
                not isinstance(payload.get("page"), int)
                or isinstance(payload.get("page"), bool)
                or not isinstance(payload.get("hierarchy"), dict)
                or not isinstance(payload.get("chunk_id"), str)
                or not payload["chunk_id"]
            ):
                raise IndexReconciliationError(
                    f"scroll spot-check: point {uid} payload shape invalid "
                    f"(need page:int/hierarchy:dict/chunk_id:str, "
                    f"got {sorted(payload)})",
                    verified_ids=[],
                    missing_ids=[uid],
                    expected=len(uuid_ids),
                    actual=len(by_id),
                )
        return len(sample)

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
        Missing collections are created on demand (empty result), so an
        unseeded domain reads as "no hits", never a 500; connection
        failures still raise and map to 503/500 upstream.
        """
        from qdrant_client.hybrid.fusion import reciprocal_rank_fusion
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        self.ensure_collections()
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
    page = p.get("page", 0)
    try:
        page = int(page) if page is not None else 0
    except (TypeError, ValueError):
        page = 0
    return {
        "source": p["source"],
        "version": p["version"],
        "edition": p["edition"],
        "pub_date": p.get("pub_date"),
        "doc_date": p.get("doc_date"),
        "language": p.get("language", ""),
        "article_or_section": p["article_or_section"],
        "text": p.get("text", ""),
        "page": page,
        "hierarchy": dict(p.get("hierarchy") or {}),
        "chunk_id": p.get("chunk_id") or p.get("id", ""),
        "category": p.get("category", ""),
        "file_sha": p.get("file_sha", ""),
        "hijri_date": p.get("hijri_date"),
        "coverage_note": p.get("coverage_note"),
    }
