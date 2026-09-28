"""Library coverage and upload endpoints: titles, versions, editions, dates, gaps, document upload."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.config import settings
from app.schemas.library import LibraryUploadOut
from app.services import library_coverage

router = APIRouter(prefix="/api/v1/library", tags=["library"])


@router.get("/coverage")
async def coverage() -> dict:
    return await library_coverage.coverage()


@router.post(
    "/upload",
    response_model=LibraryUploadOut,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/documents",
    response_model=LibraryUploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_library_document(
    file: Annotated[UploadFile, File(...)],
    source: Annotated[str, Form(...)],
    version: Annotated[str, Form(...)],
    edition: Annotated[str, Form()] = "ar-general",
    pub_date: Annotated[str | None, Form()] = None,
    doc_date: Annotated[str | None, Form()] = None,
    hijri_date: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = "ar",
    coverage_note: Annotated[str | None, Form()] = None,
) -> LibraryUploadOut:
    if not source.strip() or not version.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source and version are required fields",
        )

    # Read bounded file content
    chunks_raw: list[bytes] = []
    total = 0
    while True:
        piece = await file.read(1024 * 1024)
        if not piece:
            break
        total += len(piece)
        if total > settings.UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"file size {total} exceeds maximum limit of {settings.UPLOAD_MAX_BYTES} bytes",
            )
        chunks_raw.append(piece)
    content = b"".join(chunks_raw)

    payload = await library_coverage.upload_library_document(
        content=content,
        filename=file.filename or "authority_doc.txt",
        source=source,
        version=version,
        edition=edition,
        pub_date=pub_date,
        doc_date=doc_date,
        hijri_date=hijri_date,
        language=language,
        coverage_note=coverage_note,
    )
    return LibraryUploadOut(**payload)
