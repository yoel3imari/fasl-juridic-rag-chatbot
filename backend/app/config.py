from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    LLM_PROVIDER: str = "ollama"
    LLM_MODEL: str = "llama3.2"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

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


settings = Settings()
