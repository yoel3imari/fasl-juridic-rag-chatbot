"""LLM layer: provider-agnostic agent factory + matter-privacy guard."""

from app.domain.privacy import (
    EVIDENCE_MARKERS,
    EXTERNAL_PROVIDERS,
    LOCAL_PROVIDERS,
    ConsentRequiredError,
    PrivacyViolationError,
    check_privacy,
    contains_matter_evidence,
    is_external_provider,
)
from app.infrastructure.llm.agent import (
    KNOWN_PROVIDERS,
    build_model_string,
    get_agent,
    get_agent_with_tools,
)
from app.infrastructure.llm.errors import (
    InvalidModelError,
    ProviderUnreachableError,
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
    "get_agent_with_tools",
    "is_external_provider",
]
