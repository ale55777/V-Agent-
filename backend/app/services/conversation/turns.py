"""Pure conversation logic: voice commands, wake word, echo filtering, turn completion."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from enum import Enum


class Command(str, Enum):
    PAUSE = "pause"
    STOP = "stop"
    RESUME = "resume"
    REPEAT = "repeat"
    END = "end"


_COMMANDS: dict[str, Command] = {
    "pause": Command.PAUSE,
    "pause please": Command.PAUSE,
    "hold on": Command.PAUSE,
    "stop": Command.STOP,
    "stop talking": Command.STOP,
    "stop please": Command.STOP,
    "continue": Command.RESUME,
    "please continue": Command.RESUME,
    "resume": Command.RESUME,
    "im back": Command.RESUME,
    "i am back": Command.RESUME,
    "listen to me": Command.RESUME,
    "repeat that": Command.REPEAT,
    "repeat": Command.REPEAT,
    "say that again": Command.REPEAT,
    "end conversation": Command.END,
    "end the conversation": Command.END,
    "end this conversation": Command.END,
    # Whisper tiny reliably hears a lone "end" as "and" (measured with two different voices).
    # Only an utterance consisting of exactly this phrase matches, so it is safe to accept.
    "and conversation": Command.END,
    "and the conversation": Command.END,
    "and this conversation": Command.END,
}

# Prompting Whisper with the vocabulary on short clips raised recognition from 41/42 to 42/42,
# with no new false commands across 26 other short phrases (scripted voices, tiny model).
COMMAND_HINT = "Pause. Stop. Continue. I'm back. Repeat that. End conversation."
COMMAND_HINT_MAX_SECONDS = 2.0

_NORMALISE = re.compile(r"[^\w\s]")
_FILLERS = re.compile(r"^(?:(?:um+|uh+|ok|okay|hey|please|echo|echo ai|so)\s+)+")
_WAKE_GREETINGS = {"hey", "hi", "hello", "ok", "okay", "yo"}


def normalise(text: str) -> str:
    return " ".join(_NORMALISE.sub("", text.lower()).split())


def parse_command(text: str) -> Command | None:
    """Only a short phrase that is entirely a command counts.

    "Stop" is a command; "how do I stop a leaky tap" must reach the model.
    """
    cleaned = normalise(text)
    return _COMMANDS.get(cleaned) or _COMMANDS.get(_FILLERS.sub("", cleaned))


def strip_wake_word(text: str, wake_word: str) -> tuple[bool, str]:
    """Return (heard, remainder). The wake word must open the utterance."""
    words = text.split()
    target = normalise(wake_word)
    for index, word in enumerate(words[:3]):
        cleaned = normalise(word)
        if cleaned == target or cleaned == f"{target}ai":
            remainder = " ".join(words[index + 1 :]).lstrip(" ,.!?-")
            if remainder.lower().startswith("ai "):
                remainder = remainder[3:]
            return True, remainder
        if cleaned not in _WAKE_GREETINGS:
            break
    return False, text


def is_echo(transcript: str, spoken_text: str, threshold: float = 0.72) -> bool:
    """True when a transcript looks like the agent's own voice picked up by the mic."""
    heard, spoken = normalise(transcript), normalise(spoken_text)
    if not heard or not spoken:
        return False
    heard_words = heard.split()
    spoken_words = spoken.split()
    if len(heard_words) < 2:
        return False  # One word is never enough evidence to discard real speech.
    if heard in spoken and len(heard_words) >= 3:
        return True
    if len(heard_words) <= 3:
        # Too short for fuzzy matching: "end conversation" scored 0.82 against the agent's
        # "voice conversation" and the user's command was thrown away. Exact words only.
        return f" {heard} " in f" {spoken} "
    # Compare against the same-length window of agent speech that matches best.
    size = len(heard_words)
    best = 0.0
    for start in range(0, max(1, len(spoken_words) - size + 1)):
        window = " ".join(spoken_words[start : start + size])
        best = max(best, SequenceMatcher(None, heard, window).ratio())
        if best >= threshold:
            return True
    return False


_CONTINUATION_END = {
    "and", "but", "or", "so", "because", "the", "a", "an", "to", "of", "in", "on", "for", "with",
    "that", "which", "if", "when", "my", "your", "is", "are", "was", "um", "uh", "like", "then",
}


def silence_multiplier(partial: str) -> float:
    """How much longer to wait for the user, given what they have said so far."""
    stripped = partial.strip()
    if not stripped:
        return 1.0
    if stripped[-1] in ",;:-":
        return 2.0
    words = normalise(stripped).split()
    if words and words[-1] in _CONTINUATION_END:
        return 2.2
    if stripped[-1] in ".!?":
        return 1.0
    return 1.3


def spoken_portion(text: str, played_ms: float, duration_ms: float) -> str:
    """Estimate the words of a chunk heard before playback stopped, whole words only."""
    if duration_ms <= 0 or played_ms >= duration_ms:
        return text
    if played_ms <= 0:
        return ""
    words = text.split()
    count = int(len(words) * (played_ms / duration_ms))
    return " ".join(words[:count])


def title_from(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text.rstrip(" .,!?") or "New conversation"
    return text[:limit].rsplit(" ", 1)[0].rstrip(" .,!?") + "…"
