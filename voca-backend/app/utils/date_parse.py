"""Flexible date/time parsing for tool calls (e.g. agent sends 'Friday' or '10 AM')."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone


# Weekday names for "Friday" -> next Friday
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_date_flexible(s: str) -> str | None:
    """Return YYYY-MM-DD or None. Accepts YYYY-MM-DD or weekday name (e.g. 'friday' -> next Friday)."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # Already YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            pass
    # today / tomorrow / weekday
    lower = s.lower()
    today = date.today()
    if lower == "today":
        return today.isoformat()
    if re.match(r"^to+m+o?r+o?w$", lower):
        return (today + timedelta(days=1)).isoformat()
    for i, name in enumerate(_WEEKDAYS):
        if name in lower:
            # This or next occurrence
            days_ahead = (i - today.weekday()) % 7
            if "next" in lower and days_ahead == 0:
                days_ahead = 7
            d = today + timedelta(days=days_ahead)
            return d.isoformat()
    return None


def parse_time_flexible(s: str) -> str | None:
    """Return HH:MM 24h or None. Accepts 09:00, 9:00, 10 AM, 2:30 PM, morning, afternoon, evening, noon, etc."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    lower = s.lower()

    # Natural language periods → canonical hour
    _PERIOD_MAP = {
        "morning": "09:00",
        "noon": "12:00",
        "midday": "12:00",
        "afternoon": "14:00",
        "evening": "18:00",
        "night": "19:00",
        "anytime": "10:00",
        "any time": "10:00",
        "flexible": "10:00",
        "asap": "09:00",
        "as soon as possible": "09:00",
        "now": "09:00",
        "soonest": "09:00",
    }
    for phrase, canonical in _PERIOD_MAP.items():
        if phrase in lower:
            return canonical

    # "after X PM" / "after X:00" → use that time directly
    m_after = re.search(r"after\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lower)
    if m_after:
        h = int(m_after.group(1))
        mi = int(m_after.group(2)) if m_after.group(2) else 0
        suffix = m_after.group(3) or ""
        if suffix == "pm" and h < 12:
            h += 12
        elif suffix == "am" and h == 12:
            h = 0
        if 0 <= h <= 23:
            return f"{h:02d}:{mi:02d}"

    # "between X PM and Y PM" → use lower bound
    m_range = re.search(r"between\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lower)
    if m_range:
        h = int(m_range.group(1))
        mi = int(m_range.group(2)) if m_range.group(2) else 0
        suffix = m_range.group(3) or ""
        if suffix == "pm" and h < 12:
            h += 12
        elif suffix == "am" and h == 12:
            h = 0
        if 0 <= h <= 23:
            return f"{h:02d}:{mi:02d}"

    # Already HH:MM or H:MM
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)?$", s, re.I)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if m.group(3):
            if m.group(3).lower() == "pm" and h < 12:
                h += 12
            elif m.group(3).lower() == "am" and h == 12:
                h = 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    # "10 AM", "2 PM"
    m = re.match(r"^(\d{1,2})\s*(am|pm)$", s, re.I)
    if m:
        h = int(m.group(1))
        if m.group(2).lower() == "pm" and h < 12:
            h += 12
        elif m.group(2).lower() == "am" and h == 12:
            h = 0
        if 0 <= h <= 23:
            return f"{h:02d}:00"
    # bare hour "10", "14" only if clearly numeric
    m = re.match(r"^(\d{1,2})$", s)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return f"{h:02d}:00"
    return None
