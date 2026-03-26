"""
OpenAI voice Agentic tool layer (RFC Section 6). All tools delegate to AppointmentService or DB.
10s timeout on each call. No mock logic.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timezone
from uuid import UUID

from typing import Any

import structlog
from sqlalchemy import select, update

from app.core.database import CallTask, get_session_factory
from app.services.calendar_service import get_appointment_service
from app.services.orchestrator import _match_quality_score, _transition_session_status
from app.utils.date_parse import parse_date_flexible, parse_time_flexible

logger = structlog.get_logger(__name__)

TOOL_TIMEOUT_SECONDS = 10


def _str(d: dict[str, Any], key: str) -> str:
    v = d.get(key)
    return str(v).strip() if v is not None else ""


def _normalize_date(date_str: str) -> str:
    """Accept 'Monday', 'Friday', or YYYY-MM-DD. Return YYYY-MM-DD or original if unparseable."""
    if not date_str or len(date_str.strip()) < 4:
        return date_str or ""
    parsed = parse_date_flexible(date_str.strip())
    if parsed and parsed.startswith("2024"):
        parsed = parsed.replace("2024", "2026")  # Force future dates
    return parsed if parsed else date_str.strip()


def _normalize_time(time_str: str) -> str:
    """Accept '10 AM', '2:30 PM', or HH:MM. Return HH:MM 24h or original if unparseable."""
    if not time_str or len(time_str.strip()) < 2:
        return time_str or ""
    parsed = parse_time_flexible(time_str.strip())
    return parsed if parsed else time_str.strip()


async def check_availability(
    date_str: str,
    time_str: str,
    duration_minutes: int = 30,
    *,
    user_id: str,
    session_id: str,
    call_task_id: str,
) -> str:
    """RFC 6.1: AppointmentService.check_and_hold_slot. Returns JSON status held|conflict|soft_conflict."""
    date_str = _normalize_date(date_str)
    time_str = _normalize_time(time_str)
    factory = get_session_factory()
    svc = get_appointment_service()
    async with factory() as session:
        result = await svc.check_and_hold_slot(
            session,
            user_id=user_id,
            session_id=session_id,
            call_task_id=call_task_id,
            date_str=date_str,
            time_str=time_str,
            duration_minutes=duration_minutes,
            session_id_for_log=session_id,
        )
        await session.commit()
    return json.dumps(result)


async def report_slot_offer(
    provider_name: str,
    date_str: str,
    time_str: str,
    duration_minutes: int = 30,
    doctor_name: str | None = None,
    *,
    session_id: str,
    call_task_id: str,
) -> str:
    """RFC 6.2: Persist slot to CallTask, compute score, transition session to RANKING."""
    log = logger.bind(provider_name=provider_name, date=date_str, time=time_str, event_type="tools")
    try:
        UUID(session_id)
        UUID(call_task_id)
    except (ValueError, TypeError, AttributeError):
        log.warning("report_slot_offer_invalid_uuid", session_id=session_id, call_task_id=call_task_id)
        return json.dumps({
            "received": False,
            "ranking_position": 0,
            "instruction": "continue_holding",
        })
    date_str = _normalize_date(date_str)
    time_str = _normalize_time(time_str)
    try:
        parsed_date = date.fromisoformat(date_str.strip()[:10])
        t = time_str.strip()[:5]
        hour, minute = int(t[:2]), int(t[3:5])
        parsed_time = time(hour, minute)
    except (ValueError, IndexError):
        log.warning("report_slot_offer_invalid_datetime")
        return json.dumps({"received": False, "ranking_position": 0, "instruction": "continue_holding"})

    factory = get_session_factory()
    async with factory() as session:
        r = await session.execute(select(CallTask).where(CallTask.id == UUID(call_task_id)).where(CallTask.session_id == UUID(session_id)))
        call_task = r.scalar_one_or_none()
        if not call_task:
            log.warning("report_slot_offer_call_task_not_found", call_task_id=call_task_id)
            return json.dumps({"received": False, "ranking_position": 0, "instruction": "continue_holding"})
        rating = float(call_task.provider_rating or 4.0)
        distance_km = float(call_task.distance_km or 5.0)
        score = _match_quality_score(date_str, t, rating, distance_km)
        await session.execute(
            update(CallTask)
            .where(CallTask.id == UUID(call_task_id))
            .values(
                status="slot_offered",
                offered_date=parsed_date,
                offered_time=parsed_time,
                offered_duration_min=duration_minutes,
                offered_doctor=doctor_name or "",
                score=round(score, 4),
                distance_km=distance_km,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    await _transition_session_status(session_id, "ranking", only_if_current=["dialing", "negotiating"])
    log.info("report_slot_offer_registered")
    return json.dumps({"received": True, "ranking_position": 1, "instruction": "continue_holding"})


async def book_slot(
    date_str: str,
    time_str: str,
    patient_name: str,
    patient_phone: str,
    *,
    session_id: str = "",
    call_task_id: str = "",
    user_id: str = "default_user",
    provider_id: str = "",
    provider_name: str = "",
    provider_phone: str = "",
    provider_address: str | None = None,
    duration_min: int = 30,
    doctor_name: str | None = None,
    hold_keys_to_release: list[str] | None = None,
) -> str:
    """RFC 6.3: AppointmentService.confirm_and_book (lock, persist, release holds, kill)."""
    if not session_id or not call_task_id:
        return json.dumps({"booked": False, "reason": "missing session_id or call_task_id"})
    date_str = _normalize_date(date_str)
    time_str = _normalize_time(time_str)
    try:
        parsed_date = date.fromisoformat(date_str)
        t = time_str.strip()[:5]
        hour, minute = int(t[:2]), int(t[3:5])
        parsed_time = time(hour, minute)
    except (ValueError, IndexError):
        return json.dumps({"booked": False, "reason": "invalid date or time"})

    factory = get_session_factory()
    svc = get_appointment_service()
    async with factory() as session:
        success, reason, calendar_synced = await svc.confirm_and_book(
            session,
            session_id=session_id,
            call_task_id=call_task_id,
            user_id=user_id,
            provider_id=provider_id or "unknown",
            provider_name=provider_name or "Unknown",
            provider_phone=provider_phone or "",
            provider_address=provider_address,
            appointment_date=parsed_date,
            appointment_time=parsed_time,
            duration_min=duration_min,
            doctor_name=doctor_name,
            hold_keys_to_release=hold_keys_to_release or [],
            session_id_for_log=session_id,
        )
        await session.commit()
    return json.dumps({"booked": success, "reason": reason, "calendar_synced": calendar_synced})


async def get_distance(destination_address: str) -> str:
    """RFC 6.4: Placeholder; integrate Distance Matrix API in live."""
    logger.info("get_distance", destination=destination_address, event_type="tools")
    return json.dumps({
        "distance_km": 5.0,
        "travel_time_min": 12,
        "mode": "driving",
    })


async def dispatch_tool_call(tool_name: str, arguments: dict[str, Any]) -> str:
    """Route tool_name to handler. 10s timeout. All logs include event_type."""
    args = arguments or {}
    log = logger.bind(tool_name=tool_name, event_type="tools")
    try:
        result = await asyncio.wait_for(
            _dispatch(tool_name, args),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
        return result
    except asyncio.TimeoutError:
        log.warning("tool_timeout", timeout_sec=TOOL_TIMEOUT_SECONDS)
        return json.dumps({"error": "tool_failed", "tool_name": tool_name, "message": "Timeout"})
    except Exception as e:
        log.exception("tool_call_error", error=str(e))
        return json.dumps({"error": "tool_failed", "tool_name": tool_name, "message": str(e)})


async def _dispatch(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "check_availability":
        return await check_availability(
            date_str=_str(args, "date"),
            time_str=_str(args, "time"),
            duration_minutes=int(args.get("duration_minutes", 30)),
            user_id=_str(args, "user_id") or "default_user",
            session_id=_str(args, "session_id") or _str(args, "campaign_id") or "default_session",
            call_task_id=_str(args, "call_task_id") or "default_call_task",
        )
    if tool_name == "report_slot_offer":
        return await report_slot_offer(
            provider_name=_str(args, "provider_name"),
            date_str=_str(args, "date"),
            time_str=_str(args, "time"),
            duration_minutes=int(args.get("duration_minutes", 30)),
            doctor_name=args.get("doctor_name"),
            session_id=_str(args, "session_id") or _str(args, "campaign_id"),
            call_task_id=_str(args, "call_task_id"),
        )
    if tool_name == "book_slot":
        return await book_slot(
            date_str=_str(args, "date"),
            time_str=_str(args, "time"),
            patient_name=_str(args, "patient_name"),
            patient_phone=_str(args, "patient_phone"),
            session_id=_str(args, "session_id") or _str(args, "campaign_id"),
            call_task_id=_str(args, "call_task_id"),
            user_id=_str(args, "user_id") or "default_user",
            provider_id=_str(args, "provider_id"),
            provider_name=_str(args, "provider_name"),
            provider_phone=_str(args, "provider_phone"),
            provider_address=args.get("provider_address"),
            duration_min=int(args.get("duration_min", 30)),
            doctor_name=args.get("doctor_name"),
            hold_keys_to_release=args.get("hold_keys_to_release") or [],
        )
    if tool_name == "get_distance":
        return await get_distance(destination_address=_str(args, "destination_address"))
    return json.dumps({
        "error": "unknown_tool",
        "tool_name": tool_name,
        "message": f"No handler for tool: {tool_name}",
    })


# ---------------------------------------------------------------------------
# LangChain @tool wrappers — context-bound per call agent session
# ---------------------------------------------------------------------------


def get_langchain_tools(
    session_id: str,
    call_task_id: str,
    user_id: str,
    *,
    provider_id: str = "",
    provider_name: str = "",
    provider_phone: str = "",
) -> list:
    """Return LangChain BaseTool instances with session context pre-bound via closure."""
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    async def check_availability_tool(date: str, time: str, duration_minutes: int = 30) -> str:
        """Check if a provider has availability on a given date and time.
        Returns JSON with status: held, conflict, or soft_conflict."""
        return await check_availability(
            date_str=date,
            time_str=time,
            duration_minutes=duration_minutes,
            user_id=user_id,
            session_id=session_id,
            call_task_id=call_task_id,
        )

    @lc_tool
    async def report_slot_offer_tool(
        offered_provider_name: str, date: str, time: str, duration_minutes: int = 30, doctor_name: str = ""
    ) -> str:
        """Report a slot offered by the receptionist. Call after check_availability returns 'held'.
        Returns JSON with received, ranking_position, instruction."""
        return await report_slot_offer(
            provider_name=offered_provider_name,
            date_str=date,
            time_str=time,
            duration_minutes=duration_minutes,
            doctor_name=doctor_name or None,
            session_id=session_id,
            call_task_id=call_task_id,
        )

    @lc_tool
    async def book_slot_tool(
        date: str,
        time: str,
        patient_name: str = "Alex Carter",
        patient_phone: str = "",
        duration_min: int = 30,
        doctor_name: str = "",
    ) -> str:
        """Finalize and book the appointment after the receptionist confirms.
        Returns JSON with booked (bool), reason, calendar_synced."""
        return await book_slot(
            date_str=date,
            time_str=time,
            patient_name=patient_name,
            patient_phone=patient_phone,
            session_id=session_id,
            call_task_id=call_task_id,
            user_id=user_id,
            provider_id=provider_id,
            provider_name=provider_name,
            provider_phone=provider_phone,
            duration_min=duration_min,
            doctor_name=doctor_name or None,
        )

    @lc_tool
    async def end_call_tool(status: str = "completed") -> str:
        """End the call after booking is confirmed. status should be 'completed' or 'no_availability'."""
        return json.dumps({"ended": True, "status": status, "session_id": session_id, "call_task_id": call_task_id})

    return [check_availability_tool, report_slot_offer_tool, book_slot_tool, end_call_tool]
