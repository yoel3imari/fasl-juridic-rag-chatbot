"""Task 3: authority payload + deterministic point-id schema.

Locks the additive payload contract (page/hierarchy/category) and the
deterministic chunk_id ordinal/page segment.
"""

from __future__ import annotations

OLD_AUTHORITY_KEYS = {
    "source",
    "version",
    "edition",
    "pub_date",
    "doc_date",
    "language",
    "article_or_section",
    "text",
}

NEW_AUTHORITY_KEYS = {
    "page",
    "hierarchy",
    "chunk_id",
    "category",
    "file_sha",
    "hijri_date",
    "coverage_note",
}


def _base_kwargs(**over):
    kw = {
        "source": "Code du Travail",
        "version": "2023",
        "edition": "ar-general",
        "article_or_section": "Article 1",
        "text": "المادة 1 - نص تجريبي",
    }
    kw.update(over)
    return kw


def test_chunk_id_deterministic_on_identical_input():
    from app.services.library_seed import chunk_id

    a = chunk_id(**_base_kwargs(page=3, ordinal=0))
    b = chunk_id(**_base_kwargs(page=3, ordinal=0))
    assert a == b
    # prefix preserved: source:version:edition:article:hash
    assert a.startswith("Code du Travail:2023:ar-general:Article 1:")


def test_chunk_id_differs_across_pages_for_identical_text():
    from app.services.library_seed import chunk_id

    a = chunk_id(**_base_kwargs(page=3, ordinal=0))
    b = chunk_id(**_base_kwargs(page=4, ordinal=0))
    assert a != b


def test_chunk_id_differs_across_ordinals_same_page():
    from app.services.library_seed import chunk_id

    a = chunk_id(**_base_kwargs(page=3, ordinal=0))
    b = chunk_id(**_base_kwargs(page=3, ordinal=1))
    assert a != b


def test_chunk_id_missing_page_does_not_crash_and_is_deterministic():
    from app.services.library_seed import chunk_id

    a = chunk_id(**_base_kwargs(page=None, ordinal=None))
    b = chunk_id(**_base_kwargs())
    assert a == b


def test_chunk_id_legacy_calls_still_work():
    from app.services.library_seed import chunk_id

    legacy = chunk_id("Code du Travail", "2023", "ar-general", "Article 1", "نص")
    assert legacy.startswith("Code du Travail:2023:ar-general:Article 1:")


def test_authority_payload_is_superset_of_old_keys():
    from app.infrastructure.qdrant.store import _authority_payload

    p = {
        "source": "Code du Travail",
        "version": "2023",
        "edition": "ar-general",
        "pub_date": "2023-01-01",
        "doc_date": "2023-01-01",
        "language": "ar",
        "article_or_section": "Article 1",
        "text": "نص",
        "page": 3,
        "hierarchy": {"code": "Code du Travail"},
        "chunk_id": "cid",
        "category": "travail",
        "file_sha": "abc",
        "hijri_date": "1444-01-01",
        "coverage_note": "note",
    }
    out = _authority_payload(p)
    assert OLD_AUTHORITY_KEYS <= set(out), (
        f"missing old keys: {OLD_AUTHORITY_KEYS - set(out)}"
    )
    assert NEW_AUTHORITY_KEYS <= set(out), (
        f"missing new keys: {NEW_AUTHORITY_KEYS - set(out)}"
    )


def test_authority_payload_defaults_when_new_fields_absent():
    from app.infrastructure.qdrant.store import _authority_payload

    p = {
        "source": "S",
        "version": "V",
        "edition": "ar-general",
        "article_or_section": "Article 1",
    }
    out = _authority_payload(p)  # must not raise
    assert OLD_AUTHORITY_KEYS <= set(out)
    assert NEW_AUTHORITY_KEYS <= set(out)
    # deterministic default for missing page
    assert out["page"] == 0
    assert out["hierarchy"] == {}


def test_point_id_uuid5_stable_and_distinct():
    from app.infrastructure.qdrant.store import _point_id

    assert _point_id("a") == _point_id("a")
    assert _point_id("a") != _point_id("b")


def test_build_provenance_carries_new_keys():
    from app.infrastructure.authority.extractor import Chunk
    from app.infrastructure.authority.manifest import EditionType, ManifestEntry
    from app.services.library_seed import build_provenance

    entry = ManifestEntry(
        source="Code du Travail",
        version="2023",
        edition=EditionType.AR_GENERAL,
        file_path="dummy.pdf",
        pub_date="2023-01-01",
        language="ar",
        coverage_note="note",
    )
    chunk = Chunk(
        text="المادة 1 نص",
        article_or_section="Article 1",
        hierarchy={"code": "X"},
        page=5,
    )
    prov = build_provenance(entry, chunk, ordinal=2)
    for k in (
        "page",
        "hierarchy",
        "chunk_id",
        "category",
        "file_sha",
        "hijri_date",
        "coverage_note",
    ):
        assert k in prov, f"provenance missing {k}"
    assert prov["page"] == 5
    # existing keys unchanged
    assert prov["source"] == "Code du Travail"
    assert prov["collection"] == "legal_authorities"
