"""Provider-agnostic agent factory: model string only, no provider branching."""

from __future__ import annotations

import os
from typing import Any

from app.config import settings
from app.domain.privacy import EXTERNAL_PROVIDERS, LOCAL_PROVIDERS
from app.infrastructure.llm.errors import InvalidModelError

KNOWN_PROVIDERS: frozenset[str] = EXTERNAL_PROVIDERS | LOCAL_PROVIDERS

_STORED_KEY_ENV_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("openrouter", ("OPENROUTER_API_KEY",)),
    ("openai", ("OPENAI_API_KEY",)),
    ("anthropic", ("ANTHROPIC_API_KEY",)),
    ("google", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    ("groq", ("GROQ_API_KEY",)),
)


def _inject_stored_api_keys() -> None:
    """Inject stored API keys into os.environ without logging values.

    Only sets a variable when the stored key is non-empty and the env var
    is not already set. Failures (missing/corrupt file) are silent so
    agent construction never breaks.
    """
    try:
        from app.repositories import settings as settings_store
    except Exception:
        return
    try:
        stored = settings_store.load_llm_settings()
    except Exception:
        return
    keys = stored.get("api_keys")
    if not isinstance(keys, dict):
        return
    for provider, env_names in _STORED_KEY_ENV_MAP:
        value = keys.get(provider, "")
        if not isinstance(value, str) or not value:
            continue
        for env_name in env_names:
            if not os.environ.get(env_name):
                os.environ[env_name] = value


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
    _inject_stored_api_keys()
    os.environ.setdefault("OLLAMA_BASE_URL", settings.OLLAMA_BASE_URL)
    agent = Agent(model_string)
    agent.model_name = model_string
    return agent


def get_agent_with_tools(
    provider: str | None = None,
    model: str | None = None,
    *,
    store: Any,
    embedder: Any,
    matter_id: int,
    top_k: int = 30,
) -> Any:
    """Build the chat agent with retrieval tools registered.

    Wraps get_agent, then registers search_matter_tool /
    search_authority_tool / search_both_tool (matter_id pre-filter bound)
    via app.llm.tools.register_retrieval_tools. See tools.py for why the
    chat route still drives a manual envelope loop around these tools.
    """
    from app.llm.tools import ToolContext, register_retrieval_tools

    agent = get_agent(provider=provider, model=model)
    register_retrieval_tools(
        agent,
        ToolContext(store=store, embedder=embedder, matter_id=matter_id, top_k=top_k),
    )
    return agent
