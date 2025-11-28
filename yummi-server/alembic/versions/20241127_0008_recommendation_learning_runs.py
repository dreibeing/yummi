"""Add recommendation learning runs table.

Revision ID: 8b1f2db44063
Revises: 6a1f9a0c2d41
Create Date: 2025-11-27 17:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "8b1f2db44063"
down_revision = "6a1f9a0c2d41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")
    op.create_table(
        "recommendation_learning_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("trigger_event", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("event_context", json_type, nullable=True),
        sa.Column("usage_snapshot", json_type, nullable=True),
        sa.Column("prompt_payload", json_type, nullable=True),
        sa.Column("response_payload", json_type, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_recommendation_learning_runs_user_id",
        "recommendation_learning_runs",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_recommendation_learning_runs_user_id", table_name="recommendation_learning_runs")
    op.drop_table("recommendation_learning_runs")
