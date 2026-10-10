"""Regression tests for the 2011 Moroccan constitution (3 shortlist-ROOT files).

The constitution is filed at the shortlist ROOT, so it matched no subject
folder and no leading instrument: all three copies parsed to
``quarantined=True``/``category='unknown'``/``source=''`` and every chunk
landed with ``article_or_section=None``. The Arabic copy
(``دستور_2011.pdf``) additionally emits 48.2% Arabic PRESENTATION FORMS, which
broke every article-marker regex and made literal string search fail.

Covered here: filename provenance at the root, the bidi-reversed ``5 الفصل``
item marker, NFKC normalization at the extraction boundary, and article labels
on the real PDF. Tests that need the real corpus skip cleanly when it is absent.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

from app.config import settings
from app.domain.authority.law_patterns import (
    LAW_HEADER_RE,
    item_label,
    law_type_for,
)
from app.infrastructure.authority.catalog import (
    CONSTITUTION_CATEGORY,
    LAW_TYPE_CONSTITUTION,
    map_category,
    parse_filename,
)
from app.infrastructure.authority.extractor import extract_text, split_statute_structure

RIGHTS = "مادة الحقوق والحريات"
ROOT = ""  # a file at the shortlist ROOT has no folder at all

CONSTITUTION_FILES = [
    "دستور_2011.pdf",
    "الدستور-2011.pdf",
    "costitution-2011.pdf",
]
# The one Arabic copy with a usable (if mis-encoded) text layer.
ARABIC_PDF = "دستور_2011.pdf"
# Literal clause title that was UNFINDABLE before NFKC normalization.
OFFICIAL_LANGUAGES = "اللغات الرسمية"

_PRESENTATION_FORMS = re.compile("[\ufb50-\ufdff\ufe70-\ufeff]")


def _presentation_form_pct(text: str) -> float:
    if not text:
        return 0.0
    return 100.0 * len(_PRESENTATION_FORMS.findall(text)) / len(text)


def _raw_pdf_text(path: Path) -> str:
    """Un-normalized text layer, bypassing extract_text's NFKC boundary."""
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _corpus_pdf(name: str) -> Path:
    root = Path(settings.LIBRARY_SOURCE_DIR)
    path = root / name
    if not root.is_dir() or not path.is_file():
        pytest.skip(f"corpus file absent: {path}")
    return path


def _normalized_pdf_text(name: str = ARABIC_PDF) -> str:
    return "\n".join(text for _, text in extract_text(str(_corpus_pdf(name))))


# ---------------------------------------------------------------------------
# 1. Root-level provenance: no quarantine, no empty source, real category.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", CONSTITUTION_FILES)
def test_root_constitution_parses_without_quarantine(filename):
    parsed = parse_filename(filename, ROOT)
    assert parsed.quarantined is False
    assert parsed.source
    assert parsed.category != "unknown"
    assert parsed.law_type == LAW_TYPE_CONSTITUTION
    assert parsed.version
    assert parsed.edition in ("ar-general", "fr-translation")


def test_root_constitution_category_is_the_constitution_itself():
    # Not merely non-"unknown": a root file has no folder, so its law type
    # must supply the category.
    parsed = parse_filename(ARABIC_PDF, ROOT)
    assert parsed.category == CONSTITUTION_CATEGORY
    assert map_category(ROOT, LAW_TYPE_CONSTITUTION) == CONSTITUTION_CATEGORY
    # Backward compatible: the optional law_type arg is truly optional.
    assert map_category(ROOT) == "unknown"
    assert map_category(RIGHTS) == RIGHTS


# ---------------------------------------------------------------------------
# 2. A constitution inside a real subject folder keeps THAT folder's category.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", CONSTITUTION_FILES)
def test_constitution_in_a_real_folder_keeps_the_folder_category(filename):
    parsed = parse_filename(filename, RIGHTS)
    assert parsed.quarantined is False
    assert parsed.category == RIGHTS
    assert parsed.source == LAW_TYPE_CONSTITUTION


# ---------------------------------------------------------------------------
# 3. Bidi-reversed item markers: "5 الفصل" == "الفصل 5".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "الفصل 5",
        "5 الفصل",
        "5 الفصل•اللغات الرسمية او الوطنية",
    ],
)
def test_reversed_fasl_marker_labels_like_the_forward_form(line):
    assert item_label(line) == item_label("الفصل 5") == "Fasl 5"


@pytest.mark.parametrize(
    "line",
    [
        "الفصل الأول",  # unnumbered chapter, never an article item
        "تتمتع اللغات الرسمية بالدستور",  # prose, not a marker
        "الباب الأول",  # a different heading level
    ],
)
def test_reversed_marker_regex_does_not_over_match(line):
    assert item_label(line) is None


def test_reversed_fasl_markers_are_article_boundaries_when_chunking():
    pages = [
        (
            1,
            "2 الفصل•الاستفتاءات\nيجري استفتاء شعبي.\n"
            "5 الفصل•اللغات الرسمية او الوطنية\nالعربية هي اللغة الرسمية.\n",
        )
    ]
    chunks = split_statute_structure(pages, source_path="/x/دستور_2011.pdf")
    assert [c.article_or_section for c in chunks] == ["Fasl 2", "Fasl 5"]
    assert OFFICIAL_LANGUAGES in chunks[1].text


# ---------------------------------------------------------------------------
# 4. NFKC at the extraction boundary (regression on the real PDF).
# ---------------------------------------------------------------------------


def test_nfkc_makes_the_arabic_constitution_searchable():
    raw = _raw_pdf_text(_corpus_pdf(ARABIC_PDF))
    # The bug this locks: presentation forms made the clause unfindable.
    assert _presentation_form_pct(raw) > 40.0
    assert OFFICIAL_LANGUAGES not in raw

    normalized = _normalized_pdf_text()
    assert OFFICIAL_LANGUAGES in normalized
    assert _presentation_form_pct(normalized) < 0.01


def test_nfkc_normalization_is_idempotent_and_leaves_text_intact():
    raw = _raw_pdf_text(_corpus_pdf(ARABIC_PDF))
    normalized = _normalized_pdf_text()
    # normalize() is a pure function of the extracted text, so applying it to
    # already-normalized output must be a no-op.
    assert unicodedata.normalize("NFKC", normalized) == normalized
    # The letters themselves survive: NFKC only collapses compatibility forms.
    assert len(normalized) > 0.9 * len(raw)


# ---------------------------------------------------------------------------
# 5. The impossible assertion: real chunks carry article labels.
# ---------------------------------------------------------------------------


def test_real_constitution_chunks_carry_article_labels():
    path = _corpus_pdf(ARABIC_PDF)
    chunks = split_statute_structure(extract_text(str(path)), source_path=str(path))
    assert chunks, "expected chunks from the real constitution PDF"
    labelled = [c for c in chunks if c.article_or_section is not None]
    assert labelled, (
        f"all {len(chunks)} chunks had article_or_section=None: "
        "the bidi-reversed الفصل markers were not recognized"
    )
    assert any((c.article_or_section or "").startswith("Fasl ") for c in labelled)


def test_official_languages_clause_lands_in_a_labelled_chunk():
    path = _corpus_pdf(ARABIC_PDF)
    chunks = split_statute_structure(extract_text(str(path)), source_path=str(path))
    target = [c for c in chunks if OFFICIAL_LANGUAGES in c.text]
    assert target, f"{OFFICIAL_LANGUAGES!r} not found in any chunk"
    for c in target:
        assert c.article_or_section, "clause chunk lost its article label"
        assert c.token_count is not None


def test_constitution_header_form_is_recognized():
    # Both the definite and bare spellings map to the same law type.
    assert law_type_for("دستور") == law_type_for("الدستور") == "constitution"
    m = LAW_HEADER_RE.match("دستور المملكة المغربية الصادر عام 2011")
    assert m is not None
    assert law_type_for(m.group(1)) == "constitution"
    assert LAW_HEADER_RE.match("الدستور") is not None
