"""Reply-language follow-through: detection, localized static replies, prompt instruction.

Given a user message in English/French/Arabic, the two canned replies and
both prompt paths must steer (or select) the answer language accordingly.
"""

from __future__ import annotations

from app.domain.language import detect_lang
from app.domain.prompts import (
    OUT_OF_SCOPE_REPLY,
    OUT_OF_SCOPE_REPLY_BY_LANG,
    PROVISIONAL_NOT_FOUND,
    PROVISIONAL_NOT_FOUND_BY_LANG,
    assemble_prompt,
    out_of_scope_reply,
    provisional_not_found,
)
from app.services.chat import TOOL_GUIDANCE

_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)


def _has_arabic_script(text: str) -> bool:
    return any(any(lo <= ord(ch) <= hi for lo, hi in _ARABIC_RANGES) for ch in text)


# --------------------------------------------------------------------------- #
# detect_lang — majority-rule heuristic
# --------------------------------------------------------------------------- #
def test_detect_lang_arabic_question_returns_ar() -> None:
    assert detect_lang("ما هي أقدمية الدعوى أمام القضاء المغربي؟") == "ar"


def test_detect_lang_english_question_returns_en() -> None:
    assert detect_lang("What is the statute of limitations under Moroccan law?") == "en"


def test_detect_lang_french_with_accents_returns_fr() -> None:
    assert detect_lang("Quel est le délai pour introduire un appel ?") == "fr"


def test_detect_lang_empty_and_whitespace_return_en() -> None:
    assert detect_lang("") == "en"
    assert detect_lang("   \n\t") == "en"


def test_detect_lang_majority_rule_ignores_few_arabic_chars() -> None:
    text = (
        "This is a fairly long English sentence that contains only three "
        "Arabic letters right here: ءبج, and then carries on in English."
    )
    assert detect_lang(text) == "en"


def test_detect_lang_reclamation_returns_fr() -> None:
    assert detect_lang("Réclamation") == "fr"


# --------------------------------------------------------------------------- #
# Localized static-reply accessors
# --------------------------------------------------------------------------- #
def test_accessors_default_to_legacy_english_constants() -> None:
    assert provisional_not_found() == PROVISIONAL_NOT_FOUND
    assert out_of_scope_reply() == OUT_OF_SCOPE_REPLY


def test_accessors_return_own_variant_per_language() -> None:
    for lang in ("ar", "fr", "en"):
        assert provisional_not_found(lang) == PROVISIONAL_NOT_FOUND_BY_LANG[lang]
        assert out_of_scope_reply(lang) == OUT_OF_SCOPE_REPLY_BY_LANG[lang]


def test_accessors_unknown_lang_falls_back_to_english() -> None:
    assert provisional_not_found("de") == PROVISIONAL_NOT_FOUND
    assert out_of_scope_reply("de") == OUT_OF_SCOPE_REPLY


def test_accessors_are_case_insensitive() -> None:
    assert provisional_not_found("AR") == provisional_not_found("ar")
    assert provisional_not_found("Fr") == provisional_not_found("fr")
    assert out_of_scope_reply("AR") == out_of_scope_reply("ar")
    assert out_of_scope_reply("Fr") == out_of_scope_reply("fr")


def test_variants_differ_across_languages_and_are_non_empty() -> None:
    for accessor in (provisional_not_found, out_of_scope_reply):
        en, ar, fr = accessor("en"), accessor("ar"), accessor("fr")
        assert en and ar and fr
        assert ar != en
        assert fr != en
        assert ar != fr


def test_arabic_variants_contain_arabic_script() -> None:
    assert _has_arabic_script(provisional_not_found("ar"))
    assert _has_arabic_script(out_of_scope_reply("ar"))


# --------------------------------------------------------------------------- #
# Prompt paths carry the language instruction
# --------------------------------------------------------------------------- #
def test_assemble_prompt_contains_language_instruction() -> None:
    prompt = assemble_prompt("...", [], [])
    assert "SAME language" in prompt
    assert "Respond in the SAME language as the user's question" in prompt


def test_tool_guidance_contains_language_instruction() -> None:
    assert "same language as the user's message" in TOOL_GUIDANCE
