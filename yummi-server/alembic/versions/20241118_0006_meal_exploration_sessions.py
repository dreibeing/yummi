"""Add meal exploration sessions table.

Revision ID: 0b7a0db95c31
Revises: 2e9a7af81b44
Create Date: 2025-11-18 09:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0b7a0db95c31"
down_revision = "2e9a7af81b44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")
    op.create_table(
        "meal_exploration_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("manifest_id", sa.String(length=64), nullable=True),
        sa.Column("tags_version", sa.String(length=32), nullable=True),
        sa.Column("prompt_context", json_type, nullable=True),
        sa.Column("exploration_results", json_type, nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
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
        "ix_meal_exploration_sessions_user_id",
        "meal_exploration_sessions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_meal_exploration_sessions_user_id", table_name="meal_exploration_sessions")
    op.drop_table("meal_exploration_sessions")
