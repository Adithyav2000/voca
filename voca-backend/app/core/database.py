"""
PostgreSQL async connection and table definitions (RFC Section 5.1).
Uses SQLAlchemy 2.0 async with asyncpg. Tables: sessions, call_tasks, appointments.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import AsyncGenerator
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings


def _get_async_url() -> str:
    url = get_settings().DATABASE_URL
    if "asyncpg" in url:
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


class Base(DeclarativeBase):
    pass


class User(Base):
    """Multi-user OAuth: one row per user; refresh_token encrypted at rest."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="users_email_unique"),)

    id: Mapped[PG_UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    google_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted at rest
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    sessions: Mapped[list["Session"]] = relationship("Session", back_populates="user")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'provider_lookup', 'dialing', 'negotiating', 'ranking', 'confirmed', 'failed', 'cancelled')",
            name="sessions_status_check",
        ),
    )

    id: Mapped[PG_UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[PG_UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    service_type: Mapped[str] = mapped_column(String(64), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    location_lat: Mapped[float] = mapped_column(Float, nullable=False)
    location_lng: Mapped[float] = mapped_column(Float, nullable=False)
    max_radius_km: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    preferred_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    preferred_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    weight_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    weight_rating: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    weight_distance: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    confirmed_call_task_id: Mapped[PG_UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("call_tasks.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="sessions")
    call_tasks: Mapped[list["CallTask"]] = relationship(
        "CallTask",
        back_populates="session",
        primaryjoin="Session.id == CallTask.session_id",
    )


class CallTask(Base):
    __tablename__ = "call_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'ringing', 'connected', 'negotiating', 'slot_offered', 'completed', 'no_answer', 'rejected', 'error', 'cancelled')",
            name="call_tasks_status_check",
        ),
    )

    id: Mapped[PG_UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[PG_UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    travel_time_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    twilio_call_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    offered_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    offered_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    offered_duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offered_doctor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    transcript: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    hold_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    session: Mapped["Session"] = relationship(
        "Session",
        back_populates="call_tasks",
        primaryjoin="CallTask.session_id == Session.id",
    )


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("user_id", "appointment_date", "appointment_time", name="appointments_user_date_time_unique"),
        CheckConstraint(
            "status IN ('confirmed', 'cancelled', 'rescheduled')",
            name="appointments_status_check",
        ),
    )

    id: Mapped[PG_UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[PG_UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    call_task_id: Mapped[PG_UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_tasks.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    appointment_date: Mapped[date] = mapped_column(Date, nullable=False)
    appointment_time: Mapped[time] = mapped_column(Time, nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    doctor_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    google_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    calendar_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _get_async_url(),
            echo=False,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create tables if they do not exist."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
