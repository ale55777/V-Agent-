"""Shared test fixtures.

Unit tests run on SQLite with scripted model doubles, so they exercise the real
state machine quickly. Tests that need real models are marked `models`, and
Postgres-specific tests are marked `postgres`; both skip themselves when the
dependency is not available.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ECHOAI_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256-signing")

import numpy as np
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import session as db_session
from app.models.entities import Base
from app.services.asr.whisper import Transcript


class ScriptedRecognizer:
    """Returns queued transcripts, or the default, instead of running Whisper."""

    name = "scripted-stt"

    def __init__(self, default: str = "hello there") -> None:
        self.queue: list[str] = []
        self.default = default
        self.calls: list[tuple[int, bool]] = []

    def transcribe(self, samples, language, final, hint=""):
        self.calls.append((len(samples), final))
        text = self.queue.pop(0) if final and self.queue else self.default
        return Transcript(text=text, language="en", duration=len(samples) / 16000)


class ScriptedChat:
    """Streams a fixed reply. `gate` can hold generation open to test cancellation."""

    def __init__(self, reply: str = "Sure thing. Here is an answer for you.") -> None:
        self.reply = reply
        self.prompts: list[list[dict]] = []
        self.cancelled = 0
        self.gate = None
        self.error: Exception | None = None

    async def stream(self, messages, model=None):
        import asyncio

        self.prompts.append(messages)
        if self.error:
            raise self.error
        try:
            for token in self.reply.split(" "):
                if self.gate is not None:
                    await self.gate.wait()
                await asyncio.sleep(0)
                yield token + " "
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class ScriptedTTS:
    name = "scripted-tts"
    sample_rate = 16000

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def voices(self):
        return [{"id": "test", "label": "Test", "engine": "scripted", "language": "en"}]

    def synthesize(self, text, voice, language="en"):
        self.spoken.append(text)
        # 60 ms of audio per word, so durations scale with the text.
        yield np.zeros(int(16000 * 0.06 * max(1, len(text.split()))), dtype=np.float32)


class ScriptedVAD:
    """Probability comes from the frame itself: loud frames count as speech."""

    def probability(self, frame):
        return 0.95 if float(np.abs(frame).max()) > 0.1 else 0.02

    def reset(self):
        pass


class MemoryStore:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def save_message(self, speaker, content, status, response_id):
        message = {
            "speaker": speaker, "content": content, "status": status,
            "response_id": response_id, "sequence_number": len(self.messages) + 1,
        }
        self.messages.append(message)
        return message


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    db_session.configure_engine(url)
    engine = db_session.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield db_session.session_factory()
    await engine.dispose()


class FakeRegistry:
    def __init__(self) -> None:
        import asyncio

        self.ready = asyncio.Event()
        self.ready.set()
        self.status = {k: {"state": "ready"} for k in ("vad", "stt", "tts", "llm")}
        self.recognizer = ScriptedRecognizer()
        self.llm = FakeLLM()
        self.engines = {"scripted": ScriptedTTS()}

    groq_available = False

    def pipeline(self, ai_engine):
        return self.recognizer, self.llm

    def new_vad(self):
        return ScriptedVAD()

    def tts_for(self, engine):
        return self.engines["scripted"]

    def default_tts(self):
        return self.engines["scripted"]

    def voices(self):
        return self.engines["scripted"].voices()


class FakeLLM(ScriptedChat):
    async def list_models(self):
        return [{"name": "scripted-llm", "size": 1, "family": "test"}]


@pytest_asyncio.fixture
async def app(database):
    from fastapi import FastAPI

    from app.api.routes import router as api_router
    from app.websocket.voice import router as ws_router

    application = FastAPI()
    application.state.registry = FakeRegistry()
    application.include_router(api_router)
    application.include_router(ws_router)
    return application


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def pytest_configure(config):
    config.addinivalue_line("markers", "models: needs the real downloaded AI models")
    config.addinivalue_line("markers", "postgres: needs the real PostgreSQL container")


@pytest.fixture
def anyio_backend():
    return "asyncio"
