.DEFAULT_GOAL := help
.PHONY: help dev dev-local up down logs test build clean bulk-catalog bulk-extract bulk-run bulk-status

help: ## Display available targets
	@echo "Available targets:"
	@echo "  dev        - Full dev stack (fastapi :8000, nextjs :3000, qdrant :6333, crispembed :8080)"
	@echo "  dev-local  - Infra containers only (qdrant, crispembed); run backend/frontend locally"
	@echo "  up         - Prod stack (compose + prod overlay, detached)"
	@echo "  down       - Stop compose stack"
	@echo "  logs       - Follow compose logs"
	@echo "  test       - Backend + frontend + live compose tests"
	@echo "  build      - Build frontend"
	@echo "  clean      - Remove caches, venv, node_modules, .next"
	@echo "  bulk-catalog - Catalog the law shortlist into the SQLite ledger"
	@echo "  bulk-extract - Extract catalogued files to zstd artifacts"
	@echo "  bulk-run     - Resumable end-to-end embed+index orchestration"
	@echo "  bulk-status  - Ledger vs Qdrant count reconciliation report"

# Base compose (docker-compose.yml) is dev-first: dev targets, bind mounts,
# reload commands, develop.watch. Prod needs the overlay:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d
# Full dev stack: fastapi :8000, nextjs :3000, qdrant :6333,
# crispembed :8080. First boot builds CrispEmbed from source + pulls the
# granite-embedding-107m GGUF — allow extra time once; restarts reuse the model volume.
dev:
	docker compose up -d

# Local app code (backend/frontend run on host) + infra in containers.
# Add new infra services to this list; fastapi/nextjs stay local.
dev-local:
	docker compose --env-file .env up -d qdrant crispembed

build:
	docker compose --env-file .env build

up:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d

down:
	docker compose down

logs:
	docker compose logs -f

test:
	cd backend && uv run pytest -q
	cd frontend && npm run test -- --run
	cd backend && FASL_LIVE_COMPOSE=1 uv run pytest ../tests/test_compose.py -v

clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	rm -rf backend/.venv frontend/.next frontend/node_modules +
	docker compose down -v

# Bulk law-corpus ingest (plan law-corpus-bulk-ingest): resumable staged CLI.
# All three run from the repo root and delegate to the real backend CLI
# (`python -m app.cli.library.bulk <subcommand>`); pass LIMIT=20 for a subset.
bulk-catalog:
	cd backend && uv run python -m app.cli.library.bulk catalog $(if $(LIMIT),--limit $(LIMIT))

bulk-extract:
	cd backend && uv run python -m app.cli.library.bulk extract $(if $(LIMIT),--limit $(LIMIT))

bulk-run:
	cd backend && uv run python -m app.cli.library.bulk run $(if $(LIMIT),--limit $(LIMIT))

# Ledger-vs-Qdrant reconciliation: SQLite file/chunk stage counts, derived
# manifest entry count, live Qdrant points_count, and the served model/dim.
bulk-status:
	cd backend && uv run python -c "import sqlite3, json, urllib.request; \
	db = sqlite3.connect('data/matters.db'); \
	files = db.execute('select count(*), sum(case when index_status = \'indexed\' then 1 else 0 end) from library_import_files').fetchone(); \
	chunks = db.execute('select count(*), sum(case when status = \'indexed\' then 1 else 0 end) from library_import_chunks').fetchone(); \
	print(f'ledger files total={files[0]} indexed={files[1]}'); \
	print(f'ledger chunks total={chunks[0]} indexed={chunks[1]}'); \
	manifest = json.load(open('data/library-manifest.json')); \
	print(f\"manifest entries={len(manifest.get('entries', []))}\"); \
	q = json.load(urllib.request.urlopen('http://localhost:6333/collections/legal_authorities')); \
	print(f\"qdrant legal_authorities points_count={q['result']['points_count']}\"); \
	h = json.load(urllib.request.urlopen('http://localhost:8080/health')); \
	print(f\"crispembed dim={h.get('dim')}\")"
