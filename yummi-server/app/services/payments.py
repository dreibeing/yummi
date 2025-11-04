from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Payment, PaymentStatus, WalletTransaction

logger = logging.getLogger(__name__)


def _map_payfast_status(status: Optional[str]) -> str:
    status_upper = (status or "").upper()
    if status_upper == "COMPLETE":
        return PaymentStatus.COMPLETE
    if status_upper in {"CANCELLED", "CANCELED"}:
        return PaymentStatus.CANCELLED
    if status_upper == "FAILED":
        return PaymentStatus.FAILED
    if status_upper == "PENDING":
        return PaymentStatus.PENDING
    return PaymentStatus.PROCESSING


async def create_payfast_payment(
    session: AsyncSession,
    *,
    reference: str,
    user_id: Optional[str],
    user_email: Optional[str],
    amount_minor: int,
    currency: str,
    item_name: str,
    item_description: Optional[str],
    checkout_payload: Dict[str, Any],
) -> Payment:
    payment = Payment(
        provider="payfast",
        provider_reference=reference,
        user_id=user_id,
        user_email=user_email,
        amount_minor=amount_minor,
        currency=currency.upper(),
        item_name=item_name,
        item_description=item_description,
        status=PaymentStatus.PENDING,
        checkout_payload=checkout_payload,
    )
    session.add(payment)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await get_payment_by_reference(session, reference)
        if existing:
            return existing
        raise
    await session.refresh(payment)
    return payment


async def update_payfast_payment_from_itn(
    session: AsyncSession,
    payload: Dict[str, str],
) -> Optional[Payment]:
    reference = payload.get("custom_str2")
    if not reference:
        logger.warning("PayFast ITN missing custom_str2 reference", extra={"payload": payload})
        return None

    payment = await get_payment_by_reference(session, reference)
    if not payment:
        logger.warning("PayFast ITN received for unknown reference", extra={"reference": reference})
        return None

    pf_payment_id = payload.get("pf_payment_id") or payload.get("m_payment_id")
    payment.provider_payment_id = pf_payment_id
    payment.last_itn_payload = payload
    payment.pf_status = payload.get("payment_status")
    payment.status = _map_payfast_status(payload.get("payment_status"))

    try:
        await session.commit()
    except Exception:  # pragma: no cover
        await session.rollback()
        raise
    await session.refresh(payment)

    if payment.status == PaymentStatus.COMPLETE:
        await ensure_wallet_credit_for_payment(session, payment)

    return payment


async def get_payment_by_reference(session: AsyncSession, reference: str) -> Optional[Payment]:
    result = await session.execute(
        select(Payment).where(Payment.provider_reference == reference)
    )
    return result.scalar_one_or_none()


async def ensure_wallet_credit_for_payment(
    session: AsyncSession, payment: Payment
) -> Optional[WalletTransaction]:
    if payment.status != PaymentStatus.COMPLETE or not payment.user_id:
        return None

    result = await session.execute(
        select(WalletTransaction).where(WalletTransaction.payment_id == payment.id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    txn = WalletTransaction(
        user_id=payment.user_id,
        user_email=payment.user_email,
        payment_id=payment.id,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        entry_type="credit",
        note=f"PayFast payment {payment.provider_reference}",
    )
    session.add(txn)
    try:
        await session.commit()
    except Exception:  # pragma: no cover
        await session.rollback()
        raise
    await session.refresh(txn)
    return txn


async def get_user_wallet_summary(
    session: AsyncSession, user_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    result = await session.execute(
        select(WalletTransaction).where(WalletTransaction.user_id == user_id)
    )
    transactions = result.scalars().all()
    if not transactions:
        return {
            "userId": user_id,
            "balanceMinor": 0,
            "currency": "ZAR",
            "transactions": [],
        }
    balance = sum(txn.amount_minor if txn.entry_type == "credit" else -txn.amount_minor for txn in transactions)
    currency = transactions[0].currency if transactions else "ZAR"
    return {
        "userId": user_id,
        "balanceMinor": balance,
        "currency": currency,
        "transactions": [
            {
                "id": str(txn.id),
                "amountMinor": txn.amount_minor,
                "currency": txn.currency,
                "entryType": txn.entry_type,
                "note": txn.note,
                "createdAt": txn.created_at.isoformat(),
                "paymentId": str(txn.payment_id),
            }
            for txn in sorted(transactions, key=lambda t: t.created_at, reverse=True)
        ],
    }
