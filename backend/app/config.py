from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    LLM_PROVIDER: str = "ollama"
    LLM_MODEL: str = "llama3.2"
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""

    EMBEDDING_MODEL: str = "bge-m3"

    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_EVIDENCE_COLLECTION: str = "matter_evidence"
    QDRANT_AUTHORITY_COLLECTION: str = "legal_authorities"
    QDRANT_LOCAL_PATH: str | None = None
    DATABASE_URL: str = "sqlite+aiosqlite:///./matters.db"

    MATTER_PRIVACY_MODE: str = "strict"
    LIBRARY_VERSION: str = "1.0.0"
    STORAGE_DIR: str = "./storage"
    UPLOAD_MAX_BYTES: int = 20_000_000

    CRISPEMBED_URL: str = "http://localhost:8080"
    EMBEDDING_DIM: int = 1024
    OCR_CONFIDENCE_THRESHOLD: float = 0.6
    OCR_LANGUAGES: str = "ara+fra"
    OCR_TIMEOUT_SECONDS: float = 30.0

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
