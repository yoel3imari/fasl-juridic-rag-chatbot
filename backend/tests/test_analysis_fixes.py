"""Failing-first regression tests for the three coordinator-review defects (task 8).

1. source_statements must store the FULL section text (no silent [:500] cut).
2. The 'Missing notice date' gap must require a notice-context date; an
   unrelated effective date must NOT clear it.
3. Dates from needs_review=true sections stay in dates[] but are NOT
   promoted to verified_facts.

Pure-engine tests (no DB). SYNTHETIC test data only.
"""

from __future__ import annotations

from app.analysis.engine import build_analysis

LONG_TEXT = (
    "Termination letter. Mr. Karim Bennani, your employment contract is terminated "
    "effective 2024-03-15. Your notice period is 1 month. Final compensation "
    "of 15000 MAD will be paid. Signed by Societe Atlas SARL. "
    "Additional context paragraph one with filler words to push past five hundred. "
    "Additional context paragraph two with filler words to push past five hundred. "
    "Additional context paragraph three with filler words to push past five hundred. "
    "Additional context paragraph four with filler words to push past five hundred. "
    "Additional context paragraph five with filler words to push past five hundred. "
    "Additional context paragraph six with filler words to push past five hundred. "
)

FULL_TYPES = ["contract", "letter", "correspondence", "payslip", "payslip", "payslip"]


def _sec(text: str, doc_id: int = 1, page: int = 1, needs_review: bool = False) -> dict:
    return {
        "document_id": doc_id,
        "page": page,
        "span": [0, len(text)],
        "title": "Section 1",
        "text": text,
        "needs_review": needs_review,
    }


def test_source_statements_store_full_text() -> None:
    """Sections longer than 500 chars must not be silently truncated."""
    assert len(LONG_TEXT) > 500
    content = build_analysis([_sec(LONG_TEXT)], ["letter"], [])
    sources = content["knowledge"]["source_statements"]
    assert len(sources) == 1
    assert sources[0]["text"] == LONG_TEXT


def test_notice_gap_requires_notice_context_date() -> None:
    """An unrelated effective date must NOT clear the missing-notice-date gap."""
    text = (
        "The agreement takes effective 2024-01-10. Payment terms follow "
        "in the next sections of this file."
    )
    content = build_analysis([_sec(text)], FULL_TYPES, [])
    joined = " ".join(content["gaps"]).lower()
    assert "notice date" in joined


def test_notice_context_date_clears_notice_gap() -> None:
    """A date in notice/péavis context satisfies the gap (no false positive)."""
    text = "Your notice period starts 2024-03-15. The contract ends after that."
    content = build_analysis([_sec(text)], FULL_TYPES, [])
    joined = " ".join(content["gaps"]).lower()
    assert "notice date" not in joined


def test_needs_review_dates_not_promoted_to_facts() -> None:
    """Low-confidence OCR dates stay in dates[] but never become verified facts."""
    text = "Termination effective 2024-03-15. Notice period is 1 month."
    content = build_analysis([_sec(text, needs_review=True)], ["letter"], [])
    assert content["dates"], "date must still be listed with its span ref"
    facts = " ".join(str(f) for f in content["knowledge"]["verified_facts"])
    assert "2024-03-15" not in facts
    sources = content["knowledge"]["source_statements"]
    assert len(sources) == 1, "review-flagged sections are still cited as sources"


def test_clean_section_dates_still_verified() -> None:
    """Control: dates from clean sections keep their verified-fact entry."""
    text = "Termination effective 2024-03-15. Notice period is 1 month."
    content = build_analysis([_sec(text, needs_review=False)], ["letter"], [])
    facts = " ".join(str(f) for f in content["knowledge"]["verified_facts"])
    assert "2024-03-15" in facts
