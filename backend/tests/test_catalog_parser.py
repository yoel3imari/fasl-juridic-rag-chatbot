# -*- coding: utf-8 -*-
"""Golden tests for the shortlist filename provenance parser.

Every golden filename below is a REAL file under
/home/xozev/Documents/legal/shortlist (verified with `ls`/`find`).
Dates/numbers are asserted exactly as they appear in the filename;
nothing is invented. Unparseable names must land in quarantine.
"""

from __future__ import annotations

import pytest

from app.library.catalog import parse_filename

CIVIL = "المادة المدنية"
COMMERCE = "المادة التجارية"
ELECTRONIC = "مادة المعاملات الالكترونية"
FAMILY = "المادة الأسرية"
RIGHTS = "مادة الحقوق والحريات"
CIVIL_SERVICE = "مادة الوظيفة العمومية"
INSURANCE = "مادة التأمين و التقاعد"
CRIMINAL = "المادة الجنائية"


def test_module_exposes_parse_filename():
    assert callable(parse_filename)


# (filename, folder, expected fields)
GOLDEN = [
    # ---- ظهير شريف ----
    (
        "ظهير شريف رقم 1.02.298صادر في 25 من رجب 1423 (3 أكتوبر 2002) بتنفيذ القانون رقم 18.00 المتعلق بنظام الملكية المشتركة للعقارات المبنية.pdf",
        f"{CIVIL}/المادة العقارية",
        {
            "law_type": "ظهير شريف",
            "law_number": "1.02.298",
            "hijri_date": "25 من رجب 1423",
            "gregorian_date": "3 أكتوبر 2002",
            "language": "ar",
            "edition": "ar-general",
            "category": CIVIL,
            "quarantined": False,
        },
    ),
    (
        "ظهير شريف رقم 1.25.64 صادر في 22 من جمادى الأولى 1447 (14 نوفمبر 2025) بتنفيذ القانون رقم 03.25 المتعلق بهيئات التوظيف الجماعي للقيم المنقولة.pdf",
        COMMERCE,
        {
            "law_type": "ظهير شريف",
            "law_number": "1.25.64",
            "hijri_date": "22 من جمادى الأولى 1447",
            "gregorian_date": "14 نوفمبر 2025",
            "language": "ar",
            "edition": "ar-general",
            "category": COMMERCE,
            "quarantined": False,
        },
    ),
    (
        "ظهير شريف رقم 1.18.78 صادر في 23 من ذي القعدة 1439 (6 أغسطس 2018) بتنفيذ القانون رقم 27.18 القاضي بالمصادقة على المرسوم بقانون رقم 2.18.117 الصادر في.pdf",
        ELECTRONIC,
        {
            "law_type": "ظهير شريف",
            "law_number": "1.18.78",
            "hijri_date": "23 من ذي القعدة 1439",
            "gregorian_date": "6 أغسطس 2018",
            "language": "ar",
            "edition": "ar-general",
            "category": ELECTRONIC,
            "quarantined": False,
        },
    ),
    (
        "ظهير شريف يتضمن الامر بتنفيذ القانون رقم 4.79 الملغى والمعوض بموجبه الفصل 46 من الظهير الشريف رقم 1.57.187 بتاريخ 24 جمادى الثانية 1383 (12 نونبر1963).pdf",
        RIGHTS,
        {
            "law_type": "ظهير شريف",
            "law_number": "4.79",
            "hijri_date": "24 جمادى الثانية 1383",
            "gregorian_date": "12 نونبر1963",
            "language": "ar",
            "edition": "ar-general",
            "category": RIGHTS,
            "quarantined": False,
        },
    ),
    (
        "ظهير شريف يتضمن الأمر بتنفيذ القانون رقم 6.78 الذي يلغى بموجبه الفصل 582 من الظهير الشريف رقم 1.58.261 بتاريخ فاتح شعبان 1378 (10 يبراير1959) بمثابة ق.pdf",
        CRIMINAL,
        {
            "law_type": "ظهير شريف",
            "law_number": "6.78",
            "hijri_date": "فاتح شعبان 1378",
            "gregorian_date": "10 يبراير1959",
            "language": "ar",
            "edition": "ar-general",
            "category": CRIMINAL,
            "quarantined": False,
        },
    ),
    (
        "ظهير شريف بتنفيذ القانون رقم 15.01 المتعلق بكفالة الأطفال المهملين.pdf",
        FAMILY,
        {
            "law_type": "ظهير شريف",
            "law_number": "15.01",
            "hijri_date": None,
            "gregorian_date": None,
            "language": "ar",
            "edition": "ar-general",
            "category": FAMILY,
            "quarantined": False,
        },
    ),
    # ---- مرسوم ----
    (
        "مرسوم رقم 2.25.1062 صادر في 14 من شوال 1447 (2 أبريل 2026) بتطبيق القانون رقم 03.25 المتعلق بهيئات التوظيف الجماعي للقيم المنقولة.pdf",
        COMMERCE,
        {
            "law_type": "مرسوم",
            "law_number": "2.25.1062",
            "hijri_date": "14 من شوال 1447",
            "gregorian_date": "2 أبريل 2026",
            "language": "ar",
            "edition": "ar-general",
            "category": COMMERCE,
            "quarantined": False,
        },
    ),
    (
        "مرسوم رقم 2.19.429 صادر في 18 من رمضان 1440 (24 ماي 2019) بشأن النظام الأساسي الخاص بموظفي الأمن الوطني.pdf",
        CIVIL_SERVICE,
        {
            "law_type": "مرسوم",
            "law_number": "2.19.429",
            "hijri_date": "18 من رمضان 1440",
            "gregorian_date": "24 ماي 2019",
            "language": "ar",
            "edition": "ar-general",
            "category": CIVIL_SERVICE,
            "quarantined": False,
        },
    ),
    (
        "مرسوم بتطبيق الظهير الشريف رقم 1.58.376 الصادر في 3 جمادى الأولى 1378 (15 نوفمبر 1958) بتنظيم حق تأسيس الجمعيات.pdf",
        RIGHTS,
        {
            "law_type": "مرسوم",
            "law_number": "1.58.376",
            "hijri_date": "3 جمادى الأولى 1378",
            "gregorian_date": "15 نوفمبر 1958",
            "language": "ar",
            "edition": "ar-general",
            "category": RIGHTS,
            "quarantined": False,
        },
    ),
    (
        "مرسوم بتطبيق أحكام الفصلين 3-618 و16-618 من الظهير الشريف الصادر في 9 رمضان 1331 (12 أغسطس 1913) بمثابة قانون للالتزامات والعقود.pdf",
        f"{CIVIL}/المادة المدنية",
        {
            "law_type": "مرسوم",
            "law_number": None,
            "hijri_date": "9 رمضان 1331",
            "gregorian_date": "12 أغسطس 1913",
            "language": "ar",
            "edition": "ar-general",
            "category": CIVIL,
            "quarantined": False,
        },
    ),
    (
        "مرسوم رقم 2.26.577 صادر في 8 صفر 1448 (23 يوليو 2026) يقضي بالاحتفاظ بصفة المنفعة العامة للجمعية المسماة _مؤسسة محمد السادس للأشخاص في وضعية إعاقة_.pdf",
        RIGHTS,
        {
            "law_type": "مرسوم",
            "law_number": "2.26.577",
            "hijri_date": "8 صفر 1448",
            "gregorian_date": "23 يوليو 2026",
            "language": "ar",
            "edition": "ar-general",
            "category": RIGHTS,
            "quarantined": False,
        },
    ),
    (
        "مرسوم بتطبيق أحكام المادتين 4 و5 من القانون التنظيمي رقم 02.12 فيما يتعلق بمسطرة التعيين في المناصب ال.pdf",
        CIVIL_SERVICE,
        {
            "law_type": "مرسوم",
            "law_number": "02.12",
            "hijri_date": None,
            "gregorian_date": None,
            "language": "ar",
            "edition": "ar-general",
            "category": CIVIL_SERVICE,
            "quarantined": False,
        },
    ),
    # ---- قرار ----
    (
        "قرار لرئيس الحكومة رقم 3.07.26 صادر في 27 من رمضان 1447 (17 مارس 2026) بتحديد شروط وكيفيات التشغيل بموجب عقود.pdf",
        ELECTRONIC,
        {
            "law_type": "قرار",
            "law_number": "3.07.26",
            "hijri_date": "27 من رمضان 1447",
            "gregorian_date": "17 مارس 2026",
            "language": "ar",
            "edition": "ar-general",
            "category": ELECTRONIC,
            "quarantined": False,
        },
    ),
    (
        "قرار لوزير العدل رقم 381.25 بتحديد النماذج المنصوص عليها في المرسوم رقم 2.23.101 الصادر في 18 من ربيع الآخر 1446 (22 أكتوبر 2024) بتحديد كيفيات تنظيم.pdf",
        f"{CIVIL}/المادة العقارية",
        {
            "law_type": "قرار",
            "law_number": "381.25",
            "hijri_date": "18 من ربيع الآخر 1446",
            "gregorian_date": "22 أكتوبر 2024",
            "language": "ar",
            "edition": "ar-general",
            "category": CIVIL,
            "quarantined": False,
        },
    ),
    (
        "قرار لوزير العدل رقم 357.26 صادر في 27 من شعبان 1447 (16 فبراير 2026) لتطبيق المرسوم رقم 2.23.100 بتاريخ 18 من ربيع الآخر 1446 (22 أكتوبر 2024) المتعل.pdf",
        f"{CIVIL}/المادة العقارية",
        {
            "law_type": "قرار",
            "law_number": "357.26",
            "hijri_date": "27 من شعبان 1447",
            "gregorian_date": "16 فبراير 2026",
            "language": "ar",
            "edition": "ar-general",
            "category": CIVIL,
            "quarantined": False,
        },
    ),
    (
        "قرار لوزير العدل رقم 2479.25 صادر في 23 من ربيع الآخر 1447 (16 أكتوبر 2025) بتعيين ممثلي الإدارة لتمثيل الموظف.pdf",
        CIVIL_SERVICE,
        {
            "law_type": "قرار",
            "law_number": "2479.25",
            "hijri_date": "23 من ربيع الآخر 1447",
            "gregorian_date": "16 أكتوبر 2025",
            "language": "ar",
            "edition": "ar-general",
            "category": CIVIL_SERVICE,
            "quarantined": False,
        },
    ),
    # ---- قانون ----
    (
        "القانون رقم 58.25 المتعلق بالمسطرة المدنية ( صادر بتاريخ 11 فبراير 2026 ).pdf",
        f"{CIVIL}/المادة المدنية",
        {
            "law_type": "قانون",
            "law_number": "58.25",
            "hijri_date": None,
            "gregorian_date": "11 فبراير 2026",
            "language": "ar",
            "edition": "ar-general",
            "category": CIVIL,
            "quarantined": False,
        },
    ),
    (
        "القانون رقم 36.20 القاضي بتحويل صندوق الضمان المركزي إلى شركة مساهمة.pdf",
        COMMERCE,
        {
            "law_type": "قانون",
            "law_number": "36.20",
            "hijri_date": None,
            "gregorian_date": None,
            "language": "ar",
            "edition": "ar-general",
            "category": COMMERCE,
            "quarantined": False,
        },
    ),
    (
        "القانون رقم 17.05 المتعلق بزجر إهانة علم المملكة ورموزها.pdf",
        CRIMINAL,
        {
            "law_type": "قانون",
            "law_number": "17.05",
            "hijri_date": None,
            "gregorian_date": None,
            "language": "ar",
            "edition": "ar-general",
            "category": CRIMINAL,
            "quarantined": False,
        },
    ),
    (
        "قانون بمثابة مدونة الأسرة.pdf",
        FAMILY,
        {
            "law_type": "قانون",
            "law_number": None,
            "hijri_date": None,
            "gregorian_date": None,
            "language": "ar",
            "edition": "ar-general",
            "category": FAMILY,
            "quarantined": False,
        },
    ),
    (
        "القانون المتعلق بمدونة التأمينات.pdf",
        INSURANCE,
        {
            "law_type": "قانون",
            "law_number": None,
            "hijri_date": None,
            "gregorian_date": None,
            "language": "ar",
            "edition": "ar-general",
            "category": INSURANCE,
            "quarantined": False,
        },
    ),
    (
        "القانون المتعلق بالمسطرة الجنائية.pdf",
        CRIMINAL,
        {
            "law_type": "قانون",
            "law_number": None,
            "hijri_date": None,
            "gregorian_date": None,
            "language": "ar",
            "edition": "ar-general",
            "category": CRIMINAL,
            "quarantined": False,
        },
    ),
]

QUARANTINE_CASES = [
    ("scan0001.pdf", COMMERCE),
    ("document sans titre.pdf", RIGHTS),
    ("", FAMILY),
    ("أ" * 500 + ".pdf", CRIMINAL),
    ("notes-2024-final-v2.pdf", INSURANCE),
]


@pytest.mark.parametrize("filename,folder,expected", GOLDEN)
def test_golden_filenames_parse_exactly(filename, folder, expected):
    parsed = parse_filename(filename, folder)
    for field, want in expected.items():
        assert getattr(parsed, field) == want, (
            f"{filename!r}: field {field} got {getattr(parsed, field)!r}, want {want!r}"
        )
    # source/version/edition must always be populated (manifest-compatible)
    assert parsed.source
    assert parsed.version
    assert parsed.edition in ("ar-general", "fr-translation")


@pytest.mark.parametrize("filename,folder", QUARANTINE_CASES)
def test_unparseable_names_land_in_quarantine(filename, folder):
    parsed = parse_filename(filename, folder)
    assert parsed.quarantined is True
    assert parsed.quarantine_reason
    # deterministic fallback: source=<folder>, version=<normalized filename>
    assert parsed.source == folder
    assert parsed.version  # never empty, never crashes


def test_golden_files_exist_on_disk():
    """Guard: the golden set must stay anchored to real shortlist files."""
    import os

    shortlist = "/home/xozev/Documents/legal/shortlist"
    missing = []
    for filename, folder, _ in GOLDEN:
        if not os.path.exists(os.path.join(shortlist, folder, filename)):
            missing.append(os.path.join(folder, filename))
    assert not missing, f"golden files missing on disk: {missing[:5]}"


def test_french_labeled_name_gets_fr_translation_edition():
    parsed = parse_filename("ظهير شريف رقم 1.02.298 (ترجمة فرنسية).pdf", CIVIL)
    assert parsed.language == "fr"
    assert parsed.edition == "fr-translation"
    assert parsed.quarantined is False
