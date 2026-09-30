"""Hard-delete contract: document and matter purges (rows + vectors + blobs).

Operation order matters (see app/services/matter_cleanup.py): evidence points
are purged BEFORE the DB delete commits, so every successful delete must leave
rows, blob files and Qdrant points ALL gone, while a cross-matter delete must
remove nothing at all.

Qdrant exercise: tests that assert vector state use a real in-memory store
(``QdrantStore(local_path=":memory:")``) behind the ``get_purger`` factory
seam; tests that need network-free determinism use a fake. The shared
``:memory:"`` client is process-global, so real-store tests seed explicit
matter ids (7001+) that no other suite uses.

All fixtures are SYNTHETIC — never real legal text.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.search.schemas import EVIDENCE_COLLECTION
from app.main import app
from app.models import (
    Analysis,
    Base,
    Conversation,
    Document,
    DocumentSection,
    DocumentVersion,
    Draft,
    Matter,
    Message,
)
from app.models.base import get_db
from app.services import matter_cleanup

# Explicit ids for tests touching the shared in-memory Qdrant client.
REAL_STORE_MATTER_IDS: dict[str, int] = {
    "document_delete": 7001,
    "matter_delete": 7002,
    "cross_matter_victim": 7003,
    "cross_matter_attacker": 7004,
}


@pytest.fixture(autouse=True)
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated blob storage; get_storage_dir honours FASL_STORAGE_DIR."""
    root = tmp_path / "storage"
    monkeypatch.setenv("FASL_STORAGE_DIR", str(root))
    return root


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
def client(db_session_factory):
    async def _override_db():
        async with db_session_factory() as sess:
            yield sess

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class _FakePurger:
    """Network-free purger: records calls, returns a fixed point count."""

    def __init__(self, removed: int = 0) -> None:
        self.removed = removed
        self.calls: list[tuple[int, int | None]] = []

    def delete_evidence(self, *, matter_id: int, document_id: int | None = None) -> int:
        self.calls.append((matter_id, document_id))
        return self.removed


def _use_real_store(monkeypatch: pytest.MonkeyPatch):
    """Swap get_purger for a real in-memory Qdrant store; return it."""
    from app.infrastructure.qdrant.store import QdrantStore

    store = QdrantStore(local_path=":memory:", dim=4)
    monkeypatch.setattr(matter_cleanup, "get_purger", lambda: store)
    return store


def _qdrant_count(store, *, matter_id: int, document_id: int | None = None) -> int:
    """Filter-scoped live count in matter_evidence (read-only assertion)."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    must = [FieldCondition(key="matter_id", match=MatchValue(value=matter_id))]
    if document_id is not None:
        must.append(FieldCondition(key="document_id", match=MatchValue(value=document_id)))
    client = store._client()
    try:
        if not client.collection_exists(EVIDENCE_COLLECTION):
            return 0
        return int(
            client.count(
                collection_name=EVIDENCE_COLLECTION,
                count_filter=Filter(must=must),
                exact=True,
            ).count
        )
    finally:
        store._close(client)


def _seed_points(store, *, matter_id: int, document_id: int, count: int) -> None:
    """Upsert synthetic evidence points (matter-scoped raw ids for the shared :memory:)."""
    store.upsert_evidence(
        [
            {
                "id": f"{matter_id}:{document_id}:sec-{i}",
                "vector": [1.0, 0.0, 0.0, 0.0],
                "matter_id": matter_id,
                "document_id": document_id,
                "version_no": 1,
                "doc_type": "contract",
                "page": i + 1,
                "span": [0, 24],
                "faithful_ref": f"sec-{i}",
                "text": f"synthetic chunk {i} of document {document_id}",
            }
            for i in range(count)
        ]
    )


async def _seed_matter(session: AsyncSession, *, matter_id: int | None = None) -> int:
    """Create a synthetic matter; pass ``matter_id`` to pin the id (shared Qdrant)."""
    matter = Matter(
        title="Synthetic matter",
        matter_type="labor",
        jurisdiction="casablanca",
        language="ar",
    )
    if matter_id is not None:
        matter.id = matter_id
    session.add(matter)
    await session.flush()
    await session.commit()
    return int(matter.id)


async def _seed_document(
    session: AsyncSession,
    *,
    matter_id: int,
    pages: list[int],
    status: str = "indexed",
    blob_rel: str | None = None,
) -> tuple[int, str]:
    """Create a synthetic document + version + sections + blob file.

    Returns ``(document_id, blob_ref)``. ``blob_rel`` may escape the storage
    root (test g); page_end equals the value in ``pages`` (test e).
    """
    from app.services.ingestion import get_storage_dir

    doc = Document(
        matter_id=matter_id,
        filename="synthetic_doc.txt",
        original_name="synthetic_doc.txt",
        mime_type="text/plain",
        doc_type="contract",
        status=status,
        chunk_count=len(pages),
    )
    session.add(doc)
    await session.flush()
    if blob_rel is None:
        blob_rel = f"matter_{matter_id}/doc_{doc.id}_v1_synthetic.txt"
    path = get_storage_dir() / blob_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SYNTHETIC fixture content — never real legal text.\n", encoding="utf-8")
    session.add(DocumentVersion(document_id=doc.id, version_no=1, blob_ref=blob_rel))
    for index, page in enumerate(pages):
        session.add(
            DocumentSection(
                document_id=doc.id,
                matter_id=matter_id,
                version_no=1,
                section_id=f"sec-{index}",
                parent_section_id=None,
                title=f"Section {index}",
                page_start=page,
                page_end=page,
                span_start=index * 24,
                span_end=index * 24 + 24,
                faithful_text="SYNTHETIC faithful text",
                normalized_text="synthetic faithful text",
                ocr_confidence=0.93,
                needs_review=False,
            )
        )
    await session.commit()
    return int(doc.id), blob_rel


async def _row_count(factory, model, *criteria) -> int:
    """Scalar row count for ``model`` narrowed by ``criteria``."""
    async with factory() as session:
        stmt = select(func.count()).select_from(model)
        for criterion in criteria:
            stmt = stmt.where(criterion)
        return int((await session.execute(stmt)).scalar_one())


# (a) document delete removes rows + blob file + Qdrant points ----------------


def test_delete_document_purges_rows_blobs_and_points(
    client: TestClient,
    db_session_factory,
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE document: Document/Version/Section rows, blob file and points all gone."""
    import anyio

    store = _use_real_store(monkeypatch)
    matter_id = REAL_STORE_MATTER_IDS["document_delete"]

    async def _seed() -> tuple[int, str]:
        async with db_session_factory() as session:
            await _seed_matter(session, matter_id=matter_id)
            return await _seed_document(session, matter_id=matter_id, pages=[1, 2])

    document_id, blob_rel = anyio.run(_seed)
    _seed_points(store, matter_id=matter_id, document_id=document_id, count=2)
    blob_path = storage_root / blob_rel
    assert blob_path.exists()
    assert _qdrant_count(store, matter_id=matter_id) == 2

    resp = client.delete(f"/api/v1/matters/{matter_id}/documents/{document_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "deleted",
        "document_id": document_id,
        "removed_points": 2,
        "removed_files": 1,
    }

    async def _assert_gone() -> None:
        assert await _row_count(db_session_factory, Document) == 0
        assert await _row_count(db_session_factory, DocumentVersion) == 0
        # Sections have no ORM cascade and aiosqlite FKs are OFF: this row
        # count only reaches 0 if the explicit bulk delete ran.
        assert await _row_count(db_session_factory, DocumentSection) == 0

    anyio.run(_assert_gone)
    assert not blob_path.exists()
    assert not (storage_root / f"matter_{matter_id}").exists()
    assert _qdrant_count(store, matter_id=matter_id, document_id=document_id) == 0


# (b) a second delete 404s ----------------------------------------------------


def test_second_delete_returns_404(
    client: TestClient,
    db_session_factory,
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retried DELETE finds no row: 404 "document not found", purger untouched."""
    import anyio

    fake = _FakePurger(removed=5)
    monkeypatch.setattr(matter_cleanup, "get_purger", lambda: fake)

    async def _seed() -> int:
        async with db_session_factory() as session:
            matter_id = await _seed_matter(session)
            document_id, _ = await _seed_document(session, matter_id=matter_id, pages=[1])
            return document_id

    document_id = anyio.run(_seed)

    async def _matter_id() -> int:
        async with db_session_factory() as session:
            row = (await session.execute(select(Matter.id))).scalars().first()
            return int(row)

    matter_id = anyio.run(_matter_id)
    url = f"/api/v1/matters/{matter_id}/documents/{document_id}"

    first = client.delete(url)
    assert first.status_code == 200, first.text
    assert first.json()["removed_points"] == 5
    assert fake.calls == [(matter_id, document_id)]

    second = client.delete(url)
    assert second.status_code == 404
    assert second.json()["detail"] == "document not found"
    # The purger runs only when a target exists: no second call.
    assert fake.calls == [(matter_id, document_id)]


# (c) cross-matter document delete 404s and removes nothing -------------------


def test_cross_matter_document_delete_404_and_untouched(
    client: TestClient,
    db_session_factory,
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matter B deleting matter A's document: 404, and every artifact survives."""
    import anyio

    store = _use_real_store(monkeypatch)
    victim_matter = REAL_STORE_MATTER_IDS["cross_matter_victim"]
    attacker_matter = REAL_STORE_MATTER_IDS["cross_matter_attacker"]

    async def _seed() -> tuple[int, str]:
        async with db_session_factory() as session:
            await _seed_matter(session, matter_id=victim_matter)
            await _seed_matter(session, matter_id=attacker_matter)
            return await _seed_document(session, matter_id=victim_matter, pages=[1, 2])

    document_id, blob_rel = anyio.run(_seed)
    _seed_points(store, matter_id=victim_matter, document_id=document_id, count=2)
    blob_path = storage_root / blob_rel
    assert blob_path.exists()
    assert _qdrant_count(store, matter_id=victim_matter) == 2

    resp = client.delete(f"/api/v1/matters/{attacker_matter}/documents/{document_id}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "document not found"

    async def _assert_untouched() -> None:
        assert await _row_count(db_session_factory, Document) == 1
        assert await _row_count(db_session_factory, DocumentVersion) == 1
        assert (
            await _row_count(
                db_session_factory, DocumentSection, DocumentSection.document_id == document_id
            )
            == 2
        )

    anyio.run(_assert_untouched)
    assert blob_path.exists()
    assert (storage_root / f"matter_{victim_matter}").exists()
    assert _qdrant_count(store, matter_id=victim_matter, document_id=document_id) == 2


# (d) matter delete cascades everything ---------------------------------------


def test_delete_matter_cascades_everything(
    client: TestClient,
    db_session_factory,
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE matter: docs+versions+sections+convs+msgs+analyses+drafts+points+files."""
    import anyio

    store = _use_real_store(monkeypatch)
    matter_id = REAL_STORE_MATTER_IDS["matter_delete"]

    async def _seed() -> list[str]:
        async with db_session_factory() as session:
            await _seed_matter(session, matter_id=matter_id)
            doc_a, blob_a = await _seed_document(session, matter_id=matter_id, pages=[1, 2])
            doc_b, blob_b = await _seed_document(
                session, matter_id=matter_id, pages=[3], status="needs_review"
            )
            conv = Conversation(matter_id=matter_id, title="Synthetic conversation")
            session.add(conv)
            await session.flush()
            session.add(
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content="SYNTHETIC message — never real legal text.",
                )
            )
            session.add(Analysis(matter_id=matter_id, kind="findings", content_json={}))
            session.add(
                Draft(
                    matter_id=matter_id,
                    draft_type="memo",
                    content="SYNTHETIC draft content.",
                )
            )
            await session.commit()
            _seed_points(store, matter_id=matter_id, document_id=doc_a, count=2)
            _seed_points(store, matter_id=matter_id, document_id=doc_b, count=2)
            return [blob_a, blob_b]

    blob_refs = anyio.run(_seed)
    assert _qdrant_count(store, matter_id=matter_id) == 4
    blob_paths = [storage_root / ref for ref in blob_refs]
    assert all(p.exists() for p in blob_paths)

    resp = client.delete(f"/api/v1/matters/{matter_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "deleted",
        "id": matter_id,
        "removed_documents": 2,
        "removed_points": 4,
        "removed_files": 2,
    }

    async def _assert_cascaded() -> None:
        assert await _row_count(db_session_factory, Matter) == 0
        assert await _row_count(db_session_factory, Document) == 0
        assert await _row_count(db_session_factory, DocumentVersion) == 0
        assert (
            await _row_count(
                db_session_factory, DocumentSection, DocumentSection.matter_id == matter_id
            )
            == 0
        )
        assert await _row_count(db_session_factory, Conversation) == 0
        assert await _row_count(db_session_factory, Message) == 0
        assert await _row_count(db_session_factory, Analysis) == 0
        assert await _row_count(db_session_factory, Draft) == 0

    anyio.run(_assert_cascaded)
    assert not any(p.exists() for p in blob_paths)
    assert not (storage_root / f"matter_{matter_id}").exists()
    assert _qdrant_count(store, matter_id=matter_id) == 0

    follow_up = client.get(f"/api/v1/matters/{matter_id}")
    assert follow_up.status_code == 404
    assert follow_up.json()["detail"] == "matter not found"


# (e) GET documents: empty matter, aggregates, derived needs_review ----------


def test_list_documents_empty_matter_returns_200(
    client: TestClient, db_session_factory, storage_root: Path
) -> None:
    """A matter with zero documents reads as 200 [], never 404."""
    import anyio

    async def _seed() -> int:
        async with db_session_factory() as session:
            return await _seed_matter(session)

    matter_id = anyio.run(_seed)
    resp = client.get(f"/api/v1/matters/{matter_id}/documents")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_documents_missing_matter_404(client: TestClient, storage_root: Path) -> None:
    """Unknown matter on the documents list: 404 "matter not found"."""
    resp = client.get("/api/v1/matters/999999/documents")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "matter not found"


def test_list_documents_reports_aggregates_and_review_flag(
    client: TestClient, db_session_factory, storage_root: Path
) -> None:
    """section_count, page_count (max page_end or null) and needs_review per row."""
    import anyio

    async def _seed() -> int:
        async with db_session_factory() as session:
            matter_id = await _seed_matter(session)
            # Unordered pages: page_count must be the MAX page_end (3).
            await _seed_document(
                session, matter_id=matter_id, pages=[1, 3, 2], status="needs_review"
            )
            await _seed_document(session, matter_id=matter_id, pages=[], status="indexed")
            return matter_id

    matter_id = anyio.run(_seed)
    resp = client.get(f"/api/v1/matters/{matter_id}/documents")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    first, second = rows
    # Ordered by document id ascending.
    assert first["document_id"] < second["document_id"]
    assert first["section_count"] == 3
    assert first["page_count"] == 3
    assert first["needs_review"] is True
    assert first["status"] == "needs_review"
    assert first["chunk_count"] == 3
    assert first["original_name"] == "synthetic_doc.txt"
    assert first["filename"] == "synthetic_doc.txt"
    assert first["doc_type"] == "contract"
    assert first["created_at"]
    # No sections -> null page_count, zero section_count.
    assert second["section_count"] == 0
    assert second["page_count"] is None
    assert second["needs_review"] is False
    assert second["status"] == "indexed"


# (f) PATCH lives in tests/test_matters.py -----------------------------------

# (g) blob paths escaping the storage root are never unlinked -----------------


def test_blob_escaping_storage_root_is_not_unlinked(
    client: TestClient,
    db_session_factory,
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blob_ref resolving outside the storage root is skipped, not deleted."""
    import anyio

    fake = _FakePurger(removed=0)
    monkeypatch.setattr(matter_cleanup, "get_purger", lambda: fake)

    outside = storage_root.parent / "outside.txt"
    outside.write_text("SYNTHETIC out-of-root file — must survive.\n", encoding="utf-8")

    async def _seed() -> int:
        async with db_session_factory() as session:
            matter_id = await _seed_matter(session)
            document_id, _ = await _seed_document(
                session, matter_id=matter_id, pages=[1], blob_rel="../outside.txt"
            )
            return document_id

    document_id = anyio.run(_seed)

    async def _matter_id() -> int:
        async with db_session_factory() as session:
            row = (await session.execute(select(Matter.id))).scalars().first()
            return int(row)

    matter_id = anyio.run(_matter_id)
    assert outside.exists()

    resp = client.delete(f"/api/v1/matters/{matter_id}/documents/{document_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "deleted"
    assert body["removed_files"] == 0, body

    # The escaping file must survive; the in-DB rows are still purged.
    assert outside.exists()

    async def _assert_rows_gone() -> None:
        assert await _row_count(db_session_factory, Document) == 0
        assert await _row_count(db_session_factory, DocumentSection) == 0

    anyio.run(_assert_rows_gone)
