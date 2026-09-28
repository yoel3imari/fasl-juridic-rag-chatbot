"""Document-upload schemas (todo 41 extraction from app.api.v1.documents)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
