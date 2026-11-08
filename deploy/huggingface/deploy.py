"""Deploy the EchoAI backend to a Hugging Face Docker Space.

Usage (from the repo root):  backend\\.venv\\Scripts\\python.exe deploy\\huggingface\\deploy.py

Reads from .env:
  HF_TOKEN                     a Hugging Face token with write access
  ECHOAI_CLOUD_DATABASE_URL    the database for the cloud (Supabase session pooler, IPv4)
  ECHOAI_GROQ_API_KEY, ECHOAI_SECRET_KEY, ECHOAI_CORS_ORIGINS
Secrets go to the Space's private settings, never into the uploaded files.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MODELS = ROOT / ".data" / "models"
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "tests", ".pytest_cache", "*.db")


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def stage(target: Path) -> None:
    for name in ("Dockerfile", "README.md", "requirements.txt"):
        shutil.copy2(HERE / name, target / name)
    backend = target / "backend"
    backend.mkdir()
    for name in ("app", "alembic"):
        shutil.copytree(ROOT / "backend" / name, backend / name, ignore=SKIP)
    shutil.copy2(ROOT / "backend" / "alembic.ini", backend / "alembic.ini")
    # Silero, the Piper voice and the Whisper fallback, so a cold start downloads nothing.
    for name in ("silero", "piper", "whisper"):
        shutil.copytree(MODELS / name, target / "models" / name, ignore=SKIP)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--space", default="echoai", help="Space name under your account")
    args = parser.parse_args()

    env = read_env()
    missing = [k for k in ("HF_TOKEN", "ECHOAI_CLOUD_DATABASE_URL", "ECHOAI_GROQ_API_KEY", "ECHOAI_SECRET_KEY") if not env.get(k)]
    if missing:
        print("Missing in .env: " + ", ".join(missing))
        return 1

    api = HfApi(token=env["HF_TOKEN"])
    user = api.whoami()["name"]
    repo = f"{user}/{args.space}"
    api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True)

    secrets = {
        "ECHOAI_DATABASE_URL": env["ECHOAI_CLOUD_DATABASE_URL"],
        "ECHOAI_GROQ_API_KEY": env["ECHOAI_GROQ_API_KEY"],
        "ECHOAI_SECRET_KEY": env["ECHOAI_SECRET_KEY"],
        "ECHOAI_CORS_ORIGINS": env.get("ECHOAI_CORS_ORIGINS", ""),
        "ECHOAI_ALLOW_REGISTRATION": env.get("ECHOAI_ALLOW_REGISTRATION", "false"),
    }
    for key, value in secrets.items():
        api.add_space_secret(repo, key, value)

    with tempfile.TemporaryDirectory() as tmp:
        stage(Path(tmp))
        api.upload_folder(repo_id=repo, repo_type="space", folder_path=tmp,
                          commit_message="Deploy EchoAI backend", delete_patterns=["backend/**"])

    print(f"Space: https://huggingface.co/spaces/{repo}")
    print(f"API:   https://{user.lower()}-{args.space.lower()}.hf.space")
    return 0


if __name__ == "__main__":
    sys.exit(main())
