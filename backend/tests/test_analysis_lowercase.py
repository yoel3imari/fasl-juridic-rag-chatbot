"""Regression: pipeline-realistic lowercase normalized_text (task 8 live-QA fix).

The ingestion pipeline lowercases normalized_text, so party extraction must
be case-insensitive. Pure-engine test, no DB. SYNTHETIC test data only.
"""

from __future__ import annotations

from app.analysis.engine import build_analysis

LOWERED = (
    "termination letter. mr. karim bennani, your employment contract is terminated "
    "effective 2024-03-15. your notice period is 1 month. final compensation "
    "of 15000 mad will be paid. signed by societe atlas sarl."
)


def test_lowercase_normalized_text_still_yields_parties() -> None:
    """Lowercased evidence (as stored by the pipeline) still extracts parties."""
    content = build_analysis(
        [
            {
                "document_id": 1,
                "page": 1,
                "span": [0, len(LOWERED)],
                "title": "letter",
                "text": LOWERED,
            }
        ],
        ["unknown"],
        [],
    )
    names = [p["name"].lower() for p in content["parties"]]
    assert any("karim bennani" in n for n in names)
    assert any("atlas" in n for n in names)
    assert content["dates"], "lowercase evidence must still yield dates"
