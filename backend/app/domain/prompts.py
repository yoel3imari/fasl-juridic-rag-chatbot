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

OUT_OF_SCOPE_REPLY: str = (
    "I'm FASL, a legal assistant for the Moroccan legal system, so I can only "
    "help with questions about Moroccan law. Go ahead and ask me a legal "
    "question about your case, a statute, or any matter of Moroccan law."
)

# Localized variants of the two static replies (reply language picked by
# app.domain.language.detect_lang). The English entries ARE the legacy
# constants, so the two stay byte-identical by construction.
PROVISIONAL_NOT_FOUND_BY_LANG: dict[str, str] = {
    "en": PROVISIONAL_NOT_FOUND,
    "ar": (
        "لا أعلم — لا يتضمن سياق القضية والمرجعية القانونية المقدَّم أي فقرة "
        "ذات صلة بهذا السؤال. هذا جواب مبدئي وليس استشارة قانونية: فالنتائج "
        "غير مضمونة، ولا يمكن احتساب آجال المدة من التواريخ وحدها."
    ),
    "fr": (
        "Je ne sais pas — le contexte fourni (pièce du dossier et références "
        "juridiques) ne contient aucun passage pertinent pour cette question. "
        "Ceci est provisoire et ne constitue pas un conseil juridique : les "
        "résultats ne sont jamais garantis et les délais ne peuvent être "
        "calculés à partir des seules dates."
    ),
}

OUT_OF_SCOPE_REPLY_BY_LANG: dict[str, str] = {
    "en": OUT_OF_SCOPE_REPLY,
    "ar": (
        "أنا FASL، مساعد قانوني مخصص للنظام القانوني المغربي، لذا لا يمكنني "
        "المساعدة إلا في الأسئلة المتعلقة بالقانون المغربي. تفضّل بطرح سؤال "
        "قانوني حول قضيتك أو نص قانوني أو أي مسألة من مسائل القانون المغربي."
    ),
    "fr": (
        "Je suis FASL, un assistant juridique dédié au système juridique "
        "marocain ; je ne peux donc répondre qu'aux questions de droit "
        "marocain. Posez-moi une question juridique relative à votre dossier, "
        "à un texte de loi ou à toute autre matière de droit marocain."
    ),
}


def provisional_not_found(lang: str = "en") -> str:
    """Localized provisional "I don't know" reply; unknown lang → English."""
    return PROVISIONAL_NOT_FOUND_BY_LANG.get(lang.lower(), PROVISIONAL_NOT_FOUND)


def out_of_scope_reply(lang: str = "en") -> str:
    """Localized legal-only refusal; unknown lang → English."""
    return OUT_OF_SCOPE_REPLY_BY_LANG.get(lang.lower(), OUT_OF_SCOPE_REPLY)


GREETING_REPLY: str = (
    "Hello! I'm FASL, your legal assistant for the Moroccan legal system. "
    "Ask me a question about your case, a statute, or any matter of Moroccan law."
)

GREETING_REPLY_BY_LANG: dict[str, str] = {
    "en": GREETING_REPLY,
    "ar": (
        "مرحباً! أنا FASL، مساعدك القانوني للنظام القانوني المغربي. "
        "اطرح عليّ سؤالاً حول قضيتك أو نص قانوني أو أي مسألة من مسائل القانون المغربي."
    ),
    "fr": (
        "Bonjour ! Je suis FASL, votre assistant juridique pour le système "
        "juridique marocain. Posez-moi une question sur votre dossier, un "
        "texte de loi ou toute matière de droit marocain."
    ),
}


def greeting_reply(lang: str = "en") -> str:
    """Localized greeting reply; unknown lang → English."""
    return GREETING_REPLY_BY_LANG.get(lang.lower(), GREETING_REPLY)


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
        matter_block = "\n".join(f"{_matter_tag(h)}\n{str(h.get('text', ''))}" for h in matter_hits)
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
        "Respond in the SAME language as the user's question — an English question "
        "gets an English answer, an Arabic question an Arabic answer, a French "
        "question a French answer. Keep the FACT/RULE/APPLICATION/CONCLUSION "
        "headings in that same language.\n"
        "FACT, then RULE, then APPLICATION, then CONCLUSION.\n\n"
        f"Guardrails (mandatory): {GUARDRAILS}\n\n"
        f"Question: {query}\n\n"
        f"[matter evidence]\n{matter_block}\n\n"
        f"[legal authorities]\n{authority_block}\n"
    )
