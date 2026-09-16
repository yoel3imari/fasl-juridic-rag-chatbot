"""Lexical sparse vectors: deterministic TF-hash stand-in for the BM25 side.

Honest naming: this is NOT server-side BM25 with IDF — it is a TF-weighted
token-hash sparse vector computed identically at index and query time, fused
with the dense side via RRF. Same function runs in prod and in tests, so the
lexical code path is genuinely exercised either way.
"""

from __future__ import annotations

import hashlib
import re

SPARSE_DIM: int = 2048

_TOKEN_RE = re.compile(r"[0-9a-z\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff\u0600-\u06ff]+")


def tokenize(text: str) -> list[str]:
    """Lowercase unicode word tokens (covers Arabic, French, Latin)."""
    return _TOKEN_RE.findall(text.lower())


def _index(token: str) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % SPARSE_DIM


def sparse_indices_values(text: str) -> tuple[list[int], list[float]]:
    """TF-weighted (index, value) pairs, indices sorted ascending."""
    counts: dict[int, float] = {}
    for token in tokenize(text):
        idx = _index(token)
        counts[idx] = counts.get(idx, 0.0) + 1.0
    order = sorted(counts)
    return order, [counts[i] for i in order]


def sparse_vector(text: str):
    """Qdrant SparseVector for query/upsert use."""
    from qdrant_client.models import SparseVector

    indices, values = sparse_indices_values(text)
    return SparseVector(indices=indices, values=values)
