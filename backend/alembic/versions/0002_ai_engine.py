"""Add the per-user AI engine choice (local or Groq).

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_settings",
        sa.Column("ai_engine", sa.String(10), nullable=False, server_default="local"),
    )


def downgrade() -> None:
    op.drop_column("agent_settings", "ai_engine")
