"""Matter-privacy guard: keep retrieved matter evidence local in strict mode."""

from __future__ import annotations

import logging

from app.llm.errors import ConsentRequiredError, PrivacyViolationError

logger = logging.getLogger(__name__)

EXTERNAL_PROVIDERS: frozenset[str] = frozenset(
    {"openai", "groq", "anthropic", "google", "openrouter"}
)
LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama"})

# Heuristic markers for retrieved matter spans (see task 7 domain tags).
EVIDENCE_MARKERS: tuple[str, ...] = ("[matter:", "\u00b6")


def is_external_provider(provider: str) -> bool:
    """True for cloud providers; local providers (ollama) always pass."""
    return provider.strip().lower() in EXTERNAL_PROVIDERS


def contains_matter_evidence(
    *texts: str | None, has_matter_evidence: bool = False
) -> bool:
    """Detect retrieved matter evidence: explicit flag or span markers."""
    if has_matter_evidence:
        return True
    return any(marker in (text or "") for text in texts for marker in EVIDENCE_MARKERS)


def check_privacy(
    content: str,
    *,
    provider: str,
    privacy_mode: str,
    consent: bool = False,
    has_matter_evidence: bool = False,
    system: str | None = None,
) -> None:
    """Enforce the matter-privacy rule before any provider call.

    Strict (default): any matter evidence aimed at an external provider
    raises PrivacyViolationError; local providers always pass. Non-strict:
    logs a warning and requires explicit per-request consent, otherwise
    raises ConsentRequiredError.
    """
    name = provider.strip().lower()
    if not contains_matter_evidence(
        content, system, has_matter_evidence=has_matter_evidence
    ):
        return
    if not is_external_provider(name):
        return
    if privacy_mode.strip().lower() == "strict":
        raise PrivacyViolationError(provider=name)
    logger.warning(
        "matter-privacy non-strict: matter evidence routed to external provider=%r",
        name,
    )
    if not consent:
        raise ConsentRequiredError(provider=name)
