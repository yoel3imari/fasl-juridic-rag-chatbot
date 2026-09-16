"""FastAPI application entry point."""

from fastapi import FastAPI

from app.config import settings

app = FastAPI(
    title="Moroccan Legal RAG",
    version=settings.LIBRARY_VERSION,
)


from app.api.v1.library import router as library_router

app.include_router(library_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "library_version": settings.LIBRARY_VERSION}
