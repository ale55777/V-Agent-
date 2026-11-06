"""Application settings, loaded from environment variables and the root .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", BACKEND_DIR / ".env"),
        env_prefix="ECHOAI_",
        extra="ignore",
    )

    # Network. 0.0.0.0 allows LAN access. Clients derive the API host from the
    # address they loaded the page from, so no host is ever hardcoded for them.
    host: str = "0.0.0.0"
    port: int = 8000
    lan_ip: str | None = None
    cors_origins: str = ""  # comma-separated extra origins
    tls_cert: Path | None = None
    tls_key: Path | None = None
    web_dist: Path = ROOT_DIR / "apps" / "web" / "out"

    # Security. There is deliberately no default secret: setup.ps1 generates one.
    secret_key: str = Field(default="", min_length=0)
    access_token_minutes: int = 60 * 24 * 7
    allow_registration: bool = True

    # Database
    database_url: str = "postgresql+asyncpg://echoai@127.0.0.1:5432/echoai"

    # Models
    models_dir: Path = ROOT_DIR / ".data" / "models"
    ollama_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:0.5b-instruct"
    llm_num_ctx: int = 2048
    llm_num_predict: int = 160
    llm_num_threads: int | None = None
    llm_temperature: float = 0.7
    stt_model: str = "tiny"
    stt_compute_type: str = "int8"
    stt_threads: int = 2
    tts_engine: str = "auto"  # auto | kokoro | piper
    tts_threads: int = 2
    kokoro_voice: str = "af_heart"
    piper_voice: str = "en_US-lessac-low"

    # Optional online engine (Groq). Off unless a key is set AND a user picks it in Settings.
    # It sends that user's audio and text to Groq; the local models remain the fallback.
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_stt_model: str = "whisper-large-v3-turbo"
    # Measured 2026-11-06: about 90 ms to first token, versus about 500 ms for gpt-oss-20b,
    # which reasons before answering.
    groq_llm_model: str = "qwen/qwen3.8-27b"
    groq_timeout_seconds: float = 8.0
    # After a failure, stay on the local models this long before trying Groq again.
    groq_retry_after_seconds: float = 60.0

    # Voice activity / turn taking (defaults; per-user settings override some)
    vad_threshold: float = 0.5
    vad_interrupt_threshold: float = 0.75
    vad_min_speech_ms: int = 160
    vad_interrupt_min_speech_ms: int = 280
    vad_silence_ms: int = 750
    vad_max_silence_ms: int = 1800
    vad_padding_ms: int = 192
    max_utterance_seconds: int = 30
    partial_interval_ms: int = 1200

    # Bounded audio queues
    mic_queue_frames: int = 64
    tts_queue_sentences: int = 3

    # Optional electricity estimate. Disabled until the user sets a tariff, so
    # the report never presents an invented figure.
    power_watts: float = 25.0
    electricity_price_per_kwh: float | None = None
    electricity_currency: str = "USD"


@lru_cache
def get_settings() -> Settings:
    return Settings()
