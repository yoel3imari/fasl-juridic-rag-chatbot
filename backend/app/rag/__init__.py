"""Dual-domain RAG helpers: rerank, MMR, citation + prompt assembly."""

from app.rag.assemble import (
    AUTHORITY_CITATION_KEYS,
    GUARDRAILS,
    MATTER_CITATION_KEYS,
    PROVISIONAL_NOT_FOUND,
    assemble_prompt,
    authority_citation,
    build_citations,
    matter_citation,
)
from app.rag.rerank import get_ranker, mmr_select, rerank, reset_ranker

__all__ = [
    "AUTHORITY_CITATION_KEYS",
    "GUARDRAILS",
    "MATTER_CITATION_KEYS",
    "PROVISIONAL_NOT_FOUND",
    "assemble_prompt",
    "authority_citation",
    "build_citations",
    "get_ranker",
    "matter_citation",
    "mmr_select",
    "rerank",
    "reset_ranker",
]
