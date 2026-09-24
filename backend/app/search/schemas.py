"""Shared Qdrant payload schemas — single source of truth for both collections.

``app/ingestion/indexer.py`` imports the evidence side from here so the
matter_evidence point shape is defined once, never duplicated.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

EVIDENCE_COLLECTION: str = "matter_evidence"
AUTHORITY_COLLECTION: str = "legal_authorities"


class MatterPayload(TypedDict):
    """Exact payload for matter_evidence points (citation-resolving)."""

    matter_id: int
    document_id: int
    version_no: int
    doc_type: str
    page: int
    span: list[int]
    faithful_ref: str


class EvidencePoint(MatterPayload):
    """One indexed matter section: dense vector + search text + payload."""

    id: str
    vector: list[float]
    text: str


class AuthorityPayload(TypedDict):
    """Exact payload for legal_authorities points (version/disclosure).

    New keys (page/hierarchy/chunk_id/category/file_sha/hijri_date/
    coverage_note) are NotRequired so producers that do not yet supply them
    (pre-chunker catalog) keep type-checking; _authority_payload fills
    deterministic defaults at the boundary.
    """

    source: str
    version: str
    edition: str
    pub_date: str | None
    doc_date: str | None
    language: str
    article_or_section: str
    page: NotRequired[int]
    hierarchy: NotRequired[dict[str, Any]]
    chunk_id: NotRequired[str]
    category: NotRequired[str]
    file_sha: NotRequired[str]
    hijri_date: NotRequired[str | None]
    coverage_note: NotRequired[str | None]


class AuthorityPoint(AuthorityPayload):
    """One indexed authority chunk: dense vector + search text + payload."""

    id: str
    vector: list[float]
    text: str
