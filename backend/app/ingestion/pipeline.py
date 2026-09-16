"""Upload orchestration: store → classify → extract → sections → persist → embed → index.

Blocking I/O (PyMuPDF/DOCX/file writes) runs in worker threads; the async
session stays on the event loop. Failures in embed/index never report
``indexed``: the status stays ``needs_review`` or ``pending-indexing`` with
the error recorded.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion import indexer as indexer_mod
from app.ingestion.classifier import classify
from app.ingestion.errors import (
    CorruptFileError,
    MatterNotFoundError,
    OversizeError,
    UnsupportedTypeError,
)
from app.ingestion.extract import extract_pages
from app.ingestion.indexer import EvidencePoint
from app.ingestion.schemas import IngestResult, SectionResult, UploadInput
from app.ingestion.sections import build_sections

ALLOWED_SUFFIXES: Final[frozenset[str]] = frozenset({".pdf", ".docx", ".txt", ".md"})
MAX_UPLOAD_BYTES: Final[int] = 20_000_000


def get_storage_dir() -> Path:
    """Originals root; FASL_STORAGE_DIR overrides settings (tests/live QA)."""
    from app.config import settings

    return Path(os.getenv("FASL_STORAGE_DIR", settings.STORAGE_DIR))


def sanitize_filename(name: str) -> str:
    """Strip directories and unsafe chars; never trust client-supplied paths."""
    base = Path(name or "upload").name.strip() or "upload"
    safe = "".join(c if c.isalnum() or c in {"-", "_", "."} else "_" for c in base)
    return safe[:180] or "upload"


def check_upload_size(size: int) -> None:
    """Raise OversizeError when the payload exceeds the bound."""
    if size > MAX_UPLOAD_BYTES:
        raise OversizeError(size=size, limit=MAX_UPLOAD_BYTES)


def check_suffix(filename: str) -> str:
    """Return the lowercase suffix or raise UnsupportedTypeError."""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise UnsupportedTypeError(filename=filename)
    return suffix


async def ingest_upload(session: AsyncSession, upload: UploadInput) -> IngestResult:
    """Run the full pipeline for one upload; flush rows, leave commit to caller."""
    from anyio import to_thread

    from app.models.document import Document, DocumentVersion
    from app.models.document_section import DocumentSection
    from app.models.matter import Matter

    matter_id = upload.matter_id
    content = upload.content
    content_type = upload.content_type
    safe_name = sanitize_filename(upload.original_name)
    suffix = check_suffix(safe_name)
    check_upload_size(len(content))
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise MatterNotFoundError(matter_id=matter_id)

    pages = await to_thread.run_sync(lambda: extract_pages(content, suffix, safe_name))
    full_text = "\n".join(p.text for p in pages)
    if not full_text.strip() and not any(p.needs_review for p in pages):
        raise CorruptFileError(filename=safe_name, reason="no extractable text found")

    doc_type = classify(full_text)
    sections = build_sections(pages)
    needs_review = (
        doc_type == "unknown"
        or any(s.needs_review for s in sections)
        or any(p.needs_review for p in pages)
    )

    digest = hashlib.sha256(content).hexdigest()[:16]
    document = Document(
        matter_id=matter_id,
        filename=f"{digest}_{safe_name}",
        original_name=safe_name,
        mime_type=content_type or _guess_mime(suffix),
        doc_type=doc_type,
        status="needs_review" if needs_review else "extracted",
        chunk_count=len(sections),
    )
    session.add(document)
    await session.flush()

    blob_rel = f"matter_{matter_id}/doc_{document.id}_v1_{safe_name}"
    storage_dir = get_storage_dir()
    target = storage_dir / blob_rel
    created: set[Path] = set()
    try:
        final = await to_thread.run_sync(lambda: _write_immutable(target, content))
        created.add(final)
        blob_rel = str(final.relative_to(storage_dir))
        session.add(
            DocumentVersion(document_id=document.id, version_no=1, blob_ref=blob_rel)
        )
        for section in sections:
            session.add(
                DocumentSection(
                    document_id=document.id,
                    matter_id=matter_id,
                    version_no=1,
                    section_id=section.section_id,
                    parent_section_id=section.parent_section_id,
                    title=section.title,
                    page_start=section.page_start,
                    page_end=section.page_end,
                    span_start=section.span_start,
                    span_end=section.span_end,
                    faithful_text=section.faithful_text,
                    normalized_text=section.normalized_text,
                    ocr_confidence=section.ocr_confidence,
                    needs_review=section.needs_review,
                )
            )
        await session.flush()

        indexed, error = await _embed_and_index(
            matter_id=matter_id,
            document_id=int(document.id),
            doc_type=doc_type,
            sections=sections,
        )
        status = document.status
        if error is not None:
            status = status if needs_review else "pending-indexing"
        elif not needs_review:
            status = "indexed"
        document.status = status
        await session.flush()
    except Exception:
        await session.rollback()
        await to_thread.run_sync(lambda: discard_created(list(created), created))
        raise
    return IngestResult(
        document_id=int(document.id),
        matter_id=matter_id,
        version_no=1,
        blob_ref=blob_rel,
        doc_type=doc_type,
        status=status,
        needs_review=needs_review,
        sections=tuple(sections),
        indexed_count=indexed,
        error=error,
    )


def _write_immutable(target: Path, content: bytes, *, attempt: int = 0) -> Path:
    """Exclusive-create the original; never overwrite an existing file.

    First tries the canonical name with O_EXCL; on collision retries once with
    a content-hash nonce suffix. Returns the final path actually written.
    """
    from app.ingestion.errors import StorageError

    target.parent.mkdir(parents=True, exist_ok=True)
    if attempt > 0:
        nonce = hashlib.sha256(content).hexdigest()[:8]
        target = target.with_name(f"{target.stem}_{nonce}{target.suffix}")
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        if attempt > 0:
            raise StorageError(reason=f"original already exists: {target}") from exc
        return _write_immutable(target, content, attempt=1)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
    except OSError:
        target.unlink(missing_ok=True)  # partial write of our own exclusive file
        raise
    return target


def discard_created(paths: list[Path], created: set[Path]) -> None:
    """Delete only files created by this upload; pre-existing originals are kept."""
    for path in paths:
        if path in created:
            path.unlink(missing_ok=True)


def _guess_mime(suffix: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }[suffix]


async def _embed_and_index(
    *, matter_id: int, document_id: int, doc_type: str, sections: list[SectionResult]
) -> tuple[int, str | None]:
    """Embed normalized text via CrispEmbed HTTP and index matter_evidence.

    Returns (indexed_count, error). Any transport failure yields (0, message)
    so callers never claim success.
    """
    if not sections:
        return 0, None
    try:
        from app.library.embedder import CrispEmbedClient

        vectors = await CrispEmbedClient().embed([s.normalized_text for s in sections])
        if len(vectors) != len(sections):
            return 0, (
                "embedding count mismatch: "
                f"{len(vectors)} vectors for {len(sections)} sections"
            )
        points: list[EvidencePoint] = [
            EvidencePoint(
                id=f"{document_id}:{s.section_id}",
                vector=vec,
                matter_id=matter_id,
                document_id=document_id,
                version_no=1,
                doc_type=doc_type,
                page=s.page_start,
                span=[s.span_start, s.span_end],
                faithful_ref=s.section_id,
                text=s.normalized_text,
            )
            for s, vec in zip(sections, vectors, strict=True)
        ]
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK -- external-service boundary: record, don't crash
        return 0, f"embedding failed: {exc}"
    try:
        stored = await indexer_mod.get_indexer().index(points)
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK -- external-service boundary: record, don't crash
        return 0, f"indexing failed: {exc}"
    return stored, None
