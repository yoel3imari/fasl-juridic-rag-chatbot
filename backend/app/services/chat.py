"""ReAct retrieval tools for the Fasl chat LLM.

The chat agent decides per query whether to answer directly (greetings,
smalltalk) or to call retrieval tools (legal questions), up to
MAX_TOOL_ROUNDS bounded rounds. Tool results flow through the existing
rerank → build_citations → assemble_prompt path in the chat route.

`matter` is optional: a message that is not about Moroccan law at all
never reaches retrieval — the intent gate (app.domain.intent) returns an
``out_of_scope`` verdict instead, which the route answers with the
legal-only clarification. Matterless conversations additionally lose the
matter-bearing tools and search authority only.

Design note (pydantic-ai 2.43.0): native tools (``Agent(tools=[...])`` /
``@agent.tool_plain``) ARE available and ARE registered by
``register_retrieval_tools`` / ``get_agent_with_tools``. ``run_envelope_loop``
drives a manual JSON tool-call envelope loop instead of
native auto-execution, because the SSE contract requires citations
BEFORE tokens, citation assembly needs Python-side rerank/MMR
(``rerank`` + ``mmr_select`` + ``build_citations``), and the privacy
guard (``check_privacy``) must run before any external provider call
carrying matter evidence. Native auto-execution would hide tool calls
inside the run and break all three. The envelope keeps every tool call
observable in Python while the registered native tools document the
same capabilities for direct agent use.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from app.domain.intent import (
    INTENT_RULE,
    OUT_OF_SCOPE_TOOL,
    build_classify_prompt,
    greeting_lang,
    is_greeting,
    is_out_of_scope,
)
from app.domain.language import detect_lang
from app.domain.prompts import greeting_reply
from app.infrastructure.llm import agent as agent_mod
from app.infrastructure.llm.errors import ProviderUnreachableError
from app.infrastructure.observability.metrics import metrics
from app.services import search as svc

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS: int = 3

# Context-window guard for the ReAct decision round. The prior-round summary is
# the only part of the decision prompt that can grow across rounds (the user
# query is fixed), so it is capped before every round. The value is in CHARS,
# not tokens: exact tokenization needs a model-specific tokenizer the chat
# service must not depend on, so `estimate_tokens` below uses the cheap
# chars-per-token heuristic instead. Callers may override per request through
# `Settings.REACT_PRIOR_SUMMARY_MAX_CHARS`; this constant is the fallback for
# direct (non-HTTP) callers such as tests.
DEFAULT_PRIOR_SUMMARY_MAX_CHARS: int = 1200
_CHARS_PER_TOKEN: int = 4


def estimate_tokens(text: str | None) -> int:
    """Cheap context-size estimate (~chars/4) — no tokenizer dependency.

    Deliberately approximate: the ReAct loop only needs a monotone proxy to
    decide when the decision prompt is growing toward the context window, not
    an exact count. The classic 4-chars-per-token ratio is a reasonable middle
    ground for the mixed French/Arabic/English prose this app streams.
    """
    return len(text or "") // _CHARS_PER_TOKEN


def cap_prior_summary(
    summary: str | None, max_chars: int = DEFAULT_PRIOR_SUMMARY_MAX_CHARS
) -> str | None:
    """Bound a prior-round summary to ``max_chars``, preserving the counts line.

    ``summarize_hits`` emits one short counts line followed by an
    ``Excerpts:`` block; when the whole summary overflows the budget the
    counts line is always kept and the excerpt block is cut, with an ellipsis
    marker so the model knows the context was truncated (rather than silently
    answering from a partial excerpt).
    """
    if not summary or max_chars <= 0 or len(summary) <= max_chars:
        return summary
    first_line, sep, rest = summary.partition("\n")
    ellipsis = " …"
    budget = max_chars - len(first_line) - len(sep) - len(ellipsis)
    if budget <= 0:
        return first_line[:max_chars]
    return f"{first_line}{sep}{rest[:budget].rstrip()}{ellipsis}"


# Log clamps. A query may carry matter evidence, so logs get a bounded preview
# instead of the full text; a preview is enough to explain any decision round.
_QUERY_PREVIEW_MAX: int = 80
_ERROR_PREVIEW_MAX: int = 300


def _preview(text: str | None, limit: int = _QUERY_PREVIEW_MAX) -> str:
    """First ``limit`` chars plus an ellipsis when clipped."""
    value = text if isinstance(text, str) else str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


SEARCH_MATTER: str = "search_matter"
SEARCH_AUTHORITY: str = "search_authority"
SEARCH_BOTH: str = "search_both"

# Retrieval tools hit the vector store; INTENT_TOOLS never do — an
# `out_of_scope` envelope is an intent-gate verdict, not a tool to execute.
RETRIEVAL_TOOLS: frozenset[str] = frozenset({SEARCH_MATTER, SEARCH_AUTHORITY, SEARCH_BOTH})
INTENT_TOOLS: frozenset[str] = frozenset({OUT_OF_SCOPE_TOOL})
VALID_TOOLS: frozenset[str] = RETRIEVAL_TOOLS | INTENT_TOOLS

TOOL_GUIDANCE: str = (
    "You are the FASL Moroccan legal research assistant. "
    "You have retrieval tools over private matter evidence and a versioned "
    "authority library. To retrieve context, reply with EXACTLY one line of "
    "JSON and nothing else, using this envelope:\n"
    '{"tool": "search_matter" | "search_authority" | "search_both", '
    '"query": "<focused search query>"}\n'
    "- search_matter: the question is about the user's own case documents.\n"
    "- search_authority: the question is about statutes, codes, or case law.\n"
    "- search_both: default for legal questions (both domains may help).\n"
    "If the message is a greeting or smalltalk, or needs no legal context, "
    "answer directly in 1-2 sentences mentioning FASL. "
    "Answer in the same language as the user's message (mirror their language: "
    "Arabic→Arabic, French→French, English→English). "
    "Never invent articles, sections, document ids, or citations. " + INTENT_RULE
)

# Matterless conversations (matter_id=None) have no private evidence scope, so
# the two matter-bearing tools are withdrawn from the decision prompt. Built per
# call — TOOL_GUIDANCE itself is never mutated.
_NO_MATTER_GUIDANCE: str = (
    'Matter context is unavailable for this conversation: "search_matter" '
    'and "search_both" are UNAVAILABLE — only "search_authority" may be used.'
)

# Matches the first {...} block containing a "tool" key (DOTALL for multiline).
_ENVELOPE_RE: re.Pattern[str] = re.compile(r"\{[^{}]*\"tool\"[^{}]*\}", re.DOTALL)


def build_decision_prompt(
    query: str, *, prior_summary: str | None = None, has_matter: bool = True
) -> str:
    """Prompt asking the LLM to choose: direct answer vs tool-call envelope.

    ``has_matter=False`` (a matterless conversation) additionally withdraws
    ``search_matter`` and ``search_both`` from the guidance, leaving only
    ``search_authority`` available.
    """
    guidance = TOOL_GUIDANCE if has_matter else f"{TOOL_GUIDANCE}\n{_NO_MATTER_GUIDANCE}"
    base = f"{guidance}\n\nUser message: {query}"
    if prior_summary:
        base += (
            "\n\nPrior tool results (do NOT repeat the same call; refine the "
            f"query or answer):\n{prior_summary}"
        )
    return base


def _is_valid_tool_call(data: Any) -> dict[str, str] | None:
    """Validate one parsed candidate into a normalized tool-call envelope.

    Returns ``{"tool": ..., "query": ...}`` when ``data`` is a dict carrying a
    known tool name and a non-empty string query, else None. Extracted so the
    direct-JSON path and the regex-extraction path in :func:`parse_tool_call`
    share exactly one rule set — a new valid tool is honoured in both places
    automatically.
    """
    if not isinstance(data, dict):
        return None
    tool = data.get("tool")
    query = data.get("query")
    if isinstance(tool, str) and tool in VALID_TOOLS and isinstance(query, str) and query.strip():
        return {"tool": tool, "query": query.strip()}
    return None


def parse_tool_call(text: str) -> dict[str, str] | None:
    """Parse a JSON tool-call envelope from LLM output.

    Two passes, in order: (1) the whole trimmed output as a direct JSON
    envelope — the common case for a compliant model; (2) the first
    ``{... "tool" ...}`` block found anywhere in the text, for models that wrap
    the envelope in prose or a JSON array. Malformed JSON in either pass is
    skipped, never fatal: a None return means "the LLM answered directly", so a
    broken envelope must never crash the ReAct loop.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    candidates: list[str] = [stripped]
    candidates.extend(m.group(0) for m in _ENVELOPE_RE.finditer(stripped))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        parsed = _is_valid_tool_call(data)
        if parsed is not None:
            return parsed
    return None


def summarize_hits(matter_hits: list[dict[str, Any]], authority_hits: list[dict[str, Any]]) -> str:
    """Compact prior-round summary so later rounds can refine, not repeat."""
    parts = [f"matter passages: {len(matter_hits)}, authority passages: {len(authority_hits)}."]
    excerpts: list[str] = []
    for hit in (matter_hits + authority_hits)[:4]:
        excerpts.append(str(hit.get("text", ""))[:250])
    if excerpts:
        parts.append("Excerpts:\n" + "\n---\n".join(excerpts))
    return "\n".join(parts)


@dataclass
class ToolContext:
    """Bound retrieval dependencies for one chat request (matter pre-filter kept).

    ``matter_id=None`` means a matterless conversation: no private evidence
    scope exists, so no matter-bearing store call may be issued.
    """

    store: svc.Store
    embedder: svc.Embedder
    matter_id: int | None
    top_k: int = 30


async def execute_tool_call(
    ctx: ToolContext, tool: str, query: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run one retrieval tool, returning raw (matter_hits, authority_hits).

    Matter isolation is preserved: matter_id is pre-filtered in the store,
    never post-hoc. With ``ctx.matter_id is None`` the matter-bearing halves
    are skipped outright — ``hybrid_query`` builds NO filter for a None
    matter_id, so reaching the store would return private evidence from
    EVERY matter. svc errors propagate unchanged; the HTTP mapping
    (embedding failure -> 503, store failure -> 500) lives in the chat
    route, not in this service.
    """
    if tool not in RETRIEVAL_TOOLS:
        # out_of_scope is an intent verdict handled by run_envelope_loop;
        # executing it as a retrieval tool is always a programming error.
        raise ValueError(f"not a retrieval tool: {tool}")
    if tool == SEARCH_MATTER:
        if ctx.matter_id is None:
            # Matter isolation: never reach the store without a scope filter.
            return [], []
        res = await svc.search_matter(
            store=ctx.store,
            embedder=ctx.embedder,
            matter_id=ctx.matter_id,
            query=query,
            top_k=ctx.top_k,
        )
        return list(res["matter"]), []
    if tool == SEARCH_AUTHORITY:
        res = await svc.search_authority(
            store=ctx.store, embedder=ctx.embedder, query=query, top_k=ctx.top_k
        )
        return [], list(res["authority"])
    if tool == SEARCH_BOTH:
        # Compose the two single-domain tools instead of duplicating their
        # store calls here. SEARCH_MATTER keeps the matter-isolation guard
        # (matter_id None → ([], []) with ZERO store calls), SEARCH_AUTHORITY
        # runs unscoped; a new domain rule added to either branch is inherited
        # automatically. Both halves are cached per domain in the svc layer, so
        # this issues the same two embed+store calls as before, in the same
        # order (matter first).
        matter_hits, _ = await execute_tool_call(ctx, SEARCH_MATTER, query)
        _, auth_hits = await execute_tool_call(ctx, SEARCH_AUTHORITY, query)
        return matter_hits, auth_hits
    raise ValueError(f"unknown retrieval tool: {tool}")


async def collect_text(agent: Any, prompt: str) -> str:
    """Run one agent round and collect its full text (decision rounds stay silent)."""
    parts: list[str] = []
    async with agent.run_stream(prompt) as result:
        async for chunk in result.stream_text(delta=True):
            parts.append(chunk)
    return "".join(parts)


# User-visible pipeline stages, in emission order. The SSE route shares this
# vocabulary so progressive status frames stay consistent across the loop's
# in-generator stages (retrieving) and the route's own (classifying,
# assembling, drafting).
STAGE_MESSAGES: dict[str, str] = {
    "classifying": "Classifying...",
    "retrieving": "Retrieving...",
    "assembling": "Assembling...",
    "drafting": "Drafting...",
}


@dataclass
class StageEvent:
    """One progressive pipeline stage, forwarded live to the SSE stream."""

    stage: str
    message: str


@dataclass
class EnvelopeOutcome:
    """Result of the bounded ReAct envelope loop for one chat request."""

    matter_raw: list[dict[str, Any]]
    auth_raw: list[dict[str, Any]]
    direct_text: str | None
    tool_rounds: int
    provider_error: str | None
    out_of_scope: bool = False


async def run_envelope_loop(
    *,
    query: str,
    agent: Any,
    ctx: ToolContext,
    provider: str,
    privacy_check: Callable[..., None],
    prior_summary_max_chars: int = DEFAULT_PRIOR_SUMMARY_MAX_CHARS,
) -> AsyncIterator[StageEvent | EnvelopeOutcome]:
    """Drive the bounded ReAct tool loop (max 3 tool rounds) as a stage stream.

    Yields :class:`StageEvent`s as the work they announce begins — a single
    ``retrieving`` event before the first tool call (deduped across rounds) —
    and finishes with the :class:`EnvelopeOutcome` carrying everything the
    route needs. The LLM either answers directly (no envelope → zero
    retrieval) or emits a JSON tool-call envelope per round. Decision rounds
    are collected silently; provider failures degrade into
    ``provider_error`` instead of raising. ``privacy_check`` runs before
    every decision round (privacy first, always) and may raise its domain
    errors (``PrivacyViolationError`` / ``ConsentRequiredError``), as may
    tool calls via the svc store/embedding errors — those propagate to the
    route, which converts them to in-stream SSE error frames.

    ``prior_summary_max_chars`` bounds the per-round prior-tool-results block
    (see :func:`cap_prior_summary`); the route passes
    ``Settings.REACT_PRIOR_SUMMARY_MAX_CHARS`` so deployments can tune the
    decision-prompt context budget.

    The intent gate is enforced twice, because a small model can bypass a
    prose instruction: (1) an ``out_of_scope`` envelope is honoured on
    round 1 only, and (2) a round-1 *prose* answer is re-checked by one
    constrained classification call before it may become ``direct_text``.
    """
    matter_raw: list[dict[str, Any]] = []
    auth_raw: list[dict[str, Any]] = []
    direct_text: str | None = None
    tool_rounds = 0
    provider_error: str | None = None
    prior_summary: str | None = None
    prior_summary_truncated = False
    out_of_scope = False
    has_matter = ctx.matter_id is not None
    logger.info(
        "envelope loop: start provider=%r has_matter=%s max_rounds=%d query=%r",
        _preview(provider),
        has_matter,
        MAX_TOOL_ROUNDS,
        _preview(query),
    )
    if is_greeting(query):
        logger.info("envelope loop: greeting bypass, no tool rounds")
        metrics.incr("chat.greeting.bypass")
        yield EnvelopeOutcome(
            matter_raw=[],
            auth_raw=[],
            direct_text=greeting_reply(greeting_lang(query) or detect_lang(query)),
            tool_rounds=0,
            provider_error=None,
            out_of_scope=False,
        )
        return
    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        decision_prompt = build_decision_prompt(
            query, prior_summary=prior_summary, has_matter=has_matter
        )
        privacy_check(decision_prompt, has_matter_evidence=bool(matter_raw))
        logger.info(
            "envelope loop: round %d/%d decision attempt (prior_summary=%s)",
            round_no,
            MAX_TOOL_ROUNDS,
            prior_summary is not None,
        )
        try:
            text = await collect_text(agent, decision_prompt)
        except Exception as exc:  # provider down mid-loop: degrade, never crash
            logger.warning(
                "envelope loop: round %d provider error %s: %s",
                round_no,
                type(exc).__name__,
                _preview(str(exc), _ERROR_PREVIEW_MAX),
            )
            provider_error = str(ProviderUnreachableError(provider=provider, reason=str(exc)))
            break
        call = parse_tool_call(text)
        logger.info(
            "envelope loop: round %d collected %d chars, parsed=%s",
            round_no,
            len(text),
            call["tool"] if call is not None else "direct/out_of_scope",
        )
        if call is None:
            if prior_summary is not None:
                logger.info("envelope loop: round %d direct answer after retrieval", round_no)
                direct_text = text
                break
            # Enforcement round: round 1 returned prose, so the model would
            # take the direct-answer path and silently answer a non-legal
            # question. One constrained classification call decides instead;
            # it degrades exactly like a decision round.
            classify_prompt = build_classify_prompt(query)
            privacy_check(classify_prompt, has_matter_evidence=bool(matter_raw))
            try:
                verdict = await collect_text(agent, classify_prompt)
            except Exception as exc:
                logger.warning(
                    "envelope loop: round %d classify-call provider error %s: %s",
                    round_no,
                    type(exc).__name__,
                    _preview(str(exc), _ERROR_PREVIEW_MAX),
                )
                provider_error = str(ProviderUnreachableError(provider=provider, reason=str(exc)))
                break
            logger.info(
                "envelope loop: round %d classify verdict collected %d chars",
                round_no,
                len(verdict),
            )
            if is_out_of_scope(verdict):
                logger.info("envelope loop: round %d verdict=out_of_scope", round_no)
                metrics.incr("chat.intent.out_of_scope")
                out_of_scope = True
                direct_text = None
                break
            logger.info("envelope loop: round %d verdict=in_scope, direct answer", round_no)
            metrics.incr("chat.intent.in_scope")
            direct_text = text
            break
        if call["tool"] == OUT_OF_SCOPE_TOOL:
            # Round 1 only: after retrieval rounds the verdict arrives too
            # late to be acted on. Never executed — it is not a retrieval
            # tool, and no store call may follow it.
            if prior_summary is None:
                logger.info("envelope loop: round %d envelope verdict=out_of_scope", round_no)
                metrics.incr("chat.intent.out_of_scope")
                out_of_scope = True
                direct_text = None
            break
        if tool_rounds == 0:
            # Announce retrieval once, before the first tool call; later
            # rounds are covered by the same status frame.
            yield StageEvent(stage="retrieving", message=STAGE_MESSAGES["retrieving"])
        logger.info(
            "envelope loop: round %d executing tool=%r query=%r",
            round_no,
            call["tool"],
            _preview(call["query"]),
        )
        retrieval_start = time.monotonic()
        m_new, a_new = await execute_tool_call(ctx, call["tool"], call["query"])
        metrics.timing("chat.retrieval.seconds", time.monotonic() - retrieval_start)
        metrics.incr("chat.retrieval.rounds")
        tool_rounds += 1
        matter_raw.extend(m_new)
        auth_raw.extend(a_new)
        logger.info(
            "envelope loop: round %d tool=%r hits matter=%d authority=%d "
            "(total matter=%d authority=%d)",
            round_no,
            call["tool"],
            len(m_new),
            len(a_new),
            len(matter_raw),
            len(auth_raw),
        )
        raw_summary = summarize_hits(matter_raw, auth_raw)
        prior_summary = cap_prior_summary(raw_summary, prior_summary_max_chars)
        if prior_summary != raw_summary:
            prior_summary_truncated = True
            logger.info(
                "envelope loop: round %d prior summary capped %d→%d chars (budget=%d, ~%d tokens)",
                round_no,
                len(raw_summary),
                len(prior_summary or ""),
                prior_summary_max_chars,
                estimate_tokens(prior_summary),
            )
    logger.info(
        "envelope loop: done tool_rounds=%d has_direct_text=%s has_provider_error=%s "
        "out_of_scope=%s matter_hits=%d authority_hits=%d prior_summary_truncated=%s",
        tool_rounds,
        direct_text is not None,
        provider_error is not None,
        out_of_scope,
        len(matter_raw),
        len(auth_raw),
        prior_summary_truncated,
    )
    # Tool-round distribution (0 direct/no-retrieval … MAX_TOOL_ROUNDS capped):
    # a flat counter per bucket is enough to compute the histogram over time.
    metrics.incr(f"chat.tool_round_dist.{tool_rounds}")
    yield EnvelopeOutcome(
        matter_raw=matter_raw,
        auth_raw=auth_raw,
        direct_text=direct_text,
        tool_rounds=tool_rounds,
        provider_error=provider_error,
        out_of_scope=out_of_scope,
    )


def register_retrieval_tools(agent: Any, ctx: ToolContext) -> bool:
    """Register retrieval tools on a real pydantic-ai agent.

    Returns True when registration happened, False for test doubles
    (e.g. _FakeAgent) that lack the ``tool_plain`` decorator —
    ``run_envelope_loop`` covers those via ``execute_tool_call``.
    Each closure keeps the matter_id pre-filter bound, so isolation holds
    even for native tool calls.

    When ``ctx.matter_id`` is None (matterless conversation) only
    ``search_authority`` is registered: the matter-bearing tools are
    omitted entirely so native tool use cannot reach an unscoped store
    call on the ``matter_evidence`` collection.
    """
    decorator = getattr(agent, "tool_plain", None)
    if not callable(decorator):
        return False

    @decorator
    async def search_authority_tool(query: str) -> str:
        """Search the versioned authority library (no matter scope)."""
        _, hits = await execute_tool_call(ctx, SEARCH_AUTHORITY, query)
        return json.dumps(
            [{"text": h.get("text", ""), "ref": h.get("article_or_section")} for h in hits],
            ensure_ascii=False,
        )

    if ctx.matter_id is None:
        return True

    @decorator
    async def search_matter_tool(query: str) -> str:
        """Search private matter evidence for one matter (matter_id pre-filtered)."""
        hits, _ = await execute_tool_call(ctx, SEARCH_MATTER, query)
        return json.dumps(
            [{"text": h.get("text", ""), "ref": h.get("faithful_ref")} for h in hits],
            ensure_ascii=False,
        )

    @decorator
    async def search_both_tool(query: str) -> str:
        """Search both matter evidence and authority library (default)."""
        matter_hits, auth_hits = await execute_tool_call(ctx, SEARCH_BOTH, query)
        return json.dumps(
            {
                "matter": [
                    {"text": h.get("text", ""), "ref": h.get("faithful_ref")} for h in matter_hits
                ],
                "authority": [
                    {"text": h.get("text", ""), "ref": h.get("article_or_section")}
                    for h in auth_hits
                ],
            },
            ensure_ascii=False,
        )

    return True


def get_agent_with_tools(
    provider: str | None = None,
    model: str | None = None,
    *,
    store: Any,
    embedder: Any,
    matter_id: int | None,
    top_k: int = 30,
) -> Any:
    """Build the chat agent with retrieval tools registered.

    Wraps get_agent, then registers search_authority_tool plus — only when
    ``matter_id`` is not None — search_matter_tool / search_both_tool
    (matter_id pre-filter bound) via register_retrieval_tools. See the
    module docstring for why ``run_envelope_loop`` drives a manual envelope
    loop around these tools.
    """
    agent = agent_mod.get_agent(provider=provider, model=model)
    register_retrieval_tools(
        agent,
        ToolContext(store=store, embedder=embedder, matter_id=matter_id, top_k=top_k),
    )
    return agent
