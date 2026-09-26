"""Typed errors raised by the LLM provider adapter.

The matter-privacy exceptions live in `app.domain.privacy`: the guard that raises
them is pure domain logic, and domain may not import infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass


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
