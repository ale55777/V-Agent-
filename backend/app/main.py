"""Jarvis FastAPI application."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config.settings import get_settings
from app.services.conversation.engine import raise_thread_priority
from app.services.models_registry import ModelRegistry
from app.websocket.voice import hub, router as ws_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("echoai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    raise_thread_priority()  # This thread runs the event loop.
    registry = ModelRegistry(settings)
    app.state.registry = registry
    # Models load in the background, so /health can report progress straight away.
    loader = asyncio.create_task(registry.load_all())
    yield
    await hub.close_all()
    loader.cancel()
    await registry.llm.close()
    if registry.groq_chat is not None:
        await registry.groq_chat.primary.close()
        registry.groq_recognizer.primary.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Jarvis", version="1.0.0", lifespan=lifespan)

    # Private-network origins on the web port are allowed so LAN devices work without
    # hardcoding an IP. The API uses bearer tokens, not cookies, so credentials stay off.
    extra = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=extra,
        allow_origin_regex=_private_origin_regex(),
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    app.include_router(api_router)
    app.include_router(ws_router)

    # Serving the built UI here too gives LAN/HTTPS users a single origin and certificate.
    if settings.web_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dist, html=True), name="web")
    return app


def _private_origin_regex() -> str:
    return (
        r"^https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+"
        r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$"
    )


app = create_app()
