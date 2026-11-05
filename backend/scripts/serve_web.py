"""Serve the exported Next.js UI on port 3000. Far lighter than running a Node server."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config.settings import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    app = Starlette()
    app.mount("/", StaticFiles(directory=get_settings().web_dist, html=True))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
