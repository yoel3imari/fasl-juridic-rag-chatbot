"""CrispEmbed embedding client (bge-m3) via HTTP. No Python/ONNX embedding locally.

Bulk-resilient extension (todo 14): bounded batched requests with the
``LIBRARY_EMBED_*`` settings, exponential backoff on TRANSPORT errors only
(5xx / timeout / connection -- never 4xx), ``"``/``]`` sanitization of the
REQUEST payload copy only (stored text untouched), per-chunk ledger writes
only after each successful response, and ETA from local char counts (the
server reports 0 prompt tokens).

Never claims success on failure (mirror of
``app.services.ingestion._embed_and_index`` / ``services/ingestion.py:165-171``):
repeated transport failure raises and leaves chunk rows ``pending``.

No parallel embed calls: the server serializes, so batches go sequentially.
"""

from __future__ import annotations

import anyio
from typing import Any, Sequence

import httpx
from app.config import settings

MAX_CHARS_PER_REQUEST = 24_000
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 0.05


def sanitize_request_text(text: str) -> str:
    """Sanitize ONE request-payload copy for the CrispEmbed naive string parser.

    The caller's stored text is never mutated; sanitize the copy sent on the
    wire only.
    """
    return text.replace('"', "'").replace("]", ")")


def split_batches(
    texts: Sequence[str], *, max_texts: int, max_chars: int = MAX_CHARS_PER_REQUEST
) -> list[tuple[int, int]]:
    """Plan ``(start, end)`` slices: <= ``max_texts`` texts AND <= ``max_chars`` chars each.

    Char bound uses the SANITIZED length (what actually goes on the wire).
    Never silently truncates: over-long single texts still get their own
    batch (server-side behavior unchanged), they are never dropped.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    chars = 0
    for i, text in enumerate(texts):
        size = len(sanitize_request_text(text))
        if i > start and (i - start >= max_texts or chars + size > max_chars):
            spans.append((start, i))
            start = i
            chars = 0
        chars += size
    if start < len(texts):
        spans.append((start, len(texts)))
    return spans


def estimate_eta_seconds(
    chars_done: int, chars_total: int, elapsed_s: float
) -> float | None:
    """ETA from LOCAL char counts; the server reports 0 prompt tokens."""
    if chars_done <= 0 or elapsed_s <= 0 or chars_total <= chars_done:
        return None
    rate = chars_done / elapsed_s
    return (chars_total - chars_done) / rate


def _is_retryable(exc: Exception, resp: httpx.Response | None = None) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = getattr(getattr(exc, "response", None), "status_code", 0)
        return 500 <= status < 600
    if resp is not None and 500 <= resp.status_code < 600:
        return True
    return False


class CrispEmbedClient:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        import os

        self.base_url = (
            base_url or os.getenv("CRISPEMBED_URL", "http://localhost:8080")
        ).rstrip("/")
        self.model = model or settings.EMBEDDING_MODEL

    @property
    def _timeout(self) -> float:
        return float(settings.LIBRARY_EMBED_TIMEOUT_SECONDS)

    @property
    def _batch_texts(self) -> int:
        return int(settings.LIBRARY_EMBED_BATCH_TEXTS)

    async def _post_one(
        self, client: httpx.AsyncClient, batch: list[str]
    ) -> list[list[float]]:
        last: BaseException | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={"input": batch, "model": self.model},
                )
                if 500 <= resp.status_code < 600:
                    last = httpx.HTTPStatusError(
                        f"server error {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                else:
                    resp.raise_for_status()  # 4xx raises here and is NOT retried
                    data = resp.json()
                    vecs = [item["embedding"] for item in data.get("data", [])]
                    if len(vecs) != len(batch):
                        raise RuntimeError(
                            "embedding count mismatch: "
                            f"{len(vecs)} vectors for {len(batch)} texts"
                        )
                    return vecs
            except httpx.HTTPStatusError as exc:
                last = exc
                if not _is_retryable(exc):
                    raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            if attempt < MAX_ATTEMPTS:
                await anyio.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        assert last is not None
        raise last

    def _post_one_sync(
        self, client: httpx.Client, batch: list[str]
    ) -> list[list[float]]:
        import time

        last: BaseException | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={"input": batch, "model": self.model},
                )
                if 500 <= resp.status_code < 600:
                    last = httpx.HTTPStatusError(
                        f"server error {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                else:
                    resp.raise_for_status()
                    data = resp.json()
                    vecs = [item["embedding"] for item in data.get("data", [])]
                    if len(vecs) != len(batch):
                        raise RuntimeError(
                            "embedding count mismatch: "
                            f"{len(vecs)} vectors for {len(batch)} texts"
                        )
                    return vecs
            except httpx.HTTPStatusError as exc:
                last = exc
                if not _is_retryable(exc):
                    raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        assert last is not None
        raise last

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed sequentially in bounded batches; raise on failure (no partial success claim)."""
        if not texts:
            return []
        out: list[list[float]] = []
        spans = split_batches(texts, max_texts=self._batch_texts)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for start, end in spans:
                batch = [sanitize_request_text(t) for t in texts[start:end]]
                out.extend(await self._post_one(client, batch))
        return out

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        spans = split_batches(texts, max_texts=self._batch_texts)
        with httpx.Client(timeout=self._timeout) as client:
            for start, end in spans:
                batch = [sanitize_request_text(t) for t in texts[start:end]]
                out.extend(self._post_one_sync(client, batch))
        return out

    async def embed_with_ledger(
        self, texts: list[str], chunk_rows: Sequence[Any] | None = None
    ) -> list[list[float]]:
        """Embed and mark each chunk row ``embedded`` ONLY after its batch succeeds.

        Uses the todo-7 ledger API (``set_chunk_status``); rows stay
        ``pending`` on failure -- success is never claimed on failure.
        """
        from app.cli.library.bulk_state import set_chunk_status

        if not texts:
            return []
        rows = list(chunk_rows) if chunk_rows is not None else None
        if rows is not None and len(rows) != len(texts):
            raise ValueError(f"{len(rows)} chunk rows for {len(texts)} texts")
        out: list[list[float]] = []
        spans = split_batches(texts, max_texts=self._batch_texts)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for start, end in spans:
                batch = [sanitize_request_text(t) for t in texts[start:end]]
                vecs = await self._post_one(client, batch)
                if rows is not None:
                    for row in rows[start:end]:
                        set_chunk_status(row, "embedded")
                out.extend(vecs)
        return out

    def embed_sync_with_ledger(
        self, texts: list[str], chunk_rows: Sequence[Any] | None = None
    ) -> list[list[float]]:
        from app.cli.library.bulk_state import set_chunk_status

        if not texts:
            return []
        rows = list(chunk_rows) if chunk_rows is not None else None
        if rows is not None and len(rows) != len(texts):
            raise ValueError(f"{len(rows)} chunk rows for {len(texts)} texts")
        out: list[list[float]] = []
        spans = split_batches(texts, max_texts=self._batch_texts)
        with httpx.Client(timeout=self._timeout) as client:
            for start, end in spans:
                batch = [sanitize_request_text(t) for t in texts[start:end]]
                vecs = self._post_one_sync(client, batch)
                if rows is not None:
                    for row in rows[start:end]:
                        set_chunk_status(row, "embedded")
                out.extend(vecs)
        return out
