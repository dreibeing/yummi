"""Add meal feedback events table.

Revision ID: 1f3b5a4a9c22
Revises: 8b1f2db44063
Create Date: 2025-11-27 18:10:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "1f3b5a4a9c22"
down_revision = "8b1f2db44063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")
    op.create_table(
        "meal_feedback_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("meal_id", sa.String(length=64), nullable=False),
        sa.Column("reaction", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("context", json_type, nullable=True),
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
    op.create_index("ix_meal_feedback_events_user_id", "meal_feedback_events", ["user_id"])
    op.create_index("ix_meal_feedback_events_meal_id", "meal_feedback_events", ["meal_id"])


def downgrade() -> None:
    op.drop_index("ix_meal_feedback_events_meal_id", table_name="meal_feedback_events")
    op.drop_index("ix_meal_feedback_events_user_id", table_name="meal_feedback_events")
    op.drop_table("meal_feedback_events")
