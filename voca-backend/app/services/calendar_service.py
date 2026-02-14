"""
Unified AppointmentService: PostgreSQL for persistence, Redis for hot state (RFC 3.3, 3.6).
All operations are async. No mock logic — real check_and_hold_slot and confirm_and_book.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Appointment, Session, CallTask
from app.core.redis import get_redis
from app.services.google_calendar import create_calendar_event, is_calendar_busy

logger = structlog.get_logger(__name__)

# RFC 3.6: soft lock TTL 3 minutes
HOLD_TTL_SECONDS = 180
# RFC 3.3: booking lock TTL 60 seconds
BOOKING_LOCK_TTL_SECONDS = 60


def _hold_key(user_id: str, d: str, t: str) -> str:
    return f"hold:{user_id}:{d}:{t}"


def _booking_lock_key(session_id: str) -> str:
    return f"lock:session:{session_id}:booked"


def _kill_channel(session_id: str) -> str:
    return f"kill:{session_id}"


class AppointmentService:
    """Unified service for slot holds and booking. Uses Postgres + Redis only."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    async def _redis_client(self) -> Redis:
        if self._redis is not None:
            return self._redis
        return await get_redis()

    async def check_and_hold_slot(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        session_id: str,
        call_task_id: str,
        date_str: str,
        time_str: str,
        duration_minutes: int = 30,
        session_id_for_log: str | None = None,
    ) -> dict[str, Any]:
        """
        RFC 6.1 & 3.6: (a) Check Postgres appointments for hard conflict.
        (b) Redis SET hold:{user_id}:{date}:{time} NX EX 180 for soft lock.
        Returns status: 'held' | 'conflict' | 'soft_conflict' with payload.
        """
        cid = session_id_for_log or session_id
        log = logger.bind(session_id=cid, event_type="appointment_service")

        # Normalize time to HH:MM
        t = time_str.strip()[:5] if len(time_str.strip()) >= 5 else time_str.strip()
        d = date_str.strip()

        # Parse date/time first (required for calendar and DB check)
        try:
            parsed_date = date.fromisoformat(d)
        except ValueError:
            log.warning("check_and_hold_slot_invalid_date", date_str=date_str)
            return {
                "status": "conflict",
                "conflicts": ["Invalid date"],
                "held_by": None,
                "next_free_slot": None,
                "hold_expires_in_seconds": None,
            }

        try:
            hour, minute = int(t[:2]), int(t[3:5])
            parsed_time = time(hour, minute)
        except (ValueError, IndexError):
            log.warning("check_and_hold_slot_invalid_time", time_str=time_str)
            return {
                "status": "conflict",
                "conflicts": ["Invalid time"],
                "held_by": None,
                "next_free_slot": None,
                "hold_expires_in_seconds": None,
            }

        # (0) Google Calendar (user OAuth): if user has confirmed events in this slot, return conflict (RFC 6.1)
        try:
            busy, conflict_summaries = await is_calendar_busy(
                user_id=user_id,
                calendar_id="primary",
                slot_date=parsed_date,
                slot_time=parsed_time,
                duration_minutes=duration_minutes,
            )
        except Exception as e:
            log.warning("calendar_busy_check_error", error=str(e))
            busy, conflict_summaries = False, []
        if busy and conflict_summaries:
            log.info("check_and_hold_slot_calendar_conflict", user_id=user_id, date=d, time=t, conflicts=conflict_summaries)
            return {
                "status": "conflict",
                "conflicts": conflict_summaries,
                "held_by": None,
                "next_free_slot": None,
                "hold_expires_in_seconds": None,
            }

        # (a) Check Postgres for existing appointment at this slot (hard conflict)
        result = await session.execute(
            select(Appointment).where(
                Appointment.user_id == user_id,
                Appointment.appointment_date == parsed_date,
                Appointment.appointment_time == parsed_time,
                Appointment.status == "confirmed",
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            log.info("check_and_hold_slot_conflict", user_id=user_id, date=d, time=t)
            return {
                "status": "conflict",
                "conflicts": [f"Appointment at {existing.provider_name}"],
                "held_by": None,
                "next_free_slot": None,
                "hold_expires_in_seconds": None,
            }

        # (b) Redis soft lock: SET NX EX 180
        key = _hold_key(user_id, d, t)
        value = f"{session_id}:{call_task_id}"
        redis = await self._redis_client()
        acquired = await redis.set(key, value, nx=True, ex=HOLD_TTL_SECONDS)

        if acquired:
            log.info("check_and_hold_slot_held", user_id=user_id, date=d, time=t, hold_key=key)
            return {
                "status": "held",
                "conflicts": [],
                "held_by": None,
                "next_free_slot": None,
                "hold_expires_in_seconds": HOLD_TTL_SECONDS,
                "hold_key": key,  # RFC 3.6: caller must append to CallTask.hold_keys
            }

        # Key exists: another session holds it (soft_conflict)
        existing_val = await redis.get(key) or ""
        held_by = existing_val.split(":")[0] if ":" in existing_val else "other_session"
        log.info("check_and_hold_slot_soft_conflict", user_id=user_id, date=d, time=t, held_by=held_by)
        return {
            "status": "soft_conflict",
            "conflicts": [],
            "held_by": held_by,
            "next_free_slot": None,
            "hold_expires_in_seconds": None,
        }

    async def confirm_and_book(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        call_task_id: str,
        user_id: str,
        provider_id: str,
        provider_name: str,
        provider_phone: str,
        provider_address: str | None,
        appointment_date: date,
        appointment_time: time,
        duration_min: int,
        doctor_name: str | None,
        hold_keys_to_release: list[str],
        session_id_for_log: str | None = None,
    ) -> tuple[bool, str | None]:
        """
        RFC 3.3 & 6.3: (0) Check user calendar for conflict; (a) Redis lock; (b) Persist; (c) Release holds; (d) Kill.
        Returns (success, reason_if_failed, calendar_synced).
        """
        cid = session_id_for_log or session_id
        log = logger.bind(session_id=cid, event_type="appointment_service")
        redis = await self._redis_client()
        calendar_synced = False

        # Validate UUIDs so we never raise in persist (agent must send real session_id / call_task_id from dynamic variables)
        try:
            UUID(session_id)
            UUID(call_task_id)
        except (ValueError, TypeError, AttributeError):
            log.warning("confirm_and_book_invalid_uuid", session_id=session_id, call_task_id=call_task_id)
            return False, "Use the session_id and call_task_id UUIDs from the start of this call (dynamic variables).", False

        # (0) Check user's Google Calendar for conflict before confirming (RFC 6.1)
        try:
            busy, conflict_summaries = await is_calendar_busy(
                user_id=user_id,
                calendar_id="primary",
                slot_date=appointment_date,
                slot_time=appointment_time,
                duration_minutes=duration_min,
            )
            if busy and conflict_summaries:
                msg = f"Your calendar has a conflict: {', '.join(conflict_summaries)}"
                log.info("confirm_and_book_calendar_conflict", user_id=user_id, conflicts=conflict_summaries)
                return False, msg, False
        except Exception as e:
            log.warning("confirm_and_book_calendar_check_error", error=str(e))
            # Proceed with booking; calendar sync may still work

        lock_key = _booking_lock_key(session_id)
        acquired = await redis.set(lock_key, str(call_task_id), nx=True, ex=BOOKING_LOCK_TTL_SECONDS)
        if not acquired:
            log.warning("confirm_and_book_lock_failed", session_id=session_id)
            return False, "Booking lock already held by another call", False

        try:
            appointment = Appointment(
                session_id=UUID(session_id),
                call_task_id=UUID(call_task_id),
                user_id=user_id,
                provider_id=provider_id,
                provider_name=provider_name,
                provider_phone=provider_phone,
                provider_address=provider_address,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                duration_min=duration_min,
                doctor_name=doctor_name,
                status="confirmed",
            )
            session.add(appointment)
            await session.flush()

            # Update session: confirmed_call_task_id, status
            await session.execute(
                update(Session).where(Session.id == UUID(session_id)).values(
                    status="confirmed",
                    confirmed_call_task_id=UUID(call_task_id),
                    updated_at=datetime.now(timezone.utc),
                )
            )

            await session.flush()

            # (b2) Google Calendar (user OAuth): create event for the confirmed appointment (RFC 3.3)
            try:
                event_id = await create_calendar_event(
                    user_id=user_id,
                    calendar_id="primary",
                    summary=f"Appointment: {provider_name}",
                    start_date=appointment_date,
                    start_time=appointment_time,
                    duration_minutes=duration_min,
                    description=f"Booked via VOCA. Provider: {provider_phone}",
                )
                if event_id and appointment.id:
                    await session.execute(
                        update(Appointment).where(Appointment.id == appointment.id).values(
                            google_event_id=event_id,
                            calendar_synced=True,
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    calendar_synced = True
                    log.info("confirm_and_book_calendar_synced", user_id=user_id, google_event_id=event_id)
            except Exception as cal_e:
                log.warning("confirm_and_book_calendar_failed", error=str(cal_e))

            await session.flush()
        except Exception as e:
            await session.rollback()
            log.exception("confirm_and_book_persist_failed", error=str(e))
            await redis.delete(lock_key)
            return False, str(e), False

        # (c) Release all other hold keys for this session
        for key in hold_keys_to_release:
            await redis.delete(key)
        log.info("confirm_and_book_holds_released", released=len(hold_keys_to_release), keys=hold_keys_to_release)

        # (d) Publish kill signal
        channel = _kill_channel(session_id)
        await redis.publish(channel, "confirm")
        log.info("confirm_and_book_kill_published", channel=channel)

        return True, None, calendar_synced

    async def release_holds_for_session(
        self,
        hold_keys: list[str],
        session_id_for_log: str | None = None,
    ) -> None:
        """Release a list of hold keys (e.g. on end-call or cancel)."""
        if not hold_keys:
            return
        redis = await self._redis_client()
        for key in hold_keys:
            await redis.delete(key)
        logger.info(
            "release_holds",
            session_id=session_id_for_log,
            event_type="appointment_service",
            released=len(hold_keys),
            keys=hold_keys,
        )


def get_appointment_service(redis: Redis | None = None) -> AppointmentService:
    return AppointmentService(redis=redis)
