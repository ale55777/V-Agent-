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
    assert wrapped.rate_limit.active
    assert not cooldown.active  # Chat has its own limit and carries on using Groq.
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


def limited_or(tokens, seen, name):
    """A chat model that records its calls, and is rate limited unless given tokens to say."""
    def handler(request):
        seen.append(name)
        if tokens is None:
            return httpx.Response(429, headers={"retry-after": "5"}, json={"error": {"message": "daily limit"}})
        return httpx.Response(200, content=sse(*tokens))

    return chat(handler)


async def test_rate_limited_model_hands_over_to_a_spare_and_is_then_skipped():
    seen = []
    cooldown = Cooldown(30)
    wrapped = FallbackChat(
        limited_or(None, seen, "main"), ScriptedChat("Local answer."), cooldown, [limited_or(["Spare answer."], seen, "spare")]
    )
    for _ in range(2):
        tokens = [t async for t in wrapped.stream([{"role": "user", "content": "hi"}])]
        assert "".join(tokens) == "Spare answer."
    assert seen == ["main", "spare", "spare"]
    assert not cooldown.active  # Speech to text must stay on Groq.


async def test_local_model_answers_when_every_groq_model_is_rate_limited():
    seen = []
    wrapped = FallbackChat(
        limited_or(None, seen, "main"), ScriptedChat("Local answer."), Cooldown(30), [limited_or(None, seen, "spare")]
    )
    tokens = [t async for t in wrapped.stream([{"role": "user", "content": "hi"}])]
    assert "".join(tokens).strip() == "Local answer."
    assert seen == ["main", "spare"]


async def test_no_spare_is_tried_when_groq_is_unreachable():
    seen = []

    def offline(request):
        seen.append("main")
        raise httpx.ConnectError("no internet")

    wrapped = FallbackChat(
        chat(offline), ScriptedChat("Local answer."), Cooldown(30), [limited_or(["Spare answer."], seen, "spare")]
    )
    tokens = [t async for t in wrapped.stream([{"role": "user", "content": "hi"}])]
    assert "".join(tokens).strip() == "Local answer."
    assert seen == ["main"]  # A dead connection costs one timeout, not one per model.


async def test_spare_models_think_briefly_and_keep_room_to_answer():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-oss-120b"
        assert body["reasoning_effort"] == "low" and body["include_reasoning"] is False
        assert body["max_tokens"] > settings().llm_num_predict
        return httpx.Response(200, content=sse("Hi."))

    spare = chat(handler)
    spare.model_name = "openai/gpt-oss-120b"
    assert [t async for t in spare.stream([{"role": "user", "content": "hi"}])] == ["Hi."]
