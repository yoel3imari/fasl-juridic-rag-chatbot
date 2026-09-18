.DEFAULT_GOAL := help
.PHONY: help dev dev-local up down logs test build clean

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

# Base compose (docker-compose.yml) is dev-first: dev targets, bind mounts,
# reload commands, develop.watch. Prod needs the overlay:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.docker up -d
# Full dev stack: fastapi :8000, nextjs :3000, qdrant :6333,
# crispembed :8080. First boot builds CrispEmbed from source + pulls the
# bge-m3 GGUF — allow extra time once; restarts reuse the model volume.
dev:
	docker compose --env-file .env.docker up -d

# Local app code (backend/frontend run on host) + infra in containers.
# Add new infra services to this list; fastapi/nextjs stay local.
dev-local:
	docker compose --env-file .env.docker up -d qdrant crispembed

build:
	docker compose --env-file .env.docker build

up:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.docker up -d

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
	rm -rf backend/.venv frontend/.next frontend/node_modules
