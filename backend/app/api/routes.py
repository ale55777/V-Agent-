"""REST API. Everything under /api/v1 except auth and health needs a bearer token."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.settings import get_settings
from app.core.security import (
    create_access_token,
    current_user,
    hash_password,
    login_throttle,
    verify_password,
)
from app.database.session import get_db
from app.models.entities import AgentSettings, ConversationMessage, ConversationSession, User
from app.schemas.api import (
    AgentSettingsBody,
    Credentials,
    HandoffRequest,
    SessionCreate,
    SessionRename,
    SessionSummary,
    TokenResponse,
)
from app.services.history.report import build_report, export_json, export_pdf, export_txt, safe_filename
from app.services.history.store import message_to_dict, purge_expired
from app.websocket.voice import hub

router = APIRouter(prefix="/api/v1")


# ---- health --------------------------------------------------------------------------


@router.get("/health")
async def health(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    registry = request.app.state.registry
    try:
        await db.execute(text("SELECT 1"))
        database = {"state": "ready"}
    except Exception as exc:
        database = {"state": "failed", "detail": type(exc).__name__}
    try:
        models = await registry.llm.list_models()
        ollama = {"state": "ready", "models": [m["name"] for m in models]}
    except Exception as exc:
        ollama = {"state": "failed", "detail": type(exc).__name__}
    models_status = registry.status
    voice_ready = all(models_status[k]["state"] == "ready" for k in ("vad", "stt", "tts", "llm"))
    return {
        "status": "ok" if database["state"] == "ready" and voice_ready else "degraded",
        "database": database,
        "ollama": ollama,
        "models": models_status,
        "loading": not registry.ready.is_set(),
    }


# ---- auth ----------------------------------------------------------------------------


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: Credentials, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    if not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(email=body.email, password_hash=hash_password(body.password))
    user.settings = AgentSettings()
    db.add(user)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user.id), user_id=user.id, email=user.email)


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: Credentials, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    key = f"{request.client.host if request.client else '?'}:{body.email}"
    if login_throttle.blocked(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a few minutes.")
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        login_throttle.record_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    login_throttle.reset(key)
    return TokenResponse(access_token=create_access_token(user.id), user_id=user.id, email=user.email)


@router.get("/auth/me")
async def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "email": user.email, "created_at": user.created_at}


# ---- agent configuration and models --------------------------------------------------


async def _agent_settings(db: AsyncSession, user: User) -> AgentSettings:
    agent = (await db.execute(select(AgentSettings).where(AgentSettings.user_id == user.id))).scalar_one_or_none()
    if agent is None:
        agent = AgentSettings(user_id=user.id)
        db.add(agent)
        await db.commit()
    return agent


def _agent_dict(agent: AgentSettings) -> dict:
    return AgentSettingsBody.model_construct(
        selected_llm=agent.selected_llm,
        selected_stt=agent.selected_stt,
        selected_tts=agent.selected_tts,
        selected_voice=agent.selected_voice,
        language=agent.language,
        vad_sensitivity=agent.vad_sensitivity,
        attention_mode=agent.attention_mode,
        personality=agent.personality,
        wake_word=agent.wake_word,
        retention_days=agent.retention_days,
        ai_engine=agent.ai_engine or "local",
    ).model_dump()


@router.get("/agent/settings")
async def get_agent_settings(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return _agent_dict(await _agent_settings(db, user))


@router.put("/agent/settings")
async def put_agent_settings(
    body: AgentSettingsBody, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    agent = await _agent_settings(db, user)
    for key, value in body.model_dump().items():
        setattr(agent, key, value)
    await db.commit()
    if body.retention_days:
        await purge_expired(db, user.id, body.retention_days)
    return _agent_dict(agent)


@router.get("/models")
async def available_models(request: Request, _: User = Depends(current_user)) -> dict:
    registry = request.app.state.registry
    settings = get_settings()
    try:
        llm = await registry.llm.list_models()
        llm_error = None
    except Exception as exc:
        llm, llm_error = [], str(exc)
    engine = registry.default_tts()
    return {
        "llm": llm,
        "llm_error": llm_error,
        "default_llm": settings.llm_model,
        "stt": {"active": settings.stt_model, "options": ["tiny", "base", "small"]},
        "tts": {"active": engine.name if engine else None, "engines": list(registry.engines)},
        "status": registry.status,
        "groq_available": getattr(registry, "groq_available", False),
        "groq_models": (
            {"stt": settings.groq_stt_model, "llm": settings.groq_llm_model}
            if getattr(registry, "groq_available", False) else None
        ),
    }


@router.get("/voices")
async def available_voices(request: Request, _: User = Depends(current_user)) -> dict:
    return {"voices": request.app.state.registry.voices()}


# ---- sessions ------------------------------------------------------------------------


async def _owned_session(db: AsyncSession, session_id: str, user: User, full: bool = False) -> ConversationSession:
    query = select(ConversationSession).where(
        ConversationSession.id == session_id, ConversationSession.user_id == user.id
    )
    if full:
        query = query.options(
            selectinload(ConversationSession.messages), selectinload(ConversationSession.metrics)
        )
    session = (await db.execute(query)).scalar_one_or_none()
    if session is None:
        # The same 404 for "missing" and "someone else's", so ids cannot be probed.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    session = ConversationSession(user_id=user.id, title=body.title or "New conversation")
    db.add(session)
    await db.commit()
    return {"id": session.id, "title": session.title, "start_time": session.start_time, "status": session.status}


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionSummary]:
    counts = (
        select(ConversationMessage.session_id, func.count().label("n"))
        .group_by(ConversationMessage.session_id)
        .subquery()
    )
    query = (
        select(ConversationSession, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.session_id == ConversationSession.id)
        .where(ConversationSession.user_id == user.id)
        .order_by(ConversationSession.start_time.desc())
        .limit(limit)
        .offset(offset)
    )
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        matches = exists().where(
            ConversationMessage.session_id == ConversationSession.id,
            ConversationMessage.content.ilike(like, escape="\\"),
        )
        query = query.where(or_(ConversationSession.title.ilike(like, escape="\\"), matches))
    rows = (await db.execute(query)).all()
    return [
        SessionSummary(
            id=s.id, title=s.title, status=s.status, start_time=s.start_time, end_time=s.end_time,
            duration=s.duration, active_duration=s.active_duration, paused_duration=s.paused_duration,
            estimated_cost=s.estimated_cost, owner_device_id=s.owner_device_id, message_count=count,
        )
        for s, count in rows
    ]


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    session = await _owned_session(db, session_id, user, full=True)
    return {
        "id": session.id,
        "title": session.title,
        "status": session.status,
        "start_time": session.start_time,
        "end_time": session.end_time,
        "duration": session.duration,
        "active_duration": session.active_duration,
        "paused_duration": session.paused_duration,
        "owner_device_id": session.owner_device_id,
        "messages": [message_to_dict(m) for m in session.messages],
    }


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    session = await _owned_session(db, session_id, user, full=True)
    return [message_to_dict(m) for m in session.messages]


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str, body: SessionRename, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    session = await _owned_session(db, session_id, user)
    session.title = body.title.strip()
    await db.commit()
    return {"id": session.id, "title": session.title}


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> Response:
    session = await _owned_session(db, session_id, user)
    await hub.end(session_id)
    await db.delete(session)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/{session_id}/end")
async def end_session(session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    session = await _owned_session(db, session_id, user)
    await hub.end(session_id)
    if session.status != "ended":
        await db.refresh(session)
        if session.status != "ended":  # Never connected, so the hub had nothing to close.
            from app.models.entities import utcnow

            session.status = "ended"
            session.end_time = utcnow()
            await db.commit()
    return {"id": session.id, "status": "ended"}


@router.post("/sessions/{session_id}/handoff")
async def handoff(
    session_id: str, body: HandoffRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """Move ownership to a new device. The old audio link is closed, not transferred;
    the new device opens its own socket and carries on with the saved context."""
    session = await _owned_session(db, session_id, user)
    previous = session.owner_device_id
    if previous != body.device_id:
        await hub.force_disconnect(session_id, body.device_id)
    session.owner_device_id = body.device_id
    session.status = "active"
    session.end_time = None
    await db.commit()
    return {"id": session.id, "owner_device_id": body.device_id, "previous_device_id": previous}


@router.get("/sessions/{session_id}/report")
async def session_report(
    session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    session = await _owned_session(db, session_id, user, full=True)
    return build_report(session, session.metrics, get_settings())


@router.get("/sessions/{session_id}/export/{fmt}")
async def export_session(
    session_id: str,
    fmt: Literal["txt", "json", "pdf"],
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    session = await _owned_session(db, session_id, user, full=True)
    report = build_report(session, session.metrics, get_settings())
    filename = safe_filename(session.title, fmt)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"}
    if fmt == "txt":
        return Response(export_txt(report), media_type="text/plain; charset=utf-8", headers=headers)
    if fmt == "json":
        return Response(export_json(report), media_type="application/json", headers=headers)
    return Response(export_pdf(report), media_type="application/pdf", headers=headers)
