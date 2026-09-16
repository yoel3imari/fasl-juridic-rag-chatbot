from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    LLM_PROVIDER: str = "ollama"
    LLM_MODEL: str = "llama3.2"
    EMBEDDING_MODEL: str = "bge-m3"
    QDRANT_URL: str = "http://localhost:6333"
    DATABASE_URL: str = "sqlite+aiosqlite:///./matters.db"
    MATTER_PRIVACY_MODE: str = "strict"
    LIBRARY_VERSION: str = "1.0.0"


settings = Settings()
