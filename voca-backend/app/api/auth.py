"""
Multi-user Google OAuth2: login, callback, session cookie. Production standards.
Refresh token stored encrypted at rest; Pydantic validates callback params.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import structlog
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.core.crypto import encrypt_refresh_token
from app.core.database import User, get_session_factory

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.events"
SCOPE_EMAIL = "https://www.googleapis.com/auth/userinfo.email"
SCOPE_PROFILE = "https://www.googleapis.com/auth/userinfo.profile"
SESSION_COOKIE_NAME = "session"
SESSION_MAX_AGE = 86400 * 30  # 30 days
DEMO_USER_EMAIL = "demo@voca.local"


# ----- Pydantic: OAuth callback params -----


class OAuthCallbackQuery(BaseModel):
    """Validated query params for GET /callback. Prevents open redirect / code injection."""

    code: str = Field(..., min_length=1, max_length=2000, description="Authorization code from Google")
    state: str | None = Field(None, max_length=512, description="Optional CSRF state")

    model_config = {"extra": "forbid"}


class AuthSessionResponse(BaseModel):
    """Response body for GET /session."""

    authenticated: bool = True
    user_id: str
    email: str
    display_name: str
    auth_provider: str


# ----- Session cookie: sign and verify -----


def _sign_session(user_id: str, secret: str) -> str:
    """Produce signed value: base64(user_id:hmac)."""
    raw = f"{user_id}"
    sig = hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    payload = f"{user_id}:{sig}"
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _verify_session(cookie_value: str, secret: str) -> str | None:
    """Verify and return user_id or None."""
    if not cookie_value or len(cookie_value) > 1024:
        return None
    try:
        payload = base64.urlsafe_b64decode(cookie_value.encode("ascii")).decode("utf-8")
        user_id, sig = payload.rsplit(":", 1)
        expected = hmac.new(secret.encode("utf-8"), user_id.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return user_id
    except Exception:
        return None


# ----- Routes -----


def _oauth_is_configured() -> bool:
    settings = get_settings()
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID.strip() and settings.GOOGLE_OAUTH_CLIENT_SECRET.strip())


def _build_redirect_url() -> str:
    settings = get_settings()
    return (settings.FRONTEND_ORIGIN or "").strip() or "/docs"


def _set_session_cookie(response: Response, user_id: str) -> None:
    settings = get_settings()
    cookie_value = _sign_session(user_id, settings.SESSION_SECRET_KEY)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


async def _upsert_user(email: str, display_name: str, refresh_token: str) -> tuple[str, str]:
    settings = get_settings()
    encrypted = encrypt_refresh_token(refresh_token, settings.ENCRYPTION_KEY)
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            insert(User)
            .values(
                email=email,
                display_name=display_name,
                google_refresh_token=encrypted,
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                index_elements=["email"],
                set_={
                    "google_refresh_token": encrypted,
                    "display_name": display_name,
                },
            )
            .returning(User.id, User.email)
        )
        result = await session.execute(stmt)
        row = result.one_or_none()
        await session.commit()
    if not row:
        raise HTTPException(status_code=500, detail="User upsert failed")
    return str(row[0]), str(row[1])


async def _demo_login_response() -> Response:
    user_id, email = await _upsert_user(DEMO_USER_EMAIL, "Demo User", "")
    response = Response(status_code=302, headers={"Location": _build_redirect_url()})
    _set_session_cookie(response, user_id)
    logger.info("demo_login_success", user_id=user_id, email=email, event_type="auth")
    return response


@router.get("/login")
async def auth_login(request: Request) -> Response:
    """
    Redirect user to Google OAuth consent screen.
    access_type=offline & prompt=consent to obtain refresh token.
    """
    settings = get_settings()
    if not _oauth_is_configured():
        logger.info("auth_login_demo_fallback", event_type="auth")
        return await _demo_login_response()
    # Always use redirect_uri from env (never request.url_for) so it is correct behind HTTPS proxies (e.g. ngrok).
    redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI
    state = secrets.token_urlsafe(32)
    # Request calendar + email/profile so we can create the user and access Calendar
    scopes = " ".join([SCOPE_EMAIL, SCOPE_PROFILE, SCOPE_CALENDAR])
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    import urllib.parse
    qs = urllib.parse.urlencode(params)
    url = f"{GOOGLE_AUTH_URL}?{qs}"
    logger.info("auth_login_redirect", event_type="auth")
    return Response(status_code=302, headers={"Location": url})


@router.get("/callback")
async def auth_callback(
    response: Response,
    code: Annotated[str, Query(alias="code")] = "",
    state: Annotated[str | None, Query(alias="state")] = None,
) -> Response:
    """
    Exchange authorization code for tokens. Upsert user with encrypted refresh_token.
    Set secure HTTP-only session cookie.
    """
    # Missing code (e.g. ngrok interstitial, or user opened callback URL without coming from Google) -> redirect to login
    if not (code and code.strip()):
        logger.warning("auth_callback_missing_code", event_type="auth")
        return Response(status_code=302, headers={"Location": "/api/auth/login"})
    # Pydantic validate callback params
    query = OAuthCallbackQuery(code=code.strip(), state=state)
    settings = get_settings()
    if not _oauth_is_configured():
        return await _demo_login_response()

    # redirect_uri must match the value used at login; always from settings (correct behind ngrok).
    import httpx

    async def _exchange():
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": query.code,
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code >= 400:
                logger.warning("oauth_token_exchange_failed", status=r.status_code, body=r.text, event_type="auth")
                return None, None
            data = r.json()
            return data.get("refresh_token"), data.get("access_token")

    refresh_token, access_token = await _exchange()
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Failed to obtain refresh token from Google")

    # User info (email, name) via access token
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if r.status_code >= 400:
            logger.warning("oauth_userinfo_failed", status=r.status_code, event_type="auth")
            raise HTTPException(status_code=400, detail="Failed to fetch user info")
        userinfo = r.json()
    email = (userinfo.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email")
    # Extract display name from given_name and family_name, or fall back to email
    given_name = (userinfo.get("given_name") or "").strip()
    family_name = (userinfo.get("family_name") or "").strip()
    display_name = f"{given_name} {family_name}".strip() if given_name or family_name else email

    # Encrypt refresh token and upsert user
    user_id_str, _stored_email = await _upsert_user(email, display_name, refresh_token)

    response = Response(status_code=302, headers={"Location": _build_redirect_url()})
    _set_session_cookie(response, user_id_str)
    logger.info("auth_callback_success", user_id=user_id_str, email=email, event_type="auth", timestamp_ms=round(datetime.now(timezone.utc).timestamp() * 1000))
    return response


@router.get("/session", response_model=AuthSessionResponse)
async def auth_session(request: Request) -> AuthSessionResponse:
    """Return the current authenticated user session."""
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        from uuid import UUID

        uid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid session")

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(User).where(User.id == uid))
        user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Session user not found")

    auth_provider = "demo" if user.email == DEMO_USER_EMAIL else "google"
    return AuthSessionResponse(
        user_id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        auth_provider=auth_provider,
    )


@router.post("/logout")
async def auth_logout() -> JSONResponse:
    """Clear the current session cookie."""
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=get_settings().SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


def get_current_user_id(request: Request) -> str | None:
    """Read session cookie and return user_id or None. Use in Depends for protected routes."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return None
    return _verify_session(cookie, get_settings().SESSION_SECRET_KEY)
