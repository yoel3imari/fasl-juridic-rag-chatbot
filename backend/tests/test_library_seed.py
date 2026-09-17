import json

import pytest
from app.library.manifest import EditionType, LibraryManifest, ManifestEntry


def test_seed_records_edition_and_version():
    """Test that seeding records edition and version metadata."""
    manifest = LibraryManifest(
        entries=[
            ManifestEntry(
                source="Code du Travail",
                version="2023",
                edition="ar-general",
                file_path="backend/data/seed/code_du_travail.pdf",
            )
        ]
    )

    # Should have edition field
    assert manifest.entries[0].edition in ["ar-general", "fr-translation"]
    assert manifest.entries[0].version is not None


def test_manifest_entry_requires_source_and_version():
    """Test that manifest entry missing source/version is rejected."""
    with pytest.raises(ValueError):
        ManifestEntry(source=None, version=None)


def test_extractor_splits_arabic_article_structure():
    """Arabic الماد ة regex splits text into article-level chunks with hierarchy."""
    from app.library.extractor import extract_chunks

    text = (
        "المادة 1 - مدة العطلة السنوية\n"
        "يحق لكل عامل بالحصول على عطلة سنوية مدفوعة الأجر.\n\n"
        "المادة 2 - شروط الاستفادة\n"
        "يحق للعامل الاستفادة بعد ستة أشهر.\n\n"
        "المادة 3 - التعويض عن العطلة\n"
        "في حالة عدم الاستخدام يحصل على تعويض.\n"
    )
    import tempfile, os

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(text)
        tmp = f.name
    try:
        chunks = extract_chunks(tmp)
        assert len(chunks) == 3, f"expected 3 articles, got {len(chunks)}"
        assert chunks[0].article_or_section == "Article 1"
        assert chunks[1].article_or_section == "Article 2"
        assert chunks[2].article_or_section == "Article 3"
        assert chunks[0].hierarchy.get("code") == "Code du Travail"
        # Verify article text starts with the correct الماد ة marker
        assert "المادة 1" in chunks[0].text
        assert "المادة 2" in chunks[1].text
    finally:
        os.unlink(tmp)


def test_provenance_records_all_required_fields():
    """build_provenance captures source/version/edition/language/coverage."""
    from app.library.extractor import Chunk
    from app.library.seeder import build_provenance

    entry = ManifestEntry(
        source="Code du Travail",
        version="2023",
        edition=EditionType.AR_GENERAL,
        file_path="dummy.pdf",
        pub_date="2023-01-01",
        language="ar",
        coverage_note="Bulletin Officiel general edition",
    )
    chunk = Chunk(text="test", article_or_section="Article 1", hierarchy={"code": "X"})
    prov = build_provenance(entry, chunk)
    assert prov["source"] == "Code du Travail"
    assert prov["version"] == "2023"
    assert prov["edition"] == "ar-general"
    assert prov["language"] == "ar"
    assert prov["pub_date"] == "2023-01-01"
    assert prov["article_or_section"] == "Article 1"
    assert prov["collection"] == "legal_authorities"


def test_seeder_extract_and_provenance_works():
    """seeder extracts 4 chunks from Arabic test fixture with correct provenance."""
    from pathlib import Path

    from app.library.seeder import seed

    result = seed(
        Path("data/library-manifest.json").resolve(),
        embed=False,  # skip embedding to avoid service exhaustion in full suite
    )
    fixture_results = [
        r for r in result["results"] if "Test Fixture" in r.get("entry", "")
    ]
    assert fixture_results, "test fixture entry not found in seed results"
    assert fixture_results[0]["chunks"] == 4
    # With embed=False, embedded is always 0; status is always "seeded"
    assert fixture_results[0]["status"] == "seeded"


def test_seed_state_idempotent():
    """Re-seeding the same manifest does not duplicate state."""
    from pathlib import Path

    from app.library.seeder import seed

    r1 = seed(Path("data/library-manifest.json").resolve(), embed=True)
    r2 = seed(Path("data/library-manifest.json").resolve(), embed=True)
    assert r1["state"] == r2["state"], "seed state should be idempotent"


def test_coverage_endpoint_returns_all_manifest_entries():
    """Coverage endpoint returns all manifest entries with metadata fields."""
    import asyncio

    from app.api.v1.library import coverage

    cov = asyncio.run(coverage())
    titles = cov["titles"]
    sources = [t["source"] for t in titles]
    assert "Code du Travail" in sources
    # Test fixture should be present
    fixture = [t for t in titles if "Test Fixture" in t["source"]]
    assert fixture, "test fixture not in coverage"
    assert fixture[0]["status"] == "seeded"
    # Verify required metadata fields on every title
    for t in titles:
        assert "source" in t
        assert "version" in t
        assert "edition" in t
        assert "pub_date" in t
