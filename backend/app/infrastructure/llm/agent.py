"""Provider-agnostic agent factory: universal OpenAI-compatible instantiation."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.config import Settings
from app.domain.privacy import EXTERNAL_PROVIDERS, LOCAL_PROVIDERS
from app.infrastructure.llm.errors import InvalidModelError

logger = logging.getLogger(__name__)

KNOWN_PROVIDERS: frozenset[str] = EXTERNAL_PROVIDERS | LOCAL_PROVIDERS

# Diagnostic logs only: clamp arbitrary caller-supplied config values so a long
# or malformed provider/model string cannot flood the log. API keys are never
# logged at all — presence is reported as a boolean, never the value.
_LOG_VALUE_MAX: int = 120


def _trunc(value: Any, limit: int = _LOG_VALUE_MAX) -> str:
    """Clamp a config value for logging (safe for any object)."""
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + "..."


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
    if name not in KNOWN_PROVIDERS or not model_name or any(ch.isspace() for ch in model_name):
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
    logger.info(
        "llm agent: requested provider=%r model=%r (from %s)",
        _trunc(prov),
        _trunc(mod),
        "override" if provider is not None or model is not None else "settings",
    )
    try:
        model_string = build_model_string(prov, mod)
    except InvalidModelError:
        logger.warning(
            "llm agent: invalid model configuration provider=%r model=%r; known providers=%s",
            _trunc(prov),
            _trunc(mod),
            ",".join(sorted(KNOWN_PROVIDERS)),
        )
        raise
    logger.info("llm agent: resolved model_string=%r", model_string)

    _inject_stored_api_keys()
    # Explicit env only: no setdefault — injecting the Settings default into
    # os.environ here would shadow the app-stored base_url on every later
    # call in this process (observed: 2nd request flipped stored→env).
    ollama_base_url_from_env = os.environ.get("OLLAMA_BASE_URL")

    if prov == "ollama":
        # OpenAI-compatible client (not native `ollama:`) so the same branch
        # works against real Ollama `/v1`, llama-server, and llm-advisor —
        # all of which speak OpenAI `/v1` but not the native Ollama API.
        # Precedence mirrors the cloud branch but stays local: explicit
        # OLLAMA_BASE_URL env > app-stored base_url (PUT /settings/llm) >
        # Settings default > hardcoded default. The in-app config is
        # honoured when no explicit env override is set.
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        stored_ollama_base_url: str | None = None
        try:
            from app.repositories import settings as settings_store

            stored_ollama_base_url = settings_store.get_base_url(prov)
        except Exception as exc:
            logger.debug(
                "llm agent: stored settings unavailable for provider=%r: %s",
                _trunc(prov),
                _trunc(str(exc)),
            )

        if ollama_base_url_from_env:
            ollama_base_url = ollama_base_url_from_env
            ollama_base_url_source = "env"
        elif stored_ollama_base_url:
            ollama_base_url = stored_ollama_base_url
            ollama_base_url_source = "stored"
        elif env.OLLAMA_BASE_URL:
            ollama_base_url = env.OLLAMA_BASE_URL
            ollama_base_url_source = "settings_default"
        else:
            ollama_base_url = DEFAULT_BASE_URLS["ollama"]
            ollama_base_url_source = "default"
        normalized = ollama_base_url.strip().rstrip("/")
        if not normalized.lower().endswith("/v1"):
            normalized = normalized + "/v1"
        logger.info(
            "llm agent: branch=ollama-openai-compatible model=%r base_url=%s source=%s",
            _trunc(mod),
            _trunc(normalized),
            ollama_base_url_source,
        )
        ollama_provider = OpenAIProvider(
            base_url=normalized,
            api_key="no-key-required",
        )
        ollama_model = OpenAIChatModel(mod, provider=ollama_provider)
        agent = Agent(ollama_model)
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
        except Exception as exc:
            logger.debug(
                "llm agent: stored settings unavailable for provider=%r: %s",
                _trunc(prov),
                _trunc(str(exc)),
            )

        env_base_url = os.environ.get(f"{prov.upper()}_BASE_URL")
        default_base_url = DEFAULT_BASE_URLS.get(prov)
        base_url = env_base_url or stored_base_url or default_base_url
        if env_base_url:
            base_url_source = "env"
        elif stored_base_url:
            base_url_source = "stored"
        elif default_base_url:
            base_url_source = "default"
        else:
            base_url_source = "unset"
        # has_key is a boolean only — the key value itself is never logged.
        logger.info(
            "llm agent: branch=openai-compatible provider=%r model=%r base_url=%s "
            "base_url_source=%s has_key=%s",
            _trunc(prov),
            _trunc(mod),
            _trunc(base_url or ""),
            base_url_source,
            bool(api_key),
        )
        openai_provider = OpenAIProvider(
            base_url=base_url or None,
            api_key=api_key or "no-key-required",
        )
        chat_model = OpenAIChatModel(mod, provider=openai_provider)
        agent = Agent(chat_model)

    agent.model_name = model_string
    return agent
