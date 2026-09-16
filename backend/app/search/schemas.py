"""Shared Qdrant payload schemas — single source of truth for both collections.

``app/ingestion/indexer.py`` imports the evidence side from here so the
matter_evidence point shape is defined once, never duplicated.
"""

from __future__ import annotations

from typing import TypedDict

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
    """Exact payload for legal_authorities points (version/disclosure)."""

    source: str
    version: str
    edition: str
    pub_date: str | None
    doc_date: str | None
    language: str
    article_or_section: str


class AuthorityPoint(AuthorityPayload):
    """One indexed authority chunk: dense vector + search text + payload."""

    id: str
    vector: list[float]
    text: str
