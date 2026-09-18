# FastAPI backend. Includes tesseract + Arabic/French data so the
# OCR path works inside the stack (host lacks the binary — see issues.md).
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tesseract-ocr \
    tesseract-ocr-ara \
    tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 /uv /uvx /bin/

WORKDIR /app

# Development target: full deps, root user, bind mounts via docker-compose.dev.yml.
FROM base AS dev

COPY pyproject.toml uv.lock ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY data ./data
RUN uv sync --frozen

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -sf http://localhost:8000/health || exit 1
# Migrate the container SQLite DB on every start (idempotent); without this
# the shipped image has no tables and every write fails with OperationalError.
CMD ["sh", "-c", "uv run alembic upgrade head && exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]

# Production target: lean, non-root.
FROM base AS prod

COPY pyproject.toml uv.lock ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY data ./data
RUN uv sync --frozen --no-dev

RUN useradd -u 10000 -m appuser \
    && chown -R appuser /app

USER appuser

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -sf http://localhost:8000/health || exit 1
# Migrate the container SQLite DB on every start (idempotent); without this
# the shipped image has no tables and every write fails with OperationalError.
CMD ["sh", "-c", "uv run alembic upgrade head && exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
