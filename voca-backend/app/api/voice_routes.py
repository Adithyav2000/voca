"""
Voice routes — Twilio TwiML, status callbacks, and Media Stream WebSocket.

POST /api/voice/twiml/{call_task_id}    — Returns TwiML that opens a <Stream>
POST /api/voice/status/{call_task_id}   — Twilio call lifecycle events
WS   /api/voice/ws/{call_task_id}       — Twilio Media Stream ↔ OpenAI Realtime bridge
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from sqlalchemy import select
from starlette.requests import Request

from app.config import get_settings
from app.core.database import CallTask, Session as DbSession, get_session_factory
from app.services.realtime_bridge import handle_twilio_media_stream

logger = structlog.get_logger(__name__)
voice_router = APIRouter(prefix="/api/voice", tags=["voice"])


# ---------------------------------------------------------------------------
# helper: load call context from DB for bridging
# ---------------------------------------------------------------------------

async def _load_call_context(call_task_id: str) -> dict | None:
    """Fetch the CallTask + parent Session so the bridge knows session_id, provider info, etc."""
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(CallTask).where(CallTask.id == UUID(call_task_id))
        )
        ct = r.scalar_one_or_none()
        if ct is None:
            return None
        r2 = await db.execute(
            select(DbSession).where(DbSession.id == ct.session_id)
        )
        sess = r2.scalar_one_or_none()
        return {
            "session_id": str(ct.session_id),
            "user_id": str(sess.user_id) if sess else "",
            "provider_name": ct.provider_name,
            "provider_phone": ct.provider_phone,
            "service_type": sess.service_type if sess else "appointment",
            "target_date": str(sess.preferred_date) if sess and sess.preferred_date else None,
            "target_time": sess.preferred_time if sess else None,
        }


# ---------------------------------------------------------------------------
# TwiML endpoint — Twilio fetches this when the outbound call connects
# ---------------------------------------------------------------------------

@voice_router.api_route("/twiml/{call_task_id}", methods=["GET", "POST"])
async def twiml_endpoint(call_task_id: str, request: Request) -> Response:
    """
    Return TwiML that tells Twilio to open a bidirectional Media Stream WebSocket
    back to our /api/voice/ws/{call_task_id} endpoint.
    """
    settings = get_settings()
    base_url = settings.PUBLIC_API_URL.rstrip("/")
    ws_scheme = "wss" if base_url.startswith("https") else "ws"
    ws_host = base_url.replace("https://", "").replace("http://", "")
    ws_url = f"{ws_scheme}://{ws_host}/api/voice/ws/{call_task_id}"

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "  <Connect>"
        f'    <Stream url="{ws_url}" />'
        "  </Connect>"
        "</Response>"
    )
    logger.info("twiml_served", call_task_id=call_task_id, ws_url=ws_url, event_type="voice")
    return Response(content=twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# Status callback — Twilio POSTs call lifecycle events here
# ---------------------------------------------------------------------------

@voice_router.post("/status/{call_task_id}")
async def status_callback(call_task_id: str, request: Request) -> dict:
    """
    Twilio status callback. Logs events and updates CallTask on terminal states.
    """
    form = await request.form()
    call_status = form.get("CallStatus", "")
    call_sid = form.get("CallSid", "")
    log = logger.bind(call_task_id=call_task_id, call_sid=call_sid, event_type="voice")
    log.info("twilio_status_callback", status=call_status)

    # Update DB on terminal statuses
    if call_status in ("no-answer", "busy", "failed", "canceled"):
        from datetime import datetime, timezone
        from sqlalchemy import update

        status_map = {
            "no-answer": "no_answer",
            "busy": "rejected",
            "failed": "error",
            "canceled": "cancelled",
        }
        db_status = status_map.get(call_status, "error")
        factory = get_session_factory()
        async with factory() as db:
            await db.execute(
                update(CallTask)
                .where(CallTask.id == UUID(call_task_id))
                .values(
                    status=db_status,
                    ended_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()
        log.info("call_task_terminal", db_status=db_status)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# WebSocket — Twilio Media Stream connects here
# ---------------------------------------------------------------------------

@voice_router.websocket("/ws/{call_task_id}")
async def media_stream_ws(websocket: WebSocket, call_task_id: str) -> None:
    """
    Accept the Twilio Media Stream WebSocket and hand off to the realtime bridge.
    """
    log = logger.bind(call_task_id=call_task_id, event_type="voice")

    ctx = await _load_call_context(call_task_id)
    if ctx is None:
        log.warning("ws_call_task_not_found")
        await websocket.close(code=4004, reason="call_task not found")
        return

    await websocket.accept()
    log.info("twilio_ws_accepted", provider=ctx["provider_name"])

    try:
        await handle_twilio_media_stream(
            twilio_ws=websocket,
            call_task_id=call_task_id,
            session_id=ctx["session_id"],
            user_id=ctx["user_id"],
            provider_name=ctx["provider_name"],
            provider_phone=ctx["provider_phone"],
            service_type=ctx["service_type"],
            target_date=ctx["target_date"],
            target_time=ctx["target_time"],
        )
    except WebSocketDisconnect:
        log.info("twilio_ws_disconnect_clean")
    except Exception as e:
        log.exception("media_stream_error", error=str(e))
    finally:
        log.info("media_stream_ended")
