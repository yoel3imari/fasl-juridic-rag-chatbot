# -*- coding: utf-8 -*-
"""Filename provenance parser for the Adala shortlist corpus.

Reads law type / law number / Hijri + Gregorian dates / language straight
from the Arabic filenames under ``/home/xozev/Documents/legal/shortlist``
and derives manifest-compatible ``(source, version, edition)`` provenance.

Contract (see plan todo 4):
- ``law_type`` is the LEADING instrument of the filename: القانون التنظيمي,
  ظهير شريف, مرسوم (incl. مرسوم ملكي), قرار, قانون. A mid-name mention
  (e.g. a مرسوم applying a قانون تنظيمي) never overrides the prefix.
- ``law_number`` is the first ``رقم <token>`` (e.g. ``1.02.298``).
- ``hijri_date`` is the matched Hijri phrase (e.g. ``25 من رجب 1423``);
  ``gregorian_date`` is the parenthesised date or a standalone
  ``بتاريخ/الصادر في <d month yyyy>`` phrase. Never invented: ``None``
  when the filename carries no date.
- ``language`` is ``ar`` unless the name is explicitly French-labeled;
  ``edition`` is ``fr-translation`` only then, else ``ar-general``.
- ``category`` maps the shortlist folder (incl. nested civil subfolders)
  to one of the 8 shortlist categories.
- ``source`` = law type when parsed, else the folder (fallback);
  ``version`` = law number when present, else the normalized filename stem.
- Unparseable names (no detectable law type) are QUARANTINED, never crash:
  ``quarantined=True`` with ``source=<folder>`` and
  ``version=<normalized filename>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.infrastructure.authority.manifest import EditionType

AR_GENERAL = EditionType.AR_GENERAL.value
FR_TRANSLATION = EditionType.FR_TRANSLATION.value

CIVIL = "المادة المدنية"
COMMERCE = "المادة التجارية"
ELECTRONIC = "مادة المعاملات الالكترونية"
FAMILY = "المادة الأسرية"
RIGHTS = "مادة الحقوق والحريات"
CIVIL_SERVICE = "مادة الوظيفة العمومية"
INSURANCE = "مادة التأمين و التقاعد"
CRIMINAL = "المادة الجنائية"

CATEGORIES: tuple[str, ...] = (
    CIVIL,
    COMMERCE,
    ELECTRONIC,
    FAMILY,
    RIGHTS,
    CIVIL_SERVICE,
    INSURANCE,
    CRIMINAL,
)

# Nested civil-law subfolders map to the parent civil category.
CIVIL_SUBFOLDERS: tuple[str, ...] = (
    "المادة العقارية",
    "المادة الكرائية",
    "المادة المدنية",
)

LAW_TYPE_DAHIR = "ظهير شريف"
LAW_TYPE_DECREE = "مرسوم"
LAW_TYPE_ARRETE = "قرار"
LAW_TYPE_LAW = "قانون"
LAW_TYPE_ORGANIC = "القانون التنظيمي"

_HIJRI_MONTHS = (
    "محرم",
    "صفر",
    "ربيع الأول",
    "ربيع الآخر",
    "جمادى الأولى",
    "جمادى الثانية",
    "جمادى الآخرة",
    "رجب",
    "شعبان",
    "رمضان",
    "شوال",
    "ذي القعدة",
    "ذو القعدة",
    "ذى القعدة",
    "ذي الحجة",
    "ذو الحجة",
)
_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_MAX_STEM_LEN = 120


def _normalize_arabic(text: str) -> str:
    """Normalize orthographic variants for detection only (never for output).

    All mappings are 1:1 character replacements, so regex spans stay valid
    for slicing the original text.
    """
    return (
        text.translate(_ARABIC_INDIC_DIGITS)
        .replace("ی", "ي")  # Persian yeh -> Arabic yeh
        .replace("ک", "ك")  # Persian kaf -> Arabic kaf
        .replace("ى", "ي")  # alef maqsura -> yeh (جمادى/جمادي)
    )


_HIJRI_MONTH_ALT = "|".join(
    sorted((_normalize_arabic(m) for m in _HIJRI_MONTHS), key=len, reverse=True)
)
_HIJRI_RE = re.compile(
    rf"(فاتح|\d{{1,2}})\s+(?:من\s+)?({_HIJRI_MONTH_ALT})\s+(\d{{3,4}})"
)
_LAW_NUMBER_RE = re.compile(r"رقم\s*:?\s*([A-Za-z]?\s*[_\-]?\s*\d[\d.\-–—/]*)")
_PAREN_DATE_RE = re.compile(r"\(([^)]*\d{3,4}[^)]*)\)")
_BARE_GREG_RE = re.compile(
    r"(?:بتاريخ|الصادر في|صادر في|صادر بتاريخ|المؤرخ بتاريخ)\s*(\d{1,2}\s+\S+\s+\d{4})"
)
_FR_MARKERS_RE = re.compile(
    r"ترجمة فرنسية|بالفرنسية|النسخة الفرنسية|version fran|traduction|\(fr\)|\bfr\b",
    re.IGNORECASE,
)
# Leading introducers inside a parenthesised date carry no date value.
_PAREN_INTRO_RE = re.compile(r"^(?:صادر\s+)?(?:بتاريخ\s+)?")
_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_MAX_STEM_LEN = 120


def normalize_stem(filename: str, max_len: int | None = _MAX_STEM_LEN) -> str:
    """Deterministic normalized stem: no extension, collapsed spaces.

    Detection runs on the full-length stem; only the version fallback is
    bounded, so truncation can never slice a date off.
    """
    stem = filename.strip()
    if stem.lower().endswith(".pdf"):
        stem = stem[: -len(".pdf")]
    stem = re.sub(r"\s+", " ", stem.strip())
    if max_len is not None and len(stem) > max_len:
        stem = stem[:max_len].rstrip()
    return stem


def map_category(folder: str) -> str:
    """Map a shortlist folder (bare name or nested path) to its category."""
    norm = _normalize_arabic(folder)
    for sub in CIVIL_SUBFOLDERS:
        if sub in folder or _normalize_arabic(sub) in norm:
            return CIVIL
    for category in CATEGORIES:
        if category in folder or _normalize_arabic(category) in norm:
            return category
    return folder.strip() or "unknown"


def detect_law_type(stem: str) -> str | None:
    """Leading instrument wins; anywhere-match is the fallback."""
    norm = _normalize_arabic(stem.strip())
    prefixes = (
        (("القانون التنظيمي",), LAW_TYPE_ORGANIC),
        (("ظهير شريف", "ظهير شريف"), LAW_TYPE_DAHIR),
        (("مرسوم",), LAW_TYPE_DECREE),
        (("قرار",), LAW_TYPE_ARRETE),
        (("القانون", "قانون"), LAW_TYPE_LAW),
    )
    for variants, law_type in prefixes:
        if any(norm.startswith(v) for v in variants):
            return law_type
    for variants, law_type in prefixes:
        if any(v in norm for v in variants):
            return law_type
    return None


def detect_law_number(stem: str) -> str | None:
    match = _LAW_NUMBER_RE.search(stem)
    if not match:
        return None
    token = re.sub(r"\s+", "", match.group(1)).strip(".-–—/")
    return token or None


def detect_hijri_date(stem: str) -> str | None:
    match = _HIJRI_RE.search(_normalize_arabic(stem))
    if not match:
        return None
    # Re-slice the ORIGINAL text so output keeps the filename's spelling.
    start, end = match.span()
    return stem[start:end].strip()


def detect_gregorian_date(stem: str) -> str | None:
    paren = _PAREN_DATE_RE.search(stem)
    if paren:
        return _PAREN_INTRO_RE.sub("", paren.group(1).strip()) or None
    bare = _BARE_GREG_RE.search(stem)
    if bare:
        return bare.group(1).strip()
    return None


def detect_language(stem: str) -> str:
    return "fr" if _FR_MARKERS_RE.search(stem) else "ar"


@dataclass(frozen=True)
class ParsedFile:
    filename: str
    folder: str
    category: str
    law_type: str | None
    law_number: str | None
    hijri_date: str | None
    gregorian_date: str | None
    language: str
    source: str
    version: str
    edition: str
    quarantined: bool
    quarantine_reason: str | None = None

def parse_filename(filename: str, folder: str) -> ParsedFile:
    """Parse provenance from a shortlist filename + its shortlist folder.

    Never raises on garbage/empty/very-long input: such names quarantine.
    """
    raw = filename or ""
    stem = normalize_stem(raw, max_len=None)
    short_stem = normalize_stem(raw)
    category = map_category(folder)
    law_type = detect_law_type(stem) if stem else None
    law_number = detect_law_number(stem) if stem else None
    hijri_date = detect_hijri_date(stem) if stem else None
    gregorian_date = detect_gregorian_date(stem) if stem else None
    language = detect_language(stem)
    edition = FR_TRANSLATION if language == "fr" else AR_GENERAL

    if law_type is None:
        reason = "empty filename" if not stem else "no detectable law type"
        return ParsedFile(
            filename=raw,
            folder=folder,
            category=category,
            law_type=None,
            law_number=law_number,
            hijri_date=hijri_date,
            gregorian_date=gregorian_date,
            language=language,
            source=folder,
            version=short_stem or "unnamed",
            edition=edition,
            quarantined=True,
            quarantine_reason=reason,
        )
    return ParsedFile(
        filename=raw,
        folder=folder,
        category=category,
        law_type=law_type,
        law_number=law_number,
        hijri_date=hijri_date,
        gregorian_date=gregorian_date,
        language=language,
        source=law_type,
        version=law_number or short_stem,
        edition=edition,
        quarantined=False,
    )
