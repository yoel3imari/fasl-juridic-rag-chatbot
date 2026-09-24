import pytest
from app.config import settings


def test_required_env_keys():
    """Test that all required environment keys are configured."""
    assert hasattr(settings, "LLM_PROVIDER")
    assert hasattr(settings, "LLM_MODEL")
    assert hasattr(settings, "EMBEDDING_MODEL")
    assert hasattr(settings, "QDRANT_URL")
    assert hasattr(settings, "DATABASE_URL")
    assert hasattr(settings, "MATTER_PRIVACY_MODE")
    assert hasattr(settings, "LIBRARY_VERSION")


def test_bulk_pipeline_settings_defaults():
    """Bulk pipeline settings resolve with RAM-safe defaults."""
    assert settings.EMBEDDING_MODEL == "bge-m3"
    assert settings.EMBEDDING_DIM == 1024
    assert settings.LIBRARY_SOURCE_DIR == "/home/xozev/Documents/legal/shortlist"
    assert settings.LIBRARY_ARTIFACT_DIR == "./data/library-artifacts"
    assert settings.LIBRARY_EMBED_BATCH_TEXTS == 32
    assert settings.LIBRARY_EMBED_TIMEOUT_SECONDS == 300
    assert settings.LIBRARY_INDEX_BATCH_POINTS == 128
    assert settings.LIBRARY_EXTRACT_WORKERS == 1
    assert settings.LIBRARY_OCR_MODE == "auto"
    assert settings.LIBRARY_OCR_DPI == 300
    assert settings.LIBRARY_OCR_MIN_CHARS == 20
    assert settings.LIBRARY_OCR_TIMEOUT_SECONDS == 120


def test_embedding_dim_rejects_non_integer():
    """EMBEDDING_DIM=abc must fail loudly at startup validation."""
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(EMBEDDING_DIM="abc")
