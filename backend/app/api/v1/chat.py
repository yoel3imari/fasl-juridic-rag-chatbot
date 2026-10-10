"""Chat streaming routes: POST /api/v1/chat (SSE, supported) + WebSocket (legacy).

POST /api/v1/chat accepts JSON {matter_id?, conversation_id?, content,
consent?, provider?, model?}. `matter_id` is OPTIONAL: a conversation may
be matterless, in which case retrieval is authority-only. Before retrieval
the LLM classifies intent — Moroccan-legal questions proceed down the dual
RAG path, anything else is answered with the legal-only clarification in
`app.domain.prompts.OUT_OF_SCOPE_REPLY` (the intent gate in
`app.domain.intent`, enforced once on the decision round and once on a
constrained classification round).

The WebSocket handler below is legacy — accepts JSON {matter_id,
conversation_id?, content, consent?, system?}, runs the matter-privacy
check first, then streams assistant tokens. Every failure is a structured
{"type": "error", ...} frame; the socket never crashes unhandled.

LEGACY — the WebSocket chat_stream handler below is not used by the
frontend and is not covered by the SSE contract test
(tests/test_chat_contract.py pins chat_rag, POST /api/v1/chat, which is
the supported path). It has no retrieval, no citations, and no
conversation persistence; only tests/test_privacy.py exercises it.

TRACEABILITY — ``chat_rag`` logs one INFO line per stage of the SSE
pipeline (``chat.<stage>``) so a failing turn can be traced end to end
from the uvicorn stdout: request received → provider/model resolved →
agent built → stage events → envelope outcome → the SSE branch taken
(or the exception branch). Every line carries conversation_id, provider
and model; user text appears only as an 80-char preview and API keys are
never logged. Logging is observation-only: it changes no frame, no branch
and no return value.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.config import Settings
from app.config.resolver import (
    resolve_llm_settings,
    resolve_privacy_mode,
    resolve_request_llm_settings,
)
from app.domain.citations import build_citations
from app.domain.language import detect_lang
from app.domain.privacy import (
    ConsentRequiredError,
    PrivacyViolationError,
    check_privacy,
    is_external_provider,
)
from app.domain.prompts import assemble_prompt, out_of_scope_reply, provisional_not_found
from app.domain.rerank import mmr_select
from app.infrastructure import llm as llm_mod
from app.infrastructure.llm.errors import InvalidModelError, ProviderUnreachableError
from app.infrastructure.observability.metrics import metrics
from app.infrastructure.rerank.flashrank import rerank
from app.models.base import get_db
from app.models.conversation import Conversation, Message
from app.repositories import settings as settings_store
from app.schemas.chat import PROVIDERS, ChatIn, ModelsResponse, RagChatIn
from app.services import chat as react_tools
from app.services import search as svc
from app.services.chunk_dump import dump_retrieved_chunks

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

logger = logging.getLogger(__name__)

RETRIEVE_TOP_K: int = 30
RERANK_TOP_K: int = 10

# Fallback SSE batching threshold (chars) for `_batched_tokens` when a caller
# does not thread Settings.STREAM_BATCH_CHARS through. Kept equal to the config
# default so behavior is identical whether or not the setting is passed.
_DEFAULT_BATCH_CHARS: int = 600

# Log-safety ceilings for the trace below. Prompts and user messages are NEVER
# logged whole — only these short single-line previews. Key material is never
# logged at all: `app.repositories.settings` only ever reports whether a key is
# present (masked semantics), never its value.
PREVIEW_CHARS: int = 80
DETAIL_CHARS: int = 300


def _preview(value: object, limit: int = PREVIEW_CHARS) -> str:
    """Collapse ``value`` to a single-line log preview of at most ``limit`` chars."""
    flat = " ".join(str(value).split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


async def _send_error(ws: WebSocket, code: str, detail: str) -> None:
    await ws.send_text(json.dumps({"type": "error", "code": code, "detail": detail}))


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


@router.get("/metrics")
async def chat_metrics_snapshot() -> dict[str, Any]:
    """In-process chat-pipeline metrics snapshot (counters + timers).

    Counters cover tool-round distribution (``chat.tool_round_dist.N``), intent
    verdicts, privacy-gate triggers, generation volume and in-stream error rates
    by code; timers cover retrieval and drafting latency. The registry is
    process-local, so behind several workers each worker reports its own slice.
    Read-only and never records request/matter/legal text.
    """
    return metrics.snapshot()


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _status_frame(stage: str) -> dict[str, Any]:
    return {"type": "status", "stage": stage, "message": react_tools.STAGE_MESSAGES[stage]}


def _error_frame(code: str, detail: str, conversation_id: int) -> dict[str, Any]:
    return {"type": "error", "code": code, "detail": detail, "conversation_id": conversation_id}


async def _batched_tokens(
    text: str, *, batch_chars: int = _DEFAULT_BATCH_CHARS
) -> AsyncIterator[dict[str, Any]]:
    """Yield assistant text as SSE token frames in ~equal batches.

    Contract note: tests/test_chat_contract.py (and test_intent_gate.py) freeze
    the token-frame COUNT of the static replies at exactly TWO. So whenever the
    text is short enough to need only two frames — ``<= 2 * batch_chars`` — this
    reproduces the legacy ``_halved_tokens`` half-split byte-for-byte. Only a
    reply longer than that is streamed as additional, roughly equal frames,
    which is the batching win for a long direct answer or a long model reply.
    ``batch_chars`` comes from ``Settings.STREAM_BATCH_CHARS`` and must stay
    above the longest static reply; see its comment in app/config/__init__.py.
    """
    if not text:
        yield {"type": "token", "text": ""}
        return
    if batch_chars <= 0:
        batch_chars = _DEFAULT_BATCH_CHARS
    batches = max(2, -(-len(text) // batch_chars))
    if batches == 2:
        # Byte-identical to the frozen half-split contract.
        half = len(text) // 2
        yield {"type": "token", "text": text[:half]}
        yield {"type": "token", "text": text[half:]}
        return
    size = -(-len(text) // batches)
    for start in range(0, len(text), size):
        yield {"type": "token", "text": text[start : start + size]}


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


# SSE session scope (plan todo 26, evidence from todo 7): fastapi is pinned
# `fastapi>=0.141,<0.142` (installed 0.141.1). A bare `Depends(get_db)` uses the
# default scope, which measured identical to `scope="request"`: the dependency
# teardown runs AFTER the whole response body is sent, so the AsyncSession stays
# open for every stream below (each branch of chat_rag's SSE generator)
# and their mid-stream/post-stream `session.commit()` calls run on that same live
# session. Adding `scope="function"` would close it BEFORE the stream and break
# those commits - do NOT add it. See
# .omo/evidence/backend-architecture-refactor/07/scope-timing.txt
@router.post("")
async def chat_rag(
    body: RagChatIn, session: Annotated[AsyncSession, Depends(get_db)]
) -> StreamingResponse:
    """Dual RAG query: intent gate → ReAct tool loop → rerank → reason → SSE.

    `body.matter_id` is optional. Conversations are owned by (conversation
    id, matter id) pairs: `None == None` admits a matterless conversation,
    while `None != 7` / `7 != None` reject adopting a conversation across
    a matter boundary (404, same detail string).

    Byte order is contractual: status events first, then citations event,
    then token events, then done. Every query first passes the intent gate:
    an out-of-scope message short-circuits to OUT_OF_SCOPE_REPLY with an
    extra `out_of_scope: True` key on its done frame and no retrieval.
    Everything else enters the ReAct loop: the LLM decides per query
    between a direct answer (zero tool calls, no retrieval — this is how
    greetings/smalltalk are handled) or up to MAX_TOOL_ROUNDS retrieval
    tool rounds; tool results flow through the existing rerank →
    build_citations → assemble_prompt path. Tool rounds that stay empty in
    ALL rounds short-circuit to a provisional not-found response without
    any grounded provider call — an ungrounded free-answer after attempted
    retrieval is never used.
    """
    logger.info(
        "chat.request_received matter_id=%s conversation_id=%s consent=%s "
        "requested_provider=%s requested_model=%s content=%r",
        body.matter_id,
        body.conversation_id,
        body.consent,
        body.provider,
        body.model,
        _preview(body.content),
    )
    settings = Settings()  # type: ignore[call-arg]
    resolved = resolve_request_llm_settings(settings, provider=body.provider, model=body.model)
    selected_provider = resolved.provider.strip().lower()
    selected_model = resolved.model.strip()
    logger.info(
        "chat.llm_resolved provider=%s model=%s privacy_mode=%s",
        selected_provider,
        selected_model,
        resolve_privacy_mode(settings),
    )

    if body.conversation_id is not None:
        conv = await session.get(Conversation, body.conversation_id)
        if conv is None or conv.matter_id != body.matter_id:
            logger.info(
                "chat.conversation_rejected conversation_id=%s matter_id=%s exists=%s",
                body.conversation_id,
                body.matter_id,
                conv is not None,
            )
            raise HTTPException(status_code=404, detail="conversation not found in this matter")
    else:
        conv = Conversation(matter_id=body.matter_id, title=body.content[:60])
        session.add(conv)
        await session.flush()
        logger.info(
            "chat.conversation_created conversation_id=%s matter_id=%s",
            conv.id,
            body.matter_id,
        )
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
    logger.info(
        "chat.user_message_committed conversation_id=%s matter_id=%s chars=%d",
        conversation_id,
        body.matter_id,
        len(body.content),
    )

    tool_ctx = react_tools.ToolContext(
        store=deps.get_store(),
        embedder=deps.get_embedder(),
        matter_id=body.matter_id,
        top_k=RETRIEVE_TOP_K,
    )
    try:
        agent = llm_mod.get_agent(provider=selected_provider, model=selected_model)
    except InvalidModelError as exc:
        logger.info(
            "chat.agent_build_failed conversation_id=%s provider=%s model=%s "
            "error_type=%s error=%s",
            conversation_id,
            selected_provider,
            selected_model,
            type(exc).__name__,
            _preview(exc, limit=DETAIL_CHARS),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # getattr: real pydantic-ai agents carry `model_name`; test doubles do not.
    logger.info(
        "chat.agent_built conversation_id=%s provider=%s model=%s model_name=%s api_key_present=%s",
        conversation_id,
        selected_provider,
        selected_model,
        getattr(agent, "model_name", None),
        settings_store.has_api_key(selected_provider),
    )
    # Remember last-used provider/model server-side (before streaming).
    try:
        settings_store.save_llm_settings(provider=selected_provider, model=selected_model)
        logger.info(
            "chat.llm_settings_saved conversation_id=%s provider=%s model=%s",
            conversation_id,
            selected_provider,
            selected_model,
        )
    except OSError as exc:
        logger.info(
            "chat.llm_settings_not_saved conversation_id=%s provider=%s model=%s "
            "error_type=%s error=%s",
            conversation_id,
            selected_provider,
            selected_model,
            type(exc).__name__,
            _preview(exc, limit=DETAIL_CHARS),
        )
    # Real pydantic-ai agents get native tools registered; test doubles
    # skip gracefully and are driven by react_tools.run_envelope_loop below.
    # Local providers (llama-server via llm-advisor) are excluded: the
    # envelope loop only ever reads text, while the `tools` parameter forces
    # grammar-constrained decoding the local server rejects. Retrieval still
    # runs through run_envelope_loop → execute_tool_call.
    if is_external_provider(selected_provider):
        react_tools.register_retrieval_tools(agent, tool_ctx)

    # Bounded ReAct loop (max 3 tool rounds): the LLM either answers
    # directly (no envelope → zero retrieval) or emits a JSON tool-call
    # envelope per round. Decision rounds are collected silently; only the
    # final grounded answer streams as tokens, preserving the SSE order.

    privacy_mode = resolve_privacy_mode(settings)
    logger.info(
        "chat.privacy_gate_armed conversation_id=%s provider=%s privacy_mode=%s consent=%s "
        "matter_scoped=%s",
        conversation_id,
        selected_provider,
        privacy_mode,
        body.consent,
        body.matter_id is not None,
    )

    # Domain errors (PrivacyViolationError / ConsentRequiredError) propagate
    # to the stream below, which converts them to in-stream error frames —
    # the pre-stream 403 contract became `privacy_violation` /
    # `consent_required` error frames on an HTTP 200 response.
    def _decision_privacy(decision_prompt: str, *, has_matter_evidence: bool) -> None:
        logger.info(
            "chat.decision_privacy_check conversation_id=%s provider=%s privacy_mode=%s "
            "consent=%s has_matter_evidence=%s prompt=%r",
            conversation_id,
            selected_provider,
            privacy_mode,
            body.consent,
            has_matter_evidence,
            _preview(decision_prompt),
        )
        check_privacy(
            decision_prompt,
            provider=selected_provider,
            privacy_mode=privacy_mode,
            consent=body.consent,
            has_matter_evidence=has_matter_evidence,
        )

    async def _event_stream() -> AsyncIterator[str]:
        """Single SSE pipeline: every status frame is emitted as its stage runs.

        Frame order stays statuses → citations → tokens → done on success;
        a mid-pipeline failure (privacy, consent, embedding, store) instead
        ends statuses → citations → error with NO done frame, so the HTTP
        response is 200 and the failure is signalled in-band. Conversation
        404 and InvalidModelError remain HTTP errors (raised pre-stream).
        """
        citations: list[dict[str, Any]] = []
        # Classifying fires before any pipeline work: the user sees activity
        # from the very first frame, not after retrieval finishes.
        yield _sse(_status_frame("classifying"))
        logger.info(
            "chat.stream_start conversation_id=%s provider=%s model=%s matter_id=%s top_k=%d",
            conversation_id,
            selected_provider,
            selected_model,
            body.matter_id,
            RETRIEVE_TOP_K,
        )
        try:
            # Forward progressive stages out of the ReAct loop; the loop
            # finishes by yielding the EnvelopeOutcome for the branches below.
            outcome: react_tools.EnvelopeOutcome | None = None
            async for event in react_tools.run_envelope_loop(
                query=body.content,
                agent=agent,
                ctx=tool_ctx,
                provider=selected_provider,
                privacy_check=_decision_privacy,
                prior_summary_max_chars=settings.REACT_PRIOR_SUMMARY_MAX_CHARS,
            ):
                if isinstance(event, react_tools.StageEvent):
                    logger.info(
                        "chat.stage conversation_id=%s stage=%s",
                        conversation_id,
                        event.stage,
                    )
                    yield _sse({"type": "status", "stage": event.stage, "message": event.message})
                else:
                    outcome = event
            assert outcome is not None  # the loop always yields an outcome on completion
            logger.info(
                "chat.envelope_outcome conversation_id=%s tool_rounds=%s matter_hits=%d "
                "authority_hits=%d out_of_scope=%s has_direct_text=%s has_provider_error=%s",
                conversation_id,
                outcome.tool_rounds,
                len(outcome.matter_raw),
                len(outcome.auth_raw),
                outcome.out_of_scope,
                outcome.direct_text is not None,
                outcome.provider_error is not None,
            )
            # Per-query retrieval trace: one markdown file per query under
            # backend/data/output/. Fire-and-forget (never raises; logs on failure).
            dump_retrieved_chunks(
                body.content,
                outcome.matter_raw,
                outcome.auth_raw,
                matter_id=body.matter_id,
                conversation_id=conversation_id,
                tool_rounds=outcome.tool_rounds,
            )

            if (
                outcome.provider_error is not None
                and not outcome.matter_raw
                and not outcome.auth_raw
            ):
                logger.info(
                    "chat.branch provider_unreachable conversation_id=%s tool_rounds=%s error=%s",
                    conversation_id,
                    outcome.tool_rounds,
                    _preview(outcome.provider_error, limit=DETAIL_CHARS),
                )
                metrics.incr("chat.error.provider_unreachable")
                yield _sse({"type": "citations", "citations": []})
                yield _sse(
                    _error_frame("provider_unreachable", outcome.provider_error, conversation_id)
                )
                await _persist_message(session, conversation_id, "", [])
                logger.info(
                    "chat.persist_done conversation_id=%s branch=provider_unreachable chars=0 "
                    "citations=0",
                    conversation_id,
                )
                return

            if outcome.out_of_scope:
                logger.info(
                    "chat.branch out_of_scope conversation_id=%s tool_rounds=%s lang=%s",
                    conversation_id,
                    outcome.tool_rounds,
                    detect_lang(body.content),
                )
                refusal = out_of_scope_reply(detect_lang(body.content))
                yield _sse({"type": "citations", "citations": []})
                async for frame in _batched_tokens(
                    refusal, batch_chars=settings.STREAM_BATCH_CHARS
                ):
                    yield _sse(frame)
                await _persist_message(session, conversation_id, refusal, [])
                logger.info(
                    "chat.persist_done conversation_id=%s branch=out_of_scope chars=%d citations=0",
                    conversation_id,
                    len(refusal),
                )
                yield _sse(
                    {
                        "type": "done",
                        "not_found": False,
                        "conversation_id": conversation_id,
                        "out_of_scope": True,
                    }
                )
                return

            if (
                not outcome.matter_raw
                and not outcome.auth_raw
                and outcome.tool_rounds == 0
                and outcome.direct_text is not None
            ):
                text: str = outcome.direct_text
                logger.info(
                    "chat.branch direct_text conversation_id=%s chars=%d preview=%r",
                    conversation_id,
                    len(text),
                    _preview(text),
                )
                yield _sse({"type": "citations", "citations": []})
                async for frame in _batched_tokens(text, batch_chars=settings.STREAM_BATCH_CHARS):
                    yield _sse(frame)
                await _persist_message(session, conversation_id, text, [])
                logger.info(
                    "chat.persist_done conversation_id=%s branch=direct_text chars=%d citations=0",
                    conversation_id,
                    len(text),
                )
                yield _sse({"type": "done", "not_found": False, "conversation_id": conversation_id})
                return

            # Rerank + prompt assembly (local work) — announced before it runs.
            yield _sse(_status_frame("assembling"))
            logger.info(
                "chat.rerank_start conversation_id=%s matter_hits=%d authority_hits=%d top_k=%d",
                conversation_id,
                len(outcome.matter_raw),
                len(outcome.auth_raw),
                RERANK_TOP_K,
            )
            matter_top = rerank(body.content, outcome.matter_raw, top_k=RERANK_TOP_K)
            auth_top = mmr_select(
                rerank(body.content, outcome.auth_raw, top_k=RERANK_TOP_K),
                top_k=RERANK_TOP_K,
            )
            citations = build_citations(matter_top, auth_top)
            prompt = assemble_prompt(body.content, matter_top, auth_top)
            logger.info(
                "chat.rerank_done conversation_id=%s matter_citations=%d authority_citations=%d "
                "prompt_chars=%d",
                conversation_id,
                len(matter_top),
                len(auth_top),
                len(prompt),
            )
            logger.info(
                "chat.answer_privacy_check conversation_id=%s provider=%s privacy_mode=%s "
                "consent=%s has_matter_evidence=%s prompt=%r",
                conversation_id,
                selected_provider,
                privacy_mode,
                body.consent,
                bool(matter_top),
                _preview(prompt),
            )
            check_privacy(
                prompt,
                provider=selected_provider,
                privacy_mode=privacy_mode,
                consent=body.consent,
                has_matter_evidence=bool(matter_top),
            )

            if not matter_top and not auth_top:
                text = provisional_not_found(detect_lang(body.content))
                logger.info(
                    "chat.branch provisional_not_found conversation_id=%s tool_rounds=%s chars=%d",
                    conversation_id,
                    outcome.tool_rounds,
                    len(text),
                )
                yield _sse({"type": "citations", "citations": []})
                async for frame in _batched_tokens(text, batch_chars=settings.STREAM_BATCH_CHARS):
                    yield _sse(frame)
                await _persist_message(session, conversation_id, text, [])
                logger.info(
                    "chat.persist_done conversation_id=%s branch=provisional_not_found "
                    "chars=%d citations=0",
                    conversation_id,
                    len(text),
                )
                yield _sse({"type": "done", "not_found": True, "conversation_id": conversation_id})
                return

            # Grounded answer: citations first, then drafting + token stream.
            yield _sse(_status_frame("drafting"))
            logger.info(
                "chat.drafting_start conversation_id=%s provider=%s citations=%d",
                conversation_id,
                selected_provider,
                len(citations),
            )
            yield _sse({"type": "citations", "citations": citations})

            parts: list[str] = []
            errored = False
            drafting_start = time.monotonic()
            try:
                async with agent.run_stream(prompt) as result:
                    async for chunk in result.stream_text(delta=True):
                        parts.append(chunk)
                        yield _sse({"type": "token", "text": chunk})
                logger.info(
                    "chat.drafting_tokens conversation_id=%s chunks=%d chars=%d",
                    conversation_id,
                    len(parts),
                    len("".join(parts)),
                )
            except Exception as exc:  # provider down mid-stream: degrade, never crash
                errored = True
                logger.info(
                    "chat.drafting_provider_error conversation_id=%s provider=%s chunks=%d "
                    "error_type=%s error=%s",
                    conversation_id,
                    selected_provider,
                    len(parts),
                    type(exc).__name__,
                    _preview(exc, limit=DETAIL_CHARS),
                )
                metrics.incr("chat.error.provider_unreachable")
                yield _sse(
                    _error_frame(
                        "provider_unreachable",
                        str(ProviderUnreachableError(provider=selected_provider, reason=str(exc))),
                        conversation_id,
                    )
                )
            finally:
                metrics.timing("chat.drafting.seconds", time.monotonic() - drafting_start)
                metrics.incr("chat.drafting.chars", len("".join(parts)))
                if not errored:
                    metrics.incr("chat.drafting.ok")

            await _persist_message(session, conversation_id, "".join(parts), citations)
            logger.info(
                "chat.persist_done conversation_id=%s branch=drafting chunks=%d chars=%d "
                "citations=%d errored=%s",
                conversation_id,
                len(parts),
                len("".join(parts)),
                len(citations),
                errored,
            )
            if not errored:
                yield _sse({"type": "done", "not_found": False, "conversation_id": conversation_id})
        except PrivacyViolationError as exc:
            logger.info(
                "chat.error privacy_violation conversation_id=%s provider=%s "
                "privacy_mode=%s error_type=%s error=%s",
                conversation_id,
                selected_provider,
                privacy_mode,
                type(exc).__name__,
                _preview(exc, limit=DETAIL_CHARS),
            )
            metrics.incr("chat.privacy.violation")
            metrics.incr("chat.error.privacy_violation")
            yield _sse({"type": "citations", "citations": citations})
            yield _sse(_error_frame("privacy_violation", str(exc), conversation_id))
        except ConsentRequiredError as exc:
            logger.info(
                "chat.error consent_required conversation_id=%s provider=%s privacy_mode=%s "
                "consent=%s error_type=%s error=%s",
                conversation_id,
                selected_provider,
                privacy_mode,
                body.consent,
                type(exc).__name__,
                _preview(exc, limit=DETAIL_CHARS),
            )
            metrics.incr("chat.privacy.consent_required")
            metrics.incr("chat.error.consent_required")
            yield _sse({"type": "citations", "citations": citations})
            yield _sse(_error_frame("consent_required", str(exc), conversation_id))
        except svc.EmbeddingUnavailableError as exc:
            logger.info(
                "chat.error embedding_unreachable conversation_id=%s error_type=%s error=%s",
                conversation_id,
                type(exc).__name__,
                _preview(exc, limit=DETAIL_CHARS),
            )
            metrics.incr("chat.error.embedding_unreachable")
            # No assistant row is persisted for any of these failures: the
            # turn must not look answered in the transcript.
            yield _sse({"type": "citations", "citations": citations})
            yield _sse(
                _error_frame("embedding_unreachable", f"{exc} — retry shortly", conversation_id)
            )
        except svc.SearchUnavailableError as exc:
            logger.info(
                "chat.error search_unreachable conversation_id=%s error_type=%s error=%s",
                conversation_id,
                type(exc).__name__,
                _preview(exc, limit=DETAIL_CHARS),
            )
            metrics.incr("chat.error.search_unreachable")
            yield _sse({"type": "citations", "citations": citations})
            yield _sse(_error_frame("search_unreachable", str(exc), conversation_id))

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


# LEGACY — not used by the frontend, not covered by the SSE contract test
# (chat_rag, POST /api/v1/chat, is the supported path); no retrieval, no
# citations, no conversation persistence; only tests/test_privacy.py covers it.
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
            await _send_error(ws, "invalid_payload", f"malformed chat payload: {exc.errors()}")
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
            agent = llm_mod.get_agent(provider=resolved.provider, model=resolved.model)
            async with agent.run_stream(payload.content) as result:
                async for chunk in result.stream_text(delta=True):
                    await ws.send_text(json.dumps({"type": "token", "text": chunk}))
            await ws.send_text(json.dumps({"type": "done"}))
        except Exception as exc:  # provider down, bad key, timeout: degrade, never crash
            await _send_error(
                ws,
                "provider_unreachable",
                str(ProviderUnreachableError(provider=resolved.provider, reason=str(exc))),
            )
