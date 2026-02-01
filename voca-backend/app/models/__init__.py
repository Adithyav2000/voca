"""Pydantic and SQLModel definitions."""

from app.models.schemas import (
    AvailableSlot,
    BookSlotRequest,
    BookSlotResponse,
    SessionIntent,
    SessionRequest,
    CheckAvailabilityRequest,
    CheckAvailabilityResponse,
    ConfirmSessionRequest,
    EndCallRequest,
    GetDistanceRequest,
    GetDistanceResponse,
    Provider,
    ProviderLocation,
    ReportSlotOfferRequest,
    ReportSlotOfferResponse,
    SquadPlan,
)

__all__ = [
    "AvailableSlot",
    "BookSlotRequest",
    "BookSlotResponse",
    "SessionIntent",
    "SessionRequest",
    "CheckAvailabilityRequest",
    "CheckAvailabilityResponse",
    "ConfirmSessionRequest",
    "EndCallRequest",
    "GetDistanceRequest",
    "GetDistanceResponse",
    "Provider",
    "ProviderLocation",
    "ReportSlotOfferRequest",
    "ReportSlotOfferResponse",
    "SquadPlan",
]
