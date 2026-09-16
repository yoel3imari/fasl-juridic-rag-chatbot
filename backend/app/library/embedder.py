"""CrispEmbed embedding client (bge-m3) via HTTP. No Python/ONNX embedding locally."""

from __future__ import annotations

import httpx
from app.config import settings


class CrispEmbedClient:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        import os

        self.base_url = (
            base_url or os.getenv("CRISPEMBED_URL", "http://localhost:8080")
        ).rstrip("/")
        self.model = model or settings.EMBEDDING_MODEL

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/v1/embeddings",
                json={"input": texts, "model": self.model},
            )
            resp.raise_for_status()
            data = resp.json()
        return [item["embedding"] for item in data.get("data", [])]

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self.base_url}/v1/embeddings",
                json={"input": texts, "model": self.model},
            )
            resp.raise_for_status()
            data = resp.json()
        return [item["embedding"] for item in data.get("data", [])]
