"""Granite token measurement with the ACTUAL served tokenizer.

Counts are post-tokenization lengths INCLUDING special tokens
(``<s>``/``</s>``), i.e. exactly what the embedding server sees -- never a
chars/4 proxy. The cap is 512 tokens (Granite
``max_position_embeddings=514``).

Raises RuntimeError when the tokenizer is unavailable: callers must surface
that as a blocker (needs-human-review), never substitute a proxy.
"""

from __future__ import annotations

import glob
import os

# Cap at 512 Granite tokens INCLUDING special tokens
# (Granite max_position_embeddings=514).
MAX_GRANITE_TOKENS: int = 512
# Token overlap carried from the previous piece when an article must split.
GRANITE_OVERLAP_TOKENS: int = 64
GRANITE_MODEL_ID = "ibm-granite/granite-embedding-107m-multilingual"

_tokenizer = None


def _tokenizer_json_candidates() -> list[str]:
    cands: list[str] = []
    for base in [
        os.environ.get("HF_HOME"),
        os.environ.get("HUGGINGFACE_HUB_CACHE"),
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub"),
    ]:
        if not base:
            continue
        slug = GRANITE_MODEL_ID.replace("/", "--")
        cands += glob.glob(
            os.path.join(base, f"models--{slug}", "snapshots", "*", "tokenizer.json")
        )
    return sorted(cands)


def granite_tokenizer():
    """Load the REAL Granite tokenizer (cached HF snapshot, offline)."""
    global _tokenizer
    if _tokenizer is not None:
        return _tokenizer
    try:
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Granite tokenizer unavailable: 'tokenizers' package not installed. "
            "Refusing to substitute a chars/4 proxy."
        ) from exc
    cands = _tokenizer_json_candidates()
    if not cands:
        raise RuntimeError(
            f"Granite tokenizer.json not found in HF cache for {GRANITE_MODEL_ID}. "
            "Refusing to substitute a chars/4 proxy."
        )
    _tokenizer = Tokenizer.from_file(cands[0])
    return _tokenizer


def count_granite_tokens(text: str) -> int:
    """Post-tokenization length INCLUDING special tokens (== served length)."""
    return len(granite_tokenizer().encode(text, add_special_tokens=True).ids)


def granite_overlap_prefix(prev_text: str) -> str:
    """Decode the last GRANITE_OVERLAP_TOKENS tokens of text (continuation overlap)."""
    tok = granite_tokenizer()
    ids = tok.encode(prev_text, add_special_tokens=False).ids
    return tok.decode(ids[-GRANITE_OVERLAP_TOKENS:]).strip()
