"""Served-budget splitting for a single article body.

The split target is the SERVED budget
(:data:`app.domain.authority.served_budget.SERVED_SAFE_TOKENS`, 450 served
tokens) -- NOT the raw Granite tokenizer cap (512). That is deliberate: the
embed stage measures the same served budget, so splitting to the looser raw
cap means chunks are born "legal" here and then DISCARDED at embed time,
which is exactly the disagreement that silently dropped 62% of the corpus
chunks. Splitting to the served budget makes an over-budget chunk
impossible to create, so the embed stage never has to discard work.

One piece when the body fits the served budget; otherwise greedily packed
sentence pieces where every continuation starts with a GRANITE_OVERLAP_TOKENS
overlap of the previous piece. No content is ever dropped: an over-budget
sentence is hard-cut on the real tokenizer's ids (an explicit, linked split
-- never silent truncation). Linkage labels (``continued_from``) are assigned
by the caller (``extractor.split_statute_structure``).
"""

from __future__ import annotations

import re

from app.domain.authority.granite_tokens import granite_overlap_prefix, granite_tokenizer
from app.domain.authority.served_budget import SERVED_SAFE_TOKENS, fits_served_budget

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!؟?])\s+|\n+")


def _hard_cut(text: str) -> list[str]:
    """Cut one over-budget sentence into pieces that each fit the served budget.

    Walks the REAL Granite tokenizer's ids and grows each window only while
    ``fits_served_budget`` holds for the decoded candidate, so every emitted
    piece is legal for the embed stage. Nothing is ever dropped: a window is
    a contiguous slice of the ids, every id lands in exactly one piece, and
    each piece takes at least one id (so the loop always advances) -- an
    explicit, linked split, never silent truncation.
    """
    tok = granite_tokenizer()
    ids = tok.encode(text, add_special_tokens=False).ids

    def fits(window: list[int]) -> bool:
        return fits_served_budget(tok.decode(window))

    out: list[str] = []
    start = 0
    while start < len(ids):
        # One id alone is orders of magnitude below the budget, so "at least
        # one id per piece" always fits and always advances the cursor. The
        # raw cap is only the SEARCH ceiling: every candidate is measured with
        # the real tokenizer, so hitting the ceiling early costs one extra
        # piece, never correctness.
        lo = start + 1
        hi = min(len(ids), start + SERVED_SAFE_TOKENS)
        end = start + 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits(ids[start:mid]):
                end = mid
                lo = mid + 1
            else:
                hi = mid - 1
        piece = tok.decode(ids[start:end]).strip()
        if piece:
            out.append(piece)
        start = end
    return out or [text]


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _pack_pieces(sentences: list[str], first_prefix: str = "") -> list[str]:
    """Pack sentences into pieces that each fit the served budget.

    ``first_prefix`` is overlap context carried from the previous piece. It is
    attached to the first emitted piece only when the result still fits, and is
    consumed there. It is NEVER emitted on its own: a chunk holding nothing but
    the previous piece's tail carries no new information, yet it would still be
    embedded and indexed and would compete with that piece for a retrieval
    slot. Packing therefore works on sentences alone, so every emitted piece
    contains at least one sentence of source text.
    """
    pieces: list[str] = []
    current: list[str] = []
    prefix = first_prefix

    def emit(body: str) -> None:
        nonlocal prefix
        if not body:
            return
        if prefix:
            with_prefix = f"{prefix} {body}".strip()
            if fits_served_budget(with_prefix):
                body = with_prefix
            prefix = ""
        pieces.append(body)

    for sent in sentences:
        if not fits_served_budget(sent):
            emit(" ".join(current).strip())
            current = []
            pieces.extend(_hard_cut(sent))
            continue
        cand = " ".join([*current, sent]).strip()
        if current and not fits_served_budget(cand):
            emit(" ".join(current).strip())
            current = []
        current.append(sent)
    emit(" ".join(current).strip())
    return pieces


def split_article(body: str) -> list[str]:
    """Split one article body; single piece when it fits the served budget, else linked pieces."""
    if fits_served_budget(body):
        return [body]
    pieces = _pack_pieces(_split_sentences(body))
    if len(pieces) < 2:
        return pieces
    linked: list[str] = [pieces[0]]
    for piece in pieces[1:]:
        prefix = granite_overlap_prefix(linked[-1])
        if prefix and prefix not in piece:
            linked.extend(_pack_pieces(_split_sentences(piece), first_prefix=prefix))
        else:
            linked.append(piece)
    return linked
