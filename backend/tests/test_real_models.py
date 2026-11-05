"""Checks using the actual downloaded models. Skipped when they have not been downloaded."""
from __future__ import annotations

import numpy as np
import pytest

from app.config.settings import get_settings
from app.services.audio.pcm import VAD_FRAME_SAMPLES
from app.services.conversation.turns import COMMAND_HINT, Command, parse_command
from app.services.vad.silero import SileroVAD

pytestmark = pytest.mark.models
MODELS = get_settings().models_dir
VOICE = MODELS / "piper" / f"{get_settings().piper_voice}.onnx"


def resample(samples: np.ndarray, source: int) -> np.ndarray:
    positions = np.linspace(0, len(samples) - 1, int(len(samples) * 16000 / source))
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


@pytest.fixture(scope="module")
def piper():
    if not VOICE.exists():
        pytest.skip("Piper voice not downloaded")
    from app.services.tts.engines import PiperEngine

    return PiperEngine(MODELS, get_settings().piper_voice)


@pytest.fixture(scope="module")
def whisper():
    settings = get_settings()
    if not (MODELS / "whisper").exists():
        pytest.skip("Whisper model not downloaded")
    from app.services.asr.whisper import WhisperRecognizer

    return WhisperRecognizer(settings.stt_model, MODELS, settings.stt_compute_type, settings.stt_threads)


def speak(engine, text: str) -> np.ndarray:
    return resample(np.concatenate(list(engine.synthesize(text, None))), engine.sample_rate)


def test_silero_separates_speech_from_silence_and_noise(piper):
    path = MODELS / "silero" / "silero_vad.onnx"
    if not path.exists():
        pytest.skip("Silero not downloaded")
    vad = SileroVAD(path)
    silence = max(vad.probability(np.zeros(VAD_FRAME_SAMPLES, dtype=np.float32)) for _ in range(20))
    rng = np.random.default_rng(0)
    noise = max(
        vad.probability(rng.normal(0, 0.01, VAD_FRAME_SAMPLES).astype(np.float32)) for _ in range(30)
    )
    audio = speak(piper, "Hello, this is a test of voice detection.")
    vad.reset()
    speech = max(
        vad.probability(audio[i : i + VAD_FRAME_SAMPLES]) for i in range(0, len(audio) - VAD_FRAME_SAMPLES, VAD_FRAME_SAMPLES)
    )
    assert silence < 0.3 and noise < 0.5 and speech > 0.8


def test_tts_produces_real_audio(piper):
    samples = np.concatenate(list(piper.synthesize("Testing the voice output.", None)))
    assert len(samples) / piper.sample_rate > 0.5
    assert float(np.abs(samples).max()) > 0.05


def test_tts_does_not_read_markdown_aloud():
    from app.services.tts.engines import clean_for_speech

    assert clean_for_speech("**Bold** and `code` [link](http://x)") == "Bold and code link"


def test_whisper_round_trip(piper, whisper):
    text = whisper.transcribe(speak(piper, "The weather is very nice today."), "en", True).text.lower()
    assert "weather" in text and "nice" in text


@pytest.mark.parametrize(
    "phrase, command",
    [
        ("Pause.", Command.PAUSE), ("Stop.", Command.STOP), ("Continue.", Command.RESUME),
        ("I'm back.", Command.RESUME), ("Listen to me.", Command.RESUME), ("Repeat that.", Command.REPEAT),
        ("End conversation.", Command.END),
    ],
)
def test_spoken_commands_survive_real_transcription(piper, whisper, phrase, command):
    audio = np.concatenate([np.zeros(4800, dtype=np.float32), speak(piper, phrase), np.zeros(4800, dtype=np.float32)])
    assert parse_command(whisper.transcribe(audio, "en", True, COMMAND_HINT).text) == command


def test_piper_refuses_urdu_instead_of_reading_gibberish(piper):
    with pytest.raises(RuntimeError):
        list(piper.synthesize("پاکستان", None, "ur"))


def test_urdu_speech_round_trip_through_kokoro_and_whisper(whisper):
    if not (MODELS / "kokoro" / "voices-v1.0.bin").exists():
        pytest.skip("Kokoro not downloaded")
    from app.services.tts.engines import KokoroEngine

    engine = KokoroEngine(MODELS, 2)
    audio = np.concatenate(list(engine.synthesize("پاکستان کا دارالحکومت اسلام آباد ہے۔", None, "ur")))
    assert 1 < len(audio) / engine.sample_rate < 10, "must sound like a sentence, not spelled-out letters"
    heard = whisper.transcribe(resample(audio, engine.sample_rate), "ur", True).text
    assert "پاکستان" in heard


def test_whisper_returns_nothing_useful_for_silence(whisper):
    assert len(whisper.transcribe(np.zeros(16000, dtype=np.float32), "en", True).text) < 20


async def test_missing_ollama_model_is_reported_as_an_error():
    import httpx

    from app.services.llm.ollama import LLMError, OllamaClient

    settings = get_settings()
    try:
        httpx.get(f"{settings.ollama_url}/api/version", timeout=2)
    except httpx.HTTPError:
        pytest.skip("Ollama is not running")
    client = OllamaClient(settings)
    with pytest.raises(LLMError):
        async for _ in client.stream([{"role": "user", "content": "hi"}], "no-such-model:latest"):
            pass
    await client.close()


async def test_unreachable_ollama_is_reported_as_an_error():
    from app.config.settings import Settings
    from app.services.llm.ollama import LLMError, OllamaClient

    client = OllamaClient(Settings(ollama_url="http://127.0.0.1:9"))
    with pytest.raises(LLMError):
        async for _ in client.stream([{"role": "user", "content": "hi"}]):
            pass
    await client.close()
