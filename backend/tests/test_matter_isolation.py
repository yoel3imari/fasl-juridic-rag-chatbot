"""TDD: matter isolation — documents cannot cross matter boundaries."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.models import Base, Document, Matter
from app.models.base import get_engine
from app.repositories.document import DocumentRepository
from app.repositories.matter import MatterRepository


@pytest.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
        await sess.rollback()
    await engine.dispose()


async def test_cross_matter_document_unreachable(session: AsyncSession):
    """Test that documents from one matter cannot be accessed via another matter."""
    matter_a = Matter(
        title="Matter A", matter_type="labor", jurisdiction="casablanca", language="ar"
    )
    matter_b = Matter(
        title="Matter B", matter_type="labor", jurisdiction="rabat", language="fr"
    )
    session.add_all([matter_a, matter_b])
    await session.flush()

    # Create document in matter A
    doc_a = Document(
        matter_id=matter_a.id,
        filename="doc_a.pdf",
        original_name="Contract A.pdf",
        mime_type="application/pdf",
        doc_type="contract",
        status="indexed",
        chunk_count=5,
    )
    session.add(doc_a)
    await session.flush()

    # Try to access matter A's document via matter B's query
    result = await session.execute(
        select(Document).where(Document.matter_id == matter_b.id)
    )
    docs = result.scalars().all()

    # Should be empty - matter B cannot see matter A's documents
    assert len(docs) == 0


async def test_repository_enforces_matter_scope(session: AsyncSession):
    """DocumentRepository.get/list must not leak documents across matters."""
    matters = MatterRepository(session)
    docs = DocumentRepository(session)

    matter_a = await matters.create(
        title="Matter A", matter_type="labor", jurisdiction="casablanca", language="ar"
    )
    matter_b = await matters.create(
        title="Matter B", matter_type="labor", jurisdiction="rabat", language="fr"
    )

    doc_a = await docs.create(
        matter_id=matter_a.id,
        filename="doc_a.pdf",
        original_name="Contract A.pdf",
        mime_type="application/pdf",
        doc_type="contract",
        status="indexed",
        chunk_count=5,
    )

    # Cross-matter fetch returns None
    assert await docs.get(doc_a.id, matter_b.id) is None
    # Same-matter fetch works
    assert await docs.get(doc_a.id, matter_a.id) is not None
    # Cross-matter list is empty
    assert await docs.list_for_matter(matter_b.id) == []
    assert len(await docs.list_for_matter(matter_a.id)) == 1


def test_engine_accessible():
    """Sanity: shared engine factory is available."""
    assert get_engine() is not None


async def test_matter_b_never_sees_matter_a():
    """Qdrant filter proof: both matters indexed in ONE store, B sees only B.

    SYNTHETIC fixtures only — no real legal text.
    """
    from app.search import service as svc
    from app.search.store import QdrantStore

    svc.clear_search_cache()

    class _FakeEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    store = QdrantStore(local_path=":memory:", dim=4)
    for mid, tag in ((101, "alpha"), (202, "beta")):
        store.upsert_evidence(
            [
                {
                    "id": f"{tag}-sec",
                    "vector": [1.0, 0.0, 0.0, 0.0],
                    "matter_id": mid,
                    "document_id": mid,
                    "version_no": 1,
                    "doc_type": "contract",
                    "page": 1,
                    "span": [0, 7],
                    "faithful_ref": f"{tag}-sec",
                    "text": f"clause confidentielle {tag} contrat",
                }
            ]
        )

    out_b = await svc.search_matter(
        store=store,
        embedder=_FakeEmbedder(),
        matter_id=202,
        query="clause confidentielle",
        top_k=10,
    )
    assert out_b["matter"], "matter B must see its own chunk"
    assert {h["matter_id"] for h in out_b["matter"]} == {202}
    assert all("alpha" not in h["faithful_ref"] for h in out_b["matter"])

    out_a = await svc.search_matter(
        store=store,
        embedder=_FakeEmbedder(),
        matter_id=101,
        query="clause confidentielle",
        top_k=10,
    )
    assert {h["matter_id"] for h in out_a["matter"]} == {101}
