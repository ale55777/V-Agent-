"""Initial schema.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "agent_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("selected_llm", sa.String(120)),
        sa.Column("selected_stt", sa.String(60)),
        sa.Column("selected_tts", sa.String(30)),
        sa.Column("selected_voice", sa.String(80)),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("vad_sensitivity", sa.Float(), nullable=False),
        sa.Column("attention_mode", sa.String(20), nullable=False),
        sa.Column("personality", sa.Text(), nullable=False),
        sa.Column("wake_word", sa.String(40), nullable=False),
        sa.Column("retention_days", sa.Integer()),
    )

    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("active_duration", sa.Float(), nullable=False),
        sa.Column("paused_duration", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
        sa.Column("owner_device_id", sa.String(80)),
    )
    op.create_index("ix_sessions_user_start", "conversation_sessions", ["user_id", "start_time"])
    op.create_index("ix_conversation_sessions_status", "conversation_sessions", ["status"])

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("speaker", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("interrupted", sa.Boolean(), nullable=False),
        sa.Column("response_id", sa.String(36)),
        sa.UniqueConstraint("session_id", "sequence_number", name="uq_message_sequence"),
    )
    op.create_index("ix_messages_session_seq", "conversation_messages", ["session_id", "sequence_number"])

    op.create_table(
        "session_metrics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("average_latency", sa.Float()),
        sa.Column("minimum_latency", sa.Float()),
        sa.Column("maximum_latency", sa.Float()),
        sa.Column("average_transcription_latency", sa.Float()),
        sa.Column("average_llm_first_token", sa.Float()),
        sa.Column("average_interruption_latency", sa.Float()),
        sa.Column("interruption_count", sa.Integer(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("user_speaking_duration", sa.Float(), nullable=False),
        sa.Column("ai_speaking_duration", sa.Float(), nullable=False),
        sa.Column("model_metadata", sa.JSON(), nullable=False),
        sa.Column("turns", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("session_metrics")
    op.drop_table("conversation_messages")
    op.drop_table("conversation_sessions")
    op.drop_table("agent_settings")
    op.drop_table("users")
