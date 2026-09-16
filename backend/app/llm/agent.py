"""Provider-agnostic agent factory: model string only, no provider branching."""

from __future__ import annotations

import os
from typing import Any

from app.config import settings
from app.llm.errors import InvalidModelError
from app.llm.privacy import EXTERNAL_PROVIDERS, LOCAL_PROVIDERS

KNOWN_PROVIDERS: frozenset[str] = EXTERNAL_PROVIDERS | LOCAL_PROVIDERS


def build_model_string(provider: str, model: str) -> str:
    """Validate and join provider + model into a pydantic-ai model string."""
    name = provider.strip().lower()
    model_name = model.strip()
    if (
        name not in KNOWN_PROVIDERS
        or not model_name
        or any(ch.isspace() for ch in model_name)
    ):
        raise InvalidModelError(provider=provider, model=model)
    return f"{name}:{model_name}"


def get_agent(
    provider: str | None = None,
    model: str | None = None,
) -> Any:
    """Build a pydantic_ai.Agent from settings (or explicit overrides).

    Raises InvalidModelError for unknown providers / empty model names.
    Provider SDKs read their own env (e.g. OPENAI_API_KEY); OLLAMA_BASE_URL
    gets a sane default here so local-first works out of the box.
    """
    from pydantic_ai import Agent

    model_string = build_model_string(
        provider if provider is not None else settings.LLM_PROVIDER,
        model if model is not None else settings.LLM_MODEL,
    )
    os.environ.setdefault("OLLAMA_BASE_URL", settings.OLLAMA_BASE_URL)
    agent = Agent(model_string)
    agent.model_name = model_string
    return agent
