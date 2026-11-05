"""Local text-to-speech engines: Kokoro-82M (ONNX) with a Piper fallback."""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Iterator, Protocol

import numpy as np

log = logging.getLogger(__name__)

KOKORO_MODEL = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = "voices-v1.0.bin"
_MAX_CHARS = 400


class TTSEngine(Protocol):
    name: str
    sample_rate: int

    def voices(self) -> list[dict]: ...
    def synthesize(self, text: str, voice: str | None, language: str = "en") -> Iterator[np.ndarray]: ...


_ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")


def speech_language(text: str) -> str:
    """Pick the voice by the script actually written. An English voice given Urdu letters
    reads them out one at a time (measured: 18 s of gibberish for one short sentence)."""
    return "ur" if _ARABIC_SCRIPT.search(text) else "en"


def clean_for_speech(text: str) -> str:
    """Strip markdown and symbols that would otherwise be read out loud."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_`#>~|]+", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return " ".join(text.split())


class KokoroEngine:
    sample_rate = 24000

    _VOICE_LABELS = {"a": "American", "b": "British"}
    _LANG_BY_PREFIX = {"a": "en-us", "b": "en-gb"}
    # Kokoro has no Urdu voice. A Hindi voice with espeak-ng's Urdu phonemes is intelligible:
    # Whisper transcribed the result back almost word for word. Pronunciation is approximate.
    URDU_VOICE = "hf_alpha"

    def __init__(self, models_dir: Path, threads: int = 2, default_voice: str = "af_heart") -> None:
        self._directory = models_dir / "kokoro"
        for name in (KOKORO_MODEL, KOKORO_VOICES):
            if not (self._directory / name).exists():
                raise FileNotFoundError(f"{name} has not been downloaded")
        self._threads = threads
        self._loaded = None
        self._lock = threading.Lock()
        self.default_voice = default_voice
        self.name = "kokoro-82m/int8"

    @property
    def _kokoro(self):
        """Loaded on first use. It takes hundreds of MB, which a PC where Piper is the
        default cannot spare for a voice it may never use."""
        with self._lock:
            if self._loaded is None:
                os.environ.setdefault("ONNX_NUM_THREADS", str(self._threads))
                import onnxruntime as ort
                from kokoro_onnx import Kokoro

                options = ort.SessionOptions()
                options.intra_op_num_threads = self._threads
                options.inter_op_num_threads = 1
                session = ort.InferenceSession(
                    str(self._directory / KOKORO_MODEL), sess_options=options, providers=["CPUExecutionProvider"]
                )
                self._loaded = Kokoro.from_session(session, str(self._directory / KOKORO_VOICES))
            return self._loaded

    def voices(self) -> list[dict]:
        out = []
        with np.load(self._directory / KOKORO_VOICES) as archive:  # Names only; no model load.
            names = sorted(archive.files)
        for voice in names:
            prefix = voice[0]
            if prefix not in self._LANG_BY_PREFIX:
                continue
            gender = "Female" if voice[1:2] == "f" else "Male"
            label = f"{voice.split('_', 1)[-1].title()} ({self._VOICE_LABELS[prefix]} {gender})"
            out.append({"id": voice, "label": label, "engine": "kokoro", "language": "en"})
        return out

    def synthesize(self, text: str, voice: str | None, language: str = "en") -> Iterator[np.ndarray]:
        text = clean_for_speech(text)[:_MAX_CHARS]
        if not text:
            return
        if language == "ur":
            samples, _ = self._kokoro.create(text, voice=self.URDU_VOICE, speed=1.0, lang="ur", trim=True)
        else:
            voice = voice if voice in self._kokoro.get_voices() and voice[0] in self._LANG_BY_PREFIX else self.default_voice
            samples, _ = self._kokoro.create(
                text, voice=voice, speed=1.05, lang=self._LANG_BY_PREFIX[voice[0]], trim=True
            )
        yield np.asarray(samples, dtype=np.float32)


class PiperEngine:
    def __init__(self, models_dir: Path, voice: str, threads: int = 2) -> None:
        import onnxruntime  # noqa: F401  (piper imports it; fail early with a clear error)
        from piper import PiperVoice

        directory = models_dir / "piper"
        self._voices: dict[str, PiperVoice] = {}
        self._directory = directory
        self.default_voice = voice
        self._load(voice)
        self.sample_rate = self._voices[voice].config.sample_rate
        self.name = f"piper/{voice}"

    def _load(self, voice: str):
        from piper import PiperVoice

        if voice not in self._voices:
            path = self._directory / f"{voice}.onnx"
            self._voices[voice] = PiperVoice.load(str(path))
        return self._voices[voice]

    def voices(self) -> list[dict]:
        return [
            {"id": p.stem, "label": f"{p.stem} (Piper)", "engine": "piper", "language": "en"}
            for p in sorted(self._directory.glob("*.onnx"))
        ]

    def synthesize(self, text: str, voice: str | None, language: str = "en") -> Iterator[np.ndarray]:
        if language != "en":
            raise RuntimeError(f"Piper has no {language} voice installed, and Kokoro is not available.")
        text = clean_for_speech(text)[:_MAX_CHARS]
        if not text:
            return
        path = self._directory / f"{voice}.onnx"
        model = self._load(voice if voice and path.exists() else self.default_voice)
        for chunk in model.synthesize(text):
            yield chunk.audio_float_array.astype(np.float32)
