"""Optional online engine: speech-to-text and chat on Groq, with the local models as fallback.

Nothing here runs unless the user picks "Groq" in Settings and a key is configured.
Whenever Groq fails (no internet, rate limit, timeout, server error) the same request is
answered by the local model, and Groq is left alone for a while before it is tried again,
so a dead connection costs one timeout rather than one per turn.
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time
import wave
from collections.abc import AsyncIterator

import httpx
import numpy as np

from app.config.settings import Settings
from app.services.asr.whisper import _NON_SPEECH, Transcript
from app.services.audio.pcm import float_to_pcm16
from app.services.llm.ollama import LLMError

log = logging.getLogger(__name__)

# Groq's verbose_json reports the language by name; the rest of the app uses codes.
_LANGUAGE_CODES = {"english": "en", "urdu": "ur", "hindi": "hi", "arabic": "ar", "punjabi": "pa"}


class GroqError(RuntimeError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        return None


def _error_from(response: httpx.Response, body: bytes) -> GroqError:
    text = body.decode(errors="replace")
    try:
        text = json.loads(text)["error"]["message"]
    except Exception:
        pass
    return GroqError(f"Groq returned {response.status_code}: {text[:200]}", _retry_after(response))


def _wav(samples: np.ndarray, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(float_to_pcm16(samples))
    return buffer.getvalue()


class Cooldown:
    """Remembers that Groq just failed, so the next turns go straight to the local model."""

    def __init__(self, default_seconds: float) -> None:
        self.default_seconds = default_seconds
        self._until = 0.0
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return time.monotonic() < self._until

    def trip(self, seconds: float | None = None) -> None:
        with self._lock:
            self._until = time.monotonic() + max(seconds or 0.0, self.default_seconds)

    def clear(self) -> None:
        self._until = 0.0


# ---- speech to text ---------------------------------------------------------------------


class GroqRecognizer:
    def __init__(self, settings: Settings) -> None:
        self.model_name = settings.groq_stt_model
        self.name = f"groq/{self.model_name}"
        self._client = httpx.Client(
            base_url=settings.groq_base_url,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            timeout=httpx.Timeout(settings.groq_timeout_seconds, connect=3.0),
        )

    def close(self) -> None:
        self._client.close()

    def transcribe(self, samples: np.ndarray, language: str | None, final: bool, hint: str = "") -> Transcript:
        data = {"model": self.model_name, "response_format": "verbose_json", "temperature": "0"}
        if language and language != "auto":
            data["language"] = language
        if hint:
            data["prompt"] = hint
        try:
            response = self._client.post(
                "/audio/transcriptions", data=data, files={"file": ("speech.wav", _wav(samples), "audio/wav")}
            )
        except httpx.HTTPError as exc:
            raise GroqError(f"Could not reach Groq: {exc}") from exc
        if response.status_code != 200:
            raise _error_from(response, response.content)
        body = response.json()
        segments = body.get("segments") or []
        parts, confidences = [], []
        for segment in segments:
            # Same hallucination filter as the local recogniser.
            if segment.get("no_speech_prob", 0) > 0.75 and segment.get("avg_logprob", 0) < -0.7:
                continue
            parts.append(segment.get("text", ""))
            confidences.append(float(np.exp(segment.get("avg_logprob", 0.0))))
        text = " ".join(" ".join(parts).split()) if segments else " ".join(body.get("text", "").split())
        if _NON_SPEECH.match(text):
            text = ""
        detected = str(body.get("language") or language or "en").lower()
        return Transcript(
            text=text,
            language=_LANGUAGE_CODES.get(detected, detected),
            duration=float(body.get("duration") or len(samples) / 16000),
            confidence=float(np.mean(confidences)) if confidences else (1.0 if text else 0.0),
        )


class FallbackRecognizer:
    """Groq first, the local Whisper model if Groq fails."""

    # Live partial transcripts would be one request per second while the user talks, which
    # burns the free rate limit; and running them locally would bring back the CPU load this
    # engine exists to avoid. So the online engine only transcribes finished utterances.
    partials = False

    def __init__(self, primary: GroqRecognizer, fallback, cooldown: Cooldown) -> None:
        self.primary = primary
        self.fallback = fallback
        self.cooldown = cooldown
        self.name = primary.name

    def transcribe(self, samples: np.ndarray, language: str | None, final: bool, hint: str = "") -> Transcript:
        if not self.cooldown.active or self.fallback is None:
            try:
                return self.primary.transcribe(samples, language, final, hint)
            except GroqError as exc:
                if self.fallback is None:
                    raise
                log.warning("Groq speech-to-text failed, using the local model: %s", exc)
                self.cooldown.trip(exc.retry_after)
        return self.fallback.transcribe(samples, language, final, hint)


# ---- chat ------------------------------------------------------------------------------


class GroqChat:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_name = settings.groq_llm_model
        self.name = f"groq/{self.model_name}"
        self._client = httpx.AsyncClient(
            base_url=settings.groq_base_url,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            timeout=httpx.Timeout(settings.groq_timeout_seconds, connect=3.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
        # `model` names a local Ollama model, which means nothing to Groq, so it is ignored.
        settings = self.settings
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
            "max_tokens": settings.llm_num_predict,
            "temperature": settings.llm_temperature,
        }
        if self.model_name.startswith("qwen/"):
            payload["reasoning_effort"] = "none"  # A spoken reply cannot wait for thinking.
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    raise _error_from(response, await response.aread())
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    chunk = json.loads(data)
                    if error := chunk.get("error"):
                        raise GroqError(str(error.get("message", error)))
                    for choice in chunk.get("choices", []):
                        if token := (choice.get("delta") or {}).get("content"):
                            yield token
        except httpx.HTTPError as exc:
            raise GroqError(f"Could not reach Groq: {exc}") from exc


class FallbackChat:
    """Groq first, the local Ollama model if Groq fails before saying anything."""

    def __init__(self, primary: GroqChat, fallback, cooldown: Cooldown) -> None:
        self.primary = primary
        self.fallback = fallback
        self.cooldown = cooldown
        self.name = primary.name

    async def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
        if not self.cooldown.active:
            started = False
            try:
                async for token in self.primary.stream(messages, model):
                    started = True
                    yield token
                return
            except GroqError as exc:
                if started:
                    # Half a reply has already been spoken; restarting it locally would repeat it.
                    raise LLMError(str(exc)) from exc
                log.warning("Groq chat failed, using the local model: %s", exc)
                self.cooldown.trip(exc.retry_after)
        async for token in self.fallback.stream(messages, model):
            yield token
