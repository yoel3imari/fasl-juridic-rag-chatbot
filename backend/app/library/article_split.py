"""Token-capped splitting for a single article body.

One piece when the body fits MAX_GRANITE_TOKENS; otherwise greedily packed
sentence pieces where every continuation starts with a GRANITE_OVERLAP_TOKENS
overlap of the previous piece. No content is ever dropped: an over-cap
sentence is hard-cut by token window (an explicit, linked split -- never
silent truncation). Linkage labels (``continued_from``) are assigned by the
caller (``extractor.split_statute_structure``).
"""

from __future__ import annotations

import re

from app.library.granite_tokens import (
    MAX_GRANITE_TOKENS,
    count_granite_tokens,
    granite_overlap_prefix,
    granite_tokenizer,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!؟?])\s+|\n+")


def _hard_cut(text: str) -> list[str]:
    """Cut text (a single over-cap sentence) into <= cap token windows."""
    tok = granite_tokenizer()
    ids = tok.encode(text, add_special_tokens=False).ids
    room = MAX_GRANITE_TOKENS - 2  # reserve <s> </s>
    out: list[str] = []
    for i in range(0, len(ids), room):
        piece = tok.decode(ids[i : i + room]).strip()
        if piece:
            out.append(piece)
    return out or [text]


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _pack_pieces(sentences: list[str], first_prefix: str = "") -> list[str]:
    """Pack sentences into <= MAX_GRANITE_TOKENS pieces, starting with first_prefix."""
    pieces: list[str] = []
    current: list[str] = [first_prefix] if first_prefix else []
    for sent in sentences:
        if count_granite_tokens(sent) > MAX_GRANITE_TOKENS:
            if " ".join(current).strip():
                pieces.append(" ".join(current).strip())
                current = []
            pieces.extend(_hard_cut(sent))
            continue
        cand = " ".join([*current, sent]).strip()
        if current and count_granite_tokens(cand) > MAX_GRANITE_TOKENS:
            pieces.append(" ".join(current).strip())
            current = [sent]
        else:
            current = [*current, sent]
    if " ".join(current).strip():
        pieces.append(" ".join(current).strip())
    return pieces


def split_article(body: str) -> list[str]:
    """Split one article body; single piece when it fits, else linked pieces."""
    if count_granite_tokens(body) <= MAX_GRANITE_TOKENS:
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
