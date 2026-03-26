"""FastAPI entry point for VOCA."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
# ProxyHeadersMiddleware: respect X-Forwarded-Proto (https) from ngrok/reverse proxy (Starlette has no built-in)
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.auth import router as auth_router
from app.api.routes import router
from app.api.voice_routes import voice_router
from app.config import get_settings
from app.core.database import close_db, init_db
from app.core.redis import close_redis, get_redis

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB and Redis, start stale session monitor. Shutdown: cancel monitor, close both."""
    import asyncio
    from app.services.orchestrator import session_stale_monitor_loop

    await init_db()
    await get_redis()
    monitor_task = asyncio.create_task(session_stale_monitor_loop(), name="session_stale_monitor")
    yield
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
    await close_redis()
    await close_db()


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    app = FastAPI(
        title="VOCA",
        description="Voice-Orchestrated Concierge for Appointments",
        version="0.1.0",
        lifespan=lifespan,
    )

    # With credentials=True, browser rejects allow_origins=["*"]. Use explicit frontend origin when set.
    cors_origins = [settings.FRONTEND_ORIGIN] if settings.FRONTEND_ORIGIN else ["http://localhost:5173", "http://localhost:3000"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Trust X-Forwarded-Proto / X-Forwarded-For from ngrok (or other reverse proxy)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    # Force https scheme when behind ngrok so redirects/cookies use HTTPS (run innermost so it wins)
    class ForceHttpsMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request.scope["scheme"] = "https"
            return await call_next(request)

    app.add_middleware(ForceHttpsMiddleware)

    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(voice_router)

    @app.get("/health")
    async def health():
        """Liveness: app is running."""
        return {"status": "ok", "mode": "live"}

    @app.get("/ready")
    async def ready():
        """Readiness: DB and Redis are reachable (for k8s/Docker)."""
        try:
            redis = await get_redis()
            await redis.ping()
            from sqlalchemy import text
            from app.core.database import get_session_factory
            async with get_session_factory()() as session:
                await session.execute(text("SELECT 1"))
            return {"status": "ready", "db": "ok", "redis": "ok"}
        except Exception as e:
            logger.warning("ready_check_failed", error=str(e), event_type="startup")
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503, content={"status": "not ready", "error": str(e)})

    return app


app = create_app()
