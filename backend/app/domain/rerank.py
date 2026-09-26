"""Pure rerank helpers: MMR diversity and ranked-candidate ordering.

No model handle, no I/O, no third-party import. The FlashRank adapter that
produces the ranked candidates lives in
`app.infrastructure.rerank.flashrank` and calls into `order_from_ranked` here.
"""

from __future__ import annotations

import math
from typing import Any


def passthrough(items: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """Order-preserving fallback used whenever reranking is unavailable."""
    return list(items)[:top_k]


def order_from_ranked(
    items: list[dict[str, Any]], ranked: list[Any], top_k: int
) -> list[dict[str, Any]]:
    """Map a model's ranked candidates back onto the original items, in order.

    `ranked` is whatever the ranking model returned; only entries carrying a
    usable integer `id` are kept, so a malformed response degrades instead of
    silently reordering. Raises ValueError when nothing usable came back, which
    the adapter turns into a passthrough.
    """
    ordered: list[dict[str, Any]] = []
    for entry in ranked:
        idx = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(idx, int) and 0 <= idx < len(items):
            ordered.append(items[idx])
    if not ordered:
        raise ValueError("reranker returned no usable ids")
    return ordered[:top_k]


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
                sim = len(token_sets[idx] & token_sets[sel]) / len(union) if union else 0.0
                redundancy = max(redundancy, sim)
            score = lambda_mult * relevance - (1.0 - lambda_mult) * redundancy
            if score > best_score:
                best, best_score = idx, score
        selected.append(best)
        remaining.discard(best)
    return [items[i] for i in selected]
