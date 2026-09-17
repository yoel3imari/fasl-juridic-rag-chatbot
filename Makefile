.PHONY: dev dev-local up down logs test build clean

# Full local stack (task 11): fastapi :8000, nextjs :3000, qdrant :6333,
# crispembed :8080. First boot builds CrispEmbed from source + pulls the
# bge-m3 GGUF — allow extra time once; restarts reuse the model volume.
dev:
	docker compose --env-file .env.docker up --build -d

# Old behavior: run backend + frontend locally without containers.
dev-local:
	cd backend && uv run uvicorn app.main:app --reload
	cd frontend && npm run dev

up:
	docker compose --env-file .env.docker up -d

down:
	docker compose down

logs:
	docker compose logs -f

test:
	cd backend && uv run pytest -q
	cd frontend && npm run test -- --run
	cd backend && FASL_LIVE_COMPOSE=1 uv run pytest ../tests/test_compose.py -v

build:
	cd frontend && npm run build

clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	rm -rf backend/.venv frontend/.next frontend/node_modules
