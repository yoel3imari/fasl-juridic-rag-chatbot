"""Typed errors for the LLM layer and matter-privacy guard."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrivacyViolationError(Exception):
    """Strict-mode block: matter evidence must not reach an external provider.

    403-equivalent: the caller (HTTP/WS layer) translates this into a
    403 status code or a structured error frame with redaction guidance.
    """

    provider: str
    detail: str = "prompt contains matter evidence"

    def __str__(self) -> str:
        return (
            f"403 matter-privacy violation: {self.detail} "
            f"(provider={self.provider!r}). "
            "Redact matter evidence or switch to a local provider "
            "(LLM_PROVIDER=ollama) before retrying."
        )


@dataclass(frozen=True, slots=True)
class ConsentRequiredError(Exception):
    """Non-strict mode: external send needs explicit per-request consent."""

    provider: str

    def __str__(self) -> str:
        return (
            f"explicit per-request consent is required before sending "
            f"matter evidence to external provider {self.provider!r} "
            "(set consent=true on the request)"
        )


@dataclass(frozen=True, slots=True)
class InvalidModelError(Exception):
    """Raised at agent build time for unknown provider/empty model combos."""

    provider: str
    model: str

    def __str__(self) -> str:
        return (
            f"invalid LLM configuration: provider={self.provider!r} "
            f"model={self.model!r} (expect provider in "
            "openai|groq|anthropic|google|ollama with a non-empty model name)"
        )


@dataclass(frozen=True, slots=True)
class ProviderUnreachableError(Exception):
    """Raised when the configured provider cannot serve a request."""

    provider: str
    reason: str

    def __str__(self) -> str:
        return f"LLM provider {self.provider!r} unreachable: {self.reason}"
