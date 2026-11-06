"""Request and response schemas for the REST API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Credentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        local, _, domain = value.partition("@")
        if not local or "." not in domain or " " in value:
            raise ValueError("Enter a valid email address")
        return value


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class AgentSettingsBody(BaseModel):
    selected_llm: str | None = Field(default=None, max_length=120)
    selected_stt: str | None = Field(default=None, max_length=60)
    selected_tts: Literal["kokoro", "piper"] | None = None
    selected_voice: str | None = Field(default=None, max_length=80)
    language: Literal["en", "ur", "auto"] = "en"
    vad_sensitivity: float = Field(default=0.5, ge=0, le=1)
    attention_mode: Literal["always", "wake_word", "manual"] = "always"
    personality: str = Field(default="", max_length=1000)
    wake_word: str = Field(default="echo", min_length=2, max_length=40)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    ai_engine: Literal["local", "groq"] = "local"


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    device_id: str | None = Field(default=None, max_length=80)


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class HandoffRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=80)


class SessionSummary(BaseModel):
    id: str
    title: str
    status: str
    start_time: datetime
    end_time: datetime | None
    duration: float
    active_duration: float
    paused_duration: float
    estimated_cost: float
    owner_device_id: str | None
    message_count: int = 0
    preview: str | None = None
