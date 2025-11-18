"""Store latest recommendation meal IDs on preference profiles.

Revision ID: 6a1f9a0c2d41
Revises: 0b7a0db95c31
Create Date: 2025-11-18 11:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "6a1f9a0c2d41"
down_revision = "0b7a0db95c31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")
    op.add_column(
        "user_preference_profiles",
        sa.Column("latest_recommendation_meal_ids", json_type, nullable=True),
    )
    op.add_column(
        "user_preference_profiles",
        sa.Column(
            "latest_recommendation_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "user_preference_profiles",
        sa.Column("latest_recommendation_manifest_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_preference_profiles", "latest_recommendation_manifest_id")
    op.drop_column("user_preference_profiles", "latest_recommendation_generated_at")
    op.drop_column("user_preference_profiles", "latest_recommendation_meal_ids")
