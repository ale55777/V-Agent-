"""Loads and owns the shared AI models for the whole process."""
from __future__ import annotations

import asyncio
import logging
import os
import time

import numpy as np

from app.config.settings import Settings
from app.services.asr.whisper import WhisperRecognizer
from app.services.llm.ollama import OllamaClient
from app.services.tts.engines import KokoroEngine, PiperEngine
from app.services.vad.silero import SileroVAD

log = logging.getLogger(__name__)


class ModelRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm = OllamaClient(settings)
        self.recognizer: WhisperRecognizer | None = None
        self.engines: dict = {}
        self.status: dict[str, dict] = {
            "vad": {"state": "pending"},
            "stt": {"state": "pending"},
            "tts": {"state": "pending"},
            "llm": {"state": "pending"},
        }
        self.ready = asyncio.Event()

    def new_vad(self) -> SileroVAD:
        # Silero is stateful, so each session gets its own instance.
        return SileroVAD(self.settings.models_dir / "silero" / "silero_vad.onnx")

    def default_tts(self):
        preference = self.settings.tts_engine
        if preference in self.engines:
            return self.engines[preference]
        return self.engines.get("kokoro") or self.engines.get("piper")

    def tts_for(self, engine: str | None):
        return self.engines.get(engine or "") or self.default_tts()

    async def load_all(self) -> None:
        loaders = [("vad", self._load_vad), ("stt", self._load_stt), ("tts", self._load_tts)]
        for key, loader in loaders:
            started = time.perf_counter()
            try:
                detail = await asyncio.to_thread(loader)
                self.status[key] = {
                    "state": "ready",
                    "detail": detail,
                    "load_ms": round((time.perf_counter() - started) * 1000),
                }
            except Exception as exc:  # A missing model must not stop the API from serving.
                log.exception("Failed to load %s", key)
                self.status[key] = {"state": "failed", "detail": str(exc)}
        await self._warm_llm()
        self.ready.set()

    async def _warm_llm(self) -> None:
        started = time.perf_counter()
        try:
            await self.llm.warm_up()
            self.status["llm"] = {
                "state": "ready",
                "detail": self.settings.llm_model,
                "load_ms": round((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:
            log.warning("Ollama is not ready: %s", exc)
            self.status["llm"] = {"state": "failed", "detail": f"{self.settings.llm_model}: {exc}"}

    def _load_vad(self) -> str:
        vad = self.new_vad()
        vad.probability(np.zeros(512, dtype=np.float32))
        return "silero-vad (onnx)"

    def _load_stt(self) -> str:
        s = self.settings
        self.recognizer = WhisperRecognizer(s.stt_model, s.models_dir, s.stt_compute_type, s.stt_threads)
        # The first inference allocates buffers, so run it here rather than on a real turn.
        self.recognizer.transcribe(np.zeros(8000, dtype=np.float32), "en", True)
        return self.recognizer.name

    def _load_tts(self) -> str:
        s = self.settings
        errors = []
        if s.tts_engine in ("auto", "kokoro"):
            try:
                self.engines["kokoro"] = KokoroEngine(s.models_dir, s.tts_threads, s.kokoro_voice)
            except Exception as exc:
                errors.append(f"kokoro: {exc}")
                log.warning("Kokoro is unavailable: %s", exc)
        try:
            self.engines["piper"] = PiperEngine(s.models_dir, s.piper_voice, s.tts_threads)
        except Exception as exc:
            errors.append(f"piper: {exc}")
            log.warning("Piper is unavailable: %s", exc)
        if s.tts_engine == "auto":
            self.settings.tts_engine = self._fastest_engine()
        engine = self.default_tts()
        if engine is None:
            raise RuntimeError("No text-to-speech engine could be loaded: " + "; ".join(errors))
        return engine.name

    def _fastest_engine(self) -> str:
        """Benchmark on this machine and prefer Kokoro only when it keeps up with real time."""
        if "kokoro" not in self.engines:
            return "piper"
        if "piper" not in self.engines:
            return "kokoro"
        # Measured on a 2-core laptop CPU: Kokoro only manages 0.7x real time. On a small
        # machine, skip the benchmark too, because it would load the model just to reject it.
        if (os.cpu_count() or 1) <= 4:
            log.info("Using Piper by default on this %s-thread CPU", os.cpu_count())
            return "piper"
        engine = self.engines["kokoro"]
        started = time.perf_counter()
        samples = np.concatenate(list(engine.synthesize("This is a short speed test.", None)))
        elapsed = time.perf_counter() - started
        audio_seconds = len(samples) / engine.sample_rate
        ratio = elapsed / audio_seconds
        log.info("Kokoro real-time factor %.2f (below 1 is faster than real time)", ratio)
        return "kokoro" if ratio < 0.8 else "piper"

    def voices(self) -> list[dict]:
        out: list[dict] = []
        for engine in self.engines.values():
            out.extend(engine.voices())
        return out
