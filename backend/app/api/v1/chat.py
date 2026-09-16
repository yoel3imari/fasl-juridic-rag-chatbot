"""Chat streaming route: WebSocket /api/v1/chat/stream.

Accepts JSON {matter_id, conversation_id?, content, consent?, system?},
runs the matter-privacy check first, then streams assistant tokens.
Every failure is a structured {"type": "error", ...} frame; the socket
never crashes unhandled.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, ValidationError

from app import llm as llm_mod
from app.config import Settings

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: int
    conversation_id: int | None = None
    content: str
    consent: bool = False
    system: str | None = None


async def _send_error(ws: WebSocket, code: str, detail: str) -> None:
    await ws.send_text(json.dumps({"type": "error", "code": code, "detail": detail}))


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
