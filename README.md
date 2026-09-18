# Fasl — Moroccan Legal RAG

Fasl is a matter-scoped legal assistant with dual-domain RAG (private matter evidence plus a versioned authority library), claim-level citations, Arabic/French support, and a privacy-guarded LLM layer.

## Key features

- **Matter ingestion pipeline:** PDF/DOCX extraction, OCR with `ara+fra` via Tesseract, sectioning, classification, and indexing.
- **Dual-domain hybrid search:** Qdrant dense plus sparse vectors, FlashRank rerank, strict matter isolation.
- **Chat with SSE streaming:** events flow as citations, then tokens, then done.
- **Analysis loop:** structured findings with gaps and plain-language summaries (`summary_ar`, `summary_fr`).
- **Drafting with explicit review states:** acknowledge, lawyer review, and transition flows with provisional banners.
- **Versioned authority library:** seeding, coverage reporting, and gap detection.
- **Privacy modes:** strict is the default. Matter evidence stays local to Ollama and external providers are blocked unless consent is given.
- **Frontend workspace:** chat on the left, documents, analysis, drafts, and source tabs on the right, plus a command palette, source inspector, and RTL support.

## Architecture

| Service | Port | Stack |
|---|---|---|
| `fastapi` | 8000 | FastAPI, SQLAlchemy async, SQLite, Alembic |
| `nextjs` | 3000 | Next.js 16, React 19, Tailwind CSS 4, Radix UI, SSE client |
| `qdrant` | 6333 | Collections `matter_evidence`, `legal_authorities` |
| `crispembed` | 8080 | bge-m3 embeddings, 1024 dim, 449MB GGUF cached |

There are no Postgres, Redis, Ollama, or auth containers by design.

## Prerequisites

- Docker and Docker Compose v2
- Node 22+
- Python 3.11+ with `uv`
- Tesseract with `ara+fra` data for local OCR

## Quickstart

Copy the env template for local runs:

```bash
cp .env.example .env
```

Full dev stack with hot reload:

```bash
make dev
# docker compose --env-file .env.docker up -d
```

Infra only, app code on the host:

```bash
make dev-local
# starts qdrant + crispembed, then run backend and frontend locally
```

```bash
uv run uvicorn app.main:app --reload   # from backend/
npm run dev                            # from frontend/
```

Production stack (base compose plus prod overlay):

```bash
make up
# docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.docker up -d
```

Other targets:

```bash
make logs
make down
```

First boot takes extra time. CrispEmbed builds from source and pulls the bge-m3 model once, later restarts reuse the model volume.

App URLs:

- Frontend: http://localhost:3000
- API: http://localhost:8000
- Health: `GET /health` returns status plus library version
- API docs via FastAPI (`/docs`)

## Configuration

Env vars (see `.env.example`, `.env.docker`, `backend/app/config.py`):

| Var | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` or `openrouter` |
| `LLM_MODEL` | `llama3.2` | Vendor/model string for OpenRouter, e.g. `openai/gpt-4o-mini` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local LLM endpoint |
| `OPENROUTER_API_KEY` | empty | Required for cloud provider |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Cloud endpoint |
| `EMBEDDING_MODEL` | `bge-m3` | Served by CrispEmbed |
| `QDRANT_URL` | `http://localhost:6333` | `http://qdrant:6333` inside compose |
| `QDRANT_LOCAL_PATH` | empty | File path or `:memory:`, no server needed |
| `EMBEDDING_DIM` | `1024` | bge-m3 dimension |
| `CRISPEMBED_URL` | `http://localhost:8080` | `http://crispembed:8080` inside compose |
| `DATABASE_URL` | `sqlite+aiosqlite:///./matters.db` | `sqlite+aiosqlite:///./data/matters.db` in Docker |
| `MATTER_PRIVACY_MODE` | `strict` | `strict` blocks external providers for matter evidence |
| `LIBRARY_VERSION` | `1.0.0` | Returned by `/health` and coverage |
| `STORAGE_DIR` | `./storage` | `FASL_STORAGE_DIR=/app/data/storage` in Docker |
| `UPLOAD_MAX_BYTES` | `20000000` | 20MB upload cap |
| `OCR_CONFIDENCE_THRESHOLD` | `0.6` | Flags low-confidence sections for review |
| `OCR_LANGUAGES` | `ara+fra` | Tesseract language data |

`NEXT_PUBLIC_API_BASE_URL` is baked at build time for prod (see `docker-compose.prod.yml`). Runtime env alone does not change it.

## API overview

Routers live under `backend/app/api/v1/`:

- `matters` – list and create matters
- `documents` – multipart upload per matter
- `search` – domain `matter`, `authority`, or `both`
- `chat` – SSE stream (`citations`, then `token`, then `done`)
- `analysis` – run analysis per matter
- `drafts` plus `draft_schemas` – create drafts, `acknowledge`, `lawyer_review`, `transition`
- `library` – coverage and seeding

`GET /health` returns status plus library version.

## Frontend overview

Routes:

- `src/app/page.tsx` – main workspace
- `src/app/library/page.tsx` – library coverage view

Components (`src/components/`):

- `chat`, `upload`, `matter-bar`, `analysis-panel`, `drafts-panel`
- `source-inspector`, `command-palette`, `new-matter-modal`, `citation-domain-badge`

`src/lib/api.ts` holds the SSE client with `parseSseLine` and `streamChat`, plus typed `Matter` and `Authority` citations.

## Testing

```bash
make test
```

This runs backend pytest, frontend vitest, and live compose tests (`FASL_LIVE_COMPOSE=1 tests/test_compose.py`).

Granular runs:

```bash
cd backend && uv run pytest -q
cd frontend && npm run test -- --run
```

Other suites:

- Playwright e2e: `frontend/e2e/library-first.spec.ts`
- Backend per-domain suites: `backend/tests/` (analysis, ingestion, search, privacy, review states, matter isolation, library seed)
- Seeding helper: `e2e/seed_test_authority.py`

Build and cleanup:

```bash
make build
make clean
```

## Project structure

```text
backend/                  # FastAPI app (moroccan-legal-rag-backend)
  app/api/v1/             # matters, documents, search, chat, analysis, drafts, library
  app/ingestion/          # extract, ocr, sections, classifier, pipeline, indexer
  app/search/             # store, sparse, service, schemas
  app/rag/                # assemble, rerank
  app/library/            # seeder, extractor, manifest, embedder
  app/llm/                # privacy guard, agent
  app/analysis/           # engine, glossary
  app/models/             # SQLAlchemy models
  alembic/                # migrations
  tests/                  # per-domain suites
frontend/                 # Next.js app (moroccan-legal-rag-frontend)
  src/app/                # page.tsx (workspace), library/page.tsx
  src/components/         # chat, upload, panels, inspector, palette
  src/lib/                # api.ts SSE client
  e2e/                    # library-first.spec.ts
qdrant/                   # Qdrant service build
crispembed/               # CrispEmbed bge-m3 service build
tests/test_compose.py     # live compose tests
e2e/seed_test_authority.py
docker-compose.yml        # dev-first base stack
docker-compose.prod.yml   # prod overlay
.env.example / .env.docker
Makefile                  # dev, dev-local, up, down, logs, test, build, clean
```
