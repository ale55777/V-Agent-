"""Streaming chat client for a local Ollama server, plus context management."""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from app.config.settings import Settings

DEFAULT_PERSONALITY = "You are warm, curious and direct, like a knowledgeable friend."

SYSTEM_TEMPLATE = """You are EchoAI, a voice assistant having a live spoken conversation.
{personality}
Rules:
- Your words are read aloud, so write plain sentences. Never use lists, markdown, code or emoji.
- Reply in one to three short sentences unless the user asks for more detail.
- Always give a complete answer in full sentences. Never reply with a single word or a fragment.
- Do not greet the user again or repeat what they just said. Get straight to the answer.
- Use the conversation so far to understand follow-up questions.
- If you do not know something, say so plainly. Never invent facts.
- {language_rule}"""

LANGUAGE_RULES = {
    "en": "Reply in English.",
    "ur": "Reply in Urdu when the user speaks Urdu.",
    "auto": "Reply in the language the user is speaking.",
}


class LLMError(RuntimeError):
    pass


class ChatModel(Protocol):
    def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]: ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


# An interrupted reply shorter than this is left out of the prompt entirely.
MIN_INTERRUPTED_WORDS = 6


def clean_history(history: list[dict]) -> list[dict]:
    """Turn the saved conversation into a prompt the model will not learn bad habits from.

    Measured 2026-11-06: once a few replies had been cut off by the mic ("Hey,", "I"),
    the model copied them and answered every later question with one word. So cut-off
    fragments are dropped, longer cut-off replies are marked unfinished, and several user
    turns in a row (the user repeating themselves while no answer came) become one.
    """
    cleaned: list[dict] = []
    for message in history:
        role, content = message["role"], message["content"].strip()
        if not content:
            continue
        if role == "assistant" and message.get("interrupted"):
            if len(content.split()) < MIN_INTERRUPTED_WORDS:
                continue
            content = content.rstrip(" ,;:-") + "..."
        if cleaned and cleaned[-1]["role"] == role:
            cleaned[-1]["content"] += " " + content
        else:
            cleaned.append({"role": role, "content": content})
    return cleaned


def build_messages(
    history: list[dict], personality: str, language: str, num_ctx: int, reserve_for_reply: int
) -> list[dict]:
    """Build a prompt that fits the context window.

    The oldest turns are dropped first. The system prompt and the latest user turn
    are always kept, so a long conversation degrades gracefully instead of failing.
    """
    history = clean_history(history)
    system = SYSTEM_TEMPLATE.format(
        personality=personality.strip() or DEFAULT_PERSONALITY,
        language_rule=LANGUAGE_RULES.get(language, LANGUAGE_RULES["en"]),
    )
    budget = num_ctx - reserve_for_reply - estimate_tokens(system)
    kept: list[dict] = []
    for message in reversed(history):
        cost = estimate_tokens(message["content"]) + 4
        if kept and cost > budget:
            break
        budget -= cost
        kept.append(message)
    kept.reverse()
    # A conversation must not open on an assistant turn left over from trimming.
    while len(kept) > 1 and kept[0]["role"] == "assistant":
        kept.pop(0)
    return [{"role": "system", "content": system}, *kept]


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url, timeout=httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=5.0)
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[dict]:
        response = await self._client.get("/api/tags")
        response.raise_for_status()
        return [
            {"name": m["name"], "size": m.get("size"), "family": m.get("details", {}).get("family")}
            for m in response.json().get("models", [])
        ]

    async def warm_up(self, model: str | None = None) -> None:
        """Load the model and evaluate the system prompt, so Ollama's prompt cache spares the
        first real turn the cost (measured at over 5 s cold on a 2-core laptop CPU)."""
        settings = self.settings
        messages = build_messages(
            [{"role": "user", "content": "Hi"}], "", "en", settings.llm_num_ctx, settings.llm_num_predict
        )
        payload = {
            "model": model or settings.llm_model,
            "keep_alive": -1,
            "stream": False,
            "messages": messages,
            "options": {"num_ctx": settings.llm_num_ctx, "num_predict": 1},
        }
        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()

    async def stream(self, messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
        settings = self.settings
        options: dict = {
            "num_ctx": settings.llm_num_ctx,
            "num_predict": settings.llm_num_predict,
            "temperature": settings.llm_temperature,
            "repeat_penalty": 1.15,
        }
        if settings.llm_num_threads:
            options["num_thread"] = settings.llm_num_threads
        payload = {
            "model": model or settings.llm_model,
            "messages": messages,
            "stream": True,
            "keep_alive": -1,
            "options": options,
        }
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace")
                    raise LLMError(f"Ollama returned {response.status_code}: {body[:200]}")
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if error := data.get("error"):
                        raise LLMError(error)
                    if token := data.get("message", {}).get("content"):
                        yield token
                    if data.get("done"):
                        return
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach Ollama at {settings.ollama_url}: {exc}") from exc


# Sentence boundary, or a clause boundary once enough text has built up. Sending
# clauses early is what gets the first audio out before the reply has finished.
_SENTENCE_END = re.compile(r"(?<=[.!?۔؟])[\"')\]]*\s+")
_CLAUSE_END = re.compile(r"(?<=[,;:،])\s+")
_ABBREVIATIONS = re.compile(r"(?:\b(?:Mr|Mrs|Ms|Dr|St|vs|etc|e\.g|i\.e)|\b[A-Z]|\b\d+)\.\s+$")


class SentenceChunker:
    """Splits streaming tokens into speakable pieces."""

    def __init__(self, first_clause_chars: int = 24, clause_chars: int = 70, max_chars: int = 90) -> None:
        self.first_clause_chars = first_clause_chars
        self.clause_chars = clause_chars
        self.max_chars = max_chars
        self._buffer = ""
        self._emitted = 0

    def push(self, token: str) -> list[str]:
        self._buffer += token
        out: list[str] = []
        while True:
            piece = self._next_piece()
            if piece is None:
                return out
            out.append(piece)
            self._emitted += 1

    def _next_piece(self) -> str | None:
        for match in _SENTENCE_END.finditer(self._buffer):
            if _ABBREVIATIONS.search(self._buffer[: match.end()]):
                continue
            return self._take(match.end())
        limit = self.first_clause_chars if self._emitted == 0 else self.clause_chars
        if len(self._buffer) >= limit:
            for match in _CLAUSE_END.finditer(self._buffer):
                if match.start() >= limit // 2:
                    return self._take(match.end())
        # A long sentence with no punctuation still starts speaking, between two words,
        # rather than making the user wait for all of it on a slow machine.
        if len(self._buffer) >= self.max_chars:
            cut = self._buffer.rfind(" ")
            if cut > 0:
                return self._take(cut + 1)
        return None

    def _take(self, end: int) -> str | None:
        piece, self._buffer = self._buffer[:end].strip(), self._buffer[end:]
        return piece or None

    def flush(self) -> str | None:
        piece, self._buffer = self._buffer.strip(), ""
        return piece or None
