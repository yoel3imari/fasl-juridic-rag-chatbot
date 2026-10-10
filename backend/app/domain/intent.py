"""Intent gate: decide whether a message belongs to the Moroccan legal scope.

`matter` is no longer mandatory to chat: before any retrieval the LLM is
asked whether the message is a Moroccan-legal question at all. Legal
questions proceed down the normal dual-RAG path; everything else is met
with the legal-only clarification in `app.domain.prompts.OUT_OF_SCOPE_REPLY`.

Pure stdlib by contract (domain is the bottom layer: no fastapi, sqlalchemy,
pydantic, pydantic_ai or httpx). The refusal literal itself lives in
`app.domain.prompts` — that module owns every user-facing prompt literal.
"""

from __future__ import annotations

import re
import string

OUT_OF_SCOPE_TOOL: str = "out_of_scope"
IN_SCOPE: str = "IN_SCOPE"
OUT_OF_SCOPE: str = "OUT_OF_SCOPE"

# Appended to TOOL_GUIDANCE (app.services.chat) so the decision round knows
# all three parts of the gate: the out_of_scope envelope for non-legal
# messages, the greeting carve-out (without it every "bonjour" becomes the
# legal-only refusal), and the standing no-invention rule.
INTENT_RULE: str = (
    "Scope rule — first decide whether this message concerns Moroccan law: "
    "if the message is NOT about Moroccan law/legality (cooking, coding, "
    "sports, weather, general chit-chat about non-legal things), reply with "
    'EXACTLY the JSON envelope {"tool": "out_of_scope", "query": "<the '
    "user's original message>\"} and nothing else. "
    'GREETING CARVE-OUT: greetings and smalltalk ("bonjour", "salam", '
    '"hello", "thanks", "who are you") are NOT out of scope — answer them '
    "directly in 1-2 sentences mentioning FASL. "
    "Never invent articles, sections, document ids, or citations."
)

# Enforcement-round template: a separate, label-only call run when the
# decision round answered in prose, so a small model cannot bypass the gate
# by simply answering a non-legal question directly.
CLASSIFY_PROMPT: str = (
    "Classify the user message below for the FASL Moroccan legal assistant. "
    f"Reply with EXACTLY ONE of the two labels — {OUT_OF_SCOPE} or "
    f"{IN_SCOPE} — and nothing else.\n"
    f"{OUT_OF_SCOPE}: the message is not about Moroccan law or legality.\n"
    f"{IN_SCOPE}: the message IS about Moroccan law/legality, or is a "
    'greeting or smalltalk ("bonjour", "salam", "hello", "thanks", '
    '"who are you").\n'
    f"When in doubt, reply {IN_SCOPE}. Never invent articles, sections, or "
    "citations.\n"
    "User message:\n{query}"
)


def build_classify_prompt(query: str) -> str:
    """Render CLASSIFY_PROMPT for one user message."""
    return CLASSIFY_PROMPT.format(query=query)


def parse_intent_label(text: str) -> str:
    """Map classifier output onto OUT_OF_SCOPE / IN_SCOPE — FAIL OPEN.

    A legal question misrouted into the refusal blocks the product's core
    function, which is far worse than the occasional out-of-scope message
    slipping through. So ANY ambiguity, mixed labels, garbage or empty
    input resolves to IN_SCOPE; only an unambiguous OUT_OF_SCOPE label
    (with no IN_SCOPE label present) routes to the refusal.
    """
    lowered = (text or "").casefold()
    has_out = OUT_OF_SCOPE.casefold() in lowered
    has_in = IN_SCOPE.casefold() in lowered
    if has_out and not has_in:
        return OUT_OF_SCOPE
    return IN_SCOPE


def is_out_of_scope(text: str) -> bool:
    """True only when the classifier unambiguously said OUT_OF_SCOPE."""
    return parse_intent_label(text) == OUT_OF_SCOPE


# Canonical greetings / smalltalk that bypass the LLM intent gate entirely:
# a 1B local model cannot follow the carve-out reliably, and these carry no
# legal content to retrieve for. Full-message match only (after
# normalization), so "hello, I have a question about ..." never matches.
_GREETINGS_BY_LANG: dict[str, frozenset[str]] = {
    "en": frozenset(
        {
            "hello",
            "hi",
            "hey",
            "hello there",
            "thanks",
            "thank you",
            "who are you",
            "what are you",
            "good morning",
            "good afternoon",
            "good evening",
        }
    ),
    "fr": frozenset(
        {
            "bonjour",
            "salut",
            "merci",
            "bonsoir",
            "qui etes vous",
            "qui êtes vous",
        }
    ),
    "ar": frozenset(
        {
            "سلام",
            "مرحبا",
            "مرحباً",
            "شكرا",
            "شكراً",
            "السلام عليكم",
            "سلام عليكم",
            "من انت",
            "من أنت",
        }
    ),
    # Latin-script Darija / Maghreb smalltalk; mostly French-leaning usage,
    # so the reply language falls back to the detect_lang heuristic.
    "darija": frozenset(
        {
            "salam",
            "azul",
            "labas",
            "la bas",
            "cava",
            "ca va",
            "ça va",
        }
    ),
}

_GREETINGS: frozenset[str] = frozenset().union(*_GREETINGS_BY_LANG.values())

_NORMALIZE_RE: re.Pattern[str] = re.compile(f"[{re.escape(string.punctuation)}،؟؛«»‹›…—–]+")


def _normalize_greeting(text: str) -> str:
    raw = (text or "").casefold()
    raw = _NORMALIZE_RE.sub(" ", raw)
    return " ".join(raw.split())


def is_greeting(text: str) -> bool:
    """True for a bare greeting/smalltalk message — deterministic, no LLM.

    Normalizes (casefold, punctuation stripped, whitespace collapsed) then
    requires a full match against _GREETINGS plus a 40-char ceiling, so
    longer messages with real content always fall through to the LLM gate.
    """
    normalized = _normalize_greeting(text)
    if not normalized or len(normalized) > 40:
        return False
    return normalized in _GREETINGS


def greeting_lang(text: str) -> str | None:
    """Reply language for a greeting, or None when it carries no signal.

    French/Arabic-script greetings map directly; Latin-script Darija and
    English fall back to the detect_lang heuristic at the call site.
    """
    normalized = _normalize_greeting(text)
    for lang in ("fr", "ar", "en"):
        if normalized in _GREETINGS_BY_LANG[lang]:
            return lang
    return None
