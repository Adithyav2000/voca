"""
Squad orchestrator — RFC 3.2: Phase 1 + create session in DB, spawn call-agent tasks.
State machine RFC 3.1: CREATED -> PROVIDER_LOOKUP -> DIALING -> NEGOTIATING -> RANKING -> CONFIRMED.
Match quality: Earliest Time 50%, Rating 30%, Proximity 20% (Challenge 2.3).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from openai import AsyncOpenAI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.database import Session, CallTask, get_session_factory
from app.models.schemas import (
    SessionIntent,
    SessionRequest,
    Provider,
    SquadPlan,
)
from app.services.calendar_service import get_appointment_service
from app.services.provider_service import get_provider_service
from app.utils.date_parse import parse_date_flexible, parse_time_flexible

logger = structlog.get_logger(__name__)

MAX_CALL_AGENTS_LIVE = 15
SESSION_STALE_MINUTES = 5
SESSION_MONITOR_INTERVAL_SECONDS = 60
OPENAI_OUTBOUND_TIMEOUT = 30.0
WEIGHT_EARLIEST = 0.5
WEIGHT_RATING = 0.3
WEIGHT_PROXIMITY = 0.2

SERVICE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dentist": ("dentist", "dental", "tooth", "teeth", "orthodontist"),
    "doctor": ("doctor", "physician", "clinic", "checkup", "medical"),
    "mechanic": ("mechanic", "auto", "car", "oil change", "tire", "repair"),
    "hairdresser": ("hair", "haircut", "salon", "barber", "stylist"),
    "therapist": ("therapist", "therapy", "counselor", "counselling"),
    "veterinarian": ("vet", "veterinarian", "animal clinic"),
    "restaurant": ("restaurant", "dinner", "lunch", "brunch", "reserve", "reservation", "dining", "food", "eat"),
}

# GPT-4o brain instructions for OpenAI voice agent (Challenge 2.2)
OPENAI_BRAIN_INSTRUCTIONS = (
    "You are calling ON BEHALF OF THE CUSTOMER (e.g. Alex Carter). You are the caller; the other party is the receptionist. Never act as or speak for the receptionist. "
    "All times are in Pacific (PST/PDT, America/Los_Angeles). When you say times, use Pacific. "
    "You MUST use tools in this order; do not skip steps. "
    "1) check_availability: Call when the receptionist offers a date and time. Use target_date and target_time from context for date/time. "
    "2) report_slot_offer: Call immediately after check_availability returns 'held'. Use the same date and time you held, and the provider name the receptionist gave. Do not say the slot is confirmed until report_slot_offer is done. "
    "3) book_slot: When the receptionist agrees (e.g. 'Okay', 'That works', 'Sure', 'Yes'), you MUST call book_slot to finalize. Pass appointment_date, appointment_time, patient_name, patient_phone, provider_id, provider_name, provider_phone, duration_min (e.g. 30), session_id, call_task_id, user_id. Only AFTER book_slot returns success may you say the appointment is booked, confirmed, or in the system. Never say 'successfully booked', 'confirmed', or 'booked in the system' without having called book_slot first and received a successful response. If you have not yet called book_slot, say you will finalize the booking now and then call the tool. "
    "4) end_call: Call end_call ONLY after book_slot has been called and confirmed successful. When you have finished the booking and are saying goodbye, then call end_call with session_id, call_task_id, status 'completed', and hold_keys []. Do not call end_call before book_slot has succeeded. "
    "Use target_date and target_time from context for date/time. Never use past years (e.g. 2024). Do not invent provider details."
)


async def _transition_session_status(
    session_id: str, new_status: str, only_if_current: list[str] | None = None
) -> None:
    """RFC 3.1: Drive session state machine. If only_if_current is set, update only when status in list."""
    log = logger.bind(session_id=session_id, event_type="orchestrator", new_status=new_status)
    factory = get_session_factory()
    async with factory() as db_session:
        try:
            stmt = (
                update(Session)
                .where(Session.id == UUID(session_id))
                .values(status=new_status, updated_at=datetime.now(timezone.utc))
            )
            if only_if_current:
                stmt = stmt.where(Session.status.in_(only_if_current))
            r = await db_session.execute(stmt)
            await db_session.commit()
            if r.rowcount:
                log.info("session_state_transition", timestamp_ms=round(datetime.now(timezone.utc).timestamp() * 1000))
        except Exception as e:
            log.exception("session_state_transition_failed", error=str(e))


def _match_quality_score(
    offered_date_str: str,
    offered_time_str: str,
    rating: float,
    distance_km: float,
    ref_max_distance_km: float = 30.0,
) -> float:
    """Earliest 50%, Rating 30%, Proximity 20%. Normalized to 0..1."""
    try:
        from datetime import datetime as dt
        d = dt.strptime(offered_date_str + " " + offered_time_str, "%Y-%m-%d %H:%M")
        hours_until = (d - dt.now()).total_seconds() / 3600.0
        hours_until = max(0, min(hours_until, 24 * 14))  # cap 14 days
        earliest = 1.0 - (hours_until / (24 * 14))
    except Exception:
        earliest = 0.5
    rating_norm = rating / 5.0
    proximity = 1.0 - min(distance_km / ref_max_distance_km, 1.0)
    return WEIGHT_EARLIEST * earliest + WEIGHT_RATING * rating_norm + WEIGHT_PROXIMITY * proximity


def _default_target_date() -> str:
    """Return a sensible upcoming date (YYYY-MM-DD) so the agent never uses past years."""
    from datetime import date as dt_date
    d = dt_date.today() + timedelta(days=1)
    return d.isoformat()


def _guess_service_type(prompt: str) -> str:
    lower = prompt.lower()
    for service_type, keywords in SERVICE_KEYWORDS.items():
        if any(keyword in lower for keyword in keywords):
            # For restaurants, try to capture cuisine qualifier (e.g. "indian restaurant")
            if service_type == "restaurant":
                m = re.search(r"(\b\w+)\s+restaurant", lower)
                if m and m.group(1) not in ("a", "the", "any", "best", "good", "nice", "reserve"):
                    return f"{m.group(1)} restaurant"
            return service_type
    return "general"


def _guess_target_date(prompt: str) -> str | None:
    lower = prompt.lower()
    explicit = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", prompt)
    if explicit:
        return explicit.group(1)
    if "tomorrow" in lower:
        return (datetime.now().date() + timedelta(days=1)).isoformat()
    if "today" in lower:
        return datetime.now().date().isoformat()
    for weekday in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        if weekday in lower:
            return parse_date_flexible(lower)
    return None


def _guess_target_time(prompt: str) -> str | None:
    lower = prompt.lower()
    explicit = re.search(r"\b(\d{1,2}(?::\d{2})?\s?(?:am|pm))\b", lower)
    if explicit:
        return parse_time_flexible(explicit.group(1))
    for keyword in ("morning", "afternoon", "evening"):
        if keyword in lower:
            return keyword
    if "any time" in lower or "anytime" in lower or "as soon as possible" in lower:
        return "any"
    return None


def _guess_location_query(prompt: str, user_location: str) -> str | None:
    lower = prompt.lower()
    if "near me" in lower:
        return user_location.strip() or None

    match = re.search(r"\b(?:in|near|around|at)\s+([a-z0-9][a-z0-9\s,.-]+)", prompt, re.IGNORECASE)
    if match:
        candidate = re.split(
            r"\b(?:for|today|tomorrow|morning|afternoon|evening|next|on|with)\b",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" ,.")
        if candidate:
            return candidate

    return user_location.strip() or None


def _guess_urgency(prompt: str) -> str | None:
    lower = prompt.lower()
    for phrase in ("urgent", "asap", "as soon as possible", "immediately", "soon"):
        if phrase in lower:
            return phrase
    return None


def _analyze_intent_locally(prompt: str, user_location: str) -> SessionIntent:
    """Fallback intent extraction for local/demo mode when OpenAI is unavailable."""
    return SessionIntent(
        service_type=_guess_service_type(prompt),
        target_date=_guess_target_date(prompt),
        target_time=_guess_target_time(prompt),
        urgency=_guess_urgency(prompt),
        location_query=_guess_location_query(prompt, user_location),
        timezone=None,
    )


async def _run_call_agent(
    session_id: str,
    call_task_id: UUID,
    provider: Provider,
    dial_phone: str,
    event_type: str = "orchestrator",
    *,
    user_id: str = "",
    service_type: str = "dentist appointment",
    target_time: str | None = None,
    target_date: str | None = None,
    tz_str: str | None = None,
) -> None:
    """
    Single call agent task (RFC 3.2 Phase 2). Uses OpenAI to simulate conversation,
    then updates call_task in DB.
    """
    log = logger.bind(session_id=session_id, call_task_id=str(call_task_id), event_type=event_type)
    settings = get_settings()

    # OpenAI conversation initialization
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    # Prepare conversation context for OpenAI
    system_prompt = f"""You are a helpful voice assistant calling on behalf of a customer named Alex Carter.
You are calling a {service_type} provider to book an appointment.

Key information:
- Service needed: {service_type}
- Preferred date: {target_date or 'as soon as possible'}
- Preferred time: {target_time or 'flexible'}
- Provider: {provider.name}
- Phone: {dial_phone}
- Timezone: {tz_str or 'America/Los_Angeles'}

Your goal is to:
1. Greet the receptionist professionally
2. Inquire about availability for the requested date/time
3. Report the offered slot back
4. Confirm the booking

Be natural and conversational. If the receptionist refuses or has no availability, acknowledge politely and end the call."""

    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.execute(
                update(CallTask).where(CallTask.id == call_task_id).values(
                    status="ringing",
                    started_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            await _transition_session_status(session_id, "negotiating", only_if_current=["dialing"])
        except Exception as e:
            log.exception("call_agent_update_failed", error=str(e))
            return

    # Simulate OpenAI conversation
    try:
        # Create a mock conversation turn
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Call {provider.name} at {dial_phone} to check availability for {target_date} at {target_time}."},
        ]
        
        response = await client.chat.completions.create(
            model=settings.OPENAI_VOICE_MODEL,
            messages=messages,
            max_tokens=500,
            temperature=0.7,
        )
        
        log.info("openai_conversation_started", provider=provider.name, session_id=session_id)
    except Exception as e:
        log.exception("openai_conversation_error", error=str(e))

    # Simulate negotiation: after a short delay, "offer" first slot and set score
    import random
    await asyncio.sleep(random.uniform(0.5, 2.5))
    slot = provider.available_slots[0] if provider.available_slots else None
    if not slot:
        # Generate a simulated slot from the requested date/time
        from app.models.schemas import AvailableSlot
        base_date = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Convert keyword times to concrete HH:MM
        _time_keywords = {"morning": "09:00", "afternoon": "14:00", "evening": "19:00", "night": "20:00", "any": "10:00"}
        raw_time = target_time or "10:00"
        base_time = _time_keywords.get(raw_time.lower().strip(), raw_time)
        try:
            d = datetime.strptime(base_date, "%Y-%m-%d")
            d += timedelta(days=random.randint(-2, 2))
            base_date = d.strftime("%Y-%m-%d")
        except ValueError:
            pass
        try:
            h, m = int(base_time[:2]), int(base_time[3:5])
            h = max(8, min(17, h + random.randint(-2, 2)))
            base_time = f"{h:02d}:{m:02d}"
        except (ValueError, IndexError):
            base_time = "10:00"
        slot = AvailableSlot(date=base_date, time=base_time, duration_min=30)
    distance_km = provider.distance_km if provider.distance_km is not None else 5.0
    score = _match_quality_score(slot.date, slot.time, provider.rating, distance_km)
    async with factory() as session:
        try:
            from datetime import time as dt_time
            hour, minute = int(slot.time[:2]), int(slot.time[3:5])
            t = dt_time(hour, minute)
            from datetime import date as dt_date
            d = dt_date.fromisoformat(slot.date)
            await session.execute(
                update(CallTask).where(CallTask.id == call_task_id).values(
                    status="slot_offered",
                    offered_date=d,
                    offered_time=t,
                    offered_duration_min=slot.duration_min,
                    offered_doctor=slot.doctor,
                    score=round(score, 4),
                    distance_km=distance_km,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            await _transition_session_status(session_id, "ranking", only_if_current=["dialing", "negotiating"])
        except Exception as e:
            log.exception("call_agent_offer_failed", error=str(e))


async def bootstrap_session_record(request: SessionRequest, user_id: str) -> str:
    """
    Create the Session row in DB and return its UUID string immediately.
    This is the fast, non-blocking part so the HTTP response can return right away.
    """
    factory = get_session_factory()
    async with factory() as db_session:
        booking_session = Session(
            user_id=UUID(user_id),
            status="created",
            service_type="general",
            query_text=request.prompt,
            location_lat=request.location_lat,
            location_lng=request.location_lng,
            max_radius_km=10.0,
            weight_time=WEIGHT_EARLIEST,
            weight_rating=WEIGHT_RATING,
            weight_distance=WEIGHT_PROXIMITY,
        )
        db_session.add(booking_session)
        await db_session.flush()
        session_id = str(booking_session.id)
        await db_session.commit()

    logger.info(
        "session_created",
        session_id=session_id,
        event_type="orchestrator",
        timestamp_ms=round(datetime.now(timezone.utc).timestamp() * 1000),
    )
    return session_id


async def run_session_orchestration(
    orchestrator: SquadOrchestrator, request: SessionRequest, user_id: str, session_id: str
) -> SquadPlan:
    """
    Run provider lookup + spawn call-agent tasks for an already-bootstrapped session.
    Intended to be called as an asyncio background task after bootstrap_session_record().
    """
    factory = get_session_factory()
    await _transition_session_status(session_id, "provider_lookup")

    intent = await orchestrator._analyze_intent(request.prompt, request.user_location)
    logger.info("intent_analyzed", intent=intent.model_dump(), session_id=session_id, event_type="orchestrator")

    location = intent.location_query or request.user_location
    provider_svc = get_provider_service()
    origin_lat, origin_lng = request.location_lat, request.location_lng
    tz = await provider_svc.get_timezone(origin_lat, origin_lng)
    if tz:
        intent = intent.model_copy(update={"timezone": tz})
    providers = await provider_svc.search_providers(
        intent.service_type,
        location,
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        limit=MAX_CALL_AGENTS_LIVE,
    )
    if not providers:
        await _transition_session_status(session_id, "failed")
        raise ValueError("No providers found. Ensure Google Places API key is configured and location is valid.")
    n_tasks = min(MAX_CALL_AGENTS_LIVE, len(providers))

    providers = providers[:n_tasks]
    logger.info("providers_found", providers_found=len(providers), session_id=session_id, event_type="orchestrator")

    async with factory() as db_session:
        await db_session.execute(
            update(Session)
            .where(Session.id == UUID(session_id))
            .values(
                status="dialing",
                service_type=intent.service_type,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db_session.flush()
        call_tasks_list: list[CallTask] = []
        for i, p in enumerate(providers):
            ct = CallTask(
                session_id=UUID(session_id),
                provider_id=p.id,
                provider_name=p.name,
                provider_phone=p.phone,
                provider_rating=p.rating,
                distance_km=p.distance_km if p.distance_km is not None else 5.0,
                travel_time_min=p.travel_time_min,
                photo_url=p.photo_url,
                status="pending",
            )
            db_session.add(ct)
            call_tasks_list.append(ct)
        await db_session.commit()

    logger.info(
        "session_dialing",
        session_id=session_id,
        call_tasks=len(call_tasks_list),
        event_type="orchestrator",
        timestamp_ms=round(datetime.now(timezone.utc).timestamp() * 1000),
    )

    for i, ct in enumerate(call_tasks_list):
        provider = next((x for x in providers if x.id == ct.provider_id), None)
        if not provider:
            continue
        asyncio.create_task(
            _run_call_agent(
                session_id,
                ct.id,
                provider,
                provider.phone,
                user_id=user_id,
                service_type=intent.service_type or "dentist appointment",
                target_time=intent.target_time,
                target_date=intent.target_date,
                tz_str=intent.timezone,
            ),
            name=f"call_agent_{ct.id}",
        )

    return SquadPlan(session_id=session_id, intent=intent, providers=providers)


async def create_session_and_squad(
    orchestrator: SquadOrchestrator, request: SessionRequest, user_id: str
) -> SquadPlan:
    """
    Convenience wrapper: bootstrap DB record then run full orchestration inline.
    Prefer calling bootstrap_session_record + run_session_orchestration separately
    for non-blocking HTTP responses.
    """
    session_id = await bootstrap_session_record(request, user_id)
    return await run_session_orchestration(orchestrator, request, user_id, session_id)


class SquadOrchestrator:
    """Phase 1: intent analysis. create_session_and_squad does DB + spawn tasks."""

    def __init__(self, openai_client: AsyncOpenAI) -> None:
        self._client = openai_client

    async def create_squad_plan(self, request: SessionRequest) -> SquadPlan:
        """Legacy: plan only, no DB. Prefer create_session_and_squad for full flow."""
        intent = await self._analyze_intent(request.prompt, request.user_location)
        location = intent.location_query or request.user_location
        provider_svc = get_provider_service()
        origin_lat, origin_lng = request.location_lat, request.location_lng
        tz = await provider_svc.get_timezone(origin_lat, origin_lng)
        if tz:
            intent = intent.model_copy(update={"timezone": tz})
        providers = await provider_svc.search_providers(
            intent.service_type, location, origin_lat=origin_lat, origin_lng=origin_lng, limit=MAX_CALL_AGENTS_LIVE
        )
        if not providers:
            raise ValueError("No providers found. Ensure Google Places API key is configured and location is valid.")
        return SquadPlan(intent=intent, providers=providers[:MAX_CALL_AGENTS_LIVE])

    async def _analyze_intent(self, prompt: str, user_location: str) -> SessionIntent:
        settings = get_settings()
        if not settings.OPENAI_API_KEY.strip():
            logger.info("intent_analysis_fallback_local", reason="OPENAI_API_KEY not set", event_type="orchestrator")
            return _analyze_intent_locally(prompt, user_location)

        system = (
            "You extract ALL booking details from the user's message so we have date, time, location. "
            "Respond ONLY with a single JSON object. No markdown. "
            "Fields: service_type (required, e.g. dentist, mechanic, hairdresser, indian restaurant, italian restaurant, pizza place — keep qualifiers like cuisine type), "
            "target_date (YYYY-MM-DD or null if not specified or ASAP), "
            "target_time (morning|afternoon|evening|any or specific HH:MM 24h or null), "
            "urgency (string or null), "
            "location_query (city, area, address, or 'near me' / user_location; null only if no location given). "
            "Infer concrete date when user says 'tomorrow', 'next Friday', etc. Infer time when user says '10am', '3pm'. Use user_location when they say 'near me' or don't specify a place."
        )
        user = f"User location: {user_location}\n\nUser message: {prompt}"
        try:
            response = await self._client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw = response.choices[0].message.content or ""
            if not raw.strip():
                raise ValueError("LLM returned empty intent JSON")
            data: dict[str, Any] = json.loads(raw.strip())
            target_date = data.get("target_date")
            if target_date and isinstance(target_date, str):
                try:
                    from datetime import date as dt_date
                    parsed = dt_date.fromisoformat(target_date.strip()[:10])
                    if parsed < dt_date.today():
                        target_date = None
                except (ValueError, TypeError):
                    pass
            return SessionIntent(
                service_type=data.get("service_type", "").strip() or "general",
                target_date=target_date,
                target_time=data.get("target_time"),
                urgency=data.get("urgency"),
                location_query=data.get("location_query") or user_location,
                timezone=None,
            )
        except Exception as e:
            logger.warning("intent_analysis_openai_failed", error=str(e), event_type="orchestrator")
            return _analyze_intent_locally(prompt, user_location)


async def run_session_stale_monitor() -> None:
    """
    RFC 3.1: Background monitor. If a session stays in DIALING or NEGOTIATING for more than
    5 minutes without update, set it to FAILED and release all holds.
    """
    log = logger.bind(event_type="orchestrator", component="stale_monitor")
    threshold = datetime.now(timezone.utc) - timedelta(minutes=SESSION_STALE_MINUTES)
    factory = get_session_factory()
    appointment_svc = get_appointment_service()
    async with factory() as db_session:
        r = await db_session.execute(
            select(Session).where(
                Session.status.in_(["dialing", "negotiating"]),
                Session.updated_at < threshold,
            )
        )
        stale = list(r.scalars().all())
    for stale_session in stale:
        sid = str(stale_session.id)
        log.info("session_stale_failing", session_id=sid, timestamp_ms=round(datetime.now(timezone.utc).timestamp() * 1000))
        await _transition_session_status(sid, "failed", only_if_current=["dialing", "negotiating"])
        async with factory() as db_session:
            r2 = await db_session.execute(select(CallTask).where(CallTask.session_id == stale_session.id))
            tasks = r2.scalars().all()
        hold_keys: list[str] = []
        for ct in tasks:
            hold_keys.extend(ct.hold_keys or [])
        if hold_keys:
            await appointment_svc.release_holds_for_session(hold_keys, session_id_for_log=sid)
        async with factory() as db_session:
            await db_session.execute(
                update(Session).where(Session.id == stale_session.id).values(updated_at=datetime.now(timezone.utc))
            )
            await db_session.commit()


async def session_stale_monitor_loop() -> None:
    """Run stale session monitor every SESSION_MONITOR_INTERVAL_SECONDS."""
    while True:
        try:
            await run_session_stale_monitor()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("session_stale_monitor_error", error=str(e), event_type="orchestrator")
        await asyncio.sleep(SESSION_MONITOR_INTERVAL_SECONDS)
