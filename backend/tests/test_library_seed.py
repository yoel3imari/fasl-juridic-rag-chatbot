import pytest
from app.library.manifest import LibraryManifest, ManifestEntry


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
