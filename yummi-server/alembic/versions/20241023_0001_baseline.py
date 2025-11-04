"""Baseline empty schema.

Revision ID: c94d1e359bc0
Revises:
Create Date: 2024-10-23 00:00:00.000000

"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "c94d1e359bc0"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Initial migration does not create tables; placeholder for future schema."""
    # Alembic will still stamp the database with this revision.
    pass


def downgrade() -> None:
    pass
