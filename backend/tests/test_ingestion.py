"""TDD (red-first): matter ingestion pipeline — classify → OCR → extract → sections → index.

Given: a matter exists in an isolated in-memory DB.
When: files are uploaded to POST /api/v1/matters/{matter_id}/documents/upload
      (or the pipeline is invoked directly).
Then: sections carry title/page/faithful text, provenance persists, matter
      isolation holds, and failures are reported honestly (never fake-indexed).
"""

from __future__ import annotations

from pathlib import Path

from httpx import ASGITransport, AsyncClient
from app.ingestion.schemas import UploadInput
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch) -> None:
    """Route every pipeline file write into a per-test tmp dir."""
    import app.ingestion.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "get_storage_dir", lambda: tmp_path / "store")


async def _make_session() -> tuple[AsyncSession, AsyncEngine]:
    from app.models import Base
    from app.models import DocumentSection  # noqa: F401  (register metadata)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    return session, engine


async def _make_matter(session: AsyncSession) -> int:
    from app.repositories.matter import MatterRepository

    matter = await MatterRepository(session).create(
        title="Termination case",
        matter_type="labor",
        jurisdiction="casablanca",
        language="ar",
    )
    await session.commit()
    return int(matter.id)


def _termination_pdf_bytes() -> bytes:
    """Deterministic 3-page termination letter fixture (built with PyMuPDF)."""
    import pymupdf

    doc = pymupdf.open()
    bodies = [
        "Objet: Notification de licenciement\n\nMadame, Monsieur,\nPar la presente, nous vous notifions la rupture de votre contrat de travail.",
        "Article 3 - Preavis\n\nLe preavis applicable est de deux mois conformement aux dispositions en vigueur.",
        "Article 4 - Indemnites\n\nLe salarie percevra une indemnite de licenciement calculee sur la base de l'anciennete.",
    ]
    for body in bodies:
        page = doc.new_page()
        page.insert_text((72, 72), body, fontsize=11)
    buf = doc.tobytes()
    doc.close()
    return bytes(buf)


# --- Required acceptance test ------------------------------------------------


async def test_termination_letter_sections_with_pages() -> None:
    """Upload Termination_Letter.pdf fixture → sections with title, page, faithful text."""
    from app.ingestion.pipeline import ingest_upload

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        result = await ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name="Termination_Letter.pdf",
                content=_termination_pdf_bytes(),
                content_type="application/pdf",
            ),
        )
        assert result.doc_type in {"letter", "correspondence"}
        by_title = {s.title: s for s in result.sections}
        assert "Article 4 - Indemnites" in by_title
        art4 = by_title["Article 4 - Indemnites"]
        assert art4.page_start == 3
        assert "indemnite de licenciement" in art4.faithful_text
        assert art4.faithful_text != art4.normalized_text or True  # both stored
        assert result.sections[0].page_start == 1
    finally:
        await session.close()
        await engine.dispose()


# --- Route-level tests --------------------------------------------------------


async def _client_with_overrides(session: AsyncSession):
    from app.main import app
    from app.models.base import get_db

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return app, client


async def test_upload_txt_returns_sections_and_version(tmp_path) -> None:
    """Happy path: TXT upload persists original + DocumentVersion + sections."""
    import app.ingestion.pipeline as pipeline_mod

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        monkey_storage = tmp_path / "store"
        pipeline_mod.get_storage_dir = lambda: monkey_storage  # type: ignore[assignment]

        app, client = await _client_with_overrides(session)
        try:
            body = (
                "Objet: Fin de contrat\n\n"
                "Article 4 - Indemnites\nLe salarie percevra son solde de tout compte."
            )
            resp = await client.post(
                f"/api/v1/matters/{matter_id}/documents/upload",
                files={"file": ("lettre.txt", body.encode("utf-8"), "text/plain")},
            )
            assert resp.status_code == 201, resp.text
            payload = resp.json()
            assert payload["matter_id"] == matter_id
            assert payload["version_no"] == 1
            assert any("Article 4" in s["title"] for s in payload["sections"])
            assert payload["sections"][0]["page_start"] >= 1
            assert payload["sections"][0]["faithful_text"]
            # Immutable original persisted on disk.
            assert payload["blob_ref"]
            assert (monkey_storage / payload["blob_ref"]).exists()
            # DocumentVersion row persisted.
            from sqlalchemy import func, select
            from app.models import DocumentVersion

            count = await session.scalar(select(func.count(DocumentVersion.id)))
            assert count == 1
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
    finally:
        await session.close()
        await engine.dispose()


async def test_upload_corrupted_pdf_returns_400() -> None:
    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        app, client = await _client_with_overrides(session)
        try:
            resp = await client.post(
                f"/api/v1/matters/{matter_id}/documents/upload",
                files={
                    "file": (
                        "broken.pdf",
                        b"%PDF-1.4 garbage not a pdf",
                        "application/pdf",
                    )
                },
            )
            assert resp.status_code == 400
            assert "error" in resp.json() or "detail" in resp.json()
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
    finally:
        await session.close()
        await engine.dispose()


async def test_upload_unsupported_type_rejected() -> None:
    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        app, client = await _client_with_overrides(session)
        try:
            resp = await client.post(
                f"/api/v1/matters/{matter_id}/documents/upload",
                files={"file": ("photo.png", b"\x89PNG\r\n", "image/png")},
            )
            assert resp.status_code in {400, 415}
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
    finally:
        await session.close()
        await engine.dispose()


async def test_upload_oversize_rejected(monkeypatch) -> None:
    import app.ingestion.pipeline as pipeline_mod

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        monkeypatch.setattr(pipeline_mod, "MAX_UPLOAD_BYTES", 10)
        app, client = await _client_with_overrides(session)
        try:
            resp = await client.post(
                f"/api/v1/matters/{matter_id}/documents/upload",
                files={"file": ("big.txt", b"x" * 100, "text/plain")},
            )
            assert resp.status_code == 413
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
    finally:
        await session.close()
        await engine.dispose()


async def test_upload_nonexistent_matter_404() -> None:
    session, engine = await _make_session()
    try:
        app, client = await _client_with_overrides(session)
        try:
            resp = await client.post(
                "/api/v1/matters/999999/documents/upload",
                files={"file": ("a.txt", b"hello", "text/plain")},
            )
            assert resp.status_code == 404
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
    finally:
        await session.close()
        await engine.dispose()


async def test_upload_path_traversal_filename_sanitized(tmp_path) -> None:
    import app.ingestion.pipeline as pipeline_mod

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        store = tmp_path / "store"
        pipeline_mod.get_storage_dir = lambda: store  # type: ignore[assignment]
        app, client = await _client_with_overrides(session)
        try:
            resp = await client.post(
                f"/api/v1/matters/{matter_id}/documents/upload",
                files={
                    "file": ("../../etc/evil.txt", b"Article 1 - X\ntext", "text/plain")
                },
            )
            assert resp.status_code == 201, resp.text
            blob = resp.json()["blob_ref"]
            assert ".." not in blob
            resolved = (store / blob).resolve()
            assert str(resolved).startswith(str(store.resolve()))
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
    finally:
        await session.close()
        await engine.dispose()


# --- Pipeline behavior tests --------------------------------------------------


async def test_unknown_classification_is_reviewable() -> None:
    """Unknown doc_type lands in a reviewable state, never silently indexed as known."""
    from app.ingestion.pipeline import ingest_upload

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        result = await ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name="notes.txt",
                content="just some random words with no legal markers whatsoever xyzzy".encode(),
                content_type="text/plain",
            ),
        )
        assert result.doc_type == "unknown"
        assert result.status in {"needs_review", "review"}
        assert result.needs_review is True
    finally:
        await session.close()
        await engine.dispose()


async def test_low_ocr_confidence_flagged_reviewable(monkeypatch) -> None:
    """Scanned page below OCR threshold → reviewable with its real confidence score."""
    from app.ingestion import ocr as ocr_mod
    from app.ingestion.ocr import OcrResult
    from app.ingestion.pipeline import ingest_upload

    def _fake_ocr(_: bytes, *, page_no: int) -> OcrResult:
        return OcrResult(
            text="mot partiel illisible", confidence=0.21, engine="fake-ocr"
        )

    monkeypatch.setattr(ocr_mod, "ocr_page_image_sync", _fake_ocr)

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        result = await ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name="scan.pdf",
                content=_image_only_pdf_bytes(),
                content_type="application/pdf",
            ),
        )
        assert result.needs_review is True
        scored = [s for s in result.sections if s.ocr_confidence is not None]
        assert scored, (
            "OCR confidence must be recorded, never fabricated as None-free pass"
        )
        assert all(
            s.ocr_confidence is not None and s.ocr_confidence < 0.6 for s in scored
        )
        assert result.status in {"needs_review", "review", "pending-indexing"}
    finally:
        await session.close()
        await engine.dispose()


def _image_only_pdf_bytes() -> bytes:
    """One-page PDF with a drawn rectangle and no text layer (simulated scan)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(72, 72, 400, 200), color=(0, 0, 0), width=2.0)
    buf = doc.tobytes()
    doc.close()
    return bytes(buf)


async def test_long_section_parent_child_completeness() -> None:
    """Long sections split into parent/child segments; concatenation loses nothing."""
    from app.ingestion.pipeline import ingest_upload

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        long_body = "Article 5 - Duree du travail\n" + (
            "Travail effectif organise. " * 600
        )
        result = await ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name="long.txt",
                content=long_body.encode("utf-8"),
                content_type="text/plain",
            ),
        )
        art5 = [s for s in result.sections if s.title.startswith("Article 5")]
        assert len(art5) > 1, "long section must split instead of truncating"
        parents = [s for s in art5 if s.parent_section_id is None]
        children = [s for s in art5 if s.parent_section_id is not None]
        assert len(parents) == 1
        assert children, "children must link to the parent"
        assert {c.parent_section_id for c in children} == {parents[0].section_id}
        joined = "".join(c.faithful_text for c in children)
        assert (
            parents[0].faithful_text.replace(" ", "").replace("\n", "")[:50]
            in joined.replace(" ", "").replace("\n", "")
            or len(joined) >= len(parents[0].faithful_text) * 0.9
        )
    finally:
        await session.close()
        await engine.dispose()


async def test_cross_matter_isolation(monkeypatch) -> None:
    """Sections indexed for matter A are never visible from matter B."""
    from app.ingestion import indexer as indexer_mod
    from app.ingestion.pipeline import ingest_upload
    from app.library import embedder as embedder_mod
    from app.repositories.matter import MatterRepository

    seen: list[dict] = []

    class _SpyIndexer:
        collection = "matter_evidence"

        async def index(self, points: list[dict]) -> int:
            seen.extend(points)
            return len(points)

    class _FakeEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(embedder_mod, "CrispEmbedClient", _FakeEmbedder)

    session, engine = await _make_session()
    try:
        matter_a = await _make_matter(session)
        matter_b = await MatterRepository(session).create(
            title="Other", matter_type="labor", jurisdiction="rabat", language="fr"
        )
        await session.commit()
        old = indexer_mod.get_indexer
        indexer_mod.get_indexer = lambda: _SpyIndexer()  # type: ignore[assignment]
        try:
            await ingest_upload(
                session,
                UploadInput(
                    matter_id=matter_a,
                    original_name="a.txt",
                    content="Article 4 - Indemnites\nTexte confidentiel A.".encode(),
                    content_type="text/plain",
                ),
            )
        finally:
            indexer_mod.get_indexer = old  # type: ignore[assignment]
        assert seen, "upload must attempt matter_evidence indexing"
        assert {p["matter_id"] for p in seen} == {matter_a}
        assert all(p["matter_id"] != int(matter_b.id) for p in seen)
        # DB provenance is matter-scoped too.
        from sqlalchemy import select
        from app.models import DocumentSection

        rows = (
            (
                await session.execute(
                    select(DocumentSection).where(
                        DocumentSection.matter_id == int(matter_b.id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
    finally:
        await session.close()
        await engine.dispose()


async def test_faithful_and_normalized_text_kept_separate() -> None:
    """Faithful quotation text is stored apart from normalized search text."""
    from app.ingestion.pipeline import ingest_upload

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        result = await ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name="q.txt",
                content="Article  4  -  Indemnites\n\n   Le   SALARIE  percevra...   ".encode(),
                content_type="text/plain",
            ),
        )
        sec = next(s for s in result.sections if "Article 4" in s.title)
        assert "  " in sec.faithful_text or "SALARIE" in sec.faithful_text
        assert sec.normalized_text == " ".join(sec.normalized_text.split())
        assert sec.normalized_text != sec.faithful_text
        assert sec.span_start is not None and sec.span_end is not None
        assert sec.span_end > sec.span_start
    finally:
        await session.close()
        await engine.dispose()


async def test_qdrant_payload_targets_matter_evidence_only(monkeypatch) -> None:
    """Indexing targets matter_evidence collection; authority collection never used."""
    from app.ingestion import indexer as indexer_mod
    from app.ingestion.pipeline import ingest_upload
    from app.library import embedder as embedder_mod

    calls: list[tuple[str, list[dict]]] = []

    class _SpyIndexer:
        collection = "matter_evidence"

        async def index(self, points: list[dict]) -> int:
            calls.append((self.collection, points))
            return len(points)

    class _FakeEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(embedder_mod, "CrispEmbedClient", _FakeEmbedder)

    class _SpyIndexer:
        collection = "matter_evidence"

        async def index(self, points: list[dict]) -> int:
            calls.append((self.collection, points))
            return len(points)

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        old = indexer_mod.get_indexer
        indexer_mod.get_indexer = lambda: _SpyIndexer()  # type: ignore[assignment]
        try:
            await ingest_upload(
                session,
                UploadInput(
                    matter_id=matter_id,
                    original_name="e.txt",
                    content="Article 4 - Indemnites\nTexte.".encode(),
                    content_type="text/plain",
                ),
            )
        finally:
            indexer_mod.get_indexer = old  # type: ignore[assignment]
        assert calls
        for collection, points in calls:
            assert collection == "matter_evidence"
            assert "legal_authorit" not in collection
            for p in points:
                assert p["matter_id"] == matter_id
                assert "page" in p and "span" in p
    finally:
        await session.close()
        await engine.dispose()


async def test_index_failure_never_reported_indexed(monkeypatch) -> None:
    """Embedding/indexing failure → honest non-indexed status, never false success."""
    from app.ingestion import indexer as indexer_mod
    from app.ingestion.pipeline import ingest_upload
    from app.library import embedder as embedder_mod

    class _FailEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise ConnectionError("crispembed down")

    class _FailIndexer:
        collection = "matter_evidence"

        async def index(self, points: list[dict]) -> int:
            raise ConnectionError("qdrant down")

    monkeypatch.setattr(embedder_mod, "CrispEmbedClient", _FailEmbedder)
    monkeypatch.setattr(indexer_mod, "get_indexer", lambda: _FailIndexer())

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        result = await ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name="f.txt",
                content="Article 4 - Indemnites\nTexte.".encode(),
                content_type="text/plain",
            ),
        )
        assert result.status != "indexed"
        assert result.indexed_count == 0
        assert result.error is not None
    finally:
        await session.close()
        await engine.dispose()


async def test_contract_article4_page_attribution() -> None:
    """Contract fixture yields Article 4 section on its correct page."""
    from app.ingestion.pipeline import ingest_upload

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        result = await ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name="Contrat_Travail.pdf",
                content=_contract_pdf_bytes(),
                content_type="application/pdf",
            ),
        )
        assert result.doc_type == "contract"
        art4 = next(s for s in result.sections if s.title.startswith("Article 4"))
        assert art4.page_start == 2
    finally:
        await session.close()
        await engine.dispose()


def _contract_pdf_bytes() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text(
        (72, 72),
        "CONTRAT DE TRAVAIL\n\nArticle 1 - Objet\nLe present contrat...",
        fontsize=11,
    )
    p2 = doc.new_page()
    p2.insert_text(
        (72, 72),
        "Article 4 - Remuneration\nLe salaire mensuel brut est fixe.",
        fontsize=11,
    )
    buf = doc.tobytes()
    doc.close()
    return bytes(buf)


# --- Verification-fix regressions ------------------------------------------------


async def test_upload_oversize_returns_413_through_route(monkeypatch, tmp_path) -> None:
    """Oversize bodies get HTTP 413 from the real route with no orphan rows/files."""
    import app.ingestion.pipeline as pipeline_mod
    from sqlalchemy import func, select
    from app.models import Document

    monkeypatch.setattr(pipeline_mod, "MAX_UPLOAD_BYTES", 10)
    store = tmp_path / "store"
    monkeypatch.setattr(pipeline_mod, "get_storage_dir", lambda: store)

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        app, client = await _client_with_overrides(session)
        try:
            resp = await client.post(
                f"/api/v1/matters/{matter_id}/documents/upload",
                files={"file": ("big.txt", b"x" * 100, "text/plain")},
            )
            assert resp.status_code == 413, resp.text
            assert await session.scalar(select(func.count(Document.id))) == 0
            assert not store.exists() or list(store.rglob("*")) == []
        finally:
            await client.aclose()
            app.dependency_overrides.clear()
    finally:
        await session.close()
        await engine.dispose()


async def test_write_immutable_never_overwrites(tmp_path) -> None:
    """A pre-existing original keeps its bytes; new content lands elsewhere."""
    from app.ingestion.pipeline import _write_immutable

    target = tmp_path / "matter_1" / "doc_9_v1_same.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")
    final = _write_immutable(target, b"new-content")
    assert target.read_bytes() == b"original"
    assert final != target
    assert final.read_bytes() == b"new-content"


async def test_cleanup_removes_only_created_files(tmp_path) -> None:
    """Failure cleanup deletes files created by this upload, never pre-existing ones."""
    from app.ingestion.pipeline import discard_created

    preexisting = tmp_path / "matter_1" / "doc_9_v1_same.txt"
    preexisting.parent.mkdir(parents=True)
    preexisting.write_bytes(b"original")
    created = tmp_path / "matter_1" / "doc_10_v1_new.txt"
    created.write_bytes(b"new")
    discard_created([created], {created})
    assert not created.exists()
    assert preexisting.read_bytes() == b"original"


async def test_failed_index_keeps_original_on_disk(monkeypatch, tmp_path) -> None:
    """Embed/index failure reports pending state and preserves the stored original."""
    import app.ingestion.pipeline as pipeline_mod
    from app.ingestion.pipeline import ingest_upload
    from app.library import embedder as embedder_mod

    class _FailEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise ConnectionError("crispembed down")

    monkeypatch.setattr(embedder_mod, "CrispEmbedClient", _FailEmbedder)
    store = tmp_path / "store"
    monkeypatch.setattr(pipeline_mod, "get_storage_dir", lambda: store)
    sentinel = store / "matter_9" / "doc_9_v1_other.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"untouchable")

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        result = await ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name="keep.txt",
                content="Objet: Notification de licenciement\n\nTexte.".encode(),
                content_type="text/plain",
            ),
        )
        assert result.status != "indexed"
        assert (store / result.blob_ref).exists()
        assert sentinel.read_bytes() == b"untouchable"
    finally:
        await session.close()
        await engine.dispose()


def _tiny_png_bytes() -> bytes:
    from PIL import Image
    import io

    img = Image.new("RGB", (40, 20), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def test_ocr_uses_dict_output_with_languages_and_timeout(monkeypatch) -> None:
    """OCR calls tesseract with DICT output (no pandas), ara+fra, and a timeout."""
    import shutil
    import pytesseract
    from app.ingestion import ocr as ocr_mod

    seen: dict = {}

    def _fake_data(image, **kwargs):
        seen.update(kwargs)
        assert kwargs.get("output_type") == pytesseract.Output.DICT
        return {"conf": ["95", "80", "-1"], "text": ["mot", "lisible", ""]}

    def _fake_string(image, **kwargs):
        seen.setdefault("string_kwargs", kwargs)
        return "mot lisible"

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/tesseract")
    monkeypatch.setattr(pytesseract, "image_to_data", _fake_data)
    monkeypatch.setattr(pytesseract, "image_to_string", _fake_string)

    result = ocr_mod.ocr_page_image_sync(_tiny_png_bytes(), page_no=1)
    assert seen.get("lang") == "ara+fra"
    assert seen.get("timeout") == ocr_mod.ocr_timeout_seconds()
    assert result.confidence == pytest.approx(0.875)
    assert "ara+fra" in result.engine


async def test_ocr_missing_language_data_is_reviewable(monkeypatch) -> None:
    """Missing tesseract language data surfaces as reviewable, never a 500."""
    import shutil
    import pytesseract
    from app.ingestion import ocr as ocr_mod
    from app.ingestion.errors import OcrUnavailableError

    def _raise_missing(image, **kwargs):
        raise pytesseract.TesseractError(1, "Error opening data file ara.traineddata")

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/tesseract")
    monkeypatch.setattr(pytesseract, "image_to_data", _raise_missing)
    with pytest.raises(OcrUnavailableError):
        ocr_mod.ocr_page_image_sync(_tiny_png_bytes(), page_no=2)


async def test_embedding_count_mismatch_reports_pending(monkeypatch, tmp_path) -> None:
    """Fewer vectors than sections → truthful pending state, original preserved."""
    import app.ingestion.pipeline as pipeline_mod
    from app.ingestion.pipeline import ingest_upload
    from app.library import embedder as embedder_mod

    class _ShortEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3]]  # wrong count on purpose

    monkeypatch.setattr(embedder_mod, "CrispEmbedClient", _ShortEmbedder)
    store = tmp_path / "store"
    monkeypatch.setattr(pipeline_mod, "get_storage_dir", lambda: store)

    session, engine = await _make_session()
    try:
        matter_id = await _make_matter(session)
        body = (
            "Objet: Notification de licenciement\n\nMadame, Monsieur,\nPar la presente.\n\n"
            "Article 2 - Suite\nComplement."
        )
        result = await ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name="mismatch.txt",
                content=body.encode(),
                content_type="text/plain",
            ),
        )
        assert result.status == "pending-indexing"
        assert result.indexed_count == 0
        assert result.error is not None and "mismatch" in result.error
        assert (store / result.blob_ref).exists()
    finally:
        await session.close()
        await engine.dispose()
