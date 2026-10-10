"""Runtime configuration: one ``Settings`` instance, one env file.

Tuning guidance (plan: configuration documentation)
---------------------------------------------------
* ``LLM_PROVIDER`` / ``LLM_MODEL`` are *defaults* only. A stored value (written
  by ``PUT /api/v1/settings/llm``) wins for read endpoints, and a per-request
  ``provider``/``model`` wins on the chat path; see ``app.config.resolver`` for
  the exact precedence chain.
* ``MATTER_PRIVACY_MODE`` defaults to ``strict``: external providers may not see
  matter evidence without explicit consent. Do NOT relax it to make grounding
  easier — the guard is the product's core privacy guarantee.
* Retrieval quality knobs that are NOT here: the served-embedding token budget
  (``SERVED_SAFE_TOKENS = 450``) lives with its measurement in
  ``app.domain.authority.served_budget`` — it is a per-chunk invariant, not a
  deployment toggle, so it is deliberately not env-configurable.
* Chat/ReAct context and streaming knobs are grouped under the
  ``Chat / ReAct tuning`` banner below, each with its sizing rationale inline.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Backend-root anchor (plan todo 40): the SINGLE named constant every
# data-dir path derives from. Per-file `Path(__file__).parents[N]`
# arithmetic is correct only at one exact nesting depth and silently
# repoints when a module moves (todos 34 and 39 both broke this), so the
# depth math lives here alone with this comment instead of scattered
# across services. config/__init__.py sits at backend/app/config/, so
# backend/ is parents[2]; re-verify the printed value if this file moves.
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    LLM_PROVIDER: str = "ollama"
    LLM_MODEL: str = "llama3.2"
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

    EMBEDDING_MODEL: str = "granite-embedding-107m-multilingual"

    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_EVIDENCE_COLLECTION: str = "matter_evidence"
    QDRANT_AUTHORITY_COLLECTION: str = "legal_authorities"
    QDRANT_LOCAL_PATH: str | None = None
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/matters.db"

    MATTER_PRIVACY_MODE: str = "strict"
    LIBRARY_VERSION: str = "1.0.0"
    STORAGE_DIR: str = "./storage"
    UPLOAD_MAX_BYTES: int = 20_000_000

    CRISPEMBED_URL: str = "http://localhost:8080"
    EMBEDDING_DIM: int = 384
    OCR_CONFIDENCE_THRESHOLD: float = 0.6
    OCR_LANGUAGES: str = "ara+fra"
    OCR_TIMEOUT_SECONDS: float = 30.0

    # --- Chat / ReAct tuning -------------------------------------------------
    # Rationale (plan: chat ReAct improvements):
    #
    # REACT_PRIOR_SUMMARY_MAX_CHARS bounds the "Prior tool results" block the
    # decision round carries after each retrieval. It is the ONLY part of that
    # prompt that grows across the ≤3 rounds (the user query is fixed), so it is
    # the only thing that needs a cap to keep the decision prompt comfortably
    # inside the model context. ~4 chars/token, so 1200 ≈ 300 tokens — sized to
    # hold the counts line plus several 250-char excerpts, then truncate cleanly
    # (the counts line is always preserved). Raise it for long-context models;
    # lower it for small local models. See app.services.chat.cap_prior_summary.
    REACT_PRIOR_SUMMARY_MAX_CHARS: int = 1200
    # STREAM_BATCH_CHARS is the SSE batching threshold for static replies
    # (refusal / provisional-not-found / direct answer). Replies at or below it
    # keep the two-frame half-split the SSE contract pins; longer replies stream
    # in ~equal batches of this many characters so a long answer starts
    # rendering sooner. It MUST stay above the longest localized static reply
    # (currently the French refusal, ~233 chars) or the frozen two-token-frame
    # contract in tests/test_chat_contract.py breaks. See _batched_tokens.
    STREAM_BATCH_CHARS: int = 600

    LIBRARY_SOURCE_DIR: str = "/home/xozev/Documents/legal/shortlist"
    LIBRARY_ARTIFACT_DIR: str = "./data/library-artifacts"
    LIBRARY_EMBED_BATCH_TEXTS: int = 32
    LIBRARY_EMBED_TIMEOUT_SECONDS: int = 300
    LIBRARY_INDEX_BATCH_POINTS: int = 128
    LIBRARY_EXTRACT_WORKERS: int = 1
    LIBRARY_OCR_MODE: str = "auto"
    LIBRARY_OCR_DPI: int = 300
    LIBRARY_OCR_MIN_CHARS: int = 20
    LIBRARY_OCR_TIMEOUT_SECONDS: int = 120


settings = Settings()
