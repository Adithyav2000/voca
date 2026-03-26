"""
Twilio outbound call service.
Places a call via Twilio REST API and stores the call SID on the CallTask.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import lru_cache
from uuid import UUID

import structlog
from sqlalchemy import update
from twilio.rest import Client as TwilioClient

from app.config import get_settings
from app.core.database import CallTask, get_session_factory

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _get_twilio_client() -> TwilioClient:
    settings = get_settings()
    return TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def voice_enabled() -> bool:
    """Return True when both Twilio and a public callback URL are configured."""
    settings = get_settings()
    return bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and settings.TWILIO_PHONE_NUMBER
        and settings.PUBLIC_API_URL
    )


async def initiate_outbound_call(
    call_task_id: UUID,
    provider_phone: str,
    session_id: str,
) -> str:
    """
    Place an outbound call via Twilio to *provider_phone*.
    TwiML URL → our /api/voice/twiml/{call_task_id} endpoint.
    Status callback → /api/voice/status/{call_task_id}.

    Returns the Twilio Call SID.
    """
    settings = get_settings()
    base_url = settings.PUBLIC_API_URL.rstrip("/")
    log = logger.bind(
        call_task_id=str(call_task_id),
        session_id=session_id,
        provider_phone=provider_phone,
        event_type="voice",
    )

    twiml_url = f"{base_url}/api/voice/twiml/{call_task_id}"
    status_url = f"{base_url}/api/voice/status/{call_task_id}"

    log.info("placing_outbound_call", twiml_url=twiml_url)

    # Twilio client is sync — run in a thread to avoid blocking the event loop
    client = _get_twilio_client()
    call = await asyncio.to_thread(
        client.calls.create,
        to=provider_phone,
        from_=settings.TWILIO_PHONE_NUMBER,
        url=twiml_url,
        status_callback=status_url,
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        status_callback_method="POST",
        record=False,
    )

    call_sid = call.sid
    log.info("outbound_call_placed", call_sid=call_sid)

    # Persist the SID on the CallTask
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(
            update(CallTask)
            .where(CallTask.id == call_task_id)
            .values(
                twilio_call_sid=call_sid,
                status="ringing",
                started_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    return call_sid


async def hangup_call(call_sid: str) -> None:
    """Hang up an in-progress Twilio call."""
    log = logger.bind(call_sid=call_sid, event_type="voice")
    try:
        client = _get_twilio_client()
        await asyncio.to_thread(
            client.calls(call_sid).update,
            status="completed",
        )
        log.info("call_hung_up")
    except Exception as e:
        log.warning("hangup_failed", error=str(e))
