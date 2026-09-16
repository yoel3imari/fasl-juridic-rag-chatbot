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
