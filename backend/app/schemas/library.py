"""Library coverage/upload schemas (todo 41 extraction from app.api.v1.library)."""

from __future__ import annotations

from pydantic import BaseModel


class CoverageEntry(BaseModel):
    source: str
    version: str
    edition: str
    pub_date: str | None = None
    doc_date: str | None = None
    hijri_date: str | None = None
    language: str | None = None
    coverage_note: str | None = None
    chunks: int = 0
    status: str = "pending"


class LibraryUploadOut(BaseModel):
    status: str
    source: str
    version: str
    edition: str
    pub_date: str | None = None
    doc_date: str | None = None
    hijri_date: str | None = None
    language: str | None = None
    coverage_note: str | None = None
    chunks: int
    embedded: int
    message: str
