"""Matter upload route: POST /api/v1/matters/{matter_id}/documents/upload.

Multipart PDF/DOCX/TXT/MD. Reads the file with a byte bound, delegates to the
ingestion pipeline, commits once, and translates typed pipeline errors into
HTTP statuses. Oversize bodies are rejected before buffering the whole file.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion import pipeline as pipeline_mod
from app.ingestion.errors import (
    CorruptFileError,
    MatterNotFoundError,
    OversizeError,
    StorageError,
    UnsupportedTypeError,
)
from app.ingestion.schemas import UploadInput
from app.models.base import get_db

router = APIRouter(prefix="/api/v1/matters", tags=["documents"])


class SectionOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str
    parent_section_id: str | None
    title: str
    page_start: int
    page_end: int
    span_start: int
    span_end: int
    faithful_text: str
    normalized_text: str
    ocr_confidence: float | None
    needs_review: bool


class UploadOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: int
    document_id: int
    version_no: int
    filename: str
    doc_type: str
    status: str
    needs_review: bool
    blob_ref: str
    indexed_count: int
    error: str | None
    sections: list[SectionOut]


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
    except MatterNotFoundError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except OversizeError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    except UnsupportedTypeError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except CorruptFileError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except StorageError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
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
