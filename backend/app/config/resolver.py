"""Effective-settings precedence: the single owner of the fallback chain.

Three routes used to spell the same idea out independently, and two more
re-instantiated ``Settings`` per request while reading env defaults directly.
Every function here takes an explicit ``Settings`` instance so per-request
callers keep their env freshness (a fresh ``Settings()`` re-reads
``os.environ``) instead of silently falling back to the import-time global
defined in ``app.config``.

Two rules coexist and both live here:

* :func:`resolve_llm_settings` -- ``stored > env > defaults``. The file store
  written by ``PUT /api/v1/settings/llm`` wins; used by the endpoints that
  report the *current* settings (``GET /api/v1/settings/llm``,
  ``GET /api/v1/chat/models``).
* :func:`resolve_request_llm_settings` -- ``request > env > defaults``. The
  chat/draft request path deliberately does NOT consult the file store:
  ``chat_rag`` persists ``body.provider``/``body.model`` back into it, so
  reading it here would make the effective provider depend on whichever
  request came before. Contract freeze: these values must stay identical to
  the pre-move baseline.

``MATTER_PRIVACY_MODE`` is never stored, so it always comes from config.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.repositories import settings as settings_store


@dataclass(frozen=True)
class ResolvedLlmSettings:
    """The provider/model a caller should actually use."""

    provider: str
    model: str


def resolve_llm_settings(env: Settings) -> ResolvedLlmSettings:
    """Resolve ``stored > env > defaults`` into the effective provider/model."""
    stored = settings_store.load_llm_settings()
    return ResolvedLlmSettings(
        provider=str(stored.get("provider") or env.LLM_PROVIDER),
        model=str(stored.get("model") or env.LLM_MODEL),
    )


def resolve_request_llm_settings(
    env: Settings,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> ResolvedLlmSettings:
    """Resolve ``request > env > defaults``; the file store is not read."""
    return ResolvedLlmSettings(
        provider=str(provider or env.LLM_PROVIDER),
        model=str(model or env.LLM_MODEL),
    )


def resolve_privacy_mode(env: Settings) -> str:
    """Return ``MATTER_PRIVACY_MODE`` from config (it is never stored)."""
    return str(env.MATTER_PRIVACY_MODE)
