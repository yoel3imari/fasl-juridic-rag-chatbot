"""Chat streaming route: WebSocket /api/v1/chat/stream.

Accepts JSON {matter_id, conversation_id?, content, consent?, system?},
runs the matter-privacy check first, then streams assistant tokens.
Every failure is a structured {"type": "error", ...} frame; the socket
never crashes unhandled.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.config import Settings
from app.config.resolver import (
    resolve_llm_settings,
    resolve_privacy_mode,
    resolve_request_llm_settings,
)
from app.domain.citations import build_citations
from app.domain.privacy import (
    ConsentRequiredError,
    PrivacyViolationError,
    check_privacy,
)
from app.domain.prompts import PROVISIONAL_NOT_FOUND, assemble_prompt
from app.domain.rerank import mmr_select
from app.infrastructure import llm as llm_mod
from app.infrastructure.llm.errors import InvalidModelError, ProviderUnreachableError
from app.infrastructure.rerank.flashrank import rerank
from app.models.base import get_db
from app.models.conversation import Conversation, Message
from app.repositories import settings as settings_store
from app.schemas.chat import PROVIDERS, ProviderOption
from app.services import chat as react_tools
from app.services import search as svc

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

RETRIEVE_TOP_K: int = 30
RERANK_TOP_K: int = 10


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
    return ModelsResponse(
        current_provider=resolved.provider,
        current_model=resolved.model,
        privacy_mode=resolve_privacy_mode(settings),
        providers=PROVIDERS,
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
        check_privacy(
            prompt,
            provider=provider,
            privacy_mode=privacy_mode,
            consent=consent,
            has_matter_evidence=has_matter_evidence,
        )
    except PrivacyViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConsentRequiredError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


_STATUS_MESSAGES: dict[str, str] = {
    "classifying": "Classifying query",
    "searching": "Searching matter + authority",
    "thinking": "Thinking with context",
}


def _frames(*stages: str) -> list[dict[str, Any]]:
    """Status frames for the given stages, in order."""
    return [
        {"type": "status", "stage": stage, "message": _STATUS_MESSAGES[stage]}
        for stage in stages
    ]


async def _halved_tokens(text: str) -> AsyncIterator[dict[str, Any]]:
    """Yield text as two token frames (contract: split at half)."""
    half = len(text) // 2
    yield {"type": "token", "text": text[:half]}
    yield {"type": "token", "text": text[half:]}


async def _persist_message(
    session: AsyncSession,
    conversation_id: int,
    content: str,
    citations_json: list[dict[str, Any]],
) -> None:
    """Append the assistant message and commit (short Transaction 2)."""
    session.add(
        Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            citations_json=citations_json,
        )
    )
    await session.commit()


async def _sse_frames(
    *,
    statuses: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    content: AsyncIterator[dict[str, Any]],
    persist: Callable[[], Awaitable[None]],
    done: Callable[[], dict[str, Any] | None] | None = None,
) -> AsyncIterator[str]:
    """Emit the contractual SSE sequence: statuses, citations, content, done."""
    for frame in statuses:
        yield _sse(frame)
    yield _sse({"type": "citations", "citations": citations})
    async for frame in content:
        yield _sse(frame)
    await persist()
    if done is not None:
        done_frame = done()
        if done_frame is not None:
            yield _sse(done_frame)


# SSE session scope (plan todo 26, evidence from todo 7): fastapi is pinned
# `fastapi>=0.141,<0.142` (installed 0.141.1). A bare `Depends(get_db)` uses the
# default scope, which measured identical to `scope="request"`: the dependency
# teardown runs AFTER the whole response body is sent, so the AsyncSession stays
# open for every stream below (each _sse_frames branch of chat_rag)
# and their mid-stream/post-stream `session.commit()` calls run on that same live
# session. Adding `scope="function"` would close it BEFORE the stream and break
# those commits - do NOT add it. See
# .omo/evidence/backend-architecture-refactor/07/scope-timing.txt
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
        store=deps.get_store(),
        embedder=deps.get_embedder(),
        matter_id=body.matter_id,
        top_k=RETRIEVE_TOP_K,
    )
    try:
        agent = llm_mod.get_agent(provider=selected_provider, model=selected_model)
    except InvalidModelError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # Remember last-used provider/model server-side (before streaming).
    try:
        settings_store.save_llm_settings(
            provider=selected_provider, model=selected_model
        )
    except OSError:
        pass
    # Real pydantic-ai agents get native tools registered; test doubles
    # skip gracefully and are driven by react_tools.run_envelope_loop below.
    react_tools.register_retrieval_tools(agent, tool_ctx)

    # Bounded ReAct loop (max 3 tool rounds): the LLM either answers
    # directly (no envelope → zero retrieval) or emits a JSON tool-call
    # envelope per round. Decision rounds are collected silently; only the
    # final grounded answer streams as tokens, preserving the SSE order.

    def _decision_privacy(decision_prompt: str, *, has_matter_evidence: bool) -> None:
        _privacy_http(
            decision_prompt,
            provider=selected_provider,
            privacy_mode=resolve_privacy_mode(settings),
            consent=body.consent,
            has_matter_evidence=has_matter_evidence,
        )

    # execute_tool_call propagates svc errors; map them to the chat
    # contract here: embedding failure → 503, store failure → 500.
    try:
        outcome = await react_tools.run_envelope_loop(
            query=body.content,
            agent=agent,
            ctx=tool_ctx,
            provider=selected_provider,
            privacy_check=_decision_privacy,
        )
    except svc.EmbeddingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"{exc} — retry shortly") from exc
    except svc.SearchUnavailableError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    matter_raw = outcome.matter_raw
    auth_raw = outcome.auth_raw
    direct_text = outcome.direct_text
    tool_rounds = outcome.tool_rounds
    provider_error = outcome.provider_error

    if provider_error is not None and not matter_raw and not auth_raw:
        err_detail = provider_error
        failed_stages = _frames("classifying")
        if tool_rounds:
            failed_stages += _frames("searching")

        async def _failed_content() -> AsyncIterator[dict[str, Any]]:
            yield {
                "type": "error",
                "code": "provider_unreachable",
                "detail": err_detail,
                "conversation_id": conversation_id,
            }

        return StreamingResponse(
            _sse_frames(
                statuses=failed_stages,
                citations=[],
                content=_failed_content(),
                persist=lambda: _persist_message(
                    session, conversation_id, "", []
                ),
            ),
            media_type="text/event-stream",
        )

    if not matter_raw and not auth_raw and tool_rounds == 0 and direct_text is not None:
        text: str = direct_text

        return StreamingResponse(
            _sse_frames(
                statuses=_frames("classifying"),
                citations=[],
                content=_halved_tokens(text),
                persist=lambda: _persist_message(
                    session, conversation_id, text, []
                ),
                done=lambda: {
                    "type": "done",
                    "not_found": False,
                    "conversation_id": conversation_id,
                },
            ),
            media_type="text/event-stream",
        )

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

        return StreamingResponse(
            _sse_frames(
                statuses=_frames("classifying", "searching"),
                citations=[],
                content=_halved_tokens(text),
                persist=lambda: _persist_message(
                    session, conversation_id, text, []
                ),
                done=lambda: {
                    "type": "done",
                    "not_found": True,
                    "conversation_id": conversation_id,
                },
            ),
            media_type="text/event-stream",
        )

    parts: list[str] = []
    errored = False

    async def _stream_answer() -> AsyncIterator[dict[str, Any]]:
        nonlocal errored
        try:
            async with agent.run_stream(prompt) as result:
                async for chunk in result.stream_text(delta=True):
                    parts.append(chunk)
                    yield {"type": "token", "text": chunk}
        except Exception as exc:  # provider down mid-stream: degrade, never crash
            errored = True
            yield {
                "type": "error",
                "code": "provider_unreachable",
                "detail": str(
                    ProviderUnreachableError(
                        provider=selected_provider, reason=str(exc)
                    )
                ),
                "conversation_id": conversation_id,
            }

    def _done_full() -> dict[str, Any] | None:
        if errored:
            return None
        return {
            "type": "done",
            "not_found": False,
            "conversation_id": conversation_id,
        }

    return StreamingResponse(
        _sse_frames(
            statuses=_frames("classifying", "searching", "thinking"),
            citations=citations,
            content=_stream_answer(),
            persist=lambda: _persist_message(
                session, conversation_id, "".join(parts), citations
            ),
            done=_done_full,
        ),
        media_type="text/event-stream",
    )


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
            check_privacy(
                payload.content,
                provider=resolved.provider,
                privacy_mode=resolve_privacy_mode(settings),
                consent=payload.consent,
                system=payload.system,
            )
        except PrivacyViolationError as exc:
            await _send_error(ws, "privacy_violation", str(exc))
            continue
        except ConsentRequiredError as exc:
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
                    ProviderUnreachableError(
                        provider=resolved.provider, reason=str(exc)
                    )
                ),
            )
