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
from app.models.base import get_db
from app.models.conversation import Conversation, Message
from app.rag.assemble import PROVISIONAL_NOT_FOUND, assemble_prompt, build_citations
from app.rag.rerank import mmr_select, rerank
from app.search import service as svc

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

RETRIEVE_TOP_K: int = 30
RERANK_TOP_K: int = 10


def get_store() -> svc.Store:
    """Factory seam: Qdrant store from settings (URL or local path)."""
    from app.search.store import QdrantStore

    return QdrantStore()  # type: ignore[return-value]


def get_embedder() -> svc.Embedder:
    """Factory seam: CrispEmbed bge-m3 HTTP client."""
    from app.library.embedder import CrispEmbedClient

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


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _retrieve(
    body: RagChatIn, store: svc.Store, embedder: svc.Embedder
) -> tuple[list, list]:
    """Per-domain retrieval (top_k=30 each). Embedding failure → 503, store → 500."""
    try:
        matter_res = await svc.search_matter(
            store=store,
            embedder=embedder,
            matter_id=body.matter_id,
            query=body.content,
            top_k=RETRIEVE_TOP_K,
        )
    except svc.EmbeddingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"{exc} — retry shortly") from exc
    except svc.SearchUnavailableError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    try:
        auth_res = await svc.search_authority(
            store=store, embedder=embedder, query=body.content, top_k=RETRIEVE_TOP_K
        )
    except svc.EmbeddingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"{exc} — retry shortly") from exc
    except svc.SearchUnavailableError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return list(matter_res["matter"]), list(auth_res["authority"])


@router.post("")
async def chat_rag(
    body: RagChatIn, session: Annotated[AsyncSession, Depends(get_db)]
) -> StreamingResponse:
    """Dual RAG query: retrieve → rerank → reason → SSE stream.

    Byte order is contractual: citations event FIRST, then token events,
    then done. Empty context in BOTH domains short-circuits to a
    provisional not-found response without any provider call.
    """
    settings = Settings()  # type: ignore[call-arg]
    matter_hits, auth_hits = await _retrieve(body, get_store(), get_embedder())
    matter_top = rerank(body.content, matter_hits, top_k=RERANK_TOP_K)
    auth_top = mmr_select(
        rerank(body.content, auth_hits, top_k=RERANK_TOP_K),
        top_k=RERANK_TOP_K,
    )
    citations = build_citations(matter_top, auth_top)
    prompt = assemble_prompt(body.content, matter_top, auth_top)
    try:
        llm_mod.check_privacy(
            prompt,
            provider=settings.LLM_PROVIDER,
            privacy_mode=settings.MATTER_PRIVACY_MODE,
            consent=body.consent,
            has_matter_evidence=bool(matter_top),
        )
    except llm_mod.PrivacyViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except llm_mod.ConsentRequiredError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

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

    if not matter_top and not auth_top:
        text = PROVISIONAL_NOT_FOUND

        async def _gen_empty():  # type: ignore[no-untyped-def]
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
            yield _sse({"type": "done", "not_found": True})

        return StreamingResponse(_gen_empty(), media_type="text/event-stream")

    try:
        agent = llm_mod.get_agent(
            provider=settings.LLM_PROVIDER, model=settings.LLM_MODEL
        )
    except llm_mod.InvalidModelError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def _gen():  # type: ignore[no-untyped-def]
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
                            provider=settings.LLM_PROVIDER, reason=str(exc)
                        )
                    ),
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
        yield _sse({"type": "done", "not_found": False})

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.websocket("/stream")
async def chat_stream(ws: WebSocket) -> None:
    """Privacy-checked streaming chat over a persistent socket."""
    await ws.accept()
    # Fresh read per connection so env overrides apply in tests and deploys.
    settings = Settings()  # type: ignore[call-arg]
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
                provider=settings.LLM_PROVIDER,
                privacy_mode=settings.MATTER_PRIVACY_MODE,
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
                provider=settings.LLM_PROVIDER, model=settings.LLM_MODEL
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
                        provider=settings.LLM_PROVIDER, reason=str(exc)
                    )
                ),
            )
