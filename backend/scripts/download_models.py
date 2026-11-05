"""Download local model assets from their official sources, with progress, retries and hashes.

Usage: python scripts/download_models.py [--stt tiny] [--skip-kokoro] [--skip-piper]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config.settings import get_settings  # noqa: E402

KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/low"
SILERO = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
# Pinned on 2026-11-05. If an upstream file ever changes, setup stops rather than trusting it.
HASHES = {
    "silero_vad.onnx": "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
    "kokoro-v1.0.int8.onnx": "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    "en_US-lessac-low.onnx": "f7d01dde371555732c4c314111ac79672b1a5ce2fc19266ab42178fd8df7f375",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, target: Path, expected_sha256: str | None = None, retries: int = 4) -> None:
    """Resumable download. A file only moves into place once its hash has been verified."""
    if target.exists():
        if expected_sha256 is None or sha256(target) == expected_sha256:
            print(f"  ok (already present and verified) {target.name}")
            return
        print(f"  {target.name} failed verification, downloading it again")
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as response:
                if response.status_code == 416:  # Already complete
                    break
                response.raise_for_status()
                if response.status_code != 206:
                    offset = 0
                total = int(response.headers.get("content-length", 0)) + offset
                done = offset
                last = 0.0
                with partial.open("ab" if offset else "wb") as handle:
                    for block in response.iter_bytes(1 << 16):
                        handle.write(block)
                        done += len(block)
                        if time.monotonic() - last > 2:
                            last = time.monotonic()
                            percent = f"{done * 100 / total:5.1f}%" if total else ""
                            print(f"  {target.name}: {done / 1e6:7.1f} MB {percent}", flush=True)
            break
        except httpx.HTTPError as exc:
            print(f"  attempt {attempt}/{retries} failed: {exc}", flush=True)
            if attempt == retries:
                raise
            time.sleep(2 * attempt)
    size = partial.stat().st_size
    digest = sha256(partial)
    if expected_sha256 is not None and digest != expected_sha256:
        partial.unlink()
        raise RuntimeError(f"{target.name}: hash mismatch (expected {expected_sha256}, got {digest})")
    partial.replace(target)
    print(f"  downloaded and verified {target.name} ({size / 1e6:.1f} MB)", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stt", default=None)
    parser.add_argument("--skip-kokoro", action="store_true")
    parser.add_argument("--skip-piper", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    models = settings.models_dir
    print(f"Model directory: {models}")

    print("Silero VAD")
    fetch(SILERO, models / "silero" / "silero_vad.onnx", HASHES["silero_vad.onnx"])

    if not args.skip_kokoro:
        print("Kokoro-82M (int8)")
        for name in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
            fetch(f"{KOKORO}/{name}", models / "kokoro" / name, HASHES[name])

    if not args.skip_piper:
        print("Piper voice")
        voice = "en_US-lessac-low"
        fetch(f"{PIPER}/{voice}.onnx", models / "piper" / f"{voice}.onnx", HASHES[f"{voice}.onnx"])
        fetch(f"{PIPER}/{voice}.onnx.json", models / "piper" / f"{voice}.onnx.json")

    stt = args.stt or settings.stt_model
    print(f"faster-whisper: {stt}")
    from faster_whisper import download_model

    path = download_model(stt, cache_dir=str(models / "whisper"))
    print(f"  ok {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
