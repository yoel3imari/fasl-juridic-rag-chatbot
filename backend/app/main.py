"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Minimal lifespan: yield only (task 5+ own their service clients)."""
    yield


app = FastAPI(
    title="Moroccan Legal RAG",
    version=settings.LIBRARY_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Global safety net; HTTP errors keep their own status via re-raise."""
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers or {},
        )
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


from app.api.v1.chat import router as chat_router
from app.api.v1.documents import router as documents_router
from app.api.v1.library import router as library_router
from app.api.v1.search import router as search_router

app.include_router(library_router)
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(search_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "library_version": settings.LIBRARY_VERSION}
