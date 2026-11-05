"""Speech recognition with faster-whisper.

Whisper is not a true streaming recogniser. Partial transcripts are produced by
re-transcribing the growing utterance at an interval, so they appear in bursts
(about once a second), not word by word. When the CPU is busy a partial is skipped
instead of queued, so the final transcript is never delayed by stale work.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

_NON_SPEECH = re.compile(r"^[\s\[\(\*♪].*[\]\)\*♪\s]$|^\s*$")


@dataclass
class Transcript:
    text: str
    language: str
    duration: float
    confidence: float = 1.0


class SpeechRecognizer(Protocol):
    name: str

    def transcribe(self, samples: np.ndarray, language: str | None, final: bool, hint: str = "") -> Transcript: ...


class WhisperRecognizer:
    def __init__(self, model: str, models_dir: Path, compute_type: str = "int8", threads: int = 2) -> None:
        from faster_whisper import WhisperModel

        self.name = f"faster-whisper/{model}"
        self.model_name = model
        self._model = WhisperModel(
            model,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=threads,
            num_workers=1,
            download_root=str(models_dir / "whisper"),
            # Setup downloads the model. Running never touches the network, so it works offline.
            local_files_only=True,
        )

    def transcribe(self, samples: np.ndarray, language: str | None, final: bool, hint: str = "") -> Transcript:
        segments, info = self._model.transcribe(
            samples,
            language=language if language and language != "auto" else None,
            beam_size=1,  # Fastest. Latency matters more here than a small accuracy gain.
            best_of=1,
            temperature=0.0,
            vad_filter=False,  # Silero has already segmented this audio.
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=hint or None,
        )
        parts, confidences = [], []
        for segment in segments:
            if segment.no_speech_prob > 0.75 and segment.avg_logprob < -0.7:
                continue
            parts.append(segment.text)
            confidences.append(float(np.exp(segment.avg_logprob)))
        text = " ".join(" ".join(parts).split())
        if _NON_SPEECH.match(text):  # "[Music]", "(coughs)" and similar
            text = ""
        confidence = float(np.mean(confidences)) if confidences else 0.0
        return Transcript(text=text, language=info.language, duration=float(info.duration), confidence=confidence)
