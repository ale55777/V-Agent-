"""Silero VAD (ONNX) and the speech segmenter built on top of it.

The model runs through onnxruntime directly. The `silero-vad` pip package would
pull in PyTorch (over 1 GB), which this project avoids so it fits small machines.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import numpy as np

from app.services.audio.pcm import MIC_SAMPLE_RATE, VAD_FRAME_MS, VAD_FRAME_SAMPLES

_CONTEXT_SAMPLES = 64


class SpeechModel(Protocol):
    def probability(self, frame: np.ndarray) -> float: ...
    def reset(self) -> None: ...


class SileroVAD:
    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(MIC_SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT_SAMPLES), dtype=np.float32)

    def probability(self, frame: np.ndarray) -> float:
        if frame.shape[0] != VAD_FRAME_SAMPLES:
            raise ValueError(f"Silero VAD needs {VAD_FRAME_SAMPLES} samples, got {frame.shape[0]}")
        data = np.concatenate([self._context, frame.reshape(1, -1)], axis=1)
        out, self._state = self._session.run(None, {"input": data, "state": self._state, "sr": self._sr})
        self._context = data[:, -_CONTEXT_SAMPLES:]
        return float(out[0][0])


class VadEvent(str, Enum):
    NONE = "none"
    SPEECH_STARTED = "speech_started"
    SPEECH_ENDED = "speech_ended"


@dataclass
class VadConfig:
    threshold: float = 0.5
    interrupt_threshold: float = 0.75
    min_speech_ms: int = 160
    interrupt_min_speech_ms: int = 280
    silence_ms: int = 500
    max_silence_ms: int = 1800
    padding_ms: int = 192
    max_utterance_seconds: int = 30

    @classmethod
    def from_sensitivity(cls, sensitivity: float, **overrides) -> "VadConfig":
        """Map a 0..1 sensitivity slider to a probability threshold (higher = more sensitive)."""
        sensitivity = min(max(sensitivity, 0.0), 1.0)
        threshold = round(0.8 - 0.6 * sensitivity, 3)
        # Interrupting stays hard however sensitive the slider is: on laptop speakers the
        # agent's own voice reaches the mic, and a low bar let it cut itself off mid-word.
        interrupt = round(min(max(threshold + 0.25, 0.7), 0.95), 3)
        return cls(threshold=threshold, interrupt_threshold=interrupt, **overrides)


class SpeechSegmenter:
    """Turns per-frame speech probabilities into utterances.

    While the agent is speaking (`guarded=True`) the threshold and minimum duration
    are raised, because residual echo of the agent's voice can reach the microphone.
    `extend_silence` lets the turn detector wait longer when a partial transcript
    sounds unfinished, so a natural pause does not cut the user off.
    """

    def __init__(self, config: VadConfig) -> None:
        self.config = config
        self.guarded = False
        self.silence_target_ms = float(config.silence_ms)
        padding_frames = max(1, int(config.padding_ms / VAD_FRAME_MS))
        self._pre_roll: deque[bytes] = deque(maxlen=padding_frames)
        self._candidate: list[bytes] = []
        self._utterance: list[bytes] = []
        self._in_speech = False
        self._silence_ms = 0.0
        self.speech_ms = 0.0
        self.finished_utterance: bytes | None = None
        # Diagnostics for barge-in, read and reset by the engine's periodic mic report.
        self.guarded_peak = 0.0
        self.guarded_best_run_ms = 0.0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def current_audio(self) -> bytes:
        return b"".join(self._utterance)

    def extend_silence(self, multiplier: float) -> None:
        target = self.config.silence_ms * multiplier
        self.silence_target_ms = min(target, float(self.config.max_silence_ms))

    def reset(self) -> None:
        self._pre_roll.clear()
        self._candidate.clear()
        self._utterance.clear()
        self._in_speech = False
        self._silence_ms = 0.0
        self.speech_ms = 0.0
        self.silence_target_ms = float(self.config.silence_ms)
        self.finished_utterance = None

    def process(self, frame: bytes, probability: float) -> VadEvent:
        cfg = self.config
        threshold = cfg.interrupt_threshold if self.guarded else cfg.threshold

        if self.guarded:
            self.guarded_peak = max(self.guarded_peak, probability)
        if not self._in_speech:
            if probability >= threshold:
                self._candidate.append(frame)
                if self.guarded:
                    self.guarded_best_run_ms = max(self.guarded_best_run_ms, len(self._candidate) * VAD_FRAME_MS)
                needed = cfg.interrupt_min_speech_ms if self.guarded else cfg.min_speech_ms
                if len(self._candidate) * VAD_FRAME_MS >= needed:
                    self._utterance = [*self._pre_roll, *self._candidate]
                    self.speech_ms = len(self._candidate) * VAD_FRAME_MS
                    self._candidate.clear()
                    self._pre_roll.clear()
                    self._in_speech = True
                    self._silence_ms = 0.0
                    return VadEvent.SPEECH_STARTED
            else:
                self._pre_roll.extend(self._candidate)
                self._candidate.clear()
                self._pre_roll.append(frame)
            return VadEvent.NONE

        self._utterance.append(frame)
        # Hysteresis: once speech has started, a lower bar keeps it going.
        if probability >= max(cfg.threshold - 0.15, 0.1):
            self._silence_ms = 0.0
            self.speech_ms += VAD_FRAME_MS
        else:
            self._silence_ms += VAD_FRAME_MS

        too_long = len(self._utterance) * VAD_FRAME_MS >= cfg.max_utterance_seconds * 1000
        if self._silence_ms >= self.silence_target_ms or too_long:
            self.finished_utterance = b"".join(self._utterance)
            self._utterance = []
            self._in_speech = False
            self._silence_ms = 0.0
            self.silence_target_ms = float(cfg.silence_ms)
            return VadEvent.SPEECH_ENDED
        return VadEvent.NONE
