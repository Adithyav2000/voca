"""
HTTP routes (RFC Section 2, Appendix A). Dashboard, state machine, manual overrides.
All under /api. 10s timeout on tool calls.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.database import Appointment, Session, CallTask, User, get_db_session, get_session_factory
from app.core.redis import get_redis
from app.models import (
    BookSlotRequest,
    BookSlotResponse,
    SessionRequest,
    CheckAvailabilityRequest,
    CheckAvailabilityResponse,
    ConfirmSessionRequest,
    EndCallRequest,
    GetDistanceRequest,
    GetDistanceResponse,
    ReportSlotOfferRequest,
    ReportSlotOfferResponse,
    SquadPlan,
)
from app.api.auth import get_current_user_id
from app.services.calendar_service import get_appointment_service
from app.services.orchestrator import SquadOrchestrator, bootstrap_session_record, run_session_orchestration, _transition_session_status
from app.services.tools import dispatch_tool_call
from app.utils.date_parse import parse_date_flexible, parse_time_flexible
from openai import AsyncOpenAI

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api", tags=["api"])

TOOL_TIMEOUT_SECONDS = 10  # used for tool calls (check-availability, book-slot, etc.)
SSE_POLL_INTERVAL = 2.0
SSE_PING_INTERVAL = 30


async def _orchestrate_background(
    orchestrator: SquadOrchestrator, body: SessionRequest, user_id: str, session_id: str
) -> None:
    """Run provider lookup + agent tasks as a fire-and-forget background coroutine."""
    try:
        await run_session_orchestration(orchestrator, body, user_id, session_id)
    except Exception as e:
        logger.exception("orchestration_bg_error", session_id=session_id, error=str(e), event_type="routes")
        await _transition_session_status(session_id, "failed")


@router.post("/sessions", response_model=SquadPlan)
async def create_session(
    request: Request,
    body: SessionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> SquadPlan:
    """
    Create session in DB and spawn 15 concurrent call-agent tasks (RFC 3.2, Challenge 2.3).
    Requires authenticated user (session cookie). Match quality: Earliest 50%, Rating 30%, Proximity 20%.
    Automatically geocodes user_location to coordinates if location_lat/lng not provided.
    """
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    # Ensure user exists (e.g. after DB reset or stale cookie)
    try:
        uid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid session. Please sign in again.")
    r = await session.execute(select(User).where(User.id == uid))
    if r.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=401,
            detail="Your session is no longer valid (e.g. after a database reset). Please sign in again.",
        )
    
    # If coordinates not provided, geocode from user_location
    location_lat = body.location_lat
    location_lng = body.location_lng
    if location_lat is None or location_lng is None:
        from app.services.provider_service import ProviderService
        provider_svc = ProviderService()
        coords = await provider_svc.geocode(body.user_location)
        if coords:
            location_lat, location_lng = coords
            logger.info("location_geocoded", location=body.user_location, lat=location_lat, lng=location_lng, event_type="routes")
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Could not geocode location '{body.user_location}'. Please check the location or provide coordinates.",
            )
    
    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    orchestrator = SquadOrchestrator(openai_client=client)
    # Update body with geocoded coordinates for orchestrator
    body.location_lat = location_lat
    body.location_lng = location_lng

    # Step 1: Bootstrap DB record immediately (fast, < 100 ms)
    try:
        session_id = await bootstrap_session_record(body, user_id=user_id)
    except Exception as e:
        logger.exception("session_bootstrap_error", error=str(e), event_type="routes")
        raise HTTPException(status_code=500, detail=str(e)) from e

    # Step 2: Kick off provider lookup + agent orchestration as a background task (non-blocking)
    asyncio.create_task(
        _orchestrate_background(orchestrator, body, user_id, session_id),
        name=f"orchestrate_{session_id}",
    )

    # Return session_id immediately — frontend streams live updates via SSE
    return SquadPlan(session_id=session_id)


@router.post("/check-availability", response_model=CheckAvailabilityResponse)
async def check_availability(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> CheckAvailabilityResponse:
    """
    RFC 6.1: Call AppointmentService.check_and_hold_slot. Accepts flexible date/time (e.g. 'Friday', '10 AM') for OpenAI voice agent.
    """
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    # OpenAI agent may send date_str/time_str; our schema expects date/time
    if raw.get("date_str") is not None and not raw.get("date"):
        raw["date"] = raw["date_str"]
    if raw.get("time_str") is not None and not raw.get("time"):
        raw["time"] = raw["time_str"]
    # Normalize date/time for agent (e.g. "Friday" -> YYYY-MM-DD, "10 AM" -> 10:00)
    if raw.get("date"):
        parsed = parse_date_flexible(str(raw["date"]))
        if parsed:
            raw["date"] = parsed
    if raw.get("time"):
        parsed = parse_time_flexible(str(raw["time"]))
        if parsed:
            raw["time"] = parsed
    try:
        body = CheckAvailabilityRequest(**raw)
    except Exception as e:
        logger.warning("check_availability_validation", error=str(e), raw=raw, event_type="routes")
        raise HTTPException(status_code=422, detail=f"Invalid request. Use date YYYY-MM-DD and time HH:MM 24h. Error: {e}") from e
    session_id = body.session_id
    call_task_id = body.call_task_id
    user_id = body.user_id
    log = logger.bind(session_id=session_id, event_type="routes")
    svc = get_appointment_service()
    try:
        result = await asyncio.wait_for(
            svc.check_and_hold_slot(
                session,
                user_id=user_id,
                session_id=session_id,
                call_task_id=call_task_id,
                date_str=body.date,
                time_str=body.time,
                duration_minutes=body.duration_minutes,
                session_id_for_log=session_id,
            ),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.warning("check_availability_timeout", timeout_sec=TOOL_TIMEOUT_SECONDS)
        raise HTTPException(status_code=503, detail="Availability check timed out") from None
    except Exception as e:
        log.exception("check_availability_error", error=str(e))
        raise HTTPException(status_code=503, detail="Availability service unavailable") from e

    # RFC 3.6: Append successful hold key to CallTask.hold_keys for cleanup (non-negotiable)
    if result.get("status") == "held" and result.get("hold_key"):
        try:
            r = await session.execute(select(CallTask).where(CallTask.id == UUID(call_task_id)))
            ct = r.scalar_one_or_none()
            if ct is not None:
                current = list(ct.hold_keys) if ct.hold_keys else []
                if result["hold_key"] not in current:
                    current.append(result["hold_key"])
                    await session.execute(
                        update(CallTask)
                        .where(CallTask.id == UUID(call_task_id))
                        .values(hold_keys=current, updated_at=datetime.now(timezone.utc))
                    )
                    await session.flush()
                    log.info("call_task_hold_key_appended", call_task_id=call_task_id, hold_key=result["hold_key"])
        except Exception as e:
            log.warning("hold_key_append_failed", call_task_id=call_task_id, error=str(e))

    return CheckAvailabilityResponse(
        status=result["status"],
        conflicts=result.get("conflicts", []),
        held_by=result.get("held_by"),
        next_free_slot=result.get("next_free_slot"),
        hold_expires_in_seconds=result.get("hold_expires_in_seconds"),
    )


@router.post("/book-slot", response_model=BookSlotResponse)
async def book_slot(
    body: BookSlotRequest,
    session: AsyncSession = Depends(get_db_session),
) -> BookSlotResponse:
    """
    RFC 6.3 & 3.3: Call AppointmentService.confirm_and_book (lock, persist, release holds, kill).
    """
    log = logger.bind(session_id=body.session_id, event_type="routes")
    try:
        parsed_date = date.fromisoformat(body.appointment_date)
        hour, minute = int(body.appointment_time[:2]), int(body.appointment_time[3:5])
        parsed_time = time(hour, minute)
    except (ValueError, IndexError) as e:
        log.warning("book_slot_invalid_datetime", error=str(e))
        raise HTTPException(status_code=422, detail="Invalid date or time") from e

    svc = get_appointment_service()
    try:
        success, reason, _calendar_synced = await asyncio.wait_for(
            svc.confirm_and_book(
                session,
                session_id=body.session_id,
                call_task_id=body.call_task_id,
                user_id=body.user_id,
                provider_id=body.provider_id,
                provider_name=body.provider_name,
                provider_phone=body.provider_phone,
                provider_address=body.provider_address,
                appointment_date=parsed_date,
                appointment_time=parsed_time,
                duration_min=body.duration_min,
                doctor_name=body.doctor_name,
                hold_keys_to_release=body.hold_keys_to_release,
                session_id_for_log=body.session_id,
            ),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.warning("book_slot_timeout", timeout_sec=TOOL_TIMEOUT_SECONDS)
        raise HTTPException(status_code=504, detail="Booking timed out") from None
    except Exception as e:
        log.exception("book_slot_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e
    return BookSlotResponse(booked=success, reason=reason)


@router.post("/end-call")
async def end_call(
    body: EndCallRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """
    Update call task status in DB and release Redis holds (RFC 3.3 Step 5).
    If call_task_id is missing or not a valid UUID, resolve from session's call tasks (use first active or latest).
    """
    from datetime import datetime, timezone
    from uuid import UUID

    from sqlalchemy import update

    from app.core.database import CallTask

    log = logger.bind(session_id=body.session_id, event_type="routes")
    call_task_id = (body.call_task_id or "").strip()
    if call_task_id:
        try:
            UUID(call_task_id)
        except (ValueError, TypeError):
            call_task_id = ""
    if not call_task_id:
        try:
            c_uid = UUID(body.session_id)
            r = await session.execute(
                select(CallTask).where(CallTask.session_id == c_uid).order_by(CallTask.updated_at.desc()).limit(1)
            )
            ct = r.scalar_one_or_none()
            if ct:
                call_task_id = str(ct.id)
                log.info("end_call_resolved_task", call_task_id=call_task_id)
        except (ValueError, TypeError):
            pass
    if not call_task_id:
        log.warning("end_call_missing_call_task_id", session_id=body.session_id)
        return {"status": "error", "message": "Missing call_task_id and could not resolve from session"}
    try:
        await session.execute(
            update(CallTask)
            .where(CallTask.id == UUID(call_task_id))
            .values(
                status=body.status,
                ended_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()
    except Exception as e:
        log.exception("end_call_update_failed", error=str(e))
    svc = get_appointment_service()
    try:
        await asyncio.wait_for(
            svc.release_holds_for_session(body.hold_keys, session_id_for_log=body.session_id),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.warning("end_call_release_timeout")
    except Exception as e:
        log.exception("end_call_error", error=str(e))
    return {"status": "ok", "message": "Call ended and hold keys released"}


@router.post("/report-slot-offer", response_model=ReportSlotOfferResponse)
async def report_slot_offer_route(
    body: ReportSlotOfferRequest,
) -> ReportSlotOfferResponse:
    """
    OpenAI voice agent reports a calendar-held slot. Persists to CallTask, transitions session to RANKING.
    """
    log = logger.bind(session_id=body.session_id, call_task_id=body.call_task_id, event_type="routes")
    from app.services.tools import report_slot_offer
    try:
        result_json = await asyncio.wait_for(
            report_slot_offer(
                provider_name=body.provider_name,
                date_str=body.date,
                time_str=body.time,
                duration_minutes=body.duration_minutes,
                doctor_name=body.doctor_name,
                session_id=body.session_id,
                call_task_id=body.call_task_id,
            ),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
        out = json.loads(result_json)
        # instruction must be "continue_holding" or "terminate" per schema
        inst = out.get("instruction") or "continue_holding"
        if inst not in ("continue_holding", "terminate"):
            inst = "continue_holding"
        return ReportSlotOfferResponse(
            received=out.get("received", False),
            ranking_position=out.get("ranking_position", 1),
            instruction=inst,
        )
    except asyncio.TimeoutError:
        log.warning("report_slot_offer_timeout")
        return ReportSlotOfferResponse(received=False, ranking_position=0, instruction="continue_holding")
    except Exception as e:
        log.exception("report_slot_offer_error", error=str(e))
        return ReportSlotOfferResponse(received=False, ranking_position=0, instruction="continue_holding")


@router.post("/get-distance", response_model=GetDistanceResponse)
async def get_distance_route(body: GetDistanceRequest) -> GetDistanceResponse:
    """
    RFC 6.4: OpenAI voice agent tool — distance and travel time to destination. Uses Google Distance Matrix when GOOGLE_API_KEY set.
    """
    from app.services.tools import get_distance
    try:
        result_json = await asyncio.wait_for(
            get_distance(destination_address=body.destination_address),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
        out = json.loads(result_json)
        return GetDistanceResponse(
            distance_km=float(out.get("distance_km", 5.0)),
            travel_time_min=int(out.get("travel_time_min", 12)),
            mode=out.get("mode", "driving"),
        )
    except (asyncio.TimeoutError, ValueError) as e:
        logger.warning("get_distance_failed", error=str(e), event_type="routes")
        return GetDistanceResponse(distance_km=5.0, travel_time_min=12, mode="driving")


class AgenticToolRequest(BaseModel):
    """Unified webhook: OpenAI voice agent sends tool_name + arguments."""
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/tools")
async def agentic_tool_webhook(body: AgenticToolRequest) -> Any:
    """
    Single webhook URL for OpenAI voice Agentic Functions. POST with {"tool_name": "...", "arguments": {...}}.
    Dispatches to check_availability, book_slot, report_slot_offer, or get_distance. Returns JSON string result.
    """
    log = logger.bind(tool_name=body.tool_name, event_type="routes")
    try:
        result = await asyncio.wait_for(
            dispatch_tool_call(body.tool_name, body.arguments),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
        return json.loads(result) if result.strip().startswith("{") else {"result": result}
    except asyncio.TimeoutError:
        log.warning("agentic_tool_timeout")
        raise HTTPException(status_code=504, detail="Tool timeout") from None
    except Exception as e:
        log.exception("agentic_tool_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


# ----- Read endpoints & dashboard (RFC Appendix A) -----


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """Get session status and metadata."""
    try:
        uid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid session ID")
    factory = get_session_factory()
    async with factory() as db_session:
        r = await db_session.execute(select(Session).where(Session.id == uid))
        booking_session = r.scalar_one_or_none()
    if not booking_session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": str(booking_session.id),
        "user_id": str(booking_session.user_id),
        "status": booking_session.status,
        "service_type": booking_session.service_type,
        "query_text": booking_session.query_text,
        "created_at": booking_session.created_at.isoformat() if booking_session.created_at else None,
        "updated_at": booking_session.updated_at.isoformat() if booking_session.updated_at else None,
        "confirmed_call_task_id": str(booking_session.confirmed_call_task_id) if booking_session.confirmed_call_task_id else None,
    }


def _serialize_call_task(ct: CallTask) -> dict:
    return {
        "id": str(ct.id),
        "session_id": str(ct.session_id),
        "provider_id": ct.provider_id,
        "provider_name": ct.provider_name,
        "provider_phone": ct.provider_phone,
        "status": ct.status,
        "score": ct.score,
        "offered_date": ct.offered_date.isoformat() if ct.offered_date else None,
        "offered_time": ct.offered_time.strftime("%H:%M") if ct.offered_time else None,
        "offered_duration_min": ct.offered_duration_min,
        "offered_doctor": ct.offered_doctor,
        "hold_keys": ct.hold_keys or [],
        "started_at": ct.started_at.isoformat() if ct.started_at else None,
        "ended_at": ct.ended_at.isoformat() if ct.ended_at else None,
        "updated_at": ct.updated_at.isoformat() if ct.updated_at else None,
        "photo_url": ct.photo_url,
        "twilio_call_sid": ct.twilio_call_sid,
        "transcript": ct.transcript or [],
    }


@router.get("/sessions/{session_id}/stream")
async def session_stream(session_id: str):
    """
    SSE stream for real-time squad status. Yields JSON on CallTask status change.
    30-second :ping heartbeat for reverse proxies.
    """
    try:
        uid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid session ID")

    async def event_stream():
        last_snapshot: str | None = None
        last_audit_len = 0
        last_ping = asyncio.get_running_loop().time()
        log = logger.bind(session_id=session_id, event_type="stream")
        factory = get_session_factory()
        redis = await get_redis()
        while True:
            try:
                now = asyncio.get_running_loop().time()
                if now - last_ping >= SSE_PING_INTERVAL:
                    yield ": ping\n\n"
                    last_ping = now

                async with factory() as db_session:
                    r = await db_session.execute(
                        select(Session).where(Session.id == uid)
                    )
                    booking_session = r.scalar_one_or_none()
                    if not booking_session:
                        yield f"data: {json.dumps({'error': 'Session not found'})}\n\n"
                        return
                    r2 = await db_session.execute(
                        select(CallTask).where(CallTask.session_id == uid).order_by(CallTask.updated_at.desc())
                    )
                    tasks = list(r2.scalars().all())

                # Fetch audit events from Redis
                raw_audit = await redis.lrange(f"audit:{session_id}", 0, -1)
                audit_events = [json.loads(e) for e in raw_audit]
                cur_audit_len = len(audit_events)

                payload = {
                    "session_id": session_id,
                    "session_status": booking_session.status,
                    "updated_at": booking_session.updated_at.isoformat() if booking_session.updated_at else None,
                    "call_tasks": [_serialize_call_task(t) for t in tasks],
                    "audit_events": audit_events,
                }
                snapshot = json.dumps(payload, default=str)
                if snapshot != last_snapshot or cur_audit_len != last_audit_len:
                    yield f"data: {snapshot}\n\n"
                    last_snapshot = snapshot
                    last_audit_len = cur_audit_len
                    log.info("stream_event", timestamp_ms=round(datetime.now(timezone.utc).timestamp() * 1000))

                if booking_session.status in ("confirmed", "failed", "cancelled"):
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception("stream_error", error=str(e))
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                break
            await asyncio.sleep(SSE_POLL_INTERVAL)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{session_id}/results")
async def session_results(session_id: str) -> dict:
    """Return ranked list of slot offers (CallTasks with offers), sorted by match quality score."""
    try:
        uid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid session ID")
    factory = get_session_factory()
    async with factory() as db_session:
        r = await db_session.execute(
            select(CallTask)
            .where(CallTask.session_id == uid)
            .where(CallTask.status == "slot_offered")
            .order_by(CallTask.score.desc().nulls_last())
        )
        tasks = list(r.scalars().all())
    return {
        "session_id": session_id,
        "offers": [_serialize_call_task(t) for t in tasks],
    }


@router.post("/sessions/{session_id}/confirm")
async def confirm_session(session_id: str, body: ConfirmSessionRequest, db_session: AsyncSession = Depends(get_db_session)) -> dict:
    """
    Force confirm selected slot. Triggers confirm_and_book and kill signal.
    """
    log = logger.bind(session_id=session_id, event_type="routes")
    try:
        c_uid = UUID(session_id)
        ct_uid = UUID(body.call_task_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid session or call_task ID")

    async with get_session_factory()() as sess:
        r = await sess.execute(select(Session).where(Session.id == c_uid))
        booking_session = r.scalar_one_or_none()
        if not booking_session:
            raise HTTPException(status_code=404, detail="Session not found")
        r2 = await sess.execute(select(CallTask).where(CallTask.id == ct_uid).where(CallTask.session_id == c_uid))
        winning = r2.scalar_one_or_none()
        if not winning:
            raise HTTPException(status_code=404, detail="Call task not found or not in this session")
        if not winning.offered_date or not winning.offered_time:
            raise HTTPException(status_code=422, detail="Selected call task has no slot offer")
        r3 = await sess.execute(select(CallTask).where(CallTask.session_id == c_uid))
        all_tasks = list(r3.scalars().all())
    hold_keys_to_release = []
    for t in all_tasks:
        if t.id != ct_uid and getattr(t, "hold_keys", None) and isinstance(t.hold_keys, list):
            hold_keys_to_release.extend(t.hold_keys)

    user_id = str(booking_session.user_id)
    appointment_date = winning.offered_date
    appointment_time = winning.offered_time
    duration_min = winning.offered_duration_min or 30
    svc = get_appointment_service()
    success, reason, calendar_synced = await asyncio.wait_for(
        svc.confirm_and_book(
            db_session,
            session_id=session_id,
            call_task_id=body.call_task_id,
            user_id=user_id,
            provider_id=winning.provider_id,
            provider_name=winning.provider_name,
            provider_phone=winning.provider_phone,
            provider_address=None,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            duration_min=duration_min,
            doctor_name=winning.offered_doctor,
            hold_keys_to_release=hold_keys_to_release,
            session_id_for_log=session_id,
        ),
        timeout=TOOL_TIMEOUT_SECONDS,
    )
    if not success:
        log.warning("confirm_failed", reason=reason)
        raise HTTPException(status_code=409, detail=reason or "Booking failed")
    log.info("session_confirmed", call_task_id=body.call_task_id, calendar_synced=calendar_synced, timestamp_ms=round(datetime.now(timezone.utc).timestamp() * 1000))
    return {"status": "confirmed", "call_task_id": body.call_task_id, "calendar_synced": calendar_synced, "message": "Slot confirmed and kill signal sent"}


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str) -> dict:
    """Cancel session, publish kill, release all Redis holds."""
    log = logger.bind(session_id=session_id, event_type="routes")
    try:
        c_uid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid session ID")

    factory = get_session_factory()
    async with factory() as db_session:
        r = await db_session.execute(select(Session).where(Session.id == c_uid))
        booking_session = r.scalar_one_or_none()
        if not booking_session:
            raise HTTPException(status_code=404, detail="Session not found")
        await db_session.execute(
            update(Session).where(Session.id == c_uid).values(status="cancelled", updated_at=datetime.now(timezone.utc))
        )
        await db_session.commit()
        r2 = await db_session.execute(select(CallTask).where(CallTask.session_id == c_uid))
        tasks = list(r2.scalars().all())
    hold_keys = []
    for t in tasks:
        if getattr(t, "hold_keys", None) and isinstance(t.hold_keys, list):
            hold_keys.extend(t.hold_keys)
    if hold_keys:
        svc = get_appointment_service()
        await svc.release_holds_for_session(hold_keys, session_id_for_log=session_id)
    redis_client = await get_appointment_service()._redis_client()
    await redis_client.publish(f"kill:{session_id}", "cancel")
    log.info("session_cancelled", timestamp_ms=round(datetime.now(timezone.utc).timestamp() * 1000))
    return {"status": "cancelled", "message": "Session cancelled, kill signal sent, holds released"}


# ---------------------------------------------------------------------------
# Intervene — hang up a specific call task's Twilio call
# ---------------------------------------------------------------------------

class InterveneRequest(BaseModel):
    call_task_id: str


@router.post("/sessions/{session_id}/intervene")
async def intervene_call(session_id: str, body: InterveneRequest) -> dict:
    """Hang up a live Twilio call so the user can take over or stop it."""
    log = logger.bind(session_id=session_id, call_task_id=body.call_task_id, event_type="routes")
    try:
        ct_uid = UUID(body.call_task_id)
        s_uid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid ID")

    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(CallTask).where(CallTask.id == ct_uid).where(CallTask.session_id == s_uid)
        )
        ct = r.scalar_one_or_none()
        if not ct:
            raise HTTPException(status_code=404, detail="Call task not found")
        call_sid = ct.twilio_call_sid

    if not call_sid:
        log.warning("intervene_no_call_sid")
        raise HTTPException(status_code=409, detail="No active Twilio call for this task")

    from app.services.voice_service import hangup_call
    await hangup_call(call_sid)

    async with factory() as db:
        await db.execute(
            update(CallTask)
            .where(CallTask.id == ct_uid)
            .values(status="cancelled", ended_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        )
        await db.commit()

    log.info("call_intervened", call_sid=call_sid)
    return {"status": "intervened", "call_task_id": body.call_task_id, "message": "Call hung up"}


# ---------------------------------------------------------------------------
# Audit trail — live events for a session
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}/audit")
async def session_audit(session_id: str) -> dict:
    """Return all audit events stored for a session."""
    try:
        UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid session ID")
    r = await get_redis()
    raw_events = await r.lrange(f"audit:{session_id}", 0, -1)
    events = [json.loads(e) for e in raw_events]
    return {"session_id": session_id, "events": events}


class ExtractEntitiesRequest(BaseModel):
    """Request body for POST /api/extract-entities."""
    text: str = Field(..., min_length=1, max_length=500, description="User input text to extract entities from")


class ExtractedEntity(BaseModel):
    label: str
    value: str


class ExtractEntitiesResponse(BaseModel):
    entities: list[ExtractedEntity]


@router.post("/extract-entities", response_model=ExtractEntitiesResponse)
async def extract_entities(body: ExtractEntitiesRequest, request: Request) -> ExtractEntitiesResponse:
    """
    Use OpenAI to extract structured entities (Service, Date, Time, Location, Urgency) from user text.
    Returns detected entities as label-value pairs for real-time UI display.
    """
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    system_prompt = (
        "You are an entity extractor for a booking assistant. "
        "Extract entities from the user's text and return them as a JSON object with an \"entities\" array. "
        "Each entity has a \"label\" and a \"value\". "
        "Labels must be one of: Service, Date, Time, Location, Urgency. "
        "For Date values: normalize to a human-readable form (e.g. 'tomorrow', 'next monday', '2026-03-26'). "
        "For Time values: normalize to a readable form (e.g. '3pm', 'morning', '10:00 AM'). "
        "For Location: extract the place name only, without prepositions. "
        "For Service: extract the service type (e.g. 'dentist', 'doctor', 'mechanic'). "
        "For Urgency: extract urgency keywords (e.g. 'asap', 'urgent'). "
        "Only include entities that are clearly present. Do not guess or hallucinate. "
        "If no entities are found, return an empty array. "
        "Example input: 'find dentist at malvern tomorrow 3pm' "
        "Example output: {\"entities\": [{\"label\": \"Service\", \"value\": \"dentist\"}, "
        "{\"label\": \"Location\", \"value\": \"malvern\"}, "
        "{\"label\": \"Date\", \"value\": \"tomorrow\"}, "
        "{\"label\": \"Time\", \"value\": \"3pm\"}]}"
    )

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": body.text},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=200,
            ),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        entities_raw = parsed.get("entities", [])
        entities = []
        valid_labels = {"Service", "Date", "Time", "Location", "Urgency"}
        for e in entities_raw:
            if isinstance(e, dict) and e.get("label") in valid_labels and e.get("value"):
                entities.append(ExtractedEntity(label=e["label"], value=str(e["value"]).strip()))
        return ExtractEntitiesResponse(entities=entities)
    except asyncio.TimeoutError:
        logger.warning("extract_entities_timeout", event_type="routes")
        raise HTTPException(status_code=504, detail="Entity extraction timed out") from None
    except Exception as e:
        logger.exception("extract_entities_error", error=str(e), event_type="routes")
        raise HTTPException(status_code=500, detail="Entity extraction failed") from e


@router.get("/appointments")
async def list_appointments(request: Request) -> dict:
    """Return the current user's confirmed appointments from PostgreSQL (RFC Appendix A)."""
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    factory = get_session_factory()
    async with factory() as session:
        r = await session.execute(
            select(Appointment)
            .where(Appointment.user_id == user_id)
            .where(Appointment.status == "confirmed")
            .order_by(Appointment.created_at.desc())
        )
        rows = list(r.scalars().all())
    return {
        "appointments": [
            {
                "id": str(a.id),
                "session_id": str(a.session_id),
                "call_task_id": str(a.call_task_id),
                "user_id": a.user_id,
                "provider_id": a.provider_id,
                "provider_name": a.provider_name,
                "provider_phone": a.provider_phone,
                "appointment_date": a.appointment_date.isoformat(),
                "appointment_time": a.appointment_time.strftime("%H:%M"),
                "duration_min": a.duration_min,
                "doctor_name": a.doctor_name,
                "calendar_synced": a.calendar_synced,
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ],
    }
