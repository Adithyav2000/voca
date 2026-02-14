"""
ProviderService: Google Places API (Text Search) + Distance Matrix (RFC 3.2 Phase 1).
All external calls use 5s timeout and structlog. Returns real providers with distance_km, travel_time_min.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

from app.config import get_settings
from app.models.schemas import (
    AvailableSlot,
    Provider,
    ProviderLocation,
)

logger = structlog.get_logger(__name__)
EVENT_TYPE = "provider_service"
EXTERNAL_TIMEOUT = 5.0


class ProviderService:
    """Fetch providers via Google Places (Text Search) and Distance Matrix. 5s timeout per call."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or (get_settings().GOOGLE_API_KEY if get_settings() else None)

    async def geocode(self, address: str) -> tuple[float, float] | None:
        """Return (lat, lng) for address using Google Places API (Text Search), or None on failure."""
        if not address or not self._api_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=EXTERNAL_TIMEOUT) as client:
                r = await client.post(
                    "https://places.googleapis.com/v1/places:searchText",
                    headers={
                        "Content-Type": "application/json",
                        "X-Goog-Api-Key": self._api_key,
                        "X-Goog-FieldMask": "places.location",
                    },
                    json={"textQuery": address.strip()},
                )
                r.raise_for_status()
                data = r.json()
                places = data.get("places") or []
                if not places:
                    return None
                loc = places[0].get("location") or {}
                lat, lng = loc.get("latitude"), loc.get("longitude")
                if lat is not None and lng is not None:
                    return (float(lat), float(lng))
        except Exception as e:
            logger.warning("geocode_failed", address=address[:50], error=str(e), event_type=EVENT_TYPE)
        return None

    async def get_timezone(self, lat: float, lng: float) -> str | None:
        """Return IANA timezone id (e.g. America/Los_Angeles) for coordinates using Google Time Zone API, or None on failure."""
        if not self._api_key:
            return None
        try:
            timestamp = int(time.time())
            async with httpx.AsyncClient(timeout=EXTERNAL_TIMEOUT) as client:
                r = await client.get(
                    "https://maps.googleapis.com/maps/api/timezone/json",
                    params={"location": f"{lat},{lng}", "timestamp": timestamp, "key": self._api_key},
                )
                r.raise_for_status()
                data = r.json()
                if data.get("status") != "OK":
                    return None
                tz_id = (data.get("timeZoneId") or "").strip()
                return tz_id if tz_id else None
        except Exception as e:
            logger.warning("get_timezone_failed", lat=lat, lng=lng, error=str(e), event_type=EVENT_TYPE)
        return None

    async def search_providers(
        self,
        service_type: str,
        location: str,
        *,
        origin_lat: float,
        origin_lng: float,
        limit: int = 15,
    ) -> list[Provider]:
        """
        RFC 3.2 Phase 1: Places Text Search + Distance Matrix. Returns up to `limit` providers
        with distance_km and travel_time_min. Falls back to empty list on timeout/error.
        """
        if not self._api_key:
            logger.warning("search_providers_skipped", reason="GOOGLE_API_KEY not set", event_type=EVENT_TYPE)
            return []

        log = logger.bind(service_type=service_type, location=location, event_type=EVENT_TYPE)
        text_query = f"{service_type} near {location}"

        async with httpx.AsyncClient(timeout=EXTERNAL_TIMEOUT) as client:
            # 1) Places API (New): searchText
            places: list[dict[str, Any]] = []
            try:
                resp = await client.post(
                    "https://places.googleapis.com/v1/places:searchText",
                    headers={
                        "Content-Type": "application/json",
                        "X-Goog-Api-Key": self._api_key,
                        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.photos",
                    },
                    json={"textQuery": text_query, "maxResultCount": limit},
                )
                resp.raise_for_status()
                data = resp.json()
                places = data.get("places") or []
            except httpx.TimeoutException:
                log.warning("places_timeout", timeout_sec=EXTERNAL_TIMEOUT)
                return []
            except Exception as e:
                log.exception("places_error", error=str(e))
                return []

            if not places:
                log.info("places_empty_results")
                return []

            # 2) Distance Matrix: one origin, N destinations
            dests = []
            for p in places:
                loc = p.get("location") or {}
                lat = loc.get("latitude")
                lng = loc.get("longitude")
                if lat is not None and lng is not None:
                    dests.append(f"{lat},{lng}")
            if not dests:
                dests = ["0,0"] * len(places)

            origin = f"{origin_lat},{origin_lng}"
            distances_km: list[float] = []
            travel_times_min: list[int] = []

            try:
                dm_resp = await client.get(
                    "https://maps.googleapis.com/maps/api/distancematrix/json",
                    params={
                        "origins": origin,
                        "destinations": "|".join(dests[:limit]),
                        "key": self._api_key,
                        "mode": "driving",
                    },
                )
                dm_resp.raise_for_status()
                dm = dm_resp.json()
                rows = dm.get("rows") or []
                if rows:
                    elements = rows[0].get("elements") or []
                    for el in elements[: len(places)]:
                        d = el.get("distance", {}).get("value")  # meters
                        dur = el.get("duration", {}).get("value")  # seconds
                        distances_km.append((d / 1000.0) if d is not None else 0.0)
                        travel_times_min.append(int(dur / 60) if dur is not None else 0)
                while len(distances_km) < len(places):
                    distances_km.append(0.0)
                    travel_times_min.append(0)
            except httpx.TimeoutException:
                log.warning("distance_matrix_timeout", timeout_sec=EXTERNAL_TIMEOUT)
                distances_km = [0.0] * len(places)
                travel_times_min = [0] * len(places)
            except Exception as e:
                log.exception("distance_matrix_error", error=str(e))
                distances_km = [0.0] * len(places)
                travel_times_min = [0] * len(places)

            # 3) Optional: fetch phone per place (parallel, 5s each)
            async def fetch_phone(place_id: str) -> str:
                try:
                    # Place id may be "places/ChIJ..." or raw id
                    path = place_id if place_id.startswith("places/") else f"places/{place_id}"
                    r = await client.get(
                        f"https://places.googleapis.com/v1/{path}",
                        headers={
                            "X-Goog-Api-Key": self._api_key,
                            "X-Goog-FieldMask": "internationalPhoneNumber,nationalPhoneNumber",
                        },
                    )
                    if r.status_code != 200:
                        return ""
                    d = r.json()
                    return (
                        d.get("internationalPhoneNumber") or d.get("nationalPhoneNumber") or ""
                    ).strip()
                except Exception:
                    return ""

            place_ids = [p.get("id", "") for p in places if p.get("id")]
            if place_ids:
                try:
                    phones = await asyncio.wait_for(
                        asyncio.gather(*[fetch_phone(pid) for pid in place_ids]),
                        timeout=EXTERNAL_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    log.warning("place_details_timeout")
                    phones = [""] * len(place_ids)
            else:
                phones = [""] * len(places)

        # Build Provider list (RFC 4.5 shaped)
        providers: list[Provider] = []
        for i, p in enumerate(places[:limit]):
            place_id = p.get("id", f"place-{i}")
            name = (p.get("displayName") or {}).get("text", "") or f"Provider {i+1}"
            address = p.get("formattedAddress", "") or ""
            loc = p.get("location") or {}
            lat = float(loc.get("latitude", 0))
            lng = float(loc.get("longitude", 0))
            rating_val = p.get("rating")
            rating = float(rating_val) if rating_val is not None else 3.5
            rating = max(0.0, min(5.0, rating))
            phone = (phones[i] if i < len(phones) else "") or "+15550000000"
            dist = distances_km[i] if i < len(distances_km) else 0.0
            travel = travel_times_min[i] if i < len(travel_times_min) else 0

            # Build photo URL from first photo reference
            photo_url = None
            photos = p.get("photos") or []
            if photos and self._api_key:
                photo_name = photos[0].get("name", "")
                if photo_name:
                    photo_url = f"https://places.googleapis.com/v1/{photo_name}/media?maxWidthPx=400&key={self._api_key}"

            providers.append(
                Provider(
                    id=place_id,
                    name=name,
                    phone=phone,
                    address=address,
                    rating=rating,
                    rating_count=None,
                    available_slots=[],
                    rejection_probability=0.0,
                    type=service_type,
                    location=ProviderLocation(lat=lat, lng=lng),
                    language="en",
                    timezone="America/Los_Angeles",
                    receptionist_persona="Friendly and efficient.",
                    business_hours={"mon_fri": "9-17", "sat": "9-13", "sun": "closed"},
                    distance_km=dist,
                    travel_time_min=travel,
                    photo_url=photo_url,
                )
            )
        log.info("search_providers_done", count=len(providers))
        return providers


def get_provider_service(api_key: str | None = None) -> ProviderService:
    return ProviderService(api_key=api_key)
