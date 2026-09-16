"""LLM layer: provider-agnostic agent factory + matter-privacy guard."""

from app.llm.agent import KNOWN_PROVIDERS, build_model_string, get_agent
from app.llm.errors import (
    ConsentRequiredError,
    InvalidModelError,
    PrivacyViolationError,
    ProviderUnreachableError,
)
from app.llm.privacy import (
    EVIDENCE_MARKERS,
    EXTERNAL_PROVIDERS,
    LOCAL_PROVIDERS,
    check_privacy,
    contains_matter_evidence,
    is_external_provider,
)

__all__ = [
    "KNOWN_PROVIDERS",
    "EVIDENCE_MARKERS",
    "EXTERNAL_PROVIDERS",
    "LOCAL_PROVIDERS",
    "ConsentRequiredError",
    "InvalidModelError",
    "PrivacyViolationError",
    "ProviderUnreachableError",
    "build_model_string",
    "check_privacy",
    "contains_matter_evidence",
    "get_agent",
    "is_external_provider",
]
