"""Context assembly: single-domain citations + grounded FACT→RULE→APPLICATION→CONCLUSION prompt."""

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

GUARDRAILS: str = (
    "Answer ONLY from the provided matter + authority context below. "
    "Mark gaps explicitly where the context is silent. "
    'If the answer is not found in the context, reply with the provisional "I don\'t know" statement. '
    "Never guarantee legal outcomes. "
    "Never compute deadlines from dates."
)

PROVISIONAL_NOT_FOUND: str = (
    "I don't know — the provided matter and authority context contains "
    "no relevant passage for this question. This is provisional, not legal "
    "advice: outcomes are never guaranteed, and deadlines cannot be computed "
    "from dates alone."
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


def _matter_tag(item: dict[str, Any]) -> str:
    return (
        f"[matter: doc {item['document_id']} p.{item['page']} "
        f"\u00b6{item['span']}] ({item['doc_type']}, {item['faithful_ref']})"
    )


def _authority_tag(item: dict[str, Any]) -> str:
    return (
        f"[authority: {item['source']} {item['version']} "
        f"{item['article_or_section']} ({item['edition']})]"
    )


def assemble_prompt(
    query: str,
    matter_hits: list[dict[str, Any]],
    authority_hits: list[dict[str, Any]],
) -> str:
    """Grounded prompt with domain-tagged context and the exact guardrails."""
    if matter_hits:
        matter_block = "\n".join(
            f"{_matter_tag(h)}\n{str(h.get('text', ''))}" for h in matter_hits
        )
    else:
        matter_block = "(none)"
    if authority_hits:
        authority_block = "\n".join(
            f"{_authority_tag(h)}\n{str(h.get('text', ''))}" for h in authority_hits
        )
    else:
        authority_block = "(none)"
    return (
        "You are a Moroccan legal research assistant. Structure every answer as:\n"
        "FACT, then RULE, then APPLICATION, then CONCLUSION.\n\n"
        f"Guardrails (mandatory): {GUARDRAILS}\n\n"
        f"Question: {query}\n\n"
        f"[matter evidence]\n{matter_block}\n\n"
        f"[legal authorities]\n{authority_block}\n"
    )
