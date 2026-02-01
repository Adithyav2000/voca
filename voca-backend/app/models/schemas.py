"""Data schemas for VOCA - aligned with RFC Section 5 (Data Schemas) and Section 4.5 (Mock Provider).
Pydantic v2; all payloads validated at service boundaries.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ----- Request / Intent (Phase 1 input and LLM output) -----


class SessionRequest(BaseModel):
    """User booking request — raw input to the orchestrator. Accepts 'location' or 'user_location'."""

    prompt: str = Field(..., min_length=1, description="Natural language booking request")
    user_location: str = Field(..., min_length=1, description="User location (address or place description)")
    location_lat: float | None = Field(None, description="Latitude of user location (auto-geocoded if not provided)")
    location_lng: float | None = Field(None, description="Longitude of user location (auto-geocoded if not provided)")

    @model_validator(mode="before")
    @classmethod
    def location_alias(cls, data: dict | object) -> dict | object:
        if isinstance(data, dict) and "location" in data and "user_location" not in data:
            data = {**data, "user_location": data["location"]}
        return data


class SessionIntent(BaseModel):
    """Structured intent extracted from the user prompt via LLM (RFC Phase 1). Date, time, location from prompt; timezone resolved via Google APIs."""

    service_type: str = Field(..., description="Category: dentist, mechanic, hairdresser, etc.")
    target_date: str | None = Field(None, description="Preferred date YYYY-MM-DD or null for ASAP")
    target_time: str | None = Field(None, description="Preferred time or window: morning, afternoon, evening, any, or HH:MM")
    urgency: str | None = Field(None, description="Urgency hint from user")
    location_query: str | None = Field(None, description="Location or area extracted from prompt")
    timezone: str | None = Field(None, description="IANA timezone for the location (from Google Time Zone API)")


# ----- Provider (RFC Section 4.5 — mock provider / Google Places–shaped) -----


class ProviderLocation(BaseModel):
    """Lat/lng for a provider."""

    lat: float = Field(..., description="Latitude")
    lng: float = Field(..., description="Longitude")


class AvailableSlot(BaseModel):
    """Single slot in a provider's available_slots array (RFC 4.5)."""

    date: str = Field(..., description="YYYY-MM-DD")
    time: str = Field(..., description="HH:MM 24h")
    duration_min: int = Field(..., ge=1, description="Duration in minutes")
    doctor: str = Field("", description="Professional name if applicable")


class Provider(BaseModel):
    """Provider record — matches RFC Section 4.5 (mock) and Google Places–shaped for live."""

    id: str = Field(..., description="Unique id (e.g. mock-dentist-001 or Google Place ID)")
    name: str = Field(..., description="Business name")
    phone: str = Field(..., description="E.164 or mock number")
    rating: float = Field(..., ge=0.0, le=5.0, description="Google-style 1.0–5.0")
    address: str = Field(..., description="Full street address")
    available_slots: list[AvailableSlot] = Field(default_factory=list, description="Slots offered (mock)")
    rejection_probability: float = Field(0.0, ge=0.0, le=1.0, description="Probability of no-availability (mock)")
    # Optional RFC 4.5 fields for full mock compatibility
    type: str | None = Field(None, description="Service category")
    location: ProviderLocation | None = Field(None, description="Lat/lng")
    rating_count: int | None = Field(None, description="Number of reviews")
    language: str | None = Field(None, description="ISO 639-1")
    timezone: str | None = Field(None, description="IANA timezone")
    receptionist_persona: str | None = Field(None, description="Mock receptionist persona")
    business_hours: dict[str, str] | None = Field(None, description="mon_fri, sat, sun")
    # From Distance Matrix (live only)
    distance_km: float | None = Field(None, description="Distance from user in km")
    travel_time_min: int | None = Field(None, description="Driving time in minutes")
    photo_url: str | None = Field(None, description="Google Places photo URL")


# ----- Check availability (OpenAI webhook tool) -----

# RFC 6.1: date YYYY-MM-DD, time HH:MM 24h
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


class CheckAvailabilityRequest(BaseModel):
    """Request body for POST /check-availability (date/time slot). Validated before use."""

    date: str = Field(..., min_length=10, max_length=10, description="Date YYYY-MM-DD")
    time: str = Field(..., min_length=4, max_length=5, description="Time HH:MM 24h")
    user_id: str = Field(..., description="User ID for hold key")
    session_id: str = Field(..., description="Session ID for soft lock")
    call_task_id: str = Field(..., description="Call task ID for soft lock")
    duration_minutes: int = Field(default=30, ge=1, le=120, description="Slot duration in minutes")

    @field_validator("date")
    @classmethod
    def date_format(cls, v: str) -> str:
        if not _DATE_PATTERN.match(v):
            raise ValueError("date must be YYYY-MM-DD")
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError("invalid date") from e
        return v.strip()

    @field_validator("time")
    @classmethod
    def time_format(cls, v: str) -> str:
        t = v.strip()
        if len(t) > 5:
            t = t[:5]
        if not _TIME_PATTERN.match(t):
            raise ValueError("time must be HH:MM 24h")
        return t


class CheckAvailabilityResponse(BaseModel):
    """Response for POST /check-availability (RFC 6.1)."""

    status: Literal["held", "conflict", "soft_conflict"] = Field(..., description="held | conflict | soft_conflict")
    conflicts: list[str] = Field(default_factory=list, description="Event names if conflict")
    held_by: str | None = Field(default=None, description="Session holding slot if soft_conflict")
    next_free_slot: str | None = Field(default=None, description="HH:MM suggestion")
    hold_expires_in_seconds: int | None = Field(default=None, description="TTL if held")


# ----- Book slot (RFC 6.3) -----


class BookSlotRequest(BaseModel):
    """Request body for POST /book-slot."""

    session_id: str = Field(..., description="Session UUID")
    call_task_id: str = Field(..., description="Winning call task UUID")
    user_id: str = Field(..., description="User ID")
    provider_id: str = Field(..., description="Provider ID")
    provider_name: str = Field(..., description="Provider name")
    provider_phone: str = Field(..., description="Provider phone")
    provider_address: str | None = Field(default=None)
    appointment_date: str = Field(..., description="YYYY-MM-DD")
    appointment_time: str = Field(..., description="HH:MM 24h")
    duration_min: int = Field(..., ge=1, le=480, description="Duration minutes")
    doctor_name: str | None = Field(default=None)
    hold_keys_to_release: list[str] = Field(default_factory=list, description="Redis hold keys to release")

    @field_validator("appointment_date")
    @classmethod
    def date_fmt(cls, v: str) -> str:
        if not _DATE_PATTERN.match(v):
            raise ValueError("date must be YYYY-MM-DD")
        datetime.strptime(v, "%Y-%m-%d")
        return v.strip()

    @field_validator("appointment_time")
    @classmethod
    def time_fmt(cls, v: str) -> str:
        t = v.strip()[:5] if len(v.strip()) >= 5 else v.strip()
        if not _TIME_PATTERN.match(t):
            raise ValueError("time must be HH:MM 24h")
        return t


class BookSlotResponse(BaseModel):
    """Response for POST /book-slot."""

    booked: bool = Field(..., description="Whether booking succeeded")
    reason: str | None = Field(default=None, description="Failure reason if not booked")


# ----- Report slot offer (OpenAI webhook) -----


class ReportSlotOfferRequest(BaseModel):
    """Request body for POST /api/report-slot-offer. Agent reports a calendar-held slot."""

    session_id: str = Field(..., description="Session UUID")
    call_task_id: str = Field(..., description="Call task UUID")
    provider_name: str = Field(..., description="Business name")
    date: str = Field(..., description="YYYY-MM-DD")
    time: str = Field(..., description="HH:MM 24h")
    duration_minutes: int = Field(default=30, ge=1, le=120)
    doctor_name: str | None = Field(default=None)


class ReportSlotOfferResponse(BaseModel):
    """Response for POST /api/report-slot-offer (RFC 6.2)."""

    received: bool = Field(..., description="Offer registered")
    ranking_position: int = Field(default=1, description="Current rank among offers")
    instruction: Literal["continue_holding", "terminate"] = Field(default="continue_holding")


# ----- Get distance (OpenAI webhook) -----


class GetDistanceRequest(BaseModel):
    """Request body for POST /api/get-distance."""

    destination_address: str = Field(..., description="Full street address")
    origin_lat: float | None = Field(default=None, description="Origin latitude")
    origin_lng: float | None = Field(default=None, description="Origin longitude")


class GetDistanceResponse(BaseModel):
    """Response for POST /api/get-distance (RFC 6.4)."""

    distance_km: float = Field(..., description="Driving distance km")
    travel_time_min: int = Field(..., description="Driving time minutes")
    mode: str = Field(default="driving", description="Travel mode")


# ----- End call (session/call status + release holds) -----


class EndCallRequest(BaseModel):
    """Request body for POST /end-call. call_task_id optional; backend can resolve from session when missing."""

    session_id: str = Field(..., description="Session UUID")
    call_task_id: str | None = Field(None, description="Call task UUID; if missing, backend resolves from session")
    status: Literal["completed", "no_answer", "rejected", "error", "cancelled"] = Field(
        ..., description="Final call status"
    )
    hold_keys: list[str] = Field(default_factory=list, description="Hold keys to release for this call")


# ----- Squad plan (orchestrator output) -----


class SquadPlan(BaseModel):
    """Phase 1 output: parsed intent plus provider list (capped at 15 in live)."""

    session_id: str | None = Field(None, description="Session UUID for stream/results/confirm (set when created via create_session_and_squad)")
    intent: SessionIntent | None = Field(None, description="Extracted booking intent (populated after provider lookup completes)")
    providers: list[Provider] = Field(default_factory=list, description="Scored provider list")

    def model_dump_for_llm(self) -> dict[str, Any]:
        """For logging / serialization; avoids dumping large nested structures."""
        return {
            "session_id": self.session_id,
            "intent": self.intent.model_dump() if self.intent else None,
            "providers_count": len(self.providers),
        }


# ----- Manual overrides (Challenge 3.0) -----


class ConfirmSessionRequest(BaseModel):
    """Request body for POST /api/sessions/{id}/confirm."""

    call_task_id: str = Field(..., description="Winning call task UUID to confirm and book")
