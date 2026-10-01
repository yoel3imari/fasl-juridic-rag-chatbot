"""Provider-agnostic agent factory: universal OpenAI-compatible instantiation."""

from __future__ import annotations

import os
from typing import Any

from app.config import Settings
from app.domain.privacy import EXTERNAL_PROVIDERS, LOCAL_PROVIDERS
from app.infrastructure.llm.errors import InvalidModelError

KNOWN_PROVIDERS: frozenset[str] = EXTERNAL_PROVIDERS | LOCAL_PROVIDERS

DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

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
    Uses native Ollama for ollama and universal OpenAIProvider for cloud providers
    with zero vendor SDK dependencies.
    """
    from pydantic_ai import Agent

    env = Settings()
    prov = (provider if provider is not None else env.LLM_PROVIDER).strip().lower()
    mod = (model if model is not None else env.LLM_MODEL).strip()
    model_string = build_model_string(prov, mod)

    _inject_stored_api_keys()
    os.environ.setdefault("OLLAMA_BASE_URL", env.OLLAMA_BASE_URL)

    if prov == "ollama":
        agent = Agent(f"ollama:{mod}")
    else:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        api_key = os.environ.get(f"{prov.upper()}_API_KEY", "")
        if prov == "google" and not api_key:
            api_key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        stored_base_url: str | None = None
        try:
            from app.repositories import settings as settings_store

            if not api_key:
                api_key = settings_store.get_api_key(prov)
            stored_base_url = settings_store.get_base_url(prov)
        except Exception:
            pass

        base_url = (
            os.environ.get(f"{prov.upper()}_BASE_URL")
            or stored_base_url
            or DEFAULT_BASE_URLS.get(prov)
        )
        openai_provider = OpenAIProvider(
            base_url=base_url or None,
            api_key=api_key or "no-key-required",
        )
        chat_model = OpenAIChatModel(mod, provider=openai_provider)
        agent = Agent(chat_model)

    agent.model_name = model_string
    return agent

