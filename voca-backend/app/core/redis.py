"""Redis client for soft locks and kill switch (RFC Section 3.6, 5.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from redis.asyncio import Redis

from app.config import get_settings

if TYPE_CHECKING:
    pass

_redis: Redis | None = None


async def get_redis() -> Redis:
    """Return a shared async Redis client. Connects on first use."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
    return _redis


async def close_redis() -> None:
    """Close the shared Redis connection (e.g. on app shutdown)."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
