"""Value objects for the matter ingestion pipeline.

Faithful quotation text is always kept apart from normalized search text so
later citations resolve original spans, not search-normalized copies.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PageText:
    """Raw text of one page plus whether an OCR pass was required."""

    page_no: int
    text: str
    ocr_confidence: float | None = None
    ocr_engine: str | None = None
    needs_review: bool = False


@dataclass(frozen=True, slots=True)
class SectionResult:
    """One native section (or child segment) with page/span provenance."""

    section_id: str
    title: str
    page_start: int
    page_end: int
    span_start: int
    span_end: int
    faithful_text: str
    normalized_text: str
    parent_section_id: str | None = None
    ocr_confidence: float | None = None
    needs_review: bool = False


@dataclass(frozen=True, slots=True)
class UploadInput:
    """One upload descriptor: cohesive boundary input for ingest_upload."""

    matter_id: int
    original_name: str
    content: bytes
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of one upload: provenance first, indexing status reported honestly."""

    document_id: int
    matter_id: int
    version_no: int
    blob_ref: str
    doc_type: str
    status: str
    needs_review: bool
    sections: tuple[SectionResult, ...] = field(default_factory=tuple)
    indexed_count: int = 0
    error: str | None = None
