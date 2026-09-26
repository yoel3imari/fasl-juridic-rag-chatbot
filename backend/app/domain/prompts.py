"""Prompt text for grounded answers: guardrails, the not-found reply, and prompt assembly.

Every user-facing prompt literal lives here and nowhere else. The citation
objects these prompts reference are built in `app.domain.citations`.
"""

from __future__ import annotations

from typing import Any

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
