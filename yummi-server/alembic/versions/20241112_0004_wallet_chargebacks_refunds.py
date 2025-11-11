"""Add wallet account state + richer transaction metadata.

Revision ID: c6f9ba7a1e4b
Revises: 5f8e0f7e8c24
Create Date: 2025-11-12 12:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c6f9ba7a1e4b"
down_revision = "5f8e0f7e8c24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

    op.add_column(
        "wallet_transactions",
        sa.Column("transaction_type", sa.String(length=32), nullable=False, server_default="top_up"),
    )
    op.add_column("wallet_transactions", sa.Column("external_reference", sa.String(length=128), nullable=True))
    op.add_column("wallet_transactions", sa.Column("initiated_by", sa.String(length=320), nullable=True))
    op.add_column("wallet_transactions", sa.Column("context", json_type, nullable=True))
    op.alter_column(
        "wallet_transactions",
        "payment_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.drop_index("ix_wallet_transactions_payment_id", table_name="wallet_transactions")
    op.create_index("ix_wallet_transactions_payment_id", "wallet_transactions", ["payment_id"])

    op.create_table(
        "wallet_account_states",
        sa.Column("user_id", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("spend_blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("lock_reason", sa.String(length=64), nullable=True),
        sa.Column("lock_note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("wallet_account_states")
    op.drop_index("ix_wallet_transactions_payment_id", table_name="wallet_transactions")
    op.create_index(
        "ix_wallet_transactions_payment_id",
        "wallet_transactions",
        ["payment_id"],
        unique=True,
    )
    op.alter_column(
        "wallet_transactions",
        "payment_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("wallet_transactions", "context")
    op.drop_column("wallet_transactions", "initiated_by")
    op.drop_column("wallet_transactions", "external_reference")
    op.drop_column("wallet_transactions", "transaction_type")
