"""The real-time conversation engine for one voice session.

Pipeline: mic frames -> Silero VAD -> Whisper -> Ollama -> TTS -> client playback.

Concurrency rules:
* The event loop never runs inference. VAD, STT and TTS run on dedicated executors;
  Ollama is awaited over HTTP.
* Each response has an increasing `response_seq`. Interrupting cancels its tasks and
  bumps the sequence, so any late audio is recognisably stale on both ends.
* Only text the client confirms it played becomes a saved assistant message.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

import numpy as np

from app.services.audio.pcm import FrameAssembler, float_to_pcm16, pack_audio, pcm16_to_float
from app.services.conversation.turns import (
    COMMAND_HINT,
    COMMAND_HINT_MAX_SECONDS,
    Command,
    is_echo,
    parse_command,
    silence_multiplier,
    spoken_portion,
    strip_wake_word,
)
from app.services.llm.ollama import ChatModel, LLMError, SentenceChunker, build_messages
from app.services.metrics.tracker import SessionClock, TurnMetrics
from app.services.tts.engines import speech_language
from app.services.vad.silero import SpeechModel, SpeechSegmenter, VadConfig, VadEvent

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
# Whisper tends to hallucinate on very short clips, so they are not transcribed.
MIN_UTTERANCE_SECONDS = 0.3
WAKE_WINDOW_SECONDS = 20.0
LAG_WARNING_FRAMES = 16  # about half a second behind real time
MIC_REPORT_FRAMES = 156  # about every 5 seconds
PARTIAL_MAX_STT_SECONDS = 3.0
# Room echo and the browser's audio buffer outlast the agent's voice by a moment.
ECHO_TAIL_SECONDS = 1.0


class State(str, Enum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    PAUSED = "PAUSED"
    DISCONNECTED = "DISCONNECTED"
    ENDED = "ENDED"


class AttentionMode(str, Enum):
    ALWAYS = "always"
    WAKE_WORD = "wake_word"
    MANUAL = "manual"  # listens as in "always"; pausing is driven by the UI button


class Store(Protocol):
    """Persistence used by the engine. Implemented in services/history/store.py."""

    async def save_message(
        self, speaker: str, content: str, status: str, response_id: str | None
    ) -> dict: ...


@dataclass
class EngineConfig:
    language: str = "en"
    voice: str | None = None
    llm_model: str | None = None
    personality: str = ""
    attention_mode: AttentionMode = AttentionMode.ALWAYS
    wake_word: str = "echo"
    num_ctx: int = 2048
    num_predict: int = 160
    partial_interval_ms: int = 1200
    tts_queue_sentences: int = 3
    vad: VadConfig = field(default_factory=VadConfig)


@dataclass
class PlaybackChunk:
    seq: int
    text: str
    duration_ms: float
    played_ms: float = 0.0


@dataclass
class ActiveResponse:
    id: str
    seq: int
    chunks: dict[int, PlaybackChunk] = field(default_factory=dict)
    generated: list[str] = field(default_factory=list)
    generation_done: bool = False
    speech_done: bool = False  # Every generated sentence has been synthesised and sent.
    finalized: bool = False
    metrics: TurnMetrics | None = None
    first_audio_seen: bool = False
    next_audio_seq: int = 0

    def spoken_text(self) -> str:
        parts = []
        for seq in sorted(self.chunks):
            chunk = self.chunks[seq]
            portion = spoken_portion(chunk.text, chunk.played_ms, chunk.duration_ms)
            if portion:
                parts.append(portion)
            if chunk.played_ms < chunk.duration_ms:
                break  # Nothing after a partly played chunk was heard.
        return " ".join(parts)

    def played_seconds(self) -> float:
        return sum(chunk.played_ms for chunk in self.chunks.values()) / 1000

    def all_played(self) -> bool:
        # speech_done, not generation_done: the LLM can finish while sentences still wait for
        # TTS. Treating that as the end cut replies off after their first played sentence.
        return (
            self.speech_done
            and bool(self.chunks)
            and all(chunk.played_ms >= chunk.duration_ms for chunk in self.chunks.values())
        )


SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


class ConversationEngine:
    def __init__(
        self,
        *,
        session_id: str,
        config: EngineConfig,
        vad_model: SpeechModel,
        recognizer,
        chat_model: ChatModel,
        tts,
        store: Store,
        history: list[dict] | None = None,
        clock: SessionClock | None = None,
        executors: "Executors | None" = None,
    ) -> None:
        self.session_id = session_id
        self.config = config
        self.vad_model = vad_model
        self.recognizer = recognizer
        self.chat_model = chat_model
        self.tts = tts
        self.tts_by_language: dict = {}  # Overrides self.tts when text is in another script.
        self.store = store
        self.history: list[dict] = list(history or [])
        self.clock = clock or SessionClock()
        self.executors = executors or Executors()
        self.state = State.CONNECTING

        self._send_json: SendJson | None = None
        self._send_bytes: SendBytes | None = None
        self._segmenter = SpeechSegmenter(config.vad)
        self._assembler = FrameAssembler()
        # 8 s: a stall on a busy machine should delay the user's words, not lose them.
        self._frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=250)
        self._worker: asyncio.Task | None = None
        self._response: ActiveResponse | None = None
        self._response_tasks: list[asyncio.Task] = []
        self._response_seq = 0
        self._utterance_task: asyncio.Task | None = None
        self._user_save: asyncio.Task | None = None
        self._partial_task: asyncio.Task | None = None
        self._last_partial_at = 0.0
        self._speech_ended_at = 0.0
        self._utterance_audio = b""
        self._carry_audio = b""
        self._paused_response_text = ""
        self._last_spoken_text = ""
        # When the client will finish playing what has been sent, and when the user last
        # started talking. Speech is only checked for echo if it overlapped the agent's voice.
        self._speaking_until = 0.0
        self._speech_started_at = 0.0
        self._mic_muted_by_pause = False
        self._paused = False
        self._mic_peak = 0.0
        self._mic_speech = 0.0
        self._mic_frames = 0
        self._stt_seconds = 0.0
        self._wake_until = 0.0
        self.dropped_frames = 0
        self.ended = False
        self.on_end: Callable[[], Awaitable[None]] | None = None

    # ---- lifecycle -----------------------------------------------------------------

    def attach(self, send_json: SendJson, send_bytes: SendBytes) -> None:
        self._send_json = send_json
        self._send_bytes = send_bytes
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._frame_worker(), name=f"mic-{self.session_id}")

    async def detach(self) -> None:
        """Client connection lost: stop speaking and keep the session for reconnection."""
        self._send_json = None
        self._send_bytes = None
        await self._cancel_response(interrupted=True)
        self._segmenter.reset()
        self._assembler.clear()
        self._drain_frames()
        self.clock.pause()
        self._paused = False  # A reconnect starts listening again.
        self._mic_muted_by_pause = False
        self.state = State.DISCONNECTED

    async def shutdown(self) -> None:
        self.ended = True
        await self._cancel_response(interrupted=True)
        current = asyncio.current_task()  # "End conversation" arrives on the utterance task.
        for task in (self._utterance_task, self._partial_task, self._worker):
            if task and task is not current and not task.done():
                task.cancel()
        self.clock.stop()
        self.state = State.ENDED

    async def emit(self, event_type: str, **payload) -> None:
        if event_type in ("agent.interrupted", "transcript.discarded", "agent.response.completed"):
            log.info(
                "%s %s", event_type,
                {k: payload[k] for k in ("reason", "interrupted") if k in payload},
            )
        if self._send_json is None:
            return
        try:
            await self._send_json({"v": PROTOCOL_VERSION, "type": event_type, **payload})
        except Exception:  # A closing socket must not take the engine down.
            log.debug("emit failed for %s", event_type, exc_info=True)

    async def set_state(self, state: State, **extra) -> None:
        # Pausing is the user's decision, so work already in flight must not override it.
        # Only resume() (which clears the flag) or ending the session may leave PAUSED.
        if self._paused and state not in (State.PAUSED, State.ENDED, State.DISCONNECTED):
            return
        self.state = state
        self._segmenter.guarded = state == State.SPEAKING
        await self.emit("session.state", state=state.value, **extra)

    async def start_listening(self) -> None:
        self.clock.resume()
        await self.set_state(State.LISTENING)

    # ---- microphone ----------------------------------------------------------------

    def feed_audio(self, data: bytes) -> None:
        """Called for every mic packet. Never blocks: the oldest audio is dropped under load."""
        if self.state == State.PAUSED and self._mic_muted_by_pause:
            return
        for frame in self._assembler.push(data):
            if self._frames.full():
                try:
                    self._frames.get_nowait()
                    self.dropped_frames += 1
                except asyncio.QueueEmpty:
                    pass
            self._frames.put_nowait(frame)

    def _drain_frames(self) -> None:
        while not self._frames.empty():
            self._frames.get_nowait()

    async def _frame_worker(self) -> None:
        loop = asyncio.get_running_loop()
        finished_at = time.perf_counter()
        previous_batch_ms = 0.0
        while True:
            batch = [await self._frames.get()]
            idle = time.perf_counter() - finished_at
            # Everything queued goes in one executor call. A thread hop per frame is what
            # lets the VAD fall behind real time when the CPU is busy with the LLM and TTS,
            # and barge-in cannot work if the VAD is late.
            while not self._frames.empty():
                batch.append(self._frames.get_nowait())
            if len(batch) > LAG_WARNING_FRAMES:
                # A long previous batch means this server fell behind. A short one means the
                # frames arrived late and all together: the client or the network stalled.
                log.warning(
                    "%d ms of audio queued (%d frames dropped so far). Previous batch took %d ms; "
                    "waited %d ms for this one.",
                    len(batch) * 32, self.dropped_frames, previous_batch_ms, idle * 1000,
                )
            started = time.perf_counter()
            probabilities: list[float] = []
            try:
                probabilities = await loop.run_in_executor(self.executors.vad, self._score_frames, batch)
                for frame, probability in zip(batch, probabilities):
                    await self._handle_frame(frame, probability)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error processing a microphone frame")
            finished_at = time.perf_counter()
            previous_batch_ms = (finished_at - started) * 1000
            await self._report_mic(batch, probabilities)

    async def _report_mic(self, frames: list[bytes], probabilities: list[float]) -> None:
        """Every few seconds, report how loud the mic is and how speech-like it sounds.

        These are two numbers only; no audio is kept. They separate the causes of "it does
        not hear me": a silent device, sound that is not speech, or speech it did detect.
        """
        for frame in frames:
            self._mic_peak = max(self._mic_peak, float(np.abs(pcm16_to_float(frame)).max()))
        if probabilities:
            self._mic_speech = max(self._mic_speech, max(probabilities))
        self._mic_frames += len(frames)
        if self._mic_frames < MIC_REPORT_FRAMES:
            return
        peak, speech = self._mic_peak, self._mic_speech
        self._mic_peak = self._mic_speech = 0.0
        self._mic_frames = 0
        log.info(
            "Mic: peak level %.4f, highest speech probability %.2f (needs %.2f), state %s",
            peak, speech, self.config.vad.threshold, self.state.value,
        )
        seg = self._segmenter
        if seg.guarded_peak > 0:
            vad = self.config.vad
            log.info(
                "Barge-in check while speaking: highest probability %.2f (needs %.2f), "
                "longest run %d ms (needs %d), in speech %s, response active %s",
                seg.guarded_peak, vad.interrupt_threshold, seg.guarded_best_run_ms,
                vad.interrupt_min_speech_ms, seg.in_speech, self._response is not None,
            )
            seg.guarded_peak = seg.guarded_best_run_ms = 0.0
        await self.emit("mic.stats", peak=round(peak, 4), speech=round(speech, 3), threshold=self.config.vad.threshold)

    def _score_frames(self, frames: list[bytes]) -> list[float]:
        return [self.vad_model.probability(pcm16_to_float(frame)) for frame in frames]

    async def _handle_frame(self, frame: bytes, probability: float) -> None:
        event = self._segmenter.process(frame, probability)
        if event == VadEvent.SPEECH_STARTED:
            await self._on_speech_started()
        elif event == VadEvent.SPEECH_ENDED:
            await self._on_speech_ended()
        elif self._segmenter.in_speech:
            self._maybe_partial()

    async def _on_speech_started(self) -> None:
        await self.emit("speech.started")
        started = time.perf_counter()
        self._last_partial_at = started
        self._speech_started_at = started
        log.info("Speech started in state %s, response active %s", self.state.value, self._response is not None)
        if self.state == State.PAUSED:
            return
        if self._response is not None:
            await self._interrupt(started)
        elif self._utterance_task and not self._utterance_task.done():
            # The user carried on after a pause while the last part was still being
            # transcribed. Keep that audio so the two parts become one utterance.
            self._utterance_task.cancel()
            self._carry_audio = self._utterance_audio
            await self.set_state(State.LISTENING)

    async def _interrupt(self, started: float) -> None:
        response = self._response
        response_id = response.id if response else None
        seq = response.seq if response else self._response_seq
        await self.set_state(State.INTERRUPTED)
        # Tell the client first: this is what silences the speaker.
        await self.emit("agent.interrupted", response_id=response_id, response_seq=seq)
        await self._cancel_response(interrupted=True)
        latency_ms = (time.perf_counter() - started) * 1000
        self.clock.record_interruption(latency_ms)
        await self.set_state(State.LISTENING)
        await self._emit_metrics()

    async def _on_speech_ended(self) -> None:
        audio = self._segmenter.finished_utterance or b""
        self._segmenter.finished_utterance = None
        self._speech_ended_at = time.perf_counter()
        self.clock.add_user_speech(len(audio) / 2 / 16000)
        await self.emit("speech.ended", duration_ms=round(len(audio) / 32))
        audio, self._carry_audio = self._carry_audio + audio, b""
        if len(audio) / 2 / 16000 < MIN_UTTERANCE_SECONDS:
            return
        if self._utterance_task and not self._utterance_task.done():
            self._utterance_task.cancel()
        self._utterance_audio = audio
        self._utterance_task = asyncio.create_task(self._process_utterance(audio, self._speech_ended_at))

    def _maybe_partial(self) -> None:
        interval = self.config.partial_interval_ms / 1000
        now = time.perf_counter()
        if now - self._last_partial_at < interval:
            return
        # A live transcript is a nicety. While a reply is being generated the CPU is needed
        # for that, and a partial would also hold up the final transcription queued behind it.
        if self._response is not None or (self._utterance_task and not self._utterance_task.done()):
            return
        # When the PC is struggling, a partial only delays the final transcript queued behind it.
        if self._stt_seconds > PARTIAL_MAX_STT_SECONDS or now - self._last_partial_at < self._stt_seconds * 2:
            return
        if self._partial_task and not self._partial_task.done():
            return  # Whisper is still busy: skip, do not queue.
        if not getattr(self.recognizer, "partials", True):
            return  # The online engine only transcribes finished utterances.
        self._last_partial_at = now
        self._partial_task = asyncio.create_task(self._partial(self._segmenter.current_audio()))

    async def _partial(self, audio: bytes) -> None:
        loop = asyncio.get_running_loop()
        samples = pcm16_to_float(audio)
        try:
            transcript = await loop.run_in_executor(
                self.executors.stt, self.recognizer.transcribe, samples, self._stt_language(), False, ""
            )
        except Exception:
            log.exception("Partial transcription failed")
            return
        if not self._segmenter.in_speech or not transcript.text:
            return
        self._segmenter.extend_silence(silence_multiplier(transcript.text))
        await self.emit("transcript.partial", text=transcript.text)

    def _stt_language(self) -> str | None:
        return "en" if self.config.language == "en" else (None if self.config.language == "auto" else self.config.language)

    # ---- a finished utterance --------------------------------------------------------

    async def _process_utterance(self, audio: bytes, ended_at: float) -> None:
        loop = asyncio.get_running_loop()
        if self.state == State.LISTENING:
            await self.set_state(State.PROCESSING)
        samples = pcm16_to_float(audio)
        language = self._stt_language()
        short = len(samples) / 16000 <= COMMAND_HINT_MAX_SECONDS and language == "en"
        started = time.perf_counter()
        try:
            transcript = await loop.run_in_executor(
                self.executors.stt, self.recognizer.transcribe, samples, language, True, COMMAND_HINT if short else ""
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Transcription failed")
            await self.emit("error", code="stt_failed", message=str(exc), recoverable=True)
            await self._return_to_rest()
            return
        stt_ms = (time.perf_counter() - started) * 1000
        self._stt_seconds = stt_ms / 1000
        text = transcript.text.strip()

        if not text:
            await self.emit("transcript.discarded", reason="no_speech")
            await self._return_to_rest()
            return

        # Echo of our own voice: discard it rather than answering ourselves.
        overlapped = self._speech_started_at <= self._speaking_until + ECHO_TAIL_SECONDS
        if overlapped and self._last_spoken_text and is_echo(text, self._last_spoken_text):
            await self.emit("transcript.discarded", reason="echo", text=text)
            await self._return_to_rest()
            return

        await self._act_on_transcript(text, transcript.language, stt_ms, ended_at)

    async def _return_to_rest(self) -> None:
        if self.state == State.PAUSED:
            return
        if self.state not in (State.SPEAKING,):
            await self.set_state(State.LISTENING)

    async def _act_on_transcript(self, text: str, language: str, stt_ms: float, ended_at: float) -> None:
        command = parse_command(text)

        if self.state == State.PAUSED:
            # While paused, the only thing that gets through is a request to come back.
            if command == Command.RESUME:
                await self.resume()
            elif command == Command.END:
                await self._end_from_voice()
            else:
                await self.emit("transcript.discarded", reason="paused")
            return

        if self.config.attention_mode == AttentionMode.WAKE_WORD:
            heard, remainder = strip_wake_word(text, self.config.wake_word)
            now = time.monotonic()
            if heard:
                self._wake_until = now + WAKE_WINDOW_SECONDS
                text = remainder
                await self.emit("attention.awake", until_seconds=WAKE_WINDOW_SECONDS)
                if not text:
                    await self.set_state(State.LISTENING)
                    return
                command = parse_command(text)
            elif now > self._wake_until:
                await self.emit("transcript.discarded", reason="no_wake_word", text=text)
                await self.set_state(State.LISTENING)
                return
            else:
                self._wake_until = now + WAKE_WINDOW_SECONDS

        if command is not None:
            await self.emit("transcript.final", text=text, command=command.value, stt_ms=round(stt_ms))
            await self._run_command(command)
            return

        self.history.append({"role": "user", "content": text})
        # The database can be a slow network hop away (Supabase: 0.5 to 4 s measured), so the
        # reply does not wait for it. The transcript event follows once the row exists.
        self._user_save = asyncio.create_task(self._save_user_turn(text, language, stt_ms, self._user_save))
        metrics = TurnMetrics(speech_end=ended_at, stt_ms=stt_ms)
        await self._start_response(metrics)

    async def _save_user_turn(
        self, text: str, language: str, stt_ms: float, previous: asyncio.Task | None
    ) -> None:
        await _finished(previous)  # Two quick turns must keep their order in the transcript.
        message = None
        try:
            message = await self.store.save_message("user", text, "completed", None)
        except Exception:
            log.exception("Could not save the user's message")
        await self.emit("transcript.final", text=text, message=message, stt_ms=round(stt_ms), language=language)

    async def _run_command(self, command: Command) -> None:
        if command == Command.PAUSE:
            await self.pause(listen_for_resume=True)
        elif command == Command.STOP:
            # "Stop" ends the current speech. With nothing to stop, it behaves like pause.
            if self._response is not None:
                await self.emit(
                    "agent.interrupted", response_id=self._response.id, response_seq=self._response.seq
                )
                await self._cancel_response(interrupted=True)
                await self.set_state(State.LISTENING)
            else:
                await self.pause(listen_for_resume=True)
        elif command == Command.RESUME:
            if self.state == State.PAUSED:
                await self.resume()
            else:
                await self.set_state(State.LISTENING)
        elif command == Command.REPEAT:
            await self._repeat_last()
        elif command == Command.END:
            await self._end_from_voice()

    async def _end_from_voice(self) -> None:
        await self.emit("session.end_requested")
        if self.on_end:
            await self.on_end()

    # ---- pause and resume ------------------------------------------------------------

    async def pause(self, listen_for_resume: bool = False) -> None:
        """Pause the session.

        A voice pause keeps transcribing so "continue" can be heard (nothing is saved).
        A button pause (`listen_for_resume=False`) stops processing microphone audio.
        """
        if self._paused:
            self._mic_muted_by_pause = not listen_for_resume
            return
        self._paused = True
        current = asyncio.current_task()
        if self._utterance_task and self._utterance_task is not current and not self._utterance_task.done():
            self._utterance_task.cancel()  # Speech from before the pause gets no reply.
        self._carry_audio = b""
        interrupted_text = ""
        if self._response is not None:
            await self.emit("agent.interrupted", response_id=self._response.id, response_seq=self._response.seq)
            generated = "".join(self._response.generated)
            await self._cancel_response(interrupted=True)
            interrupted_text = generated
        self._paused_response_text = interrupted_text
        self._mic_muted_by_pause = not listen_for_resume
        self._segmenter.reset()
        self._assembler.clear()
        self._drain_frames()
        self.clock.pause()
        await self.set_state(State.PAUSED, listening_for_resume=listen_for_resume)
        await self.emit("session.paused", listening_for_resume=listen_for_resume)

    async def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        self._mic_muted_by_pause = False
        self._segmenter.reset()
        self.clock.resume()
        await self.emit("session.resumed")
        await self.set_state(State.LISTENING)
        # Exact audio cannot be resumed mid-sentence, so the model continues in new words.
        if self._paused_response_text:
            self._paused_response_text = ""
            await self._start_response(TurnMetrics(speech_end=time.perf_counter(), stt_ms=0.0), continuation=True)

    async def _repeat_last(self) -> None:
        last = next((m["content"] for m in reversed(self.history) if m["role"] == "assistant"), "")
        if not last:
            await self.set_state(State.LISTENING)
            return
        await self._start_response(TurnMetrics(speech_end=time.perf_counter(), stt_ms=0.0), fixed_text=last)

    # ---- generating and speaking -----------------------------------------------------

    async def _start_response(
        self, metrics: TurnMetrics, continuation: bool = False, fixed_text: str | None = None
    ) -> None:
        await self._cancel_response(interrupted=True)
        if self._paused:
            return
        self._response_seq += 1
        response = ActiveResponse(id=str(uuid.uuid4()), seq=self._response_seq, metrics=metrics)
        self._response = response
        await self.set_state(State.PROCESSING)
        await self.emit("agent.response.started", response_id=response.id, response_seq=response.seq)

        sentences: asyncio.Queue[str | None] = asyncio.Queue(maxsize=self.config.tts_queue_sentences)
        self._response_tasks = [
            asyncio.create_task(self._generate(response, sentences, continuation, fixed_text)),
            asyncio.create_task(self._speak(response, sentences)),
        ]

    async def _generate(
        self,
        response: ActiveResponse,
        sentences: asyncio.Queue[str | None],
        continuation: bool,
        fixed_text: str | None,
    ) -> None:
        cfg = self.config
        chunker = SentenceChunker()
        try:
            if fixed_text is not None:
                await self.emit("agent.text.delta", response_id=response.id, text=fixed_text)
                response.generated.append(fixed_text)
                for piece in chunker.push(fixed_text):
                    await sentences.put(piece)
            else:
                history = self.history
                if continuation:
                    history = [
                        *history,
                        {"role": "user", "content": "Please continue what you were saying, without starting over."},
                    ]
                messages = build_messages(
                    history, cfg.personality, cfg.language, cfg.num_ctx, cfg.num_predict
                )
                started = time.perf_counter()
                async for token in self.chat_model.stream(messages, cfg.llm_model):
                    if response.metrics and response.metrics.llm_first_token_ms is None:
                        response.metrics.llm_first_token_ms = (time.perf_counter() - started) * 1000
                    response.generated.append(token)
                    await self.emit("agent.text.delta", response_id=response.id, text=token)
                    for piece in chunker.push(token):
                        await sentences.put(piece)
            if last := chunker.flush():
                await sentences.put(last)
        except asyncio.CancelledError:
            raise
        except LLMError as exc:
            log.warning("LLM failed: %s", exc)
            await self.emit("error", code="llm_failed", message=str(exc), recoverable=True)
        except Exception as exc:
            log.exception("Response generation failed")
            await self.emit("error", code="llm_failed", message=str(exc), recoverable=True)
        finally:
            response.generation_done = True
            # Must not block during cancellation, when the speaker may be gone.
            try:
                sentences.put_nowait(None)
            except asyncio.QueueFull:
                if not asyncio.current_task().cancelling():  # type: ignore[union-attr]
                    await sentences.put(None)

    async def _speak(self, response: ActiveResponse, sentences: asyncio.Queue[str | None]) -> None:
        cfg = self.config
        try:
            while (sentence := await sentences.get()) is not None:
                await self._synthesize_sentence(response, sentence, cfg.voice)
            response.speech_done = True
            await self._check_response_complete(response)
            if not response.chunks and not response.finalized:
                # Nothing could be spoken (model produced no text, or failed).
                await self._finalize_response(response, interrupted=False)
                await self.set_state(State.LISTENING)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Speech synthesis failed")
            await self.emit("error", code="tts_failed", message=str(exc), recoverable=True)
            await self._finalize_response(response, interrupted=False)
            await self.set_state(State.LISTENING)

    async def _synthesize_sentence(self, response: ActiveResponse, sentence: str, voice: str | None) -> None:
        loop = asyncio.get_running_loop()
        stop = object()
        language = speech_language(sentence)
        tts = self.tts_by_language.get(language, self.tts)
        iterator = await loop.run_in_executor(
            self.executors.tts, lambda: iter(tts.synthesize(sentence, voice, language))
        )
        arrays: list[np.ndarray] = []
        while True:
            block = await loop.run_in_executor(self.executors.tts, next, iterator, stop)
            if block is stop:
                break
            arrays.append(block)
        if not arrays or self._response is not response:
            return
        samples = np.concatenate(arrays)
        rate = tts.sample_rate
        duration_ms = len(samples) / rate * 1000
        chunk = PlaybackChunk(seq=response.next_audio_seq, text=sentence, duration_ms=duration_ms)
        response.next_audio_seq += 1
        response.chunks[chunk.seq] = chunk
        self._last_spoken_text = f"{self._last_spoken_text} {sentence}"[-1200:]
        self._speaking_until = max(self._speaking_until, time.perf_counter()) + duration_ms / 1000

        if response.metrics and response.metrics.first_audio_ms is None:
            response.metrics.first_audio_ms = (time.perf_counter() - response.metrics.speech_end) * 1000
            log.info(
                "Turn timing (ms): speech to text %s, first word from model %s, first audio %s (%s voice)",
                _round(response.metrics.stt_ms), _round(response.metrics.llm_first_token_ms),
                _round(response.metrics.first_audio_ms), tts.name,
            )
            await self.emit(
                "metrics.turn",
                response_id=response.id,
                stt_ms=_round(response.metrics.stt_ms),
                llm_first_token_ms=_round(response.metrics.llm_first_token_ms),
                first_audio_ms=_round(response.metrics.first_audio_ms),
            )
        if self.state != State.SPEAKING:
            await self.set_state(State.SPEAKING, response_id=response.id)

        await self.emit(
            "agent.audio.chunk",
            response_id=response.id,
            response_seq=response.seq,
            chunk_seq=chunk.seq,
            text=sentence,
            duration_ms=round(duration_ms, 1),
            sample_rate=rate,
        )
        if self._send_bytes is not None:
            try:
                await self._send_bytes(pack_audio(response.seq, chunk.seq, rate, float_to_pcm16(samples)))
            except Exception:
                log.debug("audio send failed", exc_info=True)

    def on_playback(self, response_id: str, chunk_seq: int, played_ms: float) -> None:
        """The client reports how much of a chunk has actually come out of the speaker."""
        response = self._response
        if response is None or response.id != response_id:
            return
        chunk = response.chunks.get(chunk_seq)
        if chunk is None:
            return
        chunk.played_ms = max(chunk.played_ms, min(played_ms, chunk.duration_ms))
        if response.all_played():
            asyncio.create_task(self._check_response_complete(response))

    async def _check_response_complete(self, response: ActiveResponse) -> None:
        if response.all_played() and not response.finalized and self._response is response:
            await self._finalize_response(response, interrupted=False)
            await self.set_state(State.LISTENING)

    async def _finalize_response(self, response: ActiveResponse, interrupted: bool) -> None:
        if response.finalized:
            return
        response.finalized = True
        if self._response is response:
            self._response = None
        spoken = response.spoken_text().strip()
        self.clock.add_ai_speech(response.played_seconds())
        unplayed_exists = interrupted or not response.all_played()
        message = None
        if spoken:
            status = "interrupted" if unplayed_exists else "completed"
            await _finished(self._user_save)  # The question must be numbered before its answer.
            message = await self.store.save_message("assistant", spoken, status, response.id)
            self.history.append({"role": "assistant", "content": spoken, "interrupted": unplayed_exists})
        if response.metrics:
            self.clock.record_turn(response.metrics, interrupted=interrupted)
        await self.emit(
            "agent.response.completed",
            response_id=response.id,
            interrupted=unplayed_exists,
            spoken_text=spoken,
            message=message,
        )
        await self._emit_metrics()

    async def _cancel_response(self, interrupted: bool) -> None:
        tasks, self._response_tasks = self._response_tasks, []
        current = asyncio.current_task()
        for task in tasks:
            if task is not current and not task.done():
                task.cancel()
        for task in tasks:
            if task is not current:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self._response is not None:
            await self._finalize_response(self._response, interrupted=interrupted)

    async def _emit_metrics(self) -> None:
        await self.emit("metrics.updated", metrics=self.clock.snapshot())

    # ---- settings and helpers --------------------------------------------------------

    def update_config(self, **changes) -> None:
        for key, value in changes.items():
            if hasattr(self.config, key) and value is not None:
                setattr(self.config, key, value)


def raise_thread_priority() -> None:
    """Favour the calling thread on Windows, so that hearing the user never waits on inference.

    Only the VAD thread and event loop do this. Both are light. Raising the whole process was
    tried and measured to backfire: Whisper and TTS then starved the browser's own audio
    capture on a 2-core PC, and microphone frames arrived seconds late in bursts.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        thread = ctypes.windll.kernel32.GetCurrentThread()
        ctypes.windll.kernel32.SetThreadPriority(thread, 1)  # THREAD_PRIORITY_ABOVE_NORMAL
    except Exception as exc:  # A scheduling nicety must never stop a session.
        log.debug("Could not raise thread priority: %s", exc)


class Executors:
    """Single-worker pools keep inference off the event loop and bound CPU contention."""

    def __init__(self) -> None:
        self.vad = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vad", initializer=raise_thread_priority)
        self.stt = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        self.tts = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")


async def _finished(task: asyncio.Task | None) -> None:
    """Wait for a task to end, without taking on its exception or cancelling it."""
    if task is not None and not task.done():
        await asyncio.wait([task])


def _round(value: float | None) -> int | None:
    return None if value is None else round(value)
