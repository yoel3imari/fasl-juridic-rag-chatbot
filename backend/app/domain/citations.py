"""Single-domain citation objects: exact key sets and per-domain builders.

A citation carries ONE domain's fields and never a mix of both, so the
frontend can render `domain` as a reliable discriminator. The key-set
constants are the machine-checkable statement of that rule; the prompt text
that consumes them lives in `app.domain.prompts`.
"""

from __future__ import annotations

from typing import Any

# Exact key sets: a citation object carries ONE domain's fields, never mixed.
MATTER_CITATION_KEYS: frozenset[str] = frozenset(
    {"domain", "document_id", "version_no", "doc_type", "page", "span", "faithful_ref"}
)
AUTHORITY_CITATION_KEYS: frozenset[str] = frozenset(
    {
        "domain",
        "source",
        "version",
        "edition",
        "pub_date",
        "doc_date",
        "language",
        "article_or_section",
    }
)


def matter_citation(item: dict[str, Any]) -> dict[str, Any]:
    """Single-domain matter citation: document_id + page + span, no authority fields."""
    return {
        "domain": "matter",
        "document_id": item["document_id"],
        "version_no": item["version_no"],
        "doc_type": item["doc_type"],
        "page": item["page"],
        "span": list(item["span"]),
        "faithful_ref": item["faithful_ref"],
    }


def authority_citation(item: dict[str, Any]) -> dict[str, Any]:
    """Single-domain authority citation: source + version + article + edition."""
    return {
        "domain": "authority",
        "source": item["source"],
        "version": item["version"],
        "edition": item["edition"],
        "pub_date": item.get("pub_date"),
        "doc_date": item.get("doc_date"),
        "language": item.get("language", ""),
        "article_or_section": item["article_or_section"],
    }


def build_citations(
    matter_hits: list[dict[str, Any]], authority_hits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ordered citation list: matter claims first, then authority claims."""
    return [matter_citation(h) for h in matter_hits] + [
        authority_citation(h) for h in authority_hits
    ]
