"""Bulk resilient embedder tests (todo 14, TDD).

Given: CrispEmbed faked with respx/httpx fakes.
When: the client embeds batches / hits 5xx / timeout / 4xx / quote text.
Then: batching, retry-only-on-transport, payload sanitize, ledger-after-success,
       ETA from local chars, never success-on-failure.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.cli.library.bulk_state import set_chunk_status
from app.infrastructure.embeddings import client as embedder_mod


class _Chunk:
    def __init__(self, chunk_id: str) -> None:
        self.chunk_id = chunk_id
        self.status = "pending"


def _vec_payload(n: int, dim: int = 4):
    return {
        "data": [{"embedding": [0.1] * dim} for _ in range(n)],
        "usage": {"prompt_tokens": 0},
    }


def _client():
    return embedder_mod.CrispEmbedClient(
        base_url="http://crispembed-test:8080", model="test-model"
    )


@respx.mock
@pytest.mark.asyncio
async def test_batch_splitting_n_texts_into_ceil_requests(monkeypatch):
    monkeypatch.setattr(embedder_mod.settings, "LIBRARY_EMBED_BATCH_TEXTS", 2)
    route = respx.post("http://crispembed-test:8080/v1/embeddings").mock(
        side_effect=[
            httpx.Response(200, json=_vec_payload(2)),
            httpx.Response(200, json=_vec_payload(2)),
            httpx.Response(200, json=_vec_payload(1)),
        ]
    )
    out = await _client().embed(["a", "b", "c", "d", "e"])
    assert len(out) == 5
    assert route.call_count == 3  # ceil(5/2)


@respx.mock
@pytest.mark.asyncio
async def test_backoff_on_5xx_then_success(monkeypatch):
    monkeypatch.setattr(embedder_mod.settings, "LIBRARY_EMBED_BATCH_TEXTS", 32)
    route = respx.post("http://crispembed-test:8080/v1/embeddings").mock(
        side_effect=[
            httpx.Response(500, json={"error": "boom"}),
            httpx.Response(200, json=_vec_payload(1)),
        ]
    )
    out = await _client().embed(["hello"])
    assert len(out) == 1
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_backoff_on_timeout_then_success(monkeypatch):
    monkeypatch.setattr(embedder_mod.settings, "LIBRARY_EMBED_BATCH_TEXTS", 32)
    route = respx.post("http://crispembed-test:8080/v1/embeddings").mock(
        side_effect=[
            httpx.TimeoutException("hung server"),
            httpx.Response(200, json=_vec_payload(1)),
        ]
    )
    out = await _client().embed(["hello"])
    assert len(out) == 1
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_no_retry_on_4xx():
    route = respx.post("http://crispembed-test:8080/v1/embeddings").mock(
        return_value=httpx.Response(400, json={"error": "bad request"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await _client().embed(["hello"])
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_request_payload_sanitized_stored_text_untouched():
    seen: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen.append(_json.loads(request.content.decode()))
        return httpx.Response(200, json=_vec_payload(1))

    respx.post("http://crispembed-test:8080/v1/embeddings").mock(side_effect=_handler)
    raw = 'قال "المادة" [1] ] test'
    out = await _client().embed([raw])
    assert len(out) == 1
    sent = seen[0]["input"][0]
    assert '"' not in sent and "]" not in sent  # payload sanitized
    assert raw != sent  # stored text untouched (caller copy unchanged)
    assert '"' in raw and "]" in raw


@respx.mock
@pytest.mark.asyncio
async def test_ledger_written_only_after_success(monkeypatch):
    monkeypatch.setattr(embedder_mod.settings, "LIBRARY_EMBED_BATCH_TEXTS", 32)
    route = respx.post("http://crispembed-test:8080/v1/embeddings").mock(
        side_effect=[
            httpx.Response(500, json={"error": "boom"}),
            httpx.Response(200, json=_vec_payload(2)),
        ]
    )
    rows = [_Chunk("c1"), _Chunk("c2")]
    out = await _client().embed_with_ledger(["t1", "t2"], chunk_rows=rows)
    assert len(out) == 2
    assert route.call_count == 2
    assert [r.status for r in rows] == ["embedded", "embedded"]


@respx.mock
@pytest.mark.asyncio
async def test_repeated_failure_never_marks_embedded():
    respx.post("http://crispembed-test:8080/v1/embeddings").mock(
        return_value=httpx.Response(500, json={"error": "down"})
    )
    rows = [_Chunk("c1")]
    with pytest.raises(Exception):
        await _client().embed_with_ledger(["t1"], chunk_rows=rows)
    assert rows[0].status == "pending"  # never "embedded"
    # file-stage mirror of pipeline.py:165-171 (no success claim on failure)
    assert rows[0].status != "embedded"


def test_eta_from_local_chars_server_reports_zero():
    eta = embedder_mod.estimate_eta_seconds(
        chars_done=1000, chars_total=4000, elapsed_s=10.0
    )
    assert eta == pytest.approx(30.0)
    assert embedder_mod.estimate_eta_seconds(0, 4000, 0.0) is None


def test_set_chunk_status_ledger_api_marks_embedded():
    row = _Chunk("c9")
    set_chunk_status(row, "embedded")
    assert row.status == "embedded"
