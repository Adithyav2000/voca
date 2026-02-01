"""Application configuration via environment variables. No defaults for secrets."""

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _strip_str(v: str | object) -> str | object:
    """Strip whitespace from env strings to avoid trailing-space issues."""
    return v.strip() if isinstance(v, str) else v


class Settings(BaseSettings):
    """VOCA settings. All API keys and URLs are required (no defaults)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Data stores
    DATABASE_URL: str
    REDIS_URL: str

    # Twilio (for phone infrastructure)
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str

    # OpenAI (for voice conversations + transcription)
    OPENAI_API_KEY: str
    OPENAI_VOICE_MODEL: str = "gpt-4o"  # Voice-capable model for conversations

    # Google OAuth (optional — set in .env to enable login + user calendar)
    # Accepts GOOGLE_OAUTH_CLIENT_ID or GOOGLE_CLIENT_ID (same for secret/redirect)
    GOOGLE_OAUTH_CLIENT_ID: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_CLIENT_ID"),
    )
    GOOGLE_OAUTH_CLIENT_SECRET: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET"),
    )
    GOOGLE_OAUTH_REDIRECT_URI: str = Field(
        default="http://localhost:8000/api/auth/callback",
        validation_alias=AliasChoices("GOOGLE_OAUTH_REDIRECT_URI", "GOOGLE_REDIRECT_URI"),
    )

    @field_validator(
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REDIRECT_URI",
        mode="before",
    )
    @classmethod
    def strip_google_oauth_strings(cls, v: str | object) -> str | object:
        """Read .env without trailing/leading spaces for OAuth credentials."""
        return _strip_str(v)

    # Session & encryption (set in production; dev defaults allow app to start)
    SESSION_SECRET_KEY: str = "YtVaZZyvfYEWKpoWdi/b8pcrRQbTmN8qtxDikAISuHw="
    ENCRYPTION_KEY: str | None = None  # Fernet key; if unset, refresh tokens stored unencrypted (dev only)
    SESSION_COOKIE_SECURE: bool = True  # set False for localhost without HTTPS

    # Google APIs (Places + Distance Matrix; optional for fallback to mock)
    GOOGLE_API_KEY: str | None = None

    @field_validator("OPENAI_API_KEY", "GOOGLE_API_KEY", mode="before")
    @classmethod
    def strip_api_keys(cls, v: str | object) -> str | object:
        """Strip whitespace from API keys to avoid leading/trailing space issues."""
        return _strip_str(v)

    # Frontend (SPA): origin for CORS and post-login redirect (e.g. http://localhost:5173)
    FRONTEND_ORIGIN: str = ""


def get_settings() -> Settings:
    """Load and validate settings. Fails on first missing required variable."""
    return Settings()
