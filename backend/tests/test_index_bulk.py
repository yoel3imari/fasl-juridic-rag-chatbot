"""Task 15: count-reconciled batched authority indexer.

Given: authority chunks with 384-dim vectors (todo-3 payload shape).
When: QdrantStore.index_authorities_batched runs against an isolated local
      Qdrant (tmp-path file mode — hermetic, never the live :6333 slate).
Then: verified_ids cover exactly the live-proven points; count reconciles;
      scroll spot-check proves page/hierarchy/chunk_id; a dropped write
      fails loudly with no success claim for the unverified point.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.search.store import (
    IndexBatchResult,
    IndexReconciliationError,
    QdrantStore,
)

DIM = settings.EMBEDDING_DIM
assert DIM == 384
BATCH_DEFAULT = settings.LIBRARY_INDEX_BATCH_POINTS
assert BATCH_DEFAULT == 128


def _point(i: int, **over) -> dict:
    p = {
        "id": f"Code du Travail:2023:ar-general:Article {i}:p{i}:o0",
        "vector": [0.01 * ((i % 5) + 1)] * DIM,
        "text": f"SYNTHETIC bulk fixture article {i} عطلة سنوية",
        "source": "Code du Travail",
        "version": "2023",
        "edition": "ar-general",
        "language": "ar",
        "article_or_section": f"Article {i}",
        "page": i + 1,
        "hierarchy": {"code": "Code du Travail", "article": f"Article {i}"},
        "chunk_id": f"Code du Travail:2023:ar-general:Article {i}:p{i}:o0",
        "category": "travail",
        "file_sha": "sha-fixture",
    }
    p.update(over)
    return p


def _store(tmp_path) -> QdrantStore:
    return QdrantStore(local_path=str(tmp_path / "qdrant"))


class _DropLastBatch(QdrantStore):
    """Simulates a lost/queued write: drops one struct, records wait flags."""

    def __init__(self, *a, drop_raw_id: str = "", **k) -> None:
        super().__init__(*a, **k)
        self.drop_raw_id = drop_raw_id
        self.wait_flags: list[bool] = []

    def _upsert_batch(self, client, collection, structs, *, wait: bool) -> None:
        from app.search.store import _point_id

        self.wait_flags.append(wait)
        keep = [s for s in structs if str(s.id) != _point_id(self.drop_raw_id)]
        super()._upsert_batch(client, collection, keep, wait=wait)


def test_happy_batch_reconciles_count_and_spot_check(tmp_path):
    store = _store(tmp_path)
    pts = [_point(i) for i in range(5)]
    res = store.index_authorities_batched(pts)
    assert isinstance(res, IndexBatchResult)
    assert res.expected == 5
    assert res.verified_ids == [p["id"] for p in pts]
    assert res.count_after - res.count_before == 5
    assert res.batches == 1
    assert res.spot_checked == 5

    from qdrant_client.models import Filter, HasIdCondition

    from app.search.schemas import AUTHORITY_COLLECTION
    from app.search.store import _point_id

    client = store._client()
    try:
        records, _ = client.scroll(
            collection_name=AUTHORITY_COLLECTION,
            scroll_filter=Filter(
                must=[HasIdCondition(has_id=[_point_id(p["id"]) for p in pts])]
            ),
            limit=5,
            with_payload=True,
        )
    finally:
        store._close(client)
    assert len(records) == 5
    for r in records:
        payload = dict(r.payload or {})
        assert isinstance(payload["page"], int)
        assert isinstance(payload["hierarchy"], dict)
        assert isinstance(payload["chunk_id"], str) and payload["chunk_id"]


def test_batches_use_wait_false_and_split_at_batch_points(tmp_path):
    from app.search.store import _point_id

    pts = [_point(i) for i in range(5)]
    store = _DropLastBatch(local_path=str(tmp_path / "qdrant"), drop_raw_id="__none__")
    res = store.index_authorities_batched(pts, batch_points=2)
    assert res.batches == 3  # 2 + 2 + 1
    assert res.verified_ids == [p["id"] for p in pts]
    # wait=false is throughput-only: every batch write was async ...
    assert store.wait_flags == [False, False, False]
    # ... yet reconciliation read live state (all 5 retrievable).
    assert store.count_points("legal_authorities") == 5
    assert _point_id(pts[0]["id"]) != _point_id(pts[1]["id"])


def test_default_batch_size_is_128_from_settings(tmp_path):
    store = _store(tmp_path)
    pts = [_point(i) for i in range(3)]
    res = store.index_authorities_batched(pts)
    assert res.batches == 1
    assert settings.LIBRARY_INDEX_BATCH_POINTS == 128


def test_dim_mismatch_raises_before_any_write(tmp_path):
    store = _store(tmp_path)
    pts = [_point(0), _point(1, vector=[0.1] * (DIM - 1))]
    with pytest.raises(ValueError, match="dim mismatch"):
        store.index_authorities_batched(pts)
    assert store.count_points("legal_authorities") == 0


def test_dropped_point_fails_loudly_with_no_success_claim(tmp_path):
    pts = [_point(i) for i in range(4)]
    dropped = pts[-1]["id"]
    store = _DropLastBatch(local_path=str(tmp_path / "qdrant"), drop_raw_id=dropped)
    with pytest.raises(IndexReconciliationError) as exc_info:
        store.index_authorities_batched(
            pts, stable_attempts=3, stable_poll_seconds=0.01
        )
    err = exc_info.value
    assert err.missing_ids == [dropped]
    assert dropped not in err.verified_ids
    assert len(err.verified_ids) == 3  # only live-proven points claimable
    assert err.expected == 4 and err.actual == 3


def test_empty_points_is_noop(tmp_path):
    store = _store(tmp_path)
    res = store.index_authorities_batched([])
    assert res.verified_ids == [] and res.expected == 0 and res.batches == 0
    assert res.count_after == res.count_before == 0


def test_reindex_same_ids_stays_verified_without_double_count(tmp_path):
    store = _store(tmp_path)
    pts = [_point(i) for i in range(3)]
    first = store.index_authorities_batched(pts)
    second = store.index_authorities_batched(pts)
    assert second.verified_ids == [p["id"] for p in pts]
    assert second.count_after == first.count_after == 3
