"""Chat streaming route: WebSocket /api/v1/chat/stream.

Accepts JSON {matter_id, conversation_id?, content, consent?, system?},
runs the matter-privacy check first, then streams assistant tokens.
Every failure is a structured {"type": "error", ...} frame; the socket
never crashes unhandled.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm as llm_mod
from app.config import Settings
from app.config.resolver import (
    resolve_llm_settings,
    resolve_privacy_mode,
    resolve_request_llm_settings,
)
from app.domain.citations import build_citations
from app.domain.prompts import PROVISIONAL_NOT_FOUND, assemble_prompt
from app.domain.rerank import mmr_select
from app.infrastructure.rerank.flashrank import rerank
from app.llm import tools as react_tools
from app.models.base import get_db
from app.models.conversation import Conversation, Message
from app.repositories import settings as settings_store
from app.search import service as svc

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

RETRIEVE_TOP_K: int = 30
RERANK_TOP_K: int = 10


def get_store() -> svc.Store:
    """Factory seam: Qdrant store from settings (URL or local path)."""
    from app.infrastructure.qdrant.store import QdrantStore

    return QdrantStore()  # type: ignore[return-value]


def get_embedder() -> svc.Embedder:
    """Factory seam: CrispEmbed HTTP client (model from settings)."""
    from app.infrastructure.embeddings.client import CrispEmbedClient

    return CrispEmbedClient()  # type: ignore[return-value]


class ChatIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: int
    conversation_id: int | None = None
    content: str
    consent: bool = False
    system: str | None = None


async def _send_error(ws: WebSocket, code: str, detail: str) -> None:
    await ws.send_text(json.dumps({"type": "error", "code": code, "detail": detail}))


class RagChatIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: int
    content: str = Field(min_length=1)
    conversation_id: int | None = None
    consent: bool = False
    provider: str | None = None
    model: str | None = None


class ModelOption(BaseModel):
    id: str
    name: str
    description: str | None = None
    recommended: bool = False


class ProviderOption(BaseModel):
    id: str
    name: str
    type: str
    is_external: bool
    description: str
    default_model: str
    models: list[ModelOption]


class ModelsResponse(BaseModel):
    current_provider: str
    current_model: str
    privacy_mode: str
    providers: list[ProviderOption]


@router.get("/models", response_model=ModelsResponse)
@router.get("/providers", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    settings = Settings()  # type: ignore[call-arg]
    resolved = resolve_llm_settings(settings)
    providers: list[ProviderOption] = [
        ProviderOption(
            id="ollama",
            name="Ollama (Local)",
            type="local",
            is_external=False,
            description="Local on-device inference with zero data transmission. Strict privacy compliant.",
            default_model="llama3.2",
            models=[
                ModelOption(
                    id="llama3.2",
                    name="Llama 3.2 (3B)",
                    description="Lightweight, fast, local privacy",
                    recommended=True,
                ),
                ModelOption(
                    id="llama3.1",
                    name="Llama 3.1 (8B)",
                    description="Balanced general reasoning",
                ),
                ModelOption(
                    id="qwen2.5",
                    name="Qwen 2.5 (7B/14B)",
                    description="Excellent Arabic & multilingual legal understanding",
                    recommended=True,
                ),
                ModelOption(
                    id="mistral",
                    name="Mistral (7B)",
                    description="Concise legal summarization",
                ),
                ModelOption(
                    id="deepseek-r1",
                    name="DeepSeek R1 Distill",
                    description="Deep step-by-step reasoning",
                ),
            ],
        ),
        ProviderOption(
            id="openrouter",
            name="OpenRouter (Unified Cloud)",
            type="cloud",
            is_external=True,
            description="Access dozens of state-of-the-art models via OpenRouter unified gateway.",
            default_model="anthropic/claude-3.5-sonnet",
            models=[
                ModelOption(
                    id="anthropic/claude-3.5-sonnet",
                    name="Claude 3.5 Sonnet",
                    description="Top benchmark for legal reasoning and drafting",
                    recommended=True,
                ),
                ModelOption(
                    id="openai/gpt-4o",
                    name="GPT-4o",
                    description="High-capability omnimodel",
                ),
                ModelOption(
                    id="google/gemini-2.0-flash",
                    name="Gemini 2.0 Flash",
                    description="Ultra fast with large context window",
                    recommended=True,
                ),
                ModelOption(
                    id="meta-llama/llama-3.3-70b-instruct",
                    name="Llama 3.3 70B Instruct",
                    description="High performance open-weights",
                ),
                ModelOption(
                    id="deepseek/deepseek-r1",
                    name="DeepSeek R1",
                    description="Frontier reasoning for complex statutory disputes",
                ),
            ],
        ),
        ProviderOption(
            id="openai",
            name="OpenAI",
            type="cloud",
            is_external=True,
            description="Direct OpenAI API connection (requires OPENAI_API_KEY).",
            default_model="gpt-4o",
            models=[
                ModelOption(
                    id="gpt-4o",
                    name="GPT-4o",
                    description="Flagship intelligent model",
                    recommended=True,
                ),
                ModelOption(
                    id="gpt-4o-mini",
                    name="GPT-4o Mini",
                    description="Fast, cost-efficient analysis",
                ),
                ModelOption(
                    id="o1-mini",
                    name="o1-mini",
                    description="Advanced reasoning for complex statutory analysis",
                ),
            ],
        ),
        ProviderOption(
            id="anthropic",
            name="Anthropic",
            type="cloud",
            is_external=True,
            description="Direct Anthropic Claude API connection (requires ANTHROPIC_API_KEY).",
            default_model="claude-3-5-sonnet-latest",
            models=[
                ModelOption(
                    id="claude-3-5-sonnet-latest",
                    name="Claude 3.5 Sonnet",
                    description="State-of-the-art legal precision",
                    recommended=True,
                ),
                ModelOption(
                    id="claude-3-5-haiku-latest",
                    name="Claude 3.5 Haiku",
                    description="Fast and concise responses",
                ),
            ],
        ),
        ProviderOption(
            id="google",
            name="Google Gemini",
            type="cloud",
            is_external=True,
            description="Direct Google Gemini API connection (requires GEMINI_API_KEY).",
            default_model="gemini-2.0-flash",
            models=[
                ModelOption(
                    id="gemini-2.0-flash",
                    name="Gemini 2.0 Flash",
                    description="High-speed next-gen model",
                    recommended=True,
                ),
                ModelOption(
                    id="gemini-1.5-pro",
                    name="Gemini 1.5 Pro",
                    description="Deep document analysis & massive context",
                ),
            ],
        ),
        ProviderOption(
            id="groq",
            name="Groq",
            type="cloud",
            is_external=True,
            description="Ultra-low latency LPU cloud inference (requires GROQ_API_KEY).",
            default_model="llama-3.3-70b-versatile",
            models=[
                ModelOption(
                    id="llama-3.3-70b-versatile",
                    name="Llama 3.3 70B (Groq)",
                    description="Near-instant token generation",
                    recommended=True,
                ),
                ModelOption(
                    id="mixtral-8x7b-32768",
                    name="Mixtral 8x7B (Groq)",
                    description="Fast mixture of experts",
                ),
            ],
        ),
    ]
    return ModelsResponse(
        current_provider=resolved.provider,
        current_model=resolved.model,
        privacy_mode=resolve_privacy_mode(settings),
        providers=providers,
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _collect_text(agent: Any, prompt: str) -> str:
    """Run one agent round and collect its full text (decision rounds stay silent)."""
    parts: list[str] = []
    async with agent.run_stream(prompt) as result:
        async for chunk in result.stream_text(delta=True):
            parts.append(chunk)
    return "".join(parts)


def _privacy_http(
    prompt: str,
    *,
    provider: str,
    privacy_mode: str,
    consent: bool,
    has_matter_evidence: bool,
) -> None:
    """Privacy guard mapped to the chat 403 contract."""
    try:
        llm_mod.check_privacy(
            prompt,
            provider=provider,
            privacy_mode=privacy_mode,
            consent=consent,
            has_matter_evidence=has_matter_evidence,
        )
    except llm_mod.PrivacyViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except llm_mod.ConsentRequiredError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("")
async def chat_rag(
    body: RagChatIn, session: Annotated[AsyncSession, Depends(get_db)]
) -> StreamingResponse:
    """Dual RAG query: ReAct tool loop → rerank → reason → SSE stream.

    Byte order is contractual: status events first, then citations event,
    then token events, then done. Every query enters the ReAct loop: the
    LLM decides per query between a direct answer (zero tool calls, no
    retrieval — this is how greetings/smalltalk are handled) or up to
    MAX_TOOL_ROUNDS retrieval tool rounds; tool results flow through the
    existing rerank → build_citations → assemble_prompt path. Tool rounds
    that stay empty in ALL rounds short-circuit to a provisional not-found
    response without any grounded provider call — an ungrounded
    free-answer after attempted retrieval is never used.
    """
    settings = Settings()  # type: ignore[call-arg]
    resolved = resolve_request_llm_settings(
        settings, provider=body.provider, model=body.model
    )
    selected_provider = resolved.provider.strip().lower()
    selected_model = resolved.model.strip()

    if body.conversation_id is not None:
        conv = await session.get(Conversation, body.conversation_id)
        if conv is None or conv.matter_id != body.matter_id:
            raise HTTPException(
                status_code=404, detail="conversation not found in this matter"
            )
    else:
        conv = Conversation(matter_id=body.matter_id, title=body.content[:60])
        session.add(conv)
        await session.flush()
    session.add(
        Message(
            conversation_id=conv.id,
            role="user",
            content=body.content,
            citations_json=None,
        )
    )
    await session.flush()
    conversation_id = conv.id
    # Transaction 1 closes here: conversation + user message are committed
    # before any retrieval/LLM network I/O, so no DB transaction or pooled
    # connection is held across the ReAct loop or the SSE stream. All
    # assistant writes below are short Transaction-2 commits.
    await session.commit()

    tool_ctx = react_tools.ToolContext(
        store=get_store(),
        embedder=get_embedder(),
        matter_id=body.matter_id,
        top_k=RETRIEVE_TOP_K,
    )
    try:
        agent = llm_mod.get_agent(provider=selected_provider, model=selected_model)
    except llm_mod.InvalidModelError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # Remember last-used provider/model server-side (before streaming).
    try:
        settings_store.save_llm_settings(
            provider=selected_provider, model=selected_model
        )
    except OSError:
        pass
    # Real pydantic-ai agents get native tools registered; test doubles
    # skip gracefully and are driven by the envelope loop below.
    react_tools.register_retrieval_tools(agent, tool_ctx)

    # Bounded ReAct loop (max 3 tool rounds): the LLM either answers
    # directly (no envelope → zero retrieval) or emits a JSON tool-call
    # envelope per round. Decision rounds are collected silently; only the
    # final grounded answer streams as tokens, preserving the SSE order.
    matter_raw: list = []
    auth_raw: list = []
    direct_text: str | None = None
    tool_rounds = 0
    provider_error: str | None = None
    prior_summary: str | None = None
    for _ in range(react_tools.MAX_TOOL_ROUNDS):
        decision_prompt = react_tools.build_decision_prompt(
            body.content, prior_summary=prior_summary
        )
        _privacy_http(
            decision_prompt,
            provider=selected_provider,
            privacy_mode=resolve_privacy_mode(settings),
            consent=body.consent,
            has_matter_evidence=bool(matter_raw),
        )
        try:
            text = await _collect_text(agent, decision_prompt)
        except Exception as exc:  # provider down mid-loop: degrade, never crash
            provider_error = str(
                llm_mod.ProviderUnreachableError(
                    provider=selected_provider, reason=str(exc)
                )
            )
            break
        call = react_tools.parse_tool_call(text)
        if call is None:
            direct_text = text
            break
        # execute_tool_call maps embedding failure → 503, store failure → 500.
        m_new, a_new = await react_tools.execute_tool_call(
            tool_ctx, call["tool"], call["query"]
        )
        tool_rounds += 1
        matter_raw.extend(m_new)
        auth_raw.extend(a_new)
        prior_summary = react_tools.summarize_hits(matter_raw, auth_raw)

    if provider_error is not None and not matter_raw and not auth_raw:

        async def _gen_failed():  # type: ignore[no-untyped-def]
            yield _sse(
                {
                    "type": "status",
                    "stage": "classifying",
                    "message": "Classifying query",
                }
            )
            if tool_rounds:
                yield _sse(
                    {
                        "type": "status",
                        "stage": "searching",
                        "message": "Searching matter + authority",
                    }
                )
            yield _sse({"type": "citations", "citations": []})
            yield _sse(
                {
                    "type": "error",
                    "code": "provider_unreachable",
                    "detail": provider_error,
                    "conversation_id": conversation_id,
                }
            )
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content="",
                    citations_json=[],
                )
            )
            await session.commit()

        return StreamingResponse(_gen_failed(), media_type="text/event-stream")

    if not matter_raw and not auth_raw and tool_rounds == 0 and direct_text is not None:
        text = direct_text

        async def _gen_direct_agent():  # type: ignore[no-untyped-def]
            yield _sse(
                {
                    "type": "status",
                    "stage": "classifying",
                    "message": "Classifying query",
                }
            )
            yield _sse({"type": "citations", "citations": []})
            half = len(text) // 2
            yield _sse({"type": "token", "text": text[:half]})
            yield _sse({"type": "token", "text": text[half:]})
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=text,
                    citations_json=[],
                )
            )
            await session.commit()
            yield _sse(
                {"type": "done", "not_found": False, "conversation_id": conversation_id}
            )

        return StreamingResponse(_gen_direct_agent(), media_type="text/event-stream")

    matter_top = rerank(body.content, matter_raw, top_k=RERANK_TOP_K)
    auth_top = mmr_select(
        rerank(body.content, auth_raw, top_k=RERANK_TOP_K),
        top_k=RERANK_TOP_K,
    )
    citations = build_citations(matter_top, auth_top)
    prompt = assemble_prompt(body.content, matter_top, auth_top)
    _privacy_http(
        prompt,
        provider=selected_provider,
        privacy_mode=resolve_privacy_mode(settings),
        consent=body.consent,
        has_matter_evidence=bool(matter_top),
    )

    if not matter_top and not auth_top:
        text = PROVISIONAL_NOT_FOUND

        async def _gen_empty():  # type: ignore[no-untyped-def]
            yield _sse(
                {
                    "type": "status",
                    "stage": "classifying",
                    "message": "Classifying query",
                }
            )
            yield _sse(
                {
                    "type": "status",
                    "stage": "searching",
                    "message": "Searching matter + authority",
                }
            )
            yield _sse({"type": "citations", "citations": []})
            half = len(text) // 2
            yield _sse({"type": "token", "text": text[:half]})
            yield _sse({"type": "token", "text": text[half:]})
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=text,
                    citations_json=[],
                )
            )
            await session.commit()
            yield _sse(
                {"type": "done", "not_found": True, "conversation_id": conversation_id}
            )

        return StreamingResponse(_gen_empty(), media_type="text/event-stream")

    async def _gen():  # type: ignore[no-untyped-def]
        yield _sse(
            {"type": "status", "stage": "classifying", "message": "Classifying query"}
        )
        yield _sse(
            {
                "type": "status",
                "stage": "searching",
                "message": "Searching matter + authority",
            }
        )
        yield _sse(
            {
                "type": "status",
                "stage": "thinking",
                "message": "Thinking with context",
            }
        )
        yield _sse({"type": "citations", "citations": citations})
        parts: list[str] = []
        try:
            async with agent.run_stream(prompt) as result:
                async for chunk in result.stream_text(delta=True):
                    parts.append(chunk)
                    yield _sse({"type": "token", "text": chunk})
        except Exception as exc:  # provider down mid-stream: degrade, never crash
            yield _sse(
                {
                    "type": "error",
                    "code": "provider_unreachable",
                    "detail": str(
                        llm_mod.ProviderUnreachableError(
                            provider=selected_provider, reason=str(exc)
                        )
                    ),
                    "conversation_id": conversation_id,
                }
            )
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content="".join(parts),
                    citations_json=citations,
                )
            )
            await session.commit()
            return
        full = "".join(parts)
        session.add(
            Message(
                conversation_id=conversation_id,
                role="assistant",
                content=full,
                citations_json=citations,
            )
        )
        await session.commit()
        yield _sse(
            {"type": "done", "not_found": False, "conversation_id": conversation_id}
        )

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.websocket("/stream")
async def chat_stream(ws: WebSocket) -> None:
    """Privacy-checked streaming chat over a persistent socket."""
    await ws.accept()
    # Fresh read per connection so env overrides apply in tests and deploys.
    settings = Settings()  # type: ignore[call-arg]
    resolved = resolve_request_llm_settings(settings)
    while True:
        try:
            raw = await ws.receive_text()
        except WebSocketDisconnect:
            break
        try:
            payload = ChatIn.model_validate_json(raw)
        except ValidationError as exc:
            await _send_error(
                ws, "invalid_payload", f"malformed chat payload: {exc.errors()}"
            )
            continue
        try:
            llm_mod.check_privacy(
                payload.content,
                provider=resolved.provider,
                privacy_mode=resolve_privacy_mode(settings),
                consent=payload.consent,
                system=payload.system,
            )
        except llm_mod.PrivacyViolationError as exc:
            await _send_error(ws, "privacy_violation", str(exc))
            continue
        except llm_mod.ConsentRequiredError as exc:
            await _send_error(ws, "consent_required", str(exc))
            continue
        try:
            agent = llm_mod.get_agent(
                provider=resolved.provider, model=resolved.model
            )
            async with agent.run_stream(payload.content) as result:
                async for chunk in result.stream_text(delta=True):
                    await ws.send_text(json.dumps({"type": "token", "text": chunk}))
            await ws.send_text(json.dumps({"type": "done"}))
        except (
            Exception
        ) as exc:  # provider down, bad key, timeout: degrade, never crash
            await _send_error(
                ws,
                "provider_unreachable",
                str(
                    llm_mod.ProviderUnreachableError(
                        provider=resolved.provider, reason=str(exc)
                    )
                ),
            )
