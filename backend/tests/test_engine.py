"""Conversation engine behaviour: turns, interruption, pause/resume, played-text accuracy."""
from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest

from app.services.audio.pcm import VAD_FRAME_SAMPLES, float_to_pcm16, unpack_audio
from app.services.conversation.engine import (
    AttentionMode,
    ConversationEngine,
    EngineConfig,
    State,
)
from app.services.vad.silero import VadConfig

from .conftest import MemoryStore, ScriptedChat, ScriptedRecognizer, ScriptedTTS, ScriptedVAD

LOUD = float_to_pcm16(np.full(VAD_FRAME_SAMPLES, 0.5, dtype=np.float32))
QUIET = float_to_pcm16(np.zeros(VAD_FRAME_SAMPLES, dtype=np.float32))
SPEECH_FRAMES = 20  # 640 ms
SILENCE_FRAMES = 12  # 384 ms


class Harness:
    def __init__(self, config: EngineConfig | None = None, history=None) -> None:
        self.events: list[dict] = []
        self.audio: list[bytes] = []
        self.stt = ScriptedRecognizer()
        self.chat = ScriptedChat()
        self.tts = ScriptedTTS()
        self.store = MemoryStore()
        vad = VadConfig(silence_ms=300, min_speech_ms=64, interrupt_min_speech_ms=96, padding_ms=64)
        self.engine = ConversationEngine(
            session_id="s1",
            config=config or EngineConfig(vad=vad, partial_interval_ms=100000),
            vad_model=ScriptedVAD(),
            recognizer=self.stt,
            chat_model=self.chat,
            tts=self.tts,
            store=self.store,
            history=history,
        )
        self.engine.attach(self._json, self._bytes)

    async def _json(self, payload):
        self.events.append(payload)

    async def _bytes(self, payload):
        self.audio.append(payload)

    def of_type(self, kind):
        return [e for e in self.events if e["type"] == kind]

    async def start(self):
        await self.engine.start_listening()

    async def speak(self, frames=SPEECH_FRAMES, silence=SILENCE_FRAMES):
        for _ in range(frames):
            self.engine.feed_audio(LOUD)
        for _ in range(silence):
            self.engine.feed_audio(QUIET)
        await wait_for(self.engine._frames.empty)
        await settle()

    def ack_all(self, played_fraction=1.0):
        """Pretend the speaker played each chunk received so far."""
        for event in self.of_type("agent.audio.chunk"):
            self.engine.on_playback(
                event["response_id"], event["chunk_seq"], event["duration_ms"] * played_fraction
            )

    async def close(self):
        await self.engine.shutdown()


async def settle(seconds: float = 0.15):
    """Let frame processing and executor work finish."""
    await asyncio.sleep(seconds)


async def wait_for(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


@pytest.fixture
async def h():
    harness = Harness()
    await harness.start()
    yield harness
    await harness.close()


async def test_full_turn_streams_text_and_audio_then_saves_played_speech(h):
    await h.speak()
    await wait_for(lambda: h.of_type("agent.audio.chunk") and h.engine._response and h.engine._response.generation_done)
    assert h.of_type("speech.started")
    assert h.of_type("speech.ended")
    assert h.of_type("transcript.final")[0]["text"] == "hello there"
    assert h.of_type("agent.text.delta")
    assert h.audio, "binary audio must have been sent"
    response_seq, chunk_seq, rate, pcm = unpack_audio(h.audio[0])
    assert (response_seq, chunk_seq, rate) == (1, 0, 16000) and pcm
    assert h.engine.state == State.SPEAKING

    # Nothing is saved as spoken until the client confirms playback.
    assert [m["speaker"] for m in h.store.messages] == ["user"]
    h.ack_all()
    await wait_for(lambda: h.engine.state == State.LISTENING)
    assert h.store.messages[1]["speaker"] == "assistant"
    assert h.store.messages[1]["status"] == "completed"
    assert h.store.messages[1]["content"] == "Sure thing. Here is an answer for you."
    turn = h.engine.clock.turns[0]
    assert turn["first_audio_ms"] is not None and turn["llm_first_token_ms"] is not None


async def test_interruption_stops_response_and_saves_only_played_text(h):
    h.chat.reply = "One two three four five six seven eight. Nine ten eleven twelve. Thirteen fourteen fifteen."
    await h.speak()
    await wait_for(lambda: h.of_type("agent.audio.chunk"))
    first = h.of_type("agent.audio.chunk")[0]
    h.engine.on_playback(first["response_id"], 0, first["duration_ms"] * 0.5)

    h.chat.reply = "A new answer."
    h.stt.queue = ["wait explain it simply"]
    await h.speak()
    await wait_for(lambda: len(h.of_type("agent.response.started")) == 2)

    interrupted = h.of_type("agent.interrupted")
    assert len(interrupted) == 1 and interrupted[0]["response_seq"] == 1
    saved = next(m for m in h.store.messages if m["speaker"] == "assistant")
    assert saved["status"] == "interrupted"
    assert saved["content"] == "One two three four"  # half of the 8-word first chunk, nothing after it
    assert h.engine.clock.interruption_count == 1
    assert h.of_type("agent.response.started")[1]["response_seq"] == 2
    # The new prompt carries the interrupted context plus the new question.
    last_prompt = h.chat.prompts[-1]
    assert last_prompt[-1]["content"] == "wait explain it simply"
    assert "One two three four" in last_prompt[-2]["content"]


async def test_repeated_interruptions_never_leave_a_stale_active_response(h):
    h.chat.reply = "This is a long reply. It has several sentences. And it keeps going for a while."
    for attempt in range(4):
        h.stt.queue = [f"question number {attempt}"]
        await h.speak()
        await wait_for(lambda: len(h.of_type("agent.response.started")) == attempt + 1)
        await wait_for(lambda: h.of_type("agent.audio.chunk"))
    assert h.engine.clock.interruption_count == 3
    seqs = [e["response_seq"] for e in h.of_type("agent.response.started")]
    assert seqs == sorted(set(seqs))
    assert all(not task.done() or task.cancelled() or task.exception() is None for task in h.engine._response_tasks)


async def test_interrupt_with_nothing_played_saves_no_assistant_message(h):
    h.chat.reply = "Never heard by anyone. At all."
    await h.speak()
    await wait_for(lambda: h.of_type("agent.audio.chunk"))
    h.stt.queue = ["actually never mind"]
    await h.speak()
    await wait_for(lambda: len(h.of_type("agent.response.started")) == 2)
    assert all(m["content"] != "Never heard by anyone." for m in h.store.messages)
    assert sum(m["speaker"] == "assistant" for m in h.store.messages) == 0


async def test_interruption_cancels_llm_generation(h):
    h.chat.gate = asyncio.Event()
    await h.speak()
    await wait_for(lambda: h.engine._response is not None)
    h.stt.queue = ["something else"]
    await h.speak()
    h.chat.gate.set()
    await wait_for(lambda: h.chat.cancelled >= 1)


async def test_echo_of_own_voice_is_discarded(h):
    h.chat.reply = "The capital of France is Paris and it is lovely."
    await h.speak()
    await wait_for(lambda: h.of_type("agent.audio.chunk"))
    await wait_for(lambda: h.engine._response and h.engine._response.generation_done)
    h.ack_all()
    await wait_for(lambda: h.engine.state == State.LISTENING)
    before = len(h.of_type("agent.response.started"))
    h.stt.queue = ["the capital of France is Paris"]
    await h.speak()
    await wait_for(lambda: h.of_type("transcript.discarded"))
    assert h.of_type("transcript.discarded")[0]["reason"] == "echo"
    assert len(h.of_type("agent.response.started")) == before


async def test_silence_alone_never_ends_the_session(h):
    for _ in range(100):
        h.engine.feed_audio(QUIET)
    await settle()
    assert h.engine.state == State.LISTENING
    assert not h.of_type("speech.started")
    assert not h.engine.ended


async def test_pause_mid_sentence_keeps_audio_as_one_utterance(h):
    h.stt.default = "hello there"
    # Hold transcription open so the user carries on before it finishes.
    release = threading.Event()
    original = h.stt.transcribe

    def slow(samples, language, final, hint=""):
        release.wait(15)
        return original(samples, language, final, hint)

    h.stt.transcribe = slow
    await h.speak()
    await h.speak()
    release.set()
    await wait_for(lambda: h.of_type("transcript.final"))
    assert len(h.of_type("transcript.final")) == 1
    # The final transcription saw both parts of the sentence.
    longest = max(length for length, final in h.stt.calls if final)
    assert longest >= 2 * SPEECH_FRAMES * VAD_FRAME_SAMPLES


async def test_voice_pause_ignores_speech_then_resumes_on_command(h):
    h.stt.queue = ["pause"]
    await h.speak()
    await wait_for(lambda: h.engine.state == State.PAUSED)
    responses = len(h.of_type("agent.response.started"))

    h.stt.queue = ["this should be ignored"]
    await h.speak()
    await wait_for(lambda: h.of_type("transcript.discarded"))
    assert h.of_type("transcript.discarded")[-1]["reason"] == "paused"
    assert len(h.of_type("agent.response.started")) == responses

    h.stt.queue = ["I'm back."]
    await h.speak()
    await wait_for(lambda: h.engine.state == State.LISTENING)
    assert h.of_type("session.resumed")


async def test_pause_wins_over_a_transcript_still_being_saved(h):
    """Found in real Chrome: a save in flight used to overwrite PAUSED with PROCESSING."""
    saving = asyncio.Event()
    release = asyncio.Event()
    original = h.store.save_message

    async def slow_save(*args):
        saving.set()
        await release.wait()
        return await original(*args)

    h.store.save_message = slow_save
    await h.speak()
    await wait_for(saving.is_set)
    await h.engine.pause()
    release.set()
    await settle()
    assert h.engine.state == State.PAUSED
    assert not h.of_type("agent.response.started"), "no reply may start while paused"
    await h.engine.resume()
    assert h.engine.state == State.LISTENING


async def test_pause_during_interruption_stays_paused(h):
    h.chat.reply = "A long explanation begins here. It keeps on going. And on."
    await h.speak()
    await wait_for(lambda: h.of_type("agent.audio.chunk"))
    await asyncio.gather(h.engine._interrupt(0.0), h.engine.pause())
    await settle()
    assert h.engine.state == State.PAUSED


async def test_button_pause_stops_processing_microphone(h):
    await h.engine.pause(listen_for_resume=False)
    await h.speak()
    assert not h.of_type("speech.started")
    await h.engine.resume()
    assert h.engine.state == State.LISTENING
    await h.speak()
    await wait_for(lambda: h.of_type("speech.started"))


async def test_pause_while_speaking_then_resume_generates_continuation(h):
    h.chat.reply = "A long explanation begins here. It keeps on going. And on."
    await h.speak()
    await wait_for(lambda: h.of_type("agent.audio.chunk"))
    await h.engine.pause()
    assert h.of_type("agent.interrupted")
    h.chat.reply = "Picking up from there."
    await h.engine.resume()
    await wait_for(lambda: len(h.chat.prompts) == 2)
    assert "continue" in h.chat.prompts[-1][-1]["content"].lower()


async def test_repeat_that_speaks_last_reply_without_calling_llm(h):
    h.chat.reply = "Remember this sentence."
    await h.speak()
    await wait_for(lambda: h.engine._response and h.engine._response.generation_done and h.of_type("agent.audio.chunk"))
    h.ack_all()
    await wait_for(lambda: h.engine.state == State.LISTENING)
    calls = len(h.chat.prompts)
    h.stt.queue = ["repeat that"]
    await h.speak()
    await wait_for(lambda: len(h.of_type("agent.response.started")) == 2)
    await wait_for(lambda: len(h.of_type("agent.audio.chunk")) == 2)
    assert len(h.chat.prompts) == calls
    assert h.tts.spoken[-1] == "Remember this sentence."


async def test_end_conversation_command_calls_on_end_and_can_shut_down_itself(h):
    ended = asyncio.Event()

    async def on_end():
        await h.engine.shutdown()  # As the websocket layer does, from the utterance task.
        await asyncio.sleep(0)  # Must not raise CancelledError here.
        ended.set()

    h.engine.on_end = on_end
    h.stt.queue = ["End conversation."]
    await h.speak()
    await wait_for(ended.is_set)
    assert h.engine.state == State.ENDED


async def test_wake_word_mode_only_responds_after_activation():
    vad = VadConfig(silence_ms=300, min_speech_ms=64, padding_ms=64)
    h = Harness(EngineConfig(vad=vad, attention_mode=AttentionMode.WAKE_WORD, partial_interval_ms=100000))
    await h.start()
    h.stt.queue = ["what time is it"]
    await h.speak()
    await wait_for(lambda: h.of_type("transcript.discarded"))
    assert h.of_type("transcript.discarded")[0]["reason"] == "no_wake_word"
    assert not h.of_type("agent.response.started")

    h.stt.queue = ["Hey Echo, what time is it"]
    await h.speak()
    await wait_for(lambda: h.of_type("agent.response.started"))
    assert h.of_type("transcript.final")[0]["text"] == "what time is it"
    await h.close()


async def test_llm_failure_reports_error_and_recovers_to_listening(h):
    from app.services.llm.ollama import LLMError

    h.chat.error = LLMError("model failed to load")
    await h.speak()
    await wait_for(lambda: h.of_type("error"))
    await wait_for(lambda: h.engine.state == State.LISTENING)
    assert h.of_type("error")[0]["code"] == "llm_failed"
    assert h.of_type("error")[0]["recoverable"] is True


async def test_tts_failure_reports_error_and_recovers(h):
    def broken(*_a, **_k):
        raise RuntimeError("synth exploded")
        yield  # pragma: no cover

    h.tts.synthesize = broken
    await h.speak()
    await wait_for(lambda: any(e.get("code") == "tts_failed" for e in h.of_type("error")))
    await wait_for(lambda: h.engine.state == State.LISTENING)


async def test_overloaded_microphone_queue_drops_oldest_without_blocking(h):
    for _ in range(5000):
        h.engine.feed_audio(QUIET)
    assert h.engine._frames.qsize() <= 250
    assert h.engine.dropped_frames > 0


async def test_disconnect_finalizes_played_text_and_session_can_reattach(h):
    h.chat.reply = "One two three four. Five six seven eight."
    await h.speak()
    await wait_for(lambda: h.of_type("agent.audio.chunk"))
    first = h.of_type("agent.audio.chunk")[0]
    h.engine.on_playback(first["response_id"], 0, first["duration_ms"])
    await h.engine.detach()
    assert h.engine.state == State.DISCONNECTED
    saved = [m for m in h.store.messages if m["speaker"] == "assistant"]
    assert saved[0]["content"] == "One two three four." and saved[0]["status"] == "interrupted"

    h.engine.attach(h._json, h._bytes)
    await h.engine.start_listening()
    assert h.engine.state == State.LISTENING
    h.chat.reply = "We are back."
    h.stt.queue = ["are you still there"]
    await h.speak()
    await wait_for(lambda: len(h.of_type("agent.response.started")) == 2)
    assert h.chat.prompts[-1][-2]["content"] == "One two three four."


async def test_previous_history_seeds_context_when_continuing_a_conversation():
    history = [{"role": "user", "content": "My name is Sam."}, {"role": "assistant", "content": "Nice to meet you."}]
    h = Harness(history=history)
    await h.start()
    await h.speak()
    await wait_for(lambda: h.chat.prompts)
    assert h.chat.prompts[0][1]["content"] == "My name is Sam."
    await h.close()
