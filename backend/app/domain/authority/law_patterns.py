"""Law-pattern vocabulary for Moroccan statutes and decisions.

Article/item boundary regexes (``المادة``/``Article`` and numbered ``الفصل``
items, in both logical and bidi-reversed order), book/part/chapter heading
patterns, ظهير/مرسوم/قرار/قانون/دستور header forms, and the explicit
8-folder -> authority-code mapping table.
"""

from __future__ import annotations

import re
from pathlib import Path

# Explicit mapping of the 8 shortlist folders to their authority code label.
# Lookup key is the parent-folder basename of the source file; anything
# unmapped falls back to LEGACY_DEFAULT_CODE (keeps pre-existing callers
# such as the seed fixture tests stable).
FOLDER_CODE_MAP: dict[str, str] = {
    "المادة الأسرية": "مدونة الأسرة",
    "المادة التجارية": "القانون التجاري",
    "المادة الجنائية": "القانون الجنائي",
    "المادة المدنية": "قانون الالتزامات والعقود",
    "مادة التأمين و التقاعد": "التأمين والتقاعد",
    "مادة الحقوق والحريات": "الحقوق والحريات",
    "مادة المعاملات الالكترونية": "المعاملات الإلكترونية",
    "مادة الوظيفة العمومية": "الوظيفة العمومية",
}
LEGACY_DEFAULT_CODE = "Code du Travail"

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

ARTICLE_RE = re.compile(r"^\s*(?:المادة|Article)\s+([0-9٠-٩]+)", re.IGNORECASE)
# A *numbered* الفصل (الفصل 1, الفصل 12) is an article-level item, not a chapter.
FASL_ITEM_RE = re.compile(r"^\s*الفصل\s+([0-9٠-٩]+)\b")
# دستور_2011.pdf writes item markers in VISUAL order: "5 الفصل", not "الفصل 5".
# NFKC cannot reorder text, so the reversed form needs its own boundary regex.
FASL_ITEM_REVERSED = re.compile(r"^\s*([0-9٠-٩]+)\s+الفصل\b")

HEADING_RES = [
    (
        re.compile(r"^\s*(?:الكتاب| Livre)\s+(.+)$", re.IGNORECASE),
        "book",
    ),
    (
        re.compile(r"^\s*(?:الباب| Partie|Titre)\s+(.+)$", re.IGNORECASE),
        "part",
    ),
    (
        re.compile(r"^\s*(?:الفصل| Chapitre)\s+(.+)$", re.IGNORECASE),
        "chapter",
    ),
]

# Instrument vocabulary: head word -> law_type slug ("" root files included).
LAW_TYPE_CONSTITUTION = "constitution"

# ظهير/مرسوم/قرار/قانون/دستور header forms (longest alternatives first, so a
# prefix never shadows the longer form it contains).
LAW_HEADER_RE = re.compile(
    r"^\s*(القانون التنظيمي|الدستور|دستور|ظهير شريف|مرسوم بقانون|"
    r"قرار مشترك|ظهير|مرسوم|قرار|القانون|قانون)\b(.*)$"
)
_LAW_TYPE_BY_HEAD = {
    "ظهير شريف": "dahir",
    "ظهير": "dahir",
    "مرسوم بقانون": "decree-law",
    "مرسوم": "decree",
    "قرار مشترك": "decision",
    "قرار": "decision",
    "القانون التنظيمي": "organic-law",
    "القانون": "law",
    "قانون": "law",
    "الدستور": LAW_TYPE_CONSTITUTION,
    "دستور": LAW_TYPE_CONSTITUTION,
}

# Wrapped header lines (e.g. "بتنفيذ القانون رقم ..." on the line after a
# ظهير/مرسوم opener): part of the law header, never chunk content.
HEADER_CONT_RE = re.compile(
    r"^\s*(بتنفيذ|بتطبيق|بتحديد|بشأن|المتعلق|بتتميم|بتغيير|القاضي|الصادر)\b"
)


def norm_digits(raw: str) -> str:
    return raw.strip().translate(_AR_DIGITS)


def law_type_for(head_word: str) -> str:
    return _LAW_TYPE_BY_HEAD[head_word]


def item_label(line: str) -> str | None:
    """Article-level boundary label, or None. Item regexes take priority."""
    m = ARTICLE_RE.match(line)
    if m:
        return f"Article {norm_digits(m.group(1))}"
    m = FASL_ITEM_RE.match(line) or FASL_ITEM_REVERSED.match(line)
    if m:
        return f"Fasl {norm_digits(m.group(1))}"
    return None


def code_for(source_path: str | None) -> str:
    if source_path:
        folder = Path(source_path).parent.name
        if folder in FOLDER_CODE_MAP:
            return FOLDER_CODE_MAP[folder]
    return LEGACY_DEFAULT_CODE
