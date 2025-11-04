"""Add payments table for PayFast integration.

Revision ID: 3c0e3d7e9b2f
Revises: c94d1e359bc0
Create Date: 2025-11-04 13:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "3c0e3d7e9b2f"
down_revision = "c94d1e359bc0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_reference", sa.String(length=128), nullable=False, unique=True),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("user_email", sa.String(length=320), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=False),
        sa.Column("item_description", sa.String(length=255), nullable=True),
        sa.Column("checkout_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_itn_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pf_status", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index(
        "ix_payments_provider_reference",
        "payments",
        ["provider_reference"],
        unique=True,
    )
    op.create_index(
        "ix_payments_status",
        "payments",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_provider_reference", table_name="payments")
    op.drop_table("payments")
