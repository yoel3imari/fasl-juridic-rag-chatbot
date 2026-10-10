"""The ONE authoritative served-token budget for embedding input.

Why this module exists
----------------------
The budget used to be DUPLICATED in the CLI layer
(``app.cli.library.quality_gate``), while the chunker enforced a different
and LOOSER cap: the raw Granite tokenizer cap ``MAX_GRANITE_TOKENS = 512``
(``app.domain.authority.granite_tokens``). The two caps disagreed, and the
disagreement was silent -- chunks were legally created and then thrown away:

* ``article_split`` + ``extractor`` split article bodies so that
  ``count_granite_tokens(text) <= 512``;
* the embed stage then DISCARDED any chunk whose served estimate exceeded
  ``450``, marking it ``failed`` and never sending it to the server.

Every chunk in the 451..512 band was therefore born legal by the chunker's
own contract and then silently dropped: 8,390 of 13,445 created chunks on
the live ledger (62% silent data loss).

The fix is single-sourcing. The chunker splits to THIS budget
(``app.domain.authority.article_split``) and the embed stage measures
against the same one (``app.cli.library.bulk_run``), so no chunk can ever be
born over budget and no legal work is ever thrown away. Measurement is pure
domain code with no framework/CLI/infrastructure imports, and it always uses
the REAL Granite tokenizer -- never a chars/4 proxy.
"""

from __future__ import annotations

from app.domain.authority.granite_tokens import count_granite_tokens

# Served-context safety: CrispEmbed-Granite DROPS the connection (no
# server-side truncation, and a killer input can crash-loop the server) for
# inputs past ~512 served tokens. The served tokenization counts newlines
# much heavier than the local tokenizer: empirically, a chunk at 426 local
# tokens with 67 newlines (naive local+newlines estimate = 493) dropped the
# server, so each newline is weighted x4. Private-use (Co) characters were
# isolated as a suspect and EXONERATED (all Co chunks embed fine).
# A second killer family (2026-09-30, file id 110 p5:o6 + p19:o30):
# OCR-spaced tatweel artifacts (e.g. "م ـصـ ـفـن،ة", "ـكـسـ ال ك ـ ال ـح ـ دي")
# tokenize HEAVIER server-side than the HF snapshot counts locally --
# local 491/499 with zero newlines still crashed the server (underestimate
# >=21 tokens). The cap therefore carries a ~60-token safety margin below
# the 512 served limit instead of sitting at 500.
# Overlong input is never truncated (both models must see identical text);
# the splitter cuts to this budget instead, and the embed stage refuses
# loudly rather than silently dropping what it is handed.
SERVED_SAFE_TOKENS: int = 450
NEWLINE_WEIGHT: int = 4


def served_estimate(text: str) -> int:
    """Conservative served-token estimate: local tokens + weighted newlines."""
    return count_granite_tokens(text) + NEWLINE_WEIGHT * text.count("\n")


def fits_served_budget(text: str) -> bool:
    """True when ``text`` may be sent to the embedding server as-is."""
    return served_estimate(text) <= SERVED_SAFE_TOKENS


class ServedBudgetExceeded(RuntimeError):
    """A chunk could not be split into the served budget.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` handlers
    keep catching it. Callers that can degrade gracefully MUST catch this
    type, never match on the message text: the wording is documentation, and
    matching on it silently disables the fallback the moment the message is
    reworded.
    """
