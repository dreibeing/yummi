"""Add user preference profiles table.

Revision ID: 2e9a7af81b44
Revises: c6f9ba7a1e4b
Create Date: 2025-11-14 10:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "2e9a7af81b44"
down_revision = "c6f9ba7a1e4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")
    op.create_table(
        "user_preference_profiles",
        sa.Column("user_id", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("tags_version", sa.String(length=32), nullable=True),
        sa.Column("responses", json_type, nullable=False),
        sa.Column("selected_tags", json_type, nullable=False),
        sa.Column("disliked_tags", json_type, nullable=False),
        sa.Column(
            "completion_stage",
            sa.String(length=32),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_preference_profiles")
