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
| `crispembed` | 8080 | granite-embedding-107m, 384 dim, 115MB GGUF cached (bge-m3 GGUF kept in the shared volume as the gate fallback) |

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
# docker compose --env-file .env up -d
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
# docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d
```

Other targets:

```bash
make logs
make down
```

First boot takes extra time. CrispEmbed builds from source and pulls the granite-embedding-107m model once, later restarts reuse the model volume.

App URLs:

- Frontend: http://localhost:3000
- API: http://localhost:8000
- Health: `GET /health` returns status plus library version
- API docs via FastAPI (`/docs`)

## Configuration

Env vars (see `.env.example`, `.env`, `backend/app/config/`):

| Var | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` or `openrouter` |
| `LLM_MODEL` | `llama3.2` | Vendor/model string for OpenRouter, e.g. `openai/gpt-4o-mini` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local LLM endpoint |
| `OPENROUTER_API_KEY` | empty | Required for cloud provider |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Cloud endpoint |
| `EMBEDDING_MODEL` | `granite-embedding-107m` | Served by CrispEmbed (`bge-m3` is the gate fallback) |
| `QDRANT_URL` | `http://localhost:6333` | `http://qdrant:6333` inside compose |
| `QDRANT_LOCAL_PATH` | empty | File path or `:memory:`, no server needed |
| `EMBEDDING_DIM` | `384` | granite-embedding-107m dimension (`1024` on the bge-m3 fallback) |
| `CRISPEMBED_URL` | `http://localhost:8080` | `http://crispembed:8080` inside compose |
| `DATABASE_URL` | `sqlite+aiosqlite:///./matters.db` | `sqlite+aiosqlite:///./data/matters.db` in Docker; also holds the LLM config, see below |
| `MATTER_PRIVACY_MODE` | `strict` | `strict` blocks external providers for matter evidence |
| `LIBRARY_VERSION` | `1.0.0` | Returned by `/health` and coverage |
| `STORAGE_DIR` | `./storage` | `FASL_STORAGE_DIR=/app/data/storage` in Docker; source of the legacy `llm_settings.json`, read once only |
| `UPLOAD_MAX_BYTES` | `20000000` | 20MB upload cap |
| `OCR_CONFIDENCE_THRESHOLD` | `0.6` | Flags low-confidence sections for review |
| `OCR_LANGUAGES` | `ara+fra` | Tesseract language data |

`NEXT_PUBLIC_API_BASE_URL` is baked at build time for prod (see `docker-compose.prod.yml`). Runtime env alone does not change it.

### LLM config storage

The four user-settable LLM fields — `provider`, `model`, `api_keys`, `base_urls` — persist in the SQLite
database at `DATABASE_URL` (`matters.db`), in three tables:

| Table | Holds |
|---|---|
| `llm_providers` | Provider registry: the 6 known names plus their `kind` (`local` or `external`) |
| `llm_provider_credentials` | Per-provider `api_key` and `base_url` |
| `llm_active_settings` | Single active row: `provider`, `model`, and the JSON import marker |

API keys are stored as plaintext in the database file, same as they were in the JSON store.

The legacy `<STORAGE_DIR>/llm_settings.json` is imported once, on first use, into those three tables. After
that import it is kept only as a backup and is never rewritten by the application, so hand-editing the file
has no effect.

`OPENROUTER_API_KEY` and the other `*_API_KEY` vars win over the keys held in
`llm_provider_credentials`: a non-empty env value shadows the stored one
(`get_agent`, `backend/app/infrastructure/llm/agent.py:175-183`), so a stale or dead env key shadows a valid
stored key. Leave a var empty to fall through to the database.

`MATTER_PRIVACY_MODE` stays env-only and is not stored in these tables.

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
  app/                    # layered packages (a layer may import only layers below it)
    api/v1/               # HTTP routers: chat, search, library, conversations, drafts, documents, matters, settings, analysis
    cli/library/          # bulk CLI bounded context (python -m app.cli.library.bulk)
    services/             # orchestration: chat, search, ingestion, library_seed, library_coverage
    infrastructure/       # external clients: qdrant, llm, embeddings, ocr, rerank + authority/ingestion adapters
    repositories/         # SQL queries per aggregate: matter, document, conversation, analysis, draft, settings, library_import
    models/               # SQLAlchemy models (unchanged schema)
    domain/               # pure rules (no framework/SDK imports): analysis, authority, ingestion, search + citations/privacy/prompts/rerank/drafts
    schemas/              # pydantic request/response models, one module per router + provider catalog
    config/               # Settings (single env source), resolver, BACKEND_ROOT anchor
  alembic/                # migrations
  tests/                  # per-domain suites
frontend/                 # Next.js app (moroccan-legal-rag-frontend)
  src/app/                # page.tsx (workspace), library/page.tsx
  src/components/         # chat, upload, panels, inspector, palette
  src/lib/                # api.ts SSE client
  e2e/                    # library-first.spec.ts
qdrant/                   # Qdrant service build
crispembed/               # CrispEmbed granite-embedding-107m service build
tests/test_compose.py     # live compose tests
e2e/seed_test_authority.py
docker-compose.yml        # dev-first base stack
docker-compose.prod.yml   # prod overlay
.env.example / .env
Makefile                  # dev, dev-local, up, down, logs, test, build, clean
```

### Backend layer rules (enforced by `uv run lint-imports` from `backend/`)

Each layer may import only the layers below it, top to bottom:
`api > cli > services > infrastructure > repositories > models > domain`.

- `cli` sits directly under `api`: the bulk CLI may import everything except `api`.
- `domain` is bottom and pure: no `fastapi`, `sqlalchemy`, `pydantic`, provider SDKs, or document/OCR libraries (forbidden contract; `ruff` TID251 additionally bans relative imports climbing past the parent package).
- `schemas/` (transport models) and `config/` (settings) sit outside the layer order.
- One contracted exception: `infrastructure.embeddings.client -> cli.library.bulk_state`, a function-local deferred ledger write during bulk runs.

### Bulk CLI entrypoint

```bash
cd backend && uv run python -m app.cli.library.bulk {catalog,extract,migrate,reembed-matter,embed,index,run}
# or from the repo root: make bulk-catalog | bulk-extract | bulk-run | bulk-status
```

### Chat paths

`POST /api/v1/chat` (SSE: `citations`, then `token`, then `done`) is the supported path. The WebSocket `/api/v1/chat/stream` handler in `app/api/v1/chat.py` is legacy: the frontend does not use it.
