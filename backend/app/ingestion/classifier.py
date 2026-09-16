"""Keyword classifier for matter uploads.

Scores weighted signals per doc_type; files below threshold stay ``unknown``
so they land in a reviewable state instead of a wrong category.
"""

from __future__ import annotations

from typing import Final

DocType: Final = str

_SIGNALS: Final[tuple[tuple[str, tuple[tuple[str, int], ...]], ...]] = (
    (
        "contract",
        (
            ("contrat de travail", 3),
            ("remuneration", 2),
            ("employeur", 2),
            ("salaire mensuel", 2),
            ("salarie", 1),
        ),
    ),
    (
        "letter",
        (
            ("licenciement", 3),
            ("notification", 2),
            ("par la presente", 2),
            ("madame", 1),
            ("monsieur", 1),
            ("objet:", 1),
        ),
    ),
    (
        "payslip",
        (
            ("bulletin de paie", 3),
            ("salaire brut", 2),
            ("cotisations", 2),
            ("payslip", 2),
        ),
    ),
    (
        "correspondence",
        (("courriel", 2), ("correspondance", 2), ("e-mail", 1), ("email", 1)),
    ),
    (
        "judgment",
        (
            ("tribunal", 2),
            ("jugement", 3),
            ("cour d'appel", 3),
            ("decision de justice", 2),
        ),
    ),
    ("statute_copy", (("code du travail", 3), ("dahir", 2), ("bulletin officiel", 2))),
)

_THRESHOLD: Final[int] = 2


def classify(text: str) -> str:
    """Return the best doc_type for ``text`` or ``"unknown"`` below threshold."""
    lowered = text.lower()
    best: str = "unknown"
    best_score = 0
    for doc_type, signals in _SIGNALS:
        score = sum(weight for needle, weight in signals if needle in lowered)
        if score > best_score:
            best, best_score = doc_type, score
    if best_score < _THRESHOLD:
        return "unknown"
    return best
