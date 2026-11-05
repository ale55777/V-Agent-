"""Pure logic: VAD segmentation, framing, chunking, context trimming, commands, timing."""
from __future__ import annotations

import numpy as np

from app.services.audio.pcm import FrameAssembler, VAD_FRAME_SAMPLES, pack_audio, unpack_audio
from app.services.conversation.turns import (
    Command,
    is_echo,
    parse_command,
    silence_multiplier,
    spoken_portion,
    strip_wake_word,
    title_from,
)
from app.services.llm.ollama import SentenceChunker, build_messages, estimate_tokens
from app.services.metrics.tracker import SessionClock, TurnMetrics
from app.services.vad.silero import SpeechSegmenter, VadConfig, VadEvent

FRAME = b"\x00\x00" * VAD_FRAME_SAMPLES


def run(segmenter, probabilities):
    return [segmenter.process(FRAME, p) for p in probabilities]


def test_segmenter_detects_start_and_end():
    s = SpeechSegmenter(VadConfig(min_speech_ms=64, silence_ms=320, padding_ms=64))
    events = run(s, [0.0] * 3 + [0.9] * 10 + [0.0] * 12)
    assert events.count(VadEvent.SPEECH_STARTED) == 1
    assert events.count(VadEvent.SPEECH_ENDED) == 1
    assert s.finished_utterance


def test_segmenter_ignores_a_short_blip():
    s = SpeechSegmenter(VadConfig(min_speech_ms=160))
    assert VadEvent.SPEECH_STARTED not in run(s, [0.9, 0.9, 0.0, 0.0, 0.0])


def test_segmenter_includes_padding_before_speech():
    s = SpeechSegmenter(VadConfig(min_speech_ms=64, padding_ms=96, silence_ms=96))
    run(s, [0.0] * 5 + [0.9] * 2 + [0.0] * 3)
    # 3 padding + 2 speech + 3 trailing silence
    assert len(s.finished_utterance) == 8 * len(FRAME)


def test_segmenter_guard_requires_stronger_longer_speech():
    cfg = VadConfig(threshold=0.5, interrupt_threshold=0.8, min_speech_ms=64, interrupt_min_speech_ms=256)
    s = SpeechSegmenter(cfg)
    s.guarded = True
    assert VadEvent.SPEECH_STARTED not in run(s, [0.7] * 20), "echo-level probability must not interrupt"
    assert VadEvent.SPEECH_STARTED not in run(s, [0.9] * 4), "too short to interrupt"
    assert VadEvent.SPEECH_STARTED in run(s, [0.9] * 8)


def test_natural_pause_is_tolerated_when_silence_is_extended():
    s = SpeechSegmenter(VadConfig(min_speech_ms=64, silence_ms=320, max_silence_ms=1000))
    run(s, [0.9] * 5)
    s.extend_silence(2.2)
    assert VadEvent.SPEECH_ENDED not in run(s, [0.0] * 15), "still inside the extended window"
    assert VadEvent.SPEECH_ENDED in run(s, [0.0] * 10)


def test_silence_extension_is_capped():
    s = SpeechSegmenter(VadConfig(silence_ms=500, max_silence_ms=800))
    s.extend_silence(10)
    assert s.silence_target_ms == 800


def test_runaway_utterance_is_force_ended():
    s = SpeechSegmenter(VadConfig(min_speech_ms=64, max_utterance_seconds=1))
    assert VadEvent.SPEECH_ENDED in run(s, [0.9] * 40)


def test_sensitivity_maps_monotonically():
    low, high = VadConfig.from_sensitivity(0.0), VadConfig.from_sensitivity(1.0)
    assert low.threshold > high.threshold
    assert high.interrupt_threshold > high.threshold


def test_frame_assembler_rebuilds_exact_frames():
    assembler = FrameAssembler()
    frames = assembler.push(b"\x01\x00" * 300) + assembler.push(b"\x01\x00" * 300)
    assert len(frames) == 1 and len(frames[0]) == 1024


def test_audio_packet_round_trip():
    assert unpack_audio(pack_audio(7, 3, 24000, b"abcd")) == (7, 3, 24000, b"abcd")


def stream(chunker, text):
    out = []
    for token in text.split(" "):
        out += chunker.push(token + " ")
    if (last := chunker.flush()):
        out.append(last)
    return out


def test_chunker_splits_sentences_without_losing_text():
    text = "Hello there. How are you today? I am fine!"
    assert " ".join(stream(SentenceChunker(), text)) == text
    assert stream(SentenceChunker(), text)[0] == "Hello there."


def test_chunker_keeps_abbreviations_and_decimals_together():
    pieces = stream(SentenceChunker(), "Dr. Smith paid 3.5 dollars for it. Then he left.")
    assert pieces[0].startswith("Dr. Smith")


def test_chunker_releases_a_long_first_clause_early():
    pieces = stream(SentenceChunker(), "Well, thinking about this for a moment, the answer is quite simple really")
    assert len(pieces) >= 2


def test_chunker_releases_a_long_unpunctuated_sentence_without_losing_words():
    text = " ".join(f"word{i}" for i in range(60))
    pieces = stream(SentenceChunker(), text)
    assert len(pieces) > 1
    assert " ".join(pieces) == text
    assert all(len(p) <= 100 for p in pieces)


def test_context_keeps_recent_turns_when_conversation_exceeds_limit():
    history = []
    for i in range(400):
        history.append({"role": "user", "content": f"question {i} " + "word " * 30})
        history.append({"role": "assistant", "content": f"answer {i} " + "word " * 30})
    history.append({"role": "user", "content": "the latest question"})
    messages = build_messages(history, "", "en", num_ctx=2048, reserve_for_reply=160)
    assert messages[0]["role"] == "system"
    assert messages[-1]["content"] == "the latest question"
    assert messages[1]["role"] == "user", "must not begin on a dangling assistant turn"
    assert sum(estimate_tokens(m["content"]) + 4 for m in messages) <= 2048
    assert len(messages) < len(history)


def test_context_always_keeps_latest_turn_even_if_huge():
    messages = build_messages([{"role": "user", "content": "x " * 50000}], "", "en", 2048, 160)
    assert messages[-1]["role"] == "user"


def test_personality_and_language_reach_the_prompt():
    system = build_messages([], "You talk like a pirate.", "ur", 2048, 160)[0]["content"]
    assert "pirate" in system and "Urdu" in system


def test_commands_match_whole_phrases_only():
    assert parse_command("Stop.") == Command.STOP
    assert parse_command("Hey, stop") == Command.STOP
    assert parse_command("I'm back!") == Command.RESUME
    assert parse_command("listen to me") == Command.RESUME
    assert parse_command("Repeat that.") == Command.REPEAT
    assert parse_command("End conversation") == Command.END
    assert parse_command("and conversation.") == Command.END
    assert parse_command("and conversation with my boss went well") is None
    assert parse_command("how do I stop a leaky tap") is None
    assert parse_command("pause the movie and tell me a joke") is None


def test_wake_word():
    assert strip_wake_word("Hey Echo, what's up?", "echo") == (True, "what's up?")
    assert strip_wake_word("echo", "echo") == (True, "")
    assert strip_wake_word("what is an echo chamber", "echo")[0] is False


def test_echo_detection():
    spoken = "The capital of France is Paris and it is a lovely city to visit."
    assert is_echo("capital of France is Paris", spoken)
    assert is_echo("the capitol of france is paris", spoken), "tolerates a mis-transcription"
    assert not is_echo("tell me about Germany instead", spoken)
    assert not is_echo("Paris", spoken), "a single word is not enough evidence"
    assert not is_echo("anything", "")


def test_unfinished_speech_waits_longer():
    assert silence_multiplier("I went to the") > silence_multiplier("I went home.")
    assert silence_multiplier("first of all,") > 1
    assert silence_multiplier("") == 1.0


def test_spoken_portion_keeps_whole_words_only():
    assert spoken_portion("one two three four", 500, 1000) == "one two"
    assert spoken_portion("one two three four", 1000, 1000) == "one two three four"
    assert spoken_portion("one two three four", 0, 1000) == ""


def test_speech_language_follows_the_script_written():
    from app.services.tts.engines import speech_language

    assert speech_language("The capital is Islamabad.") == "en"
    assert speech_language("پاکستان کا دارالحکومت") == "ur"


def test_title_from_text():
    assert title_from("Tell me about AI.") == "Tell me about AI"
    assert title_from("word " * 40).endswith("…")


class FakeTime:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_clock_separates_active_and_paused_time():
    now = FakeTime()
    clock = SessionClock(_now=now)
    clock.resume()
    now.t += 10
    clock.pause()
    now.t += 5
    assert clock.paused_now() == 5
    clock.resume()
    now.t += 3
    clock.stop()
    assert clock.active_seconds == 13
    assert clock.paused_seconds == 5


def test_clock_metrics_are_empty_until_measured():
    snapshot = SessionClock().snapshot()
    assert snapshot["average_latency_ms"] is None
    assert snapshot["minimum_latency_ms"] is None
    assert snapshot["turn_count"] == 0


def test_clock_aggregates_turns():
    clock = SessionClock()
    for ms in (1000.0, 3000.0):
        clock.record_turn(TurnMetrics(speech_end=0, stt_ms=200, llm_first_token_ms=300, first_audio_ms=ms), False)
    # A "repeat that" turn has no transcription step and must not flatter the figures.
    clock.record_turn(TurnMetrics(speech_end=0, stt_ms=0.0, first_audio_ms=50.0), False)
    snapshot = clock.snapshot()
    assert snapshot["average_latency_ms"] == 2000
    assert snapshot["minimum_latency_ms"] == 1000
    assert snapshot["maximum_latency_ms"] == 3000
    assert np.isclose(snapshot["average_transcription_ms"], 200)
