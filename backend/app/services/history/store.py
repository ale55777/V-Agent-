"""Database access for conversations. Messages are saved one at a time as they are
finalised, so an unexpected disconnect loses at most the turn in progress."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import ConversationMessage, ConversationSession, SessionMetrics, utcnow
from app.services.conversation.turns import title_from
from app.services.metrics.tracker import SessionClock


def message_to_dict(message: ConversationMessage) -> dict:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "speaker": message.speaker,
        "content": message.content,
        "timestamp": _iso(message.timestamp),
        "sequence_number": message.sequence_number,
        "status": message.status,
        "interrupted": message.interrupted,
        "response_id": message.response_id,
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class SessionStore:
    """Implements the engine's Store protocol for one conversation session."""

    def __init__(self, factory: async_sessionmaker[AsyncSession], session_id: str) -> None:
        self.factory = factory
        self.session_id = session_id

    async def save_message(self, speaker: str, content: str, status: str, response_id: str | None) -> dict:
        async with self.factory() as db:
            session = await db.get(ConversationSession, self.session_id)
            next_seq = (
                await db.execute(
                    select(func.coalesce(func.max(ConversationMessage.sequence_number), 0)).where(
                        ConversationMessage.session_id == self.session_id
                    )
                )
            ).scalar_one() + 1
            message = ConversationMessage(
                session_id=self.session_id,
                speaker=speaker,
                content=content,
                sequence_number=next_seq,
                status=status,
                interrupted=status == "interrupted",
                response_id=response_id,
            )
            db.add(message)
            if session is not None and speaker == "user" and session.title == "New conversation":
                session.title = title_from(content)
            await db.commit()
            return message_to_dict(message)

    async def load_history(self) -> list[dict]:
        async with self.factory() as db:
            rows = (
                await db.execute(
                    select(ConversationMessage)
                    .where(ConversationMessage.session_id == self.session_id)
                    .order_by(ConversationMessage.sequence_number)
                )
            ).scalars()
            return [
                {"role": "user" if m.speaker == "user" else "assistant", "content": m.content} for m in rows
            ]

    async def save_metrics(self, clock: SessionClock, model_metadata: dict, end: bool = False) -> None:
        snapshot = clock.snapshot()
        async with self.factory() as db:
            session = await db.get(ConversationSession, self.session_id)
            if session is None:
                return
            metrics = (
                await db.execute(select(SessionMetrics).where(SessionMetrics.session_id == self.session_id))
            ).scalar_one_or_none()
            if metrics is None:
                metrics = SessionMetrics(session_id=self.session_id)
                db.add(metrics)
            metrics.average_latency = snapshot["average_latency_ms"]
            metrics.minimum_latency = snapshot["minimum_latency_ms"]
            metrics.maximum_latency = snapshot["maximum_latency_ms"]
            metrics.average_transcription_latency = snapshot["average_transcription_ms"]
            metrics.average_llm_first_token = snapshot["average_llm_first_token_ms"]
            metrics.average_interruption_latency = snapshot["average_interruption_ms"]
            metrics.interruption_count = snapshot["interruption_count"]
            metrics.turn_count = snapshot["turn_count"]
            metrics.user_speaking_duration = snapshot["user_speaking_seconds"]
            metrics.ai_speaking_duration = snapshot["ai_speaking_seconds"]
            metrics.model_metadata = model_metadata
            metrics.turns = clock.turns
            session.active_duration = snapshot["active_seconds"]
            session.paused_duration = snapshot["paused_seconds"]
            if end:
                session.end_time = utcnow()
                session.status = "ended"
                # Active + paused, so gaps before "continue conversation" do not count.
                session.duration = snapshot["active_seconds"] + snapshot["paused_seconds"]
                session.owner_device_id = None
            await db.commit()


async def purge_expired(db: AsyncSession, user_id: str, retention_days: int) -> int:
    """Apply a user's optional data retention policy."""
    from datetime import timedelta

    cutoff = utcnow() - timedelta(days=retention_days)
    result = await db.execute(
        delete(ConversationSession).where(
            ConversationSession.user_id == user_id,
            ConversationSession.start_time < cutoff,
            ConversationSession.status != "active",
        )
    )
    await db.commit()
    return result.rowcount or 0
