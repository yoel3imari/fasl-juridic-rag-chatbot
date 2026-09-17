"""Plain-language glossary loader (task 8).

Reads backend/data/glossary.json. The file holds DEFINITIONAL entries only
(term -> plain Arabic + French explanation); it carries no legal article
text, article numbers, or publication metadata, and must never be cited as
authority. Path resolves via the parents[] pattern: this file lives at
backend/app/analysis/, so backend/ is parents[2].
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


def glossary_path() -> Path:
    """Absolute path to backend/data/glossary.json."""
    return Path(__file__).parents[2] / "data" / "glossary.json"


@lru_cache(maxsize=1)
def load_glossary() -> list[dict[str, Any]]:
    """Load glossary entries; missing/unreadable file -> empty list (never crash)."""
    try:
        raw = json.loads(glossary_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = raw.get("entries", []) if isinstance(raw, dict) else []
    return [e for e in entries if isinstance(e, dict) and e.get("term")]


def match_terms(text: str) -> list[dict[str, Any]]:
    """Entries whose English term or Arabic term occurs in text (case-insensitive)."""
    lowered = text.lower()
    return [
        e
        for e in load_glossary()
        if str(e.get("term", "")).lower() in lowered
        or (e.get("ar") and str(e["ar"]) in text)
    ]
