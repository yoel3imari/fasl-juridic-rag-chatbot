"""FastAPI application entry point."""

from fastapi import FastAPI

from app.config import settings

app = FastAPI(
    title="Moroccan Legal RAG",
    version=settings.LIBRARY_VERSION,
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "library_version": settings.LIBRARY_VERSION}
