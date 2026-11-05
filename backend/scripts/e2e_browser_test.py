"""Drives the real built web app in real Chrome, against a running EchoAI server.

Chrome's fake microphone plays a generated WAV into getUserMedia, so the actual AudioWorklet,
WebSocket, playback code and UI all run. Needs `pip install playwright` and installed Chrome.
It uses a throwaway Chrome profile. Screenshots go to --out.

Usage: python scripts/e2e_browser_test.py --out DIR [--base http://localhost:3000]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
import wave
from pathlib import Path

import numpy as np
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config.settings import get_settings  # noqa: E402
from app.services.audio.pcm import float_to_pcm16  # noqa: E402
from app.services.tts.engines import PiperEngine  # noqa: E402
from scripts.e2e_voice_test import delete_user, resample  # noqa: E402

# (seconds of silence before the line, line). The fake mic starts when the session does.
TIMELINE = [
    (15.0, "What is the capital of France?"),  # Connecting can take a while on a busy PC.
    (16.0, "Tell me a long story about a brave dragon who lived on a mountain."),
    (9.0, "Wait, explain it simply please."),
    (60.0, ""),
]


def build_microphone_wav(path: Path) -> None:
    settings = get_settings()
    voice = PiperEngine(settings.models_dir, settings.piper_voice)
    parts = []
    for silence, line in TIMELINE:
        parts.append(np.zeros(int(16000 * silence), dtype=np.float32))
        if line:
            parts.append(resample(np.concatenate(list(voice.synthesize(line, None))), voice.sample_rate))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(float_to_pcm16(np.concatenate(parts)))


async def run(base: str, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="echoai"))
    wav = work / "mic.wav"
    build_microphone_wav(wav)
    email = f"browser-{uuid.uuid4().hex[:10]}@example.com"
    results: dict = {}
    console_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome",
            headless=True,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}%noloop",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            permissions=["microphone"],
            ignore_https_errors=True,  # The development certificate is self-signed.
        )
        page = await context.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.on("response", lambda r: console_errors.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
        try:
            await page.goto(base)
            await page.wait_for_url("**/login/**")
            await page.get_by_role("button", name="New here? Create an account").click()
            await page.get_by_label("Email").fill(email)
            await page.get_by_label("Password").fill("browser-test-password")
            await page.screenshot(path=out / "1-login.png")
            await page.get_by_role("button", name="Create account").click()
            await page.get_by_role("button", name="Start conversation").wait_for(timeout=30000)
            await page.screenshot(path=out / "2-ready.png")

            # Record how far each waveform really moves. A flat line means no audio reached it.
            await page.evaluate(
                """() => {
                    window.__wave = [0, 0];
                    setInterval(() => {
                        document.querySelectorAll('canvas').forEach((canvas, index) => {
                            const { width, height } = canvas;
                            const pixels = canvas.getContext('2d').getImageData(0, 0, width, height).data;
                            let top = height, bottom = 0;
                            for (let y = 0; y < height; y++) for (let x = 0; x < width; x += 4) {
                                if (pixels[(y * width + x) * 4 + 3] > 40) { top = Math.min(top, y); bottom = Math.max(bottom, y); }
                            }
                            window.__wave[index] = Math.max(window.__wave[index], bottom - top);
                        });
                    }, 50);
                }"""
            )
            await page.get_by_role("button", name="Start conversation").click()
            await page.get_by_text("Listening", exact=True).wait_for(timeout=120000)
            results["connected"] = True

            await page.get_by_text("What is the capital of France").wait_for(timeout=60000)
            results["user_transcript_shown"] = True
            print("Transcript from the fake microphone appeared in the UI")
            await page.get_by_text("dim text has not been spoken").wait_for(timeout=60000)
            results["ai_subtitles_shown"] = True
            await page.get_by_text("Speaking. Talk over me any time.").wait_for(timeout=60000)
            await page.wait_for_timeout(500)
            await page.screenshot(path=out / "3-speaking.png")
            print("AI response is streaming")

            # Real-time playback must drive the session back to listening.
            await page.locator("text=/EchoAI · \\d/").first.wait_for(timeout=60000)
            results["ai_message_saved_after_playback"] = True
            first_audio = await page.locator("dt:has-text('Last first-audio') + dd").inner_text()
            results["first_audio_shown"] = first_audio
            print(f"First-audio latency shown in UI: {first_audio}")

            await page.get_by_text("Tell me a long story").wait_for(timeout=60000)
            print("Second question heard")
            try:
                await page.get_by_text("interrupted", exact=False).first.wait_for(timeout=60000)
                results["interruption_shown"] = True
                print("The interrupted response is marked in the UI")
            except Exception:
                results["interruption_shown"] = False
            await page.screenshot(path=out / "4-interrupted.png")
            await asyncio.sleep(1)
            wave_extent = await page.evaluate("window.__wave")
            results["waveform_extent_px"] = {"microphone": wave_extent[0], "ai": wave_extent[1]}

            await page.get_by_role("button", name="Pause", exact=True).click()
            await page.get_by_role("button", name="Resume", exact=True).wait_for(timeout=20000)
            results["pause_button"] = True
            await page.screenshot(path=out / "5-paused.png")
            await page.get_by_role("button", name="Resume", exact=True).click()
            await page.get_by_role("button", name="Pause", exact=True).wait_for(timeout=10000)
            results["resume_button"] = True

            await page.get_by_role("button", name="Settings").click()
            await page.get_by_role("dialog").wait_for()
            await page.wait_for_timeout(1500)
            voices = await page.get_by_role("dialog").locator("label:has-text('Voice') option").count()
            results["voice_options"] = voices
            await page.screenshot(path=out / "6-settings.png")
            await page.get_by_role("button", name="Close settings").click()

            await page.get_by_role("button", name="End conversation").click()
            await page.wait_for_url("**/report/**", timeout=30000)
            await page.get_by_text("Conversation report").wait_for(timeout=15000)
            await page.wait_for_timeout(800)
            await page.screenshot(path=out / "7-report.png", full_page=True)
            results["report_shown"] = True

            async with page.expect_download(timeout=20000) as download:
                await page.get_by_role("button", name="PDF").click()
            saved = out / (await download.value).suggested_filename
            await (await download.value).save_as(saved)
            results["pdf_downloaded"] = saved.read_bytes()[:4] == b"%PDF"

            await page.get_by_role("button", name="View history").click()
            await page.wait_for_url("**/history/**")
            await page.get_by_placeholder("Search titles and transcripts").fill("capital")
            await page.wait_for_timeout(1200)
            results["history_search_hits"] = await page.locator("li").count()
            await page.screenshot(path=out / "8-history.png")
        except Exception as exc:
            results["failure"] = str(exc)[:500]
            await page.screenshot(path=out / "failure.png")
        finally:
            await browser.close()
            await delete_user(email)

    results["console_errors"] = console_errors[:10]
    print(json.dumps(results, indent=2))
    return 1 if "failure" in results else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:3000")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.base, args.out)))
