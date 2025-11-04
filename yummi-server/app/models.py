from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class TimestampMixin:
    """Common created/updated timestamp columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PaymentStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=PaymentStatus.PENDING)
    user_id: Mapped[Optional[str]] = mapped_column(String(128))
    user_email: Mapped[Optional[str]] = mapped_column(String(320))
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    item_description: Mapped[Optional[str]] = mapped_column(String(255))
    checkout_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    last_itn_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    pf_status: Mapped[Optional[str]] = mapped_column(String(32))

    def __repr__(self) -> str:
        return (
            f"Payment(id={self.id}, provider={self.provider}, "
            f"provider_reference={self.provider_reference}, status={self.status})"
        )
