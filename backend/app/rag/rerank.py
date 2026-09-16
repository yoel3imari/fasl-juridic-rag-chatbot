"""Dual-domain RAG helpers: per-domain FlashRank rerank + MMR diversity."""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# Tunable at module level so tests/deploys can override without branching.
RANKER_MODEL_NAME: str = "ms-marco-TinyBERT-L-2-v2"
RANKER_CACHE_DIR: str = "/tmp"

_RANKER: Any | None = None
_RANKER_FAILED: bool = False


def reset_ranker() -> None:
    """Test seam: drop the cached ranker / failure flag."""
    global _RANKER, _RANKER_FAILED
    _RANKER = None
    _RANKER_FAILED = False


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


def rerank(
    query: str, items: list[dict[str, Any]], top_k: int = 10
) -> list[dict[str, Any]]:
    """Rerank one domain's retrieval hits; passthrough (sliced) on any failure."""
    if not items:
        return []
    ranker = get_ranker()
    if ranker is None:
        return list(items)[:top_k]
    try:
        from flashrank import RerankRequest

        passages = [
            {"id": i, "text": str(item.get("text", ""))} for i, item in enumerate(items)
        ]
        ranked = ranker.rerank(RerankRequest(query=query, passages=passages))
        ordered: list[dict[str, Any]] = []
        for entry in ranked:
            idx = entry.get("id") if isinstance(entry, dict) else None
            if isinstance(idx, int) and 0 <= idx < len(items):
                ordered.append(items[idx])
        if not ordered:
            raise ValueError("reranker returned no usable ids")
        return ordered[:top_k]
    except Exception as exc:
        logger.warning("flashrank rerank failed, keeping retrieval order: %s", exc)
        return list(items)[:top_k]


def _tokens(text: str) -> frozenset[str]:
    return frozenset(text.lower().split())


def mmr_select(
    items: list[dict[str, Any]], top_k: int = 10, lambda_mult: float = 0.5
) -> list[dict[str, Any]]:
    """Maximal-marginal-relevance diversity over one domain (authority).

    Relevance proxy is the input rank (1/(rank+1)) — an internal fusion
    signal, never rendered as an authority rank. Similarity is Jaccard
    over token sets. Pure function, no I/O.
    """
    if len(items) <= top_k:
        return list(items)
    token_sets = [_tokens(str(item.get("text", ""))) for item in items]
    selected: list[int] = []
    remaining = set(range(len(items)))
    while remaining and len(selected) < top_k:
        best, best_score = -1, -math.inf
        for idx in remaining:
            relevance = 1.0 / (idx + 1)
            redundancy = 0.0
            for sel in selected:
                union = token_sets[idx] | token_sets[sel]
                sim = (
                    len(token_sets[idx] & token_sets[sel]) / len(union)
                    if union
                    else 0.0
                )
                redundancy = max(redundancy, sim)
            score = lambda_mult * relevance - (1.0 - lambda_mult) * redundancy
            if score > best_score:
                best, best_score = idx, score
        selected.append(best)
        remaining.discard(best)
    return [items[i] for i in selected]
