"""The optional Groq engine: parsing, and falling back to the local models when Groq fails."""
from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from app.config.settings import Settings
from app.services.groq import Cooldown, FallbackChat, FallbackRecognizer, GroqChat, GroqRecognizer
from app.services.llm.ollama import LLMError
from tests.conftest import ScriptedChat, ScriptedRecognizer

pytestmark = pytest.mark.anyio
SILENCE = np.zeros(16000, dtype=np.float32)


def settings() -> Settings:
    return Settings(groq_api_key="test-key", secret_key="x" * 32)


def recognizer(handler) -> GroqRecognizer:
    r = GroqRecognizer(settings())
    r._client = httpx.Client(base_url="https://groq.test", headers=r._client.headers, transport=httpx.MockTransport(handler))
    return r


def chat(handler) -> GroqChat:
    c = GroqChat(settings())
    c._client = httpx.AsyncClient(base_url="https://groq.test", headers=c._client.headers, transport=httpx.MockTransport(handler))
    return c


def sse(*tokens: str) -> bytes:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': t}}]})}\n\n" for t in tokens]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def test_transcript_is_parsed_and_filtered():
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-key"
        assert b"speech.wav" in request.content
        return httpx.Response(200, json={
            "text": "Hello there. [Music]",
            "language": "English",
            "duration": 1.0,
            "segments": [
                {"text": " Hello there.", "avg_logprob": -0.1, "no_speech_prob": 0.01},
                {"text": " Thanks for watching!", "avg_logprob": -1.2, "no_speech_prob": 0.9},
            ],
        })

    transcript = recognizer(handler).transcribe(SILENCE, "en", True)
    assert transcript.text == "Hello there."
    assert transcript.language == "en"


def test_speech_falls_back_to_local_and_cools_down():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "5"}, json={"error": {"message": "rate limited"}})

    cooldown = Cooldown(30)
    wrapped = FallbackRecognizer(recognizer(handler), ScriptedRecognizer("local words"), cooldown)
    assert wrapped.transcribe(SILENCE, "en", True).text == "local words"
    assert cooldown.active
    # While cooling down Groq is not even tried.
    assert wrapped.transcribe(SILENCE, "en", True).text == "local words"
    assert len(calls) == 1
    assert wrapped.partials is False


async def test_chat_streams_tokens():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "qwen/qwen3.8-27b"
        assert body["reasoning_effort"] == "none"
        return httpx.Response(200, content=sse("Hi", " there."))

    tokens = [t async for t in chat(handler).stream([{"role": "user", "content": "hi"}], "ollama-model")]
    assert "".join(tokens) == "Hi there."


async def test_chat_falls_back_when_offline():
    def handler(request):
        raise httpx.ConnectError("no internet")

    cooldown = Cooldown(30)
    wrapped = FallbackChat(chat(handler), ScriptedChat("Local answer."), cooldown)
    tokens = [t async for t in wrapped.stream([{"role": "user", "content": "hi"}])]
    assert "".join(tokens).strip() == "Local answer."
    assert cooldown.active


async def test_chat_failing_mid_reply_does_not_restart_locally():
    class Broken(GroqChat):
        async def stream(self, messages, model=None):
            yield "Half a "
            from app.services.groq import GroqError
            raise GroqError("connection dropped")

    wrapped = FallbackChat(Broken(settings()), ScriptedChat("Local answer."), Cooldown(30))
    received = []
    with pytest.raises(LLMError):
        async for token in wrapped.stream([{"role": "user", "content": "hi"}]):
            received.append(token)
    assert received == ["Half a "]
