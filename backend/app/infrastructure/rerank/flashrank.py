"""FlashRank rerank adapter: the model handle, its lazy globals, and the call.

Holds everything model-backed that used to live beside the pure MMR code in
`app/rag/rerank.py`. Degradation is unchanged and deliberate: if the model
cannot be loaded, or the call fails, the adapter returns an order-preserving
passthrough and logs that no rerank was claimed. It never fabricates scores.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.rerank import order_from_ranked, passthrough

logger = logging.getLogger(__name__)

# Tunable at module level so tests/deploys can override without branching.
RANKER_MODEL_NAME: str = "ms-marco-TinyBERT-L-2-v2"
RANKER_CACHE_DIR: str = "/tmp"

_RANKER: Any | None = None
_RANKER_FAILED: bool = False


def get_ranker() -> Any | None:
    """Build (once) the FlashRank ranker; None on ANY failure (honest fallback).

    Never raises, never fabricates scores — callers degrade to
    order-preserving passthrough when this returns None.
    """
    global _RANKER, _RANKER_FAILED
    if _RANKER is not None:
        return _RANKER
    if _RANKER_FAILED:
        return None
    try:
        from flashrank import Ranker

        _RANKER = Ranker(model_name=RANKER_MODEL_NAME, cache_dir=RANKER_CACHE_DIR)
        return _RANKER
    except Exception as exc:
        logger.warning(
            "flashrank unavailable (model=%r): %s — "
            "degrading to order-preserving passthrough, no rerank claimed",
            RANKER_MODEL_NAME,
            exc,
        )
        _RANKER_FAILED = True
        return None


def rerank(query: str, items: list[dict[str, Any]], top_k: int = 10) -> list[dict[str, Any]]:
    """Rerank one domain's retrieval hits; passthrough (sliced) on any failure."""
    if not items:
        return []
    ranker = get_ranker()
    if ranker is None:
        return passthrough(items, top_k)
    try:
        from flashrank import RerankRequest

        passages = [{"id": i, "text": str(item.get("text", ""))} for i, item in enumerate(items)]
        ranked = ranker.rerank(RerankRequest(query=query, passages=passages))
        return order_from_ranked(items, ranked, top_k)
    except Exception as exc:
        logger.warning("flashrank rerank failed, keeping retrieval order: %s", exc)
        return passthrough(items, top_k)
