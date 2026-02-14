"""
Google Calendar API: multi-user OAuth2. events.list (check busy) and events.insert (create event).
Uses get_user_calendar_client(user_id) with refresh token from DB; no service account.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

import structlog

from app.config import get_settings
from app.core.crypto import decrypt_refresh_token
from app.core.database import User, get_session_factory

logger = structlog.get_logger(__name__)
EVENT_TYPE = "google_calendar"
EXTERNAL_TIMEOUT = 5.0
DEFAULT_CALENDAR_ID = "primary"


def _build_calendar_service_sync(refresh_token: str, client_id: str, client_secret: str) -> Any:
    """Sync: build Calendar v3 service from refresh token; auto-refresh access token."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    creds.refresh(Request())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


async def get_user_calendar_client(user_id: str):
    """
    Fetch user's refresh token from DB, decrypt, build credentials (auto-refresh), return Calendar v3 service.
    Returns None if user not found, token invalid, or Google OAuth not configured.
    """
    try:
        uid = UUID(user_id)
    except (ValueError, TypeError):
        logger.warning("get_user_calendar_client_invalid_id", user_id=user_id, event_type=EVENT_TYPE)
        return None
    settings = get_settings()
    if not (settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET):
        return None
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select
        r = await session.execute(select(User).where(User.id == uid))
        user = r.scalar_one_or_none()
    if not user or not user.google_refresh_token:
        logger.warning("get_user_calendar_client_no_user_or_token", user_id=user_id, event_type=EVENT_TYPE)
        return None
    plain = decrypt_refresh_token(user.google_refresh_token, settings.ENCRYPTION_KEY)
    if not plain:
        logger.warning("get_user_calendar_client_decrypt_failed", user_id=user_id, event_type=EVENT_TYPE)
        return None
    try:
        service = await asyncio.wait_for(
            asyncio.to_thread(
                _build_calendar_service_sync,
                plain,
                settings.GOOGLE_OAUTH_CLIENT_ID,
                settings.GOOGLE_OAUTH_CLIENT_SECRET,
            ),
            timeout=EXTERNAL_TIMEOUT,
        )
        return service
    except asyncio.TimeoutError:
        logger.warning("get_user_calendar_client_timeout", user_id=user_id, event_type=EVENT_TYPE)
        return None
    except Exception as e:
        logger.exception("get_user_calendar_client_build_failed", user_id=user_id, error=str(e), event_type=EVENT_TYPE)
        return None


async def is_calendar_busy(
    user_id: str,
    calendar_id: str,
    slot_date: date,
    slot_time: time,
    duration_minutes: int,
) -> tuple[bool, list[str]]:
    """
    Return (True, conflict_summaries) if there is any confirmed event in the slot.
    Uses user's OAuth calendar client; calendar_id defaults to 'primary'.
    """
    cal_id = (calendar_id or "").strip() or DEFAULT_CALENDAR_ID
    service = await get_user_calendar_client(user_id)
    if not service:
        return False, []

    slot_dt = datetime.combine(slot_date, slot_time, tzinfo=timezone.utc)
    time_min = slot_dt.isoformat()
    time_max = (slot_dt + timedelta(minutes=duration_minutes)).isoformat()

    def _list():
        events_result = (
            service.events()
            .list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = events_result.get("items", [])
        return [e.get("summary", "Event") for e in events if e.get("status") == "confirmed"]

    try:
        summaries = await asyncio.wait_for(
            asyncio.to_thread(_list),
            timeout=EXTERNAL_TIMEOUT,
        )
        return bool(summaries), summaries
    except asyncio.TimeoutError:
        logger.warning("calendar_list_timeout", user_id=user_id, timeout_sec=EXTERNAL_TIMEOUT, event_type=EVENT_TYPE)
        return False, []
    except Exception as e:
        logger.exception("calendar_list_error", user_id=user_id, error=str(e), event_type=EVENT_TYPE)
        return False, []


async def create_calendar_event(
    user_id: str,
    calendar_id: str,
    summary: str,
    start_date: date,
    start_time: time,
    duration_minutes: int,
    description: str = "",
) -> str | None:
    """
    Create a calendar event in the user's calendar. calendar_id defaults to 'primary'.
    Returns event id or None on failure.
    """
    cal_id = (calendar_id or "").strip() or DEFAULT_CALENDAR_ID
    service = await get_user_calendar_client(user_id)
    if not service:
        logger.warning("create_calendar_event_skipped", user_id=user_id, reason="no client", event_type=EVENT_TYPE)
        return None

    start_dt = datetime.combine(start_date, start_time, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
    }

    def _insert():
        event = service.events().insert(calendarId=cal_id, body=body).execute()
        return event.get("id")

    try:
        event_id = await asyncio.wait_for(
            asyncio.to_thread(_insert),
            timeout=EXTERNAL_TIMEOUT,
        )
        logger.info("calendar_event_created", event_id=event_id, summary=summary, user_id=user_id, event_type=EVENT_TYPE)
        return event_id
    except asyncio.TimeoutError:
        logger.warning("calendar_insert_timeout", user_id=user_id, timeout_sec=EXTERNAL_TIMEOUT, event_type=EVENT_TYPE)
        return None
    except Exception as e:
        logger.exception("calendar_insert_error", user_id=user_id, error=str(e), event_type=EVENT_TYPE)
        return None
