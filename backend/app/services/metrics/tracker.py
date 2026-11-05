"""Real timing and latency measurement. Every figure comes from a measured event."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TurnMetrics:
    speech_end: float
    stt_ms: float
    llm_first_token_ms: float | None = None
    first_audio_ms: float | None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass
class SessionClock:
    """Tracks active and paused time. Time while disconnected counts as paused."""

    active_seconds: float = 0.0
    paused_seconds: float = 0.0
    user_speech_seconds: float = 0.0
    ai_speech_seconds: float = 0.0
    interruption_count: int = 0
    interruption_latencies: list[float] = field(default_factory=list)
    turns: list[dict] = field(default_factory=list)
    _running_since: float | None = None
    _paused_since: float | None = None
    _now: callable = time.monotonic

    def resume(self) -> None:
        now = self._now()
        if self._paused_since is not None:
            self.paused_seconds += now - self._paused_since
            self._paused_since = None
        if self._running_since is None:
            self._running_since = now

    def pause(self) -> None:
        now = self._now()
        if self._running_since is not None:
            self.active_seconds += now - self._running_since
            self._running_since = None
        if self._paused_since is None:
            self._paused_since = now

    def stop(self) -> None:
        now = self._now()
        if self._running_since is not None:
            self.active_seconds += now - self._running_since
            self._running_since = None
        if self._paused_since is not None:
            self.paused_seconds += now - self._paused_since
            self._paused_since = None

    def active_now(self) -> float:
        extra = self._now() - self._running_since if self._running_since is not None else 0.0
        return self.active_seconds + extra

    def paused_now(self) -> float:
        extra = self._now() - self._paused_since if self._paused_since is not None else 0.0
        return self.paused_seconds + extra

    def add_user_speech(self, seconds: float) -> None:
        self.user_speech_seconds += seconds

    def add_ai_speech(self, seconds: float) -> None:
        self.ai_speech_seconds += seconds

    def record_interruption(self, latency_ms: float) -> None:
        self.interruption_count += 1
        self.interruption_latencies.append(latency_ms)

    def record_turn(self, turn: TurnMetrics, interrupted: bool) -> None:
        self.turns.append(
            {
                "stt_ms": round(turn.stt_ms, 1),
                "llm_first_token_ms": None if turn.llm_first_token_ms is None else round(turn.llm_first_token_ms, 1),
                "first_audio_ms": None if turn.first_audio_ms is None else round(turn.first_audio_ms, 1),
                "interrupted": interrupted,
            }
        )

    def snapshot(self) -> dict:
        # Only turns that answered speech count. "Repeat that" and resume-continuations skip
        # transcription, so including them would make response latency look better than it is.
        audio = [t["first_audio_ms"] for t in self.turns if t["first_audio_ms"] is not None and t["stt_ms"]]
        stt = [t["stt_ms"] for t in self.turns if t["stt_ms"]]
        llm = [t["llm_first_token_ms"] for t in self.turns if t["llm_first_token_ms"] is not None]
        return {
            "active_seconds": round(self.active_now(), 2),
            "paused_seconds": round(self.paused_now(), 2),
            "user_speaking_seconds": round(self.user_speech_seconds, 2),
            "ai_speaking_seconds": round(self.ai_speech_seconds, 2),
            "interruption_count": self.interruption_count,
            "turn_count": len(self.turns),
            "average_latency_ms": _mean(audio),
            "minimum_latency_ms": min(audio) if audio else None,
            "maximum_latency_ms": max(audio) if audio else None,
            "average_transcription_ms": _mean(stt),
            "average_llm_first_token_ms": _mean(llm),
            "average_interruption_ms": _mean(self.interruption_latencies),
        }
