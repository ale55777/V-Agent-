"""End-to-end check against a RUNNING EchoAI server, with every real model in the loop.

It behaves like a browser: streams microphone audio at real-time pace over the WebSocket
and confirms playback at real-time pace. The user's "voice" is synthesised with Piper,
so transcripts and replies are genuine output from Whisper and Qwen. Nothing is scripted.
Its temporary user and data are deleted at the end.

Usage: python scripts/e2e_voice_test.py [--base http://127.0.0.1:8000] [--engine local|groq]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config.settings import get_settings  # noqa: E402
from app.services.audio.pcm import float_to_pcm16, unpack_audio  # noqa: E402
from app.services.tts.engines import PiperEngine  # noqa: E402

FRAME = 512
FRAME_SECONDS = FRAME / 16000
SCRIPT = (
    "What is the capital of France?",
    "What language do people speak there?",
    "Tell me a long story about a brave dragon who lived on a mountain.",
    "Wait, explain it simply please.",
    "Pause.",
    "I'm back.",
    "Repeat that.",
    "End conversation.",
)


def resample(samples: np.ndarray, source: int) -> np.ndarray:
    positions = np.linspace(0, len(samples) - 1, int(len(samples) * 16000 / source))
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


class Client:
    def __init__(self, ws, voice: PiperEngine) -> None:
        self.ws = ws
        self.voice = voice
        self.events: list[tuple[float, dict]] = []
        self.mic: asyncio.Queue[bytes] = asyncio.Queue()
        self.chunks: dict[tuple[int, int], dict] = {}
        self.stale_seq = 0
        self.stale_audio_played = 0
        self.playing: list[asyncio.Task] = []
        self.condition = asyncio.Condition()
        self.first_speech_sent = 0.0
        self.clips: dict[str, bytes] = {text: self.render(text) for text in SCRIPT}

    def render(self, text: str) -> bytes:
        audio = resample(np.concatenate(list(self.voice.synthesize(text, None))), self.voice.sample_rate)
        return float_to_pcm16(audio)

    def say(self, text: str) -> float:
        """Queue the sentence for the mic stream. Returns its length in seconds."""
        # Pre-synthesised, so this test client's own TTS does not compete for CPU mid-run.
        pcm = self.clips.get(text)
        if pcm is None:
            pcm = self.clips[text] = self.render(text)
        audio = np.frombuffer(pcm, dtype="<i2")
        for start in range(0, len(pcm), FRAME * 2):
            self.mic.put_nowait(pcm[start : start + FRAME * 2].ljust(FRAME * 2, b"\0"))
        return len(audio) / 16000

    async def mic_loop(self) -> None:
        """A microphone never stops: real speech when queued, otherwise silence."""
        silence = bytes(FRAME * 2)
        next_at = time.perf_counter()
        while True:
            try:
                frame = self.mic.get_nowait()
                self.first_speech_sent = self.first_speech_sent or time.perf_counter()
            except asyncio.QueueEmpty:
                frame = silence
            await self.ws.send(frame)
            next_at += FRAME_SECONDS
            await asyncio.sleep(max(0, next_at - time.perf_counter()))

    async def recv_loop(self) -> None:
        async for message in self.ws:
            now = time.perf_counter()
            if isinstance(message, bytes):
                seq, chunk, _rate, _pcm = unpack_audio(message)
                meta = self.chunks.get((seq, chunk))
                if seq < self.stale_seq:
                    continue
                if meta:
                    self.playing.append(asyncio.create_task(self.play(seq, chunk, meta)))
                continue
            event = json.loads(message)
            self.events.append((now, event))
            kind = event["type"]
            if kind == "agent.audio.chunk":
                self.chunks[(event["response_seq"], event["chunk_seq"])] = event
            elif kind == "agent.interrupted":
                self.stale_seq = event["response_seq"] + 1
            async with self.condition:
                self.condition.notify_all()

    async def play(self, seq: int, chunk: int, meta: dict) -> None:
        """Report playback progress as the audio clock would, sentences one after another."""
        previous = [t for t in self.playing if t is not asyncio.current_task() and not t.done()]
        if previous:
            await asyncio.wait(previous)
        started = time.perf_counter()
        duration = meta["duration_ms"]
        while seq >= self.stale_seq:
            played = min((time.perf_counter() - started) * 1000, duration)
            await self.ws.send(json.dumps({
                "type": "playback.progress", "response_id": meta["response_id"],
                "chunk_seq": chunk, "played_ms": played,
            }))
            if played >= duration:
                return
            await asyncio.sleep(0.08)
        self.stale_audio_played += 0

    async def wait_for(self, predicate, timeout: float = 60.0, since: int = 0):
        deadline = time.perf_counter() + timeout
        async with self.condition:
            while True:
                for moment, event in self.events[since:]:
                    if predicate(event):
                        return moment, event
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    seen = [e["type"] for _, e in self.events[since:]]
                    detail = [e for _, e in self.events[since:] if e["type"] in ("transcript.discarded", "error", "speech.ended")]
                    print(f"  Details: {detail}")
                    raise TimeoutError(f"Timed out. Events seen: {seen}")
                try:
                    await asyncio.wait_for(self.condition.wait(), remaining)
                except asyncio.TimeoutError:
                    pass

    def mark(self) -> int:
        return len(self.events)

    async def turn(self, text: str, timeout: float = 90.0) -> dict:
        since = self.mark()
        self.say(text)
        _, final = await self.wait_for(lambda e: e["type"] == "transcript.final", timeout, since)
        _, done = await self.wait_for(lambda e: e["type"] == "agent.response.completed", timeout, since)
        _, turn_metrics = await self.wait_for(lambda e: e["type"] == "metrics.turn", 1, since)
        print(f"\n  YOU (heard): {final['text']}")
        print(f"  JARVIS:      {done['spoken_text']}")
        print(
            f"  transcribe {turn_metrics['stt_ms']} ms | LLM first token {turn_metrics['llm_first_token_ms']} ms"
            f" | speech end to first audio {turn_metrics['first_audio_ms']} ms"
        )
        return {"final": final, "done": done, "metrics": turn_metrics}


async def scenario(client: Client, api: httpx.AsyncClient, session_id: str, results: dict) -> None:
    latencies = []

    print("\n[1] First question")
    first = await client.turn("What is the capital of France?")
    latencies.append(first["metrics"]["first_audio_ms"])
    assert first["final"]["text"], "no transcript"
    assert first["done"]["message"]["status"] == "completed"
    results["answer_mentions_paris"] = "paris" in first["done"]["spoken_text"].lower()

    print("\n[2] Follow-up that only makes sense with context")
    second = await client.turn("What language do people speak there?")
    latencies.append(second["metrics"]["first_audio_ms"])
    results["follow_up_mentions_french"] = "french" in second["done"]["spoken_text"].lower()

    print("\n[3] Barge-in while the AI is speaking")
    since = client.mark()
    client.say("Tell me a long story about a brave dragon who lived on a mountain.")
    await client.wait_for(lambda e: e["type"] == "session.state" and e["state"] == "SPEAKING", 90, since)
    chunk_time, chunk = await client.wait_for(lambda e: e["type"] == "agent.audio.chunk", 5, since)
    response_id = chunk["response_id"]
    await asyncio.sleep(1.0)  # Let about a second be "heard".
    since = client.mark()
    client.first_speech_sent = 0.0
    client.say("Wait, explain it simply please.")
    stop_time, stop = await client.wait_for(lambda e: e["type"] == "agent.interrupted", 10, since)
    results["barge_in_ms_from_user_speech"] = round((stop_time - client.first_speech_sent) * 1000)
    print(f"  Playback told to stop {results['barge_in_ms_from_user_speech']} ms after the first speech frame was sent")
    assert stop["response_id"] == response_id

    _, cut = await client.wait_for(
        lambda e: e["type"] == "agent.response.completed" and e["response_id"] == response_id, 10, since
    )
    generated = "".join(
        e["text"] for _, e in client.events if e["type"] == "agent.text.delta" and e["response_id"] == response_id
    )
    print(f"  Generated so far: {generated.strip()!r}")
    print(f"  Saved as spoken:  {cut['spoken_text']!r}")
    results["interrupted_saved_status"] = cut["message"]["status"] if cut["message"] else None
    results["saved_less_than_generated"] = len(cut["spoken_text"]) < len(generated.strip())

    _, outcome = await client.wait_for(lambda e: e["type"] in ("transcript.final", "transcript.discarded"), 60, since)
    ended = [e for _, e in client.events[since:] if e["type"] == "speech.ended"]
    print(f"  Utterance after barge-in: {ended[-1]['duration_ms'] if ended else '?'} ms captured; outcome {outcome}")
    assert outcome["type"] == "transcript.final", "the interrupting speech must be understood"
    final = outcome
    _, done = await client.wait_for(
        lambda e: e["type"] == "agent.response.completed" and e["response_id"] != response_id, 90, since
    )
    print(f"  YOU (heard): {final['text']}\n  JARVIS:      {done['spoken_text']}")

    print("\n[4] Voice commands")
    since = client.mark()
    client.say("Pause.")
    await client.wait_for(lambda e: e["type"] == "session.paused", 30, since)
    print("  'Pause' worked")
    await asyncio.sleep(1)
    since = client.mark()
    client.say("I'm back.")
    await client.wait_for(lambda e: e["type"] == "session.resumed", 30, since)
    print("  'I'm back' worked")
    results["pause_resume"] = True

    since = client.mark()
    client.say("Repeat that.")
    _, again = await client.wait_for(lambda e: e["type"] == "agent.response.completed", 60, since)
    print(f"  'Repeat that' spoke: {again['spoken_text']!r}")
    results["repeat_matches"] = again["spoken_text"].strip() == done["spoken_text"].strip()

    await asyncio.sleep(0.5)
    print("\n[5] Silence alone must not end anything")
    since = client.mark()
    await asyncio.sleep(5)
    results["silence_had_no_speech_events"] = not any(
        e["type"] in ("speech.started", "session.ended") for _, e in client.events[since:]
    )

    print("\n[6] End by voice")
    since = client.mark()
    client.say("End conversation.")
    _, heard = await client.wait_for(lambda e: e["type"] == "transcript.final", 30, since)
    print(f"  Whisper heard: {heard['text']!r}, command: {heard.get('command')}")
    if heard.get("command") != "end":
        raise AssertionError("'End conversation' was not recognised as a command")
    await client.wait_for(lambda e: e["type"] == "session.ended", 30, since)
    results["first_audio_ms"] = latencies
    results["first_audio_median_ms"] = statistics.median(latencies)


async def run(base: str, engine: str = "local") -> int:
    settings = get_settings()
    voice = PiperEngine(settings.models_dir, settings.piper_voice)
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    results: dict = {}
    async with httpx.AsyncClient(base_url=base, timeout=60, verify=False) as api:
        await create_user(email, "e2e-test-password")
        token = (await api.post("/api/v1/auth/login", json={"email": email, "password": "e2e-test-password"})).json()["access_token"]
        api.headers["Authorization"] = f"Bearer {token}"
        saved = (await api.get("/api/v1/agent/settings")).json()
        await api.put("/api/v1/agent/settings", json={**saved, "ai_engine": engine})
        print(f"AI engine: {engine}")
        session_id = (await api.post("/api/v1/sessions", json={})).json()["id"]
        try:
            url = base.replace("http", "ws", 1) + "/ws/v1/voice"
            tls = None
            if url.startswith("wss"):  # The development certificate is self-signed.
                tls = ssl.create_default_context()
                tls.check_hostname = False
                tls.verify_mode = ssl.CERT_NONE
            async with websockets.connect(url, max_size=None, ssl=tls) as ws:
                await ws.send(json.dumps({"type": "session.start", "token": token, "session_id": session_id, "device_id": "e2e"}))
                client = Client(ws, voice)
                tasks = [asyncio.create_task(client.recv_loop()), asyncio.create_task(client.mic_loop())]
                await client.wait_for(lambda e: e["type"] == "session.started", 240)
                try:
                    await scenario(client, api, session_id, results)
                finally:
                    for task in tasks + client.playing:
                        task.cancel()

            await asyncio.sleep(0.5)
            report = (await api.get(f"/api/v1/sessions/{session_id}/report")).json()
            print("\n[7] Saved report (from PostgreSQL)")
            print(json.dumps({k: report[k] for k in ("session", "cost")}, indent=2, default=str))
            perf = {k: v for k, v in report["performance"].items() if k != "turns"}
            print(json.dumps(perf, indent=2))
            for message in report["transcript"]:
                flag = " [interrupted]" if message["interrupted"] else ""
                print(f"  {message['sequence_number']:>2} {message['speaker']:<9}{flag} {message['content']}")
            results["saved_messages"] = len(report["transcript"])
            results["status"] = report["session"]["status"]
            for fmt, check in (("txt", b"Jarvis"), ("json", b"transcript"), ("pdf", b"%PDF")):
                exported = await api.get(f"/api/v1/sessions/{session_id}/export/{fmt}")
                assert check in exported.content[:4000], fmt
            results["exports_ok"] = True
        finally:
            await api.delete(f"/api/v1/sessions/{session_id}")
            await delete_user(email)

    print("\nRESULTS")
    print(json.dumps(results, indent=2))
    required = ["exports_ok", "pause_resume", "silence_had_no_speech_events", "saved_less_than_generated", "repeat_matches"]
    failed = [key for key in required if not results.get(key)]
    if results.get("status") != "ended":
        failed.append("status")
    if results.get("interrupted_saved_status") not in ("interrupted", None):
        failed.append("interrupted_saved_status")
    print("FAILED: " + ", ".join(failed) if failed else "PASSED")
    return 1 if failed else 0


async def create_user(email: str, password: str) -> None:
    """Made directly in the database, because sign-up may be switched off on this server."""
    from app.core.security import hash_password
    from app.database.session import session_factory
    from app.models.entities import AgentSettings, User

    async with session_factory()() as db:
        user = User(email=email, password_hash=hash_password(password))
        user.settings = AgentSettings()
        db.add(user)
        await db.commit()


async def delete_user(email: str) -> None:
    from sqlalchemy import delete

    from app.database.session import session_factory
    from app.models.entities import User

    async with session_factory()() as db:
        await db.execute(delete(User).where(User.email == email))
        await db.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--engine", choices=("local", "groq"), default="local")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.base, args.engine)))
