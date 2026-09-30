"""Matter upload route: POST /api/v1/matters/{matter_id}/documents/upload.

Multipart PDF/DOCX/TXT/MD. Reads the file with a byte bound, delegates to the
ingestion pipeline (which commits provenance before network I/O, then commits
the indexing status separately, so no DB transaction is held across embedding
or vector-store calls), and translates typed pipeline errors into
HTTP statuses. Oversize bodies are rejected before buffering the whole file.

Also owns the document lifecycle reads/deletes under the same prefix: list
documents for a matter, and hard-delete one document (rows + evidence points
+ blob originals via app.services.matter_cleanup).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, map_error
from app.domain.ingestion.errors import (
    CorruptFileError,
    MatterNotFoundError,
    OversizeError,
    StorageError,
    UnsupportedTypeError,
)
from app.domain.ingestion.schemas import UploadInput
from app.models.base import get_db
from app.repositories.document import DocumentRepository
from app.repositories.matter import MatterRepository
from app.schemas.documents import MatterDocumentOut, SectionOut, UploadOut
from app.services import ingestion as pipeline_mod
from app.services.matter_cleanup import purge_document

router = APIRouter(prefix="/api/v1/matters", tags=["documents"])


@router.get("/{matter_id}/documents", response_model=list[MatterDocumentOut])
async def list_documents(
    matter_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[MatterDocumentOut]:
    """List a matter's documents (id ascending); 200 [] when there are none."""
    if await MatterRepository(session).get(matter_id) is None:
        raise HTTPException(*map_error(NotFoundError("matter not found")))
    rows = await DocumentRepository(session).list_detail_for_matter(matter_id)
    return [
        MatterDocumentOut(
            document_id=row.document.id,
            original_name=row.document.original_name,
            filename=row.document.filename,
            doc_type=row.document.doc_type,
            status=row.document.status,
            needs_review=row.document.status == "needs_review",
            chunk_count=row.document.chunk_count,
            section_count=row.section_count,
            page_count=row.page_count,
            created_at=row.document.created_at,
        )
        for row in rows
    ]


@router.delete(
    "/{matter_id}/documents/{document_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_document(
    matter_id: int,
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Hard-delete one document: rows, evidence points, and blob originals."""
    result = await purge_document(session, matter_id=matter_id, document_id=document_id)
    if result is None:
        # Absent OR belonging to another matter: cross-matter access is a
        # hard boundary, so both read as "document not found".
        raise HTTPException(*map_error(NotFoundError("document not found")))
    return {
        "status": "deleted",
        "document_id": document_id,
        "removed_points": result.removed_points,
        "removed_files": result.removed_files,
    }


@router.post(
    "/{matter_id}/documents/upload",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    matter_id: int,
    file: Annotated[UploadFile, File(...)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UploadOut:
    """Store the immutable original, run the pipeline, persist provenance."""
    try:
        content = await _read_bounded(file)
        result = await pipeline_mod.ingest_upload(
            session,
            UploadInput(
                matter_id=matter_id,
                original_name=file.filename or "upload",
                content=content,
                content_type=file.content_type,
            ),
        )
    except (
        MatterNotFoundError,
        OversizeError,
        UnsupportedTypeError,
        CorruptFileError,
        StorageError,
    ) as exc:
        await session.rollback()
        raise HTTPException(*map_error(exc)) from exc
    # Pipeline owns its commits (provenance, then status); this is a no-op
    # safety net when the pipeline already committed.
    await session.commit()
    return UploadOut(
        matter_id=result.matter_id,
        document_id=result.document_id,
        version_no=result.version_no,
        filename=file.filename or "upload",
        doc_type=result.doc_type,
        status=result.status,
        needs_review=result.needs_review,
        blob_ref=result.blob_ref,
        indexed_count=result.indexed_count,
        error=result.error,
        sections=[
            SectionOut(
                section_id=s.section_id,
                parent_section_id=s.parent_section_id,
                title=s.title,
                page_start=s.page_start,
                page_end=s.page_end,
                span_start=s.span_start,
                span_end=s.span_end,
                faithful_text=s.faithful_text,
                normalized_text=s.normalized_text,
                ocr_confidence=s.ocr_confidence,
                needs_review=s.needs_review,
            )
            for s in result.sections
        ],
    )


async def _read_bounded(file: UploadFile) -> bytes:
    """Read the upload in chunks; abort with OversizeError past the bound."""
    chunks: list[bytes] = []
    total = 0
    while True:
        piece = await file.read(1024 * 1024)
        if not piece:
            break
        total += len(piece)
        if total > pipeline_mod.MAX_UPLOAD_BYTES:
            raise OversizeError(size=total, limit=pipeline_mod.MAX_UPLOAD_BYTES)
        chunks.append(piece)
    return b"".join(chunks)
