.PHONY: dev test build clean

dev:
	cd backend && uv run uvicorn app.main:app --reload
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -v
	cd frontend && npm test

build:
	cd frontend && npm run build

clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	rm -rf backend/.venv frontend/.next frontend/node_modules
