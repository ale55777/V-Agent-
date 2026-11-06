"""SQLAlchemy ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    settings: Mapped["AgentSettings | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class AgentSettings(Base):
    __tablename__ = "agent_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    selected_llm: Mapped[str | None] = mapped_column(String(120))
    selected_stt: Mapped[str | None] = mapped_column(String(60))
    selected_tts: Mapped[str | None] = mapped_column(String(30))
    selected_voice: Mapped[str | None] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(8), default="en")
    vad_sensitivity: Mapped[float] = mapped_column(Float, default=0.5)
    attention_mode: Mapped[str] = mapped_column(String(20), default="always")
    personality: Mapped[str] = mapped_column(Text, default="")
    wake_word: Mapped[str] = mapped_column(String(40), default="echo")
    retention_days: Mapped[int | None] = mapped_column(Integer)
    # "local" keeps everything on this PC. "groq" sends audio and text to Groq (opt-in).
    ai_engine: Mapped[str] = mapped_column(String(10), default="local", server_default="local")

    user: Mapped[User] = relationship(back_populates="settings")


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"
    __table_args__ = (Index("ix_sessions_user_start", "user_id", "start_time"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    active_duration: Mapped[float] = mapped_column(Float, default=0.0)
    paused_duration: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    # External API spend. Always 0 today: every model runs locally.
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    owner_device_id: Mapped[str | None] = mapped_column(String(80))

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ConversationMessage.sequence_number"
    )
    metrics: Mapped["SessionMetrics | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence_number", name="uq_message_sequence"),
        Index("ix_messages_session_seq", "session_id", "sequence_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id", ondelete="CASCADE"))
    speaker: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sequence_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="completed")  # completed | interrupted
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    response_id: Mapped[str | None] = mapped_column(String(36))

    session: Mapped[ConversationSession] = relationship(back_populates="messages")


class SessionMetrics(Base):
    __tablename__ = "session_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"), unique=True
    )
    # Latencies are milliseconds from end of user speech to first playable audio.
    average_latency: Mapped[float | None] = mapped_column(Float)
    minimum_latency: Mapped[float | None] = mapped_column(Float)
    maximum_latency: Mapped[float | None] = mapped_column(Float)
    average_transcription_latency: Mapped[float | None] = mapped_column(Float)
    average_llm_first_token: Mapped[float | None] = mapped_column(Float)
    average_interruption_latency: Mapped[float | None] = mapped_column(Float)
    interruption_count: Mapped[int] = mapped_column(Integer, default=0)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    user_speaking_duration: Mapped[float] = mapped_column(Float, default=0.0)
    ai_speaking_duration: Mapped[float] = mapped_column(Float, default=0.0)
    model_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    turns: Mapped[list] = mapped_column(JSON, default=list)

    session: Mapped[ConversationSession] = relationship(back_populates="metrics")
