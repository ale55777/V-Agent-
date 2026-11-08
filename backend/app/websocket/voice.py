"""WebSocket voice endpoint: /ws/v1/voice

Handshake: the client's first text frame must be
    {"type": "session.start", "token": "<jwt>", "session_id": "...", "device_id": "..."}
The token travels in a message, not the URL, so it does not land in access logs.

Afterwards, binary frames are mic audio (`audio.input`) and text frames are control messages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError, field_validator

from app.config.settings import Settings, get_settings
from app.core.security import user_from_token
from app.database.session import session_factory
from app.models.entities import AgentSettings, ConversationSession, utcnow
from app.services.conversation.engine import AttentionMode, ConversationEngine, EngineConfig, State
from app.services.history.store import SessionStore
from app.services.metrics.tracker import SessionClock
from app.services.vad.silero import VadConfig

log = logging.getLogger(__name__)
router = APIRouter()

CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_REPLACED = 4408
CLOSE_DEVICE_CONFLICT = 4409
CLOSE_HANDOFF = 4410
CLOSE_NOT_READY = 4503
HANDSHAKE_TIMEOUT = 10
MAX_AUDIO_PACKET = 16000  # half a second of PCM16; larger packets are rejected
# A disconnected session stays resumable for this long before it is closed for good.
RECONNECT_GRACE_SECONDS = 15 * 60


class StartMessage(BaseModel):
    type: str
    token: str
    session_id: str
    device_id: str

    @field_validator("device_id")
    @classmethod
    def short_device_id(cls, value: str) -> str:
        if not 1 <= len(value) <= 80:
            raise ValueError("device_id must be 1-80 characters")
        return value


class LiveSession:
    def __init__(
        self, engine: ConversationEngine, store: SessionStore, user_id: str, metadata: dict, registry
    ) -> None:
        self.engine = engine
        self.store = store
        self.user_id = user_id
        self.metadata = metadata
        self.registry = registry
        self.socket: WebSocket | None = None
        self.device_id: str | None = None
        self.send_lock = asyncio.Lock()
        # Serialises connect, disconnect and end. A refresh delivers the new connection
        # while the old socket's cleanup is still running.
        self.transition = asyncio.Lock()
        self.expiry: asyncio.Task | None = None


class SessionHub:
    """All live engines. A session has one owning device at a time."""

    def __init__(self) -> None:
        self.sessions: dict[str, LiveSession] = {}

    def get(self, session_id: str) -> LiveSession | None:
        return self.sessions.get(session_id)

    async def force_disconnect(self, session_id: str, new_device: str) -> None:
        """Handoff: the old device is told why, then released. Its audio link cannot be moved."""
        live = self.sessions.get(session_id)
        if live is None:
            return
        async with live.transition:
            if live.socket is None:
                return
            socket, live.socket = live.socket, None
            await live.engine.emit("session.handoff", to_device=new_device)
            await live.engine.detach()
            await _close(socket, CLOSE_HANDOFF, "Session moved to another device")

    async def end(self, session_id: str) -> None:
        live = self.sessions.pop(session_id, None)
        if live is None:
            return
        async with live.transition:
            if live.expiry:
                live.expiry.cancel()
            await live.engine.shutdown()
            try:
                await live.store.save_metrics(live.engine.clock, live.metadata, end=True)
            except Exception:
                log.exception("Could not save final metrics for session %s", session_id)

    async def close_all(self) -> None:
        for session_id in list(self.sessions):
            await self.end(session_id)


hub = SessionHub()


def _build_config(settings: Settings, agent: AgentSettings | None) -> EngineConfig:
    sensitivity = agent.vad_sensitivity if agent else 0.5
    vad = VadConfig.from_sensitivity(
        sensitivity,
        min_speech_ms=settings.vad_min_speech_ms,
        interrupt_min_speech_ms=settings.vad_interrupt_min_speech_ms,
        silence_ms=settings.vad_silence_ms,
        max_silence_ms=settings.vad_max_silence_ms,
        padding_ms=settings.vad_padding_ms,
        max_utterance_seconds=settings.max_utterance_seconds,
    )
    mode = AttentionMode.ALWAYS
    if agent and agent.attention_mode in {m.value for m in AttentionMode}:
        mode = AttentionMode(agent.attention_mode)
    return EngineConfig(
        language=agent.language if agent else "en",
        voice=agent.selected_voice if agent else None,
        llm_model=(agent.selected_llm if agent else None) or settings.llm_model,
        personality=agent.personality if agent else "",
        attention_mode=mode,
        wake_word=(agent.wake_word if agent else "") or "jarvis",
        num_ctx=settings.llm_num_ctx,
        num_predict=settings.llm_num_predict,
        partial_interval_ms=settings.partial_interval_ms,
        tts_queue_sentences=settings.tts_queue_sentences,
        vad=vad,
    )


async def _create_live(registry, settings: Settings, session: ConversationSession, user_id: str) -> LiveSession:
    factory = session_factory()
    async with factory() as db:
        from sqlalchemy import select

        agent = (
            await db.execute(select(AgentSettings).where(AgentSettings.user_id == user_id))
        ).scalar_one_or_none()
    config = _build_config(settings, agent)
    store = SessionStore(factory, session.id)
    history = await store.load_history()
    tts = registry.tts_for(agent.selected_tts if agent else None)
    clock = SessionClock(active_seconds=session.active_duration or 0.0, paused_seconds=session.paused_duration or 0.0)
    vad = await asyncio.to_thread(registry.new_vad)
    recognizer, chat_model = registry.pipeline(agent.ai_engine if agent else None)
    engine = ConversationEngine(
        session_id=session.id,
        config=config,
        vad_model=vad,
        recognizer=recognizer,
        chat_model=chat_model,
        tts=tts,
        store=store,
        history=history,
        clock=clock,
    )
    if "kokoro" in registry.engines:
        engine.tts_by_language["ur"] = registry.engines["kokoro"]
    metadata = {"llm": getattr(chat_model, "name", config.llm_model), "stt": recognizer.name, "tts": tts.name}
    return LiveSession(engine, store, user_id, metadata, registry)


async def _expire_later(session_id: str, delay: float = RECONNECT_GRACE_SECONDS) -> None:
    await asyncio.sleep(delay)
    await hub.end(session_id)


@router.websocket("/ws/v1/voice")
async def voice_socket(ws: WebSocket) -> None:
    await ws.accept()
    settings = get_settings()
    registry = ws.app.state.registry

    try:
        raw = await asyncio.wait_for(ws.receive_text(), HANDSHAKE_TIMEOUT)
        start = StartMessage.model_validate_json(raw)
    except (asyncio.TimeoutError, ValidationError, WebSocketDisconnect, KeyError, json.JSONDecodeError):
        await _close(ws, CLOSE_UNAUTHORIZED, "Expected session.start")
        return

    factory = session_factory()
    async with factory() as db:
        user = await user_from_token(db, start.token)
        if user is None:
            await _close(ws, CLOSE_UNAUTHORIZED, "Invalid token")
            return
        user_id = user.id
        session = await db.get(ConversationSession, start.session_id)
        if session is None or session.user_id != user_id:
            await _close(ws, CLOSE_FORBIDDEN, "Session not found")
            return
        # One device owns the mic and speaker. Others must go through the handoff endpoint.
        if session.owner_device_id not in (None, start.device_id):
            existing = hub.get(session.id)
            if existing is not None and existing.socket is not None:
                await _close(ws, CLOSE_DEVICE_CONFLICT, "Session is active on another device")
                return
        session.owner_device_id = start.device_id
        session.status = "active"
        session.end_time = None
        await db.commit()

    if not registry.ready.is_set():
        await ws.send_text(json.dumps({"v": 1, "type": "session.loading", "models": registry.status}))
        await registry.ready.wait()
    missing = [k for k in ("vad", "stt", "tts") if registry.status[k]["state"] != "ready"]
    if missing:
        await ws.send_text(
            json.dumps({"v": 1, "type": "error", "code": "models_unavailable", "message": str(registry.status), "recoverable": False})
        )
        await _close(ws, CLOSE_NOT_READY, "Models unavailable")
        return

    live = hub.get(session.id)
    resumed = live is not None
    if live is None:
        live = await _create_live(registry, settings, session, user_id)
        hub.sessions[session.id] = live

    async with live.transition:
        if live.expiry:
            live.expiry.cancel()
            live.expiry = None
        if live.socket is not None:  # Same device reconnecting over a stale socket.
            stale, live.socket = live.socket, None
            await live.engine.detach()
            await _close(stale, CLOSE_REPLACED, "Replaced by a new connection")
        live.socket = ws
        live.device_id = start.device_id

    engine = live.engine

    async def send_json(payload: dict) -> None:
        async with live.send_lock:
            await ws.send_text(json.dumps(payload, default=str))

    async def send_bytes(payload: bytes) -> None:
        async with live.send_lock:
            await ws.send_bytes(payload)

    async def end_session() -> None:
        await hub.end(session.id)
        await engine.emit("session.ended", session_id=session.id)
        await _close(ws, 1000, "Session ended")

    engine.on_end = end_session
    engine.attach(send_json, send_bytes)

    await engine.emit(
        "session.started",
        session_id=session.id,
        resumed=resumed or bool(engine.history),
        protocol=1,
        models=live.metadata,
        attention_mode=engine.config.attention_mode.value,
        started_at=session.start_time,
        external_api_cost=0.0,
        metrics=engine.clock.snapshot(),
    )
    await engine.start_listening()

    try:
        await _receive_loop(ws, live, settings)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Voice socket failed")
    finally:
        await _on_disconnect(ws, live, session.id)


async def _receive_loop(ws: WebSocket, live: LiveSession, settings: Settings) -> None:
    engine = live.engine
    last_saved = time.monotonic()
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        if (data := message.get("bytes")) is not None:
            if len(data) <= MAX_AUDIO_PACKET:
                engine.feed_audio(data)
        elif (text := message.get("text")) is not None:
            try:
                await _handle_control(json.loads(text), live)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                await engine.emit("error", code="bad_message", message=str(exc), recoverable=True)
        if time.monotonic() - last_saved > 30:
            last_saved = time.monotonic()
            await live.store.save_metrics(engine.clock, live.metadata)


async def _handle_control(data: dict, live: LiveSession) -> None:
    engine = live.engine
    kind = data["type"]
    if kind == "playback.progress":
        engine.on_playback(str(data["response_id"]), int(data["chunk_seq"]), float(data["played_ms"]))
    elif kind == "session.pause":
        await engine.pause(listen_for_resume=bool(data.get("listen_for_resume", False)))
    elif kind == "session.resume":
        await engine.resume()
    elif kind == "session.configure":
        await _configure(data, live)
    elif kind == "session.end":
        if engine.on_end:
            await engine.on_end()
    elif kind == "ping":
        await engine.emit("pong", t=data.get("t"))
    else:
        await engine.emit("error", code="unknown_message", message=str(kind), recoverable=True)


async def _configure(data: dict, live: LiveSession) -> None:
    engine = live.engine
    changes = {k: data[k] for k in ("language", "voice", "llm_model", "personality", "wake_word") if data.get(k)}
    if mode := data.get("attention_mode"):
        changes["attention_mode"] = AttentionMode(mode)
    engine.update_config(**changes)
    # A voice belongs to one TTS engine, so choosing it can switch engine mid-session.
    if voice := data.get("voice"):
        registry = live.registry
        match = next((v for v in registry.voices() if v["id"] == voice), None)
        if match and match["engine"] in registry.engines:
            engine.tts = registry.engines[match["engine"]]
            live.metadata["tts"] = engine.tts.name
    if model := data.get("llm_model"):
        if engine.chat_model is live.registry.llm:
            live.metadata["llm"] = model
    if ai_engine := data.get("ai_engine"):
        engine.recognizer, engine.chat_model = live.registry.pipeline(ai_engine)
        live.metadata["stt"] = engine.recognizer.name
        live.metadata["llm"] = getattr(engine.chat_model, "name", None) or engine.config.llm_model
        changes["ai_engine"] = ai_engine
    await engine.emit("session.configured", changes=[*changes], models=live.metadata)


async def _on_disconnect(ws: WebSocket, live: LiveSession, session_id: str) -> None:
    async with live.transition:
        if live.socket is not ws:
            return  # A newer connection already took over.
        live.socket = None
        if live.engine.ended or hub.get(session_id) is None:
            return
        await live.engine.detach()
        try:
            await live.store.save_metrics(live.engine.clock, live.metadata)
        except Exception:
            log.exception("Could not save metrics for session %s", session_id)
        live.expiry = asyncio.create_task(_expire_later(session_id))


async def _close(ws: WebSocket, code: int, reason: str) -> None:
    try:
        await ws.close(code=code, reason=reason)
    except Exception:
        pass


__all__ = ["hub", "router", "State", "utcnow"]
