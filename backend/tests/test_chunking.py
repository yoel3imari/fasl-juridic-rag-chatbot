"""Golden tests for the page/hierarchy-correct chunker (plan todo 11).

Covers >=1 fixture per law type (ظهير/مرسوم/قرار) plus a الفصل-numbered
item, asserting per-position hierarchy, real page numbers, token counts
<= 512 INCLUDING special tokens measured with the REAL Granite tokenizer
(never a chars/4 proxy), zero served-truncation, and continued_from
linkage where every continuation keeps its article reference.
"""

from __future__ import annotations

import pytest

from app.library.extractor import (
    FOLDER_CODE_MAP,
    MAX_GRANITE_TOKENS,
    count_granite_tokens,
    split_statute_structure,
)

DAHIR_PAGES = [
    (
        1,
        "ظهير شريف رقم 1.11.151 صادر في 16 من رمضان 1432 (17 أغسطس 2011)\n"
        "بتنفيذ القانون رقم 36.21 المتعلق بالحالة المدنية\n"
        "الكتاب الأول: أحكام عامة\n"
        "المادة 1 - نطاق التطبيق\n"
        "تسري أحكام هذا القانون على جميع المغاربة.\n",
    ),
    (
        2,
        "الباب الثاني: التسجيل\n"
        "المادة 2 - واجب التصريح\n"
        "يجب التصريح بالولادات خلال ثلاثين يوما.\n",
    ),
]

DECREE_PAGES = [
    (
        3,
        "مرسوم رقم 2.22.690 بتطبيق القانون رقم 36.21 المتعلق بالحالة المدنية\n"
        "المادة 1 - كيفيات التطبيق\n"
        "تحدد بنص تنظيمي كيفيات تطبيق أحكام القانون المذكور.\n",
    ),
]

DECISION_PAGES = [
    (
        5,
        "قرار لوزير العدل بتحديد استمارات التصريح بالتقييد في السجل التجاري\n"
        "المادة 1 - الاستمارات\n"
        "تحدد استمارات التصريح بقرار مشترك.\n"
        "المادة 2 - النشر\n"
        "ينشر هذا القرار بالجريدة الرسمية.\n",
    ),
]

FASL_PAGES = [
    (
        1,
        "الكتاب الأول: الالتزامات\n"
        "الفصل 1 - مبدأ سلطان الإرادة\n"
        "تنشأ الالتزامات عن الاتفاقات والتصريحات.\n"
        "الفصل 2 - الأهلية\n"
        "يشترط في الملتزم أن يكون متمتعا بالأهلية.\n",
    ),
]


def _all_chunks():
    out = []
    out += split_statute_structure(DAHIR_PAGES, source_path="/x/المادة المدنية/doc.pdf")
    out += split_statute_structure(
        DECREE_PAGES, source_path="/x/المادة المدنية/doc.pdf"
    )
    out += split_statute_structure(
        DECISION_PAGES, source_path="/x/مادة المعاملات الالكترونية/doc.pdf"
    )
    out += split_statute_structure(FASL_PAGES, source_path="/x/المادة المدنية/doc.pdf")
    return out


def test_dahir_fixture_per_position_hierarchy_and_pages():
    chunks = split_statute_structure(
        DAHIR_PAGES, source_path="/x/المادة المدنية/doc.pdf"
    )
    assert len(chunks) == 2
    assert chunks[0].article_or_section == "Article 1"
    assert chunks[1].article_or_section == "Article 2"
    # Real page numbers, never None.
    assert chunks[0].page == 1
    assert chunks[1].page == 2
    # Folder->code mapping, not the hardcoded legacy default.
    assert chunks[0].hierarchy["code"] == FOLDER_CODE_MAP["المادة المدنية"]
    # Law header detected with type.
    assert chunks[0].hierarchy.get("law_type") == "dahir"
    assert "ظهير" in (chunks[0].hierarchy.get("law") or "")
    # Per-position hierarchy CHANGES across positions (stale-state guard).
    assert chunks[0].hierarchy != chunks[1].hierarchy
    assert "الكتاب" in (chunks[0].hierarchy.get("book") or "")
    assert "الباب" in (chunks[1].hierarchy.get("part") or "")


def test_decree_and_decision_fixtures_have_law_types():
    dec = split_statute_structure(DECREE_PAGES, source_path="/x/المادة المدنية/doc.pdf")
    assert len(dec) == 1 and dec[0].page == 3
    assert dec[0].hierarchy.get("law_type") == "decree"
    assert dec[0].article_or_section == "Article 1"

    arre = split_statute_structure(
        DECISION_PAGES, source_path="/x/مادة المعاملات الالكترونية/doc.pdf"
    )
    assert len(arre) == 2
    assert [c.page for c in arre] == [5, 5]
    assert arre[0].hierarchy.get("law_type") == "decision"
    assert arre[0].hierarchy["code"] == FOLDER_CODE_MAP["مادة المعاملات الالكترونية"]


def test_fasl_numbered_items_are_boundaries_not_chapter():
    chunks = split_statute_structure(
        FASL_PAGES, source_path="/x/المادة المدنية/doc.pdf"
    )
    assert len(chunks) == 2
    assert chunks[0].article_or_section == "Fasl 1"
    assert chunks[1].article_or_section == "Fasl 2"
    assert "الفصل 1" in chunks[0].text
    # The book heading still lands in hierarchy.
    assert "الكتاب" in (chunks[0].hierarchy.get("book") or "")


def test_token_cap_measured_with_real_granite_tokenizer():
    chunks = _all_chunks()
    assert chunks, "expected chunks from golden fixtures"
    for c in chunks:
        n = count_granite_tokens(c.text)
        assert n <= MAX_GRANITE_TOKENS, f"{c.article_or_section}: {n} tokens"
        # Served length (post-tokenization incl. special tokens) == measured.
        assert c.token_count == n


def test_zero_served_truncation_across_all_fixtures():
    # If any chunk exceeded the served window the server would truncate;
    # the cap assertion IS the zero-truncation proof, re-checked here
    # independently of the stored token_count field.
    for c in _all_chunks():
        assert count_granite_tokens(c.text) <= 512


def test_overlong_article_splits_with_overlap_and_linkage():
    body = "يترتب على الإخلال بهذا الالتزام قيام المسؤولية المدنية بكامل آثارها القانونية. "
    pages = [
        (
            1,
            "ظهير شريف بتنفيذ القانون المتعلق بالالتزامات\nالمادة 7 - المسؤولية\n"
            + body * 200,
        ),
    ]
    chunks = split_statute_structure(pages, source_path="/x/المادة المدنية/doc.pdf")
    assert len(chunks) > 1, "over-long article must split, never truncate"
    for c in chunks:
        assert count_granite_tokens(c.text) <= MAX_GRANITE_TOKENS
        # Never drop the article reference on a continuation.
        assert c.article_or_section and c.article_or_section.startswith("Article 7")
    assert chunks[0].continued_from is None
    for prev, cur in zip(chunks, chunks[1:]):
        assert cur.continued_from == prev.article_or_section


def test_short_articles_are_not_split():
    chunks = split_statute_structure(
        DAHIR_PAGES, source_path="/x/المادة المدنية/doc.pdf"
    )
    for c in chunks:
        assert c.continued_from is None


def test_empty_pages_and_unnumbered_text_captured():
    chunks = split_statute_structure([(1, "   \n "), (2, "")])
    assert chunks == []
    chunks = split_statute_structure([(4, "مذكرة تفسيرية بدون ترقيم للمواد.")])
    assert len(chunks) == 1
    assert chunks[0].page == 4
    assert chunks[0].article_or_section is not None


def test_granite_tokenizer_counts_include_special_tokens():
    # Special tokens must be included: a 2-token body measures >= 4 with <s>/</s>.
    n = count_granite_tokens("المادة الأولى")
    assert n >= 4, f"special tokens missing from count: {n}"
    assert MAX_GRANITE_TOKENS == 512


def test_folder_code_map_covers_eight_shortlist_folders():
    assert len(FOLDER_CODE_MAP) == 8
    for folder in [
        "المادة الأسرية",
        "المادة التجارية",
        "المادة الجنائية",
        "المادة المدنية",
        "مادة التأمين و التقاعد",
        "مادة الحقوق والحريات",
        "مادة المعاملات الالكترونية",
        "مادة الوظيفة العمومية",
    ]:
        assert folder in FOLDER_CODE_MAP
