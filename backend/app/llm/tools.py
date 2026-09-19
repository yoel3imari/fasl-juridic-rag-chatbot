"""ReAct retrieval tools for the Fasl chat LLM.

The chat agent decides per query whether to answer directly (greetings,
smalltalk) or to call retrieval tools (legal questions), up to
MAX_TOOL_ROUNDS bounded rounds. Tool results flow through the existing
rerank → build_citations → assemble_prompt path in the chat route.

Design note (pydantic-ai 2.43.0): native tools (``Agent(tools=[...])`` /
``@agent.tool_plain``) ARE available and ARE registered by
``register_retrieval_tools`` / ``get_agent_with_tools``. The chat route
nevertheless drives a manual JSON tool-call envelope loop instead of
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
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.search import service as svc

MAX_TOOL_ROUNDS: int = 3

SEARCH_MATTER: str = "search_matter"
SEARCH_AUTHORITY: str = "search_authority"
SEARCH_BOTH: str = "search_both"

VALID_TOOLS: frozenset[str] = frozenset({SEARCH_MATTER, SEARCH_AUTHORITY, SEARCH_BOTH})

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
    "Never invent articles, sections, document ids, or citations."
)

# Matches the first {...} block containing a "tool" key (DOTALL for multiline).
_ENVELOPE_RE: re.Pattern[str] = re.compile(r"\{[^{}]*\"tool\"[^{}]*\}", re.DOTALL)


def build_decision_prompt(query: str, *, prior_summary: str | None = None) -> str:
    """Prompt asking the LLM to choose: direct answer vs tool-call envelope."""
    base = f"{TOOL_GUIDANCE}\n\nUser message: {query}"
    if prior_summary:
        base += (
            "\n\nPrior tool results (do NOT repeat the same call; refine the "
            f"query or answer):\n{prior_summary}"
        )
    return base


def parse_tool_call(text: str) -> dict[str, str] | None:
    """Parse a JSON tool-call envelope from LLM output.

    Returns ``{"tool": ..., "query": ...}`` on success, else None meaning
    the LLM answered directly.
    """
    stripped = (text or "").strip()
    candidates: list[str] = [stripped]
    candidates.extend(m.group(0) for m in _ENVELOPE_RE.finditer(stripped))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        tool = data.get("tool")
        query = data.get("query")
        if (
            isinstance(tool, str)
            and tool in VALID_TOOLS
            and isinstance(query, str)
            and query.strip()
        ):
            return {"tool": tool, "query": query.strip()}
    return None


def summarize_hits(
    matter_hits: list[dict[str, Any]], authority_hits: list[dict[str, Any]]
) -> str:
    """Compact prior-round summary so later rounds can refine, not repeat."""
    parts = [
        f"matter passages: {len(matter_hits)}, "
        f"authority passages: {len(authority_hits)}."
    ]
    excerpts: list[str] = []
    for hit in (matter_hits + authority_hits)[:4]:
        excerpts.append(str(hit.get("text", ""))[:250])
    if excerpts:
        parts.append("Excerpts:\n" + "\n---\n".join(excerpts))
    return "\n".join(parts)


@dataclass
class ToolContext:
    """Bound retrieval dependencies for one chat request (matter pre-filter kept)."""

    store: svc.Store
    embedder: svc.Embedder
    matter_id: int
    top_k: int = 30


async def execute_tool_call(
    ctx: ToolContext, tool: str, query: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run one retrieval tool, returning raw (matter_hits, authority_hits).

    Matter isolation is preserved: matter_id is pre-filtered in the store,
    never post-hoc. Error mapping matches the chat contract: embedding
    failure → 503, store failure → 500.
    """
    if tool == SEARCH_MATTER:
        try:
            res = await svc.search_matter(
                store=ctx.store,
                embedder=ctx.embedder,
                matter_id=ctx.matter_id,
                query=query,
                top_k=ctx.top_k,
            )
        except svc.EmbeddingUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail=f"{exc} — retry shortly"
            ) from exc
        except svc.SearchUnavailableError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return list(res["matter"]), []
    if tool == SEARCH_AUTHORITY:
        try:
            res = await svc.search_authority(
                store=ctx.store, embedder=ctx.embedder, query=query, top_k=ctx.top_k
            )
        except svc.EmbeddingUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail=f"{exc} — retry shortly"
            ) from exc
        except svc.SearchUnavailableError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return [], list(res["authority"])
    if tool == SEARCH_BOTH:
        try:
            matter_res = await svc.search_matter(
                store=ctx.store,
                embedder=ctx.embedder,
                matter_id=ctx.matter_id,
                query=query,
                top_k=ctx.top_k,
            )
        except svc.EmbeddingUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail=f"{exc} — retry shortly"
            ) from exc
        except svc.SearchUnavailableError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        try:
            auth_res = await svc.search_authority(
                store=ctx.store, embedder=ctx.embedder, query=query, top_k=ctx.top_k
            )
        except svc.EmbeddingUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail=f"{exc} — retry shortly"
            ) from exc
        except svc.SearchUnavailableError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return list(matter_res["matter"]), list(auth_res["authority"])
    raise HTTPException(status_code=500, detail=f"unknown retrieval tool: {tool}")


def register_retrieval_tools(agent: Any, ctx: ToolContext) -> bool:
    """Register retrieval tools on a real pydantic-ai agent.

    Returns True when registration happened, False for test doubles
    (e.g. _FakeAgent) that lack the ``tool_plain`` decorator — the chat
    route's manual envelope loop covers those via ``execute_tool_call``.
    Each closure keeps the matter_id pre-filter bound, so isolation holds
    even for native tool calls.
    """
    decorator = getattr(agent, "tool_plain", None)
    if not callable(decorator):
        return False

    @decorator
    async def search_matter_tool(query: str) -> str:
        """Search private matter evidence for one matter (matter_id pre-filtered)."""
        hits, _ = await execute_tool_call(ctx, SEARCH_MATTER, query)
        return json.dumps(
            [{"text": h.get("text", ""), "ref": h.get("faithful_ref")} for h in hits],
            ensure_ascii=False,
        )

    @decorator
    async def search_authority_tool(query: str) -> str:
        """Search the versioned authority library (no matter scope)."""
        _, hits = await execute_tool_call(ctx, SEARCH_AUTHORITY, query)
        return json.dumps(
            [
                {"text": h.get("text", ""), "ref": h.get("article_or_section")}
                for h in hits
            ],
            ensure_ascii=False,
        )

    @decorator
    async def search_both_tool(query: str) -> str:
        """Search both matter evidence and authority library (default)."""
        matter_hits, auth_hits = await execute_tool_call(ctx, SEARCH_BOTH, query)
        return json.dumps(
            {
                "matter": [
                    {"text": h.get("text", ""), "ref": h.get("faithful_ref")}
                    for h in matter_hits
                ],
                "authority": [
                    {"text": h.get("text", ""), "ref": h.get("article_or_section")}
                    for h in auth_hits
                ],
            },
            ensure_ascii=False,
        )

    return True
