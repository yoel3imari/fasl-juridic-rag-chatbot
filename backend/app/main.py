"""FastAPI application entry point."""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings


class HealthCheckFilter(logging.Filter):
    """Drop uvicorn access-log records for the /health endpoint."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn access log args: (client_addr, method, path, http_version, status_code)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            return args[2] != "/health"
        return "/health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

# uvicorn's dictConfig owns only the `uvicorn.*` loggers and installs NO root
# handler, so application loggers (e.g. `app.api.v1.chat`) would fall through to
# logging's WARNING-only lastResort and every INFO line would be dropped. Attach
# one stdout handler so the per-stage chat trace lands on the same stdout as the
# uvicorn access log, and let the `app` tree through at INFO while third-party
# chatter stays at the default WARNING. Skipped when the host already installed a
# root handler (e.g. under pytest), so it never doubles output.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s:     %(message)s",
        stream=sys.stdout,
    )
logging.getLogger("app").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Minimal lifespan: yield only (task 5+ own their service clients).

    Maintenance-safe startup dim assert (plan todo 8): log-and-degrade only,
    never crash-loop. Skipped under FASL_MIGRATION_LOCK=1.
    """
    import logging

    try:
        from app.cli.library.bulk_migrate import startup_dim_check

        startup_dim_check()
    except Exception as exc:
        logging.getLogger(__name__).warning("startup dim check skipped: %s", exc)
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


from app.api.v1.analysis import router as analysis_router
from app.api.v1.chat import router as chat_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.documents import router as documents_router
from app.api.v1.drafts import drafts_router
from app.api.v1.drafts import matters_router as draft_matters_router
from app.api.v1.library import router as library_router
from app.api.v1.matters import router as matters_router
from app.api.v1.search import router as search_router
from app.api.v1.settings import router as settings_router

app.include_router(analysis_router)
app.include_router(matters_router)
app.include_router(conversations_router)
app.include_router(library_router)
app.include_router(documents_router)
app.include_router(draft_matters_router)
app.include_router(drafts_router)
app.include_router(chat_router)
app.include_router(search_router)
app.include_router(settings_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "library_version": settings.LIBRARY_VERSION}
