"""Reply-language detection for static chat replies (heuristic only).

Picks which of the three supported reply languages — Arabic ("ar"),
French ("fr"), English ("en") — a canned reply should use for a given user
message. The heuristic is deterministic and locale-independent:

1. Count Arabic-script characters (Unicode ranges U+0600–U+06FF,
   U+0750–U+077F, U+08A0–U+08FF, U+FB50–U+FDFF, U+FE70–U+FEFF) against
   Latin letters (A–Z, a–z plus Latin-1 accented letters). Majority rule:
   "ar" iff arabic_count > latin_count (which implies arabic_count > 0).
2. Otherwise "fr" if a French marker is present: an accented character
   (é è ê à ç ù î ô â ë ï) OR a standalone French function word matched
   case-insensitively on whole tokens (le, la, les, des, une, est, pour,
   dans, avec, votre, vous, dit). Unaccented particles like "de"/"du"/"au"
   are deliberately NOT word markers — they collide with other languages;
   accents are the reliable French signal. Token matching avoids substring
   false positives (e.g. "est" inside "forest").
3. Otherwise "en" — also the result for empty or whitespace-only input.

This heuristic ONLY picks the wording language of a static reply; it is
never security-relevant behaviour — a misdetection changes the language of
a canned answer and nothing else. Pure stdlib by contract (domain is the
bottom layer: no fastapi, sqlalchemy, pydantic, pydantic_ai or httpx).
"""

from __future__ import annotations

import re
import string

_ARABIC_RANGES: tuple[tuple[int, int], ...] = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

# Latin letters: ASCII A–Z/a–z plus Latin-1 Supplement letters (accented
# vowels, ç, ñ, þ, ÿ, …) so accented French counts toward the Latin side.
_LATIN_LETTERS: frozenset[str] = frozenset(
    string.ascii_letters + "àáâãäåçèéêëìíîïðñòóôõöùúûüýþÿÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖÙÚÛÜÝÞ"
)

# Accented characters that reliably mark French.
_FRENCH_ACCENTS: frozenset[str] = frozenset("éèêàçùîôâëï")

# Standalone French function words (token-based, case-insensitive).
_FRENCH_WORDS: frozenset[str] = frozenset(
    {"le", "la", "les", "des", "une", "est", "pour", "dans", "avec", "votre", "vous", "dit"}
)

# Splits on everything that is not an ASCII letter, so markers only match
# as standalone tokens ("est" never fires inside "forest").
_TOKEN_SPLIT_RE: re.Pattern[str] = re.compile(r"[^a-zA-Z]+")


def _is_arabic(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _ARABIC_RANGES)


def detect_lang(text: str) -> str:
    """Return "ar", "fr", or "en" for ``text`` — majority-rule heuristic.

    See the module docstring for the exact rules. Deterministic, locale
    independent, pure stdlib. Picks only the reply language; never used
    for security-relevant behaviour. Empty/whitespace-only input → "en".
    """
    arabic_count = 0
    latin_count = 0
    has_french_accent = False
    for ch in text:
        if _is_arabic(ch):
            arabic_count += 1
        elif ch in _LATIN_LETTERS:
            latin_count += 1
            if ch in _FRENCH_ACCENTS:
                has_french_accent = True
    if arabic_count > latin_count:
        return "ar"
    if has_french_accent:
        return "fr"
    tokens = {t.lower() for t in _TOKEN_SPLIT_RE.split(text) if t}
    if tokens & _FRENCH_WORDS:
        return "fr"
    return "en"
