from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select, and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Payment, PaymentStatus, WalletTransaction, WalletAccountState

logger = logging.getLogger(__name__)

REFUND_WINDOW_DAYS = 90
REFUND_LIMIT_PER_WINDOW = 3
CHARGEBACK_WINDOW_DAYS = 90
CHARGEBACK_LIMIT_PER_WINDOW = 2


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _create_wallet_transaction(
    session: AsyncSession,
    *,
    user_id: str,
    user_email: Optional[str],
    payment_id: Optional[str],
    amount_minor: int,
    currency: str,
    entry_type: str,
    transaction_type: str,
    note: Optional[str] = None,
    external_reference: Optional[str] = None,
    initiated_by: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> WalletTransaction:
    txn = WalletTransaction(
        user_id=user_id,
        user_email=user_email,
        payment_id=payment_id,
        amount_minor=amount_minor,
        currency=currency,
        entry_type=entry_type,
        transaction_type=transaction_type,
        note=note,
        external_reference=external_reference,
        initiated_by=initiated_by,
        context=context,
    )
    session.add(txn)
    try:
        await session.commit()
    except Exception:  # pragma: no cover
        await session.rollback()
        raise
    await session.refresh(txn)
    return txn


async def _get_account_state(session: AsyncSession, user_id: Optional[str]) -> Optional[WalletAccountState]:
    if not user_id:
        return None
    return await session.get(WalletAccountState, user_id)


async def _upsert_account_state(
    session: AsyncSession,
    user_id: str,
    *,
    spend_blocked: Optional[bool] = None,
    lock_reason: Optional[str] = None,
    lock_note: Optional[str] = None,
) -> WalletAccountState:
    state = await session.get(WalletAccountState, user_id)
    if state is None:
        state = WalletAccountState(user_id=user_id)
        session.add(state)

    if spend_blocked is not None:
        state.spend_blocked = spend_blocked
    if spend_blocked is False:
        state.lock_reason = None
        state.lock_note = None
    else:
        if lock_reason is not None:
            state.lock_reason = lock_reason
        if lock_note is not None:
            state.lock_note = lock_note

    try:
        await session.commit()
    except Exception:  # pragma: no cover
        await session.rollback()
        raise
    await session.refresh(state)
    return state


async def _recent_transaction_count(
    session: AsyncSession,
    *,
    user_id: str,
    transaction_type: str,
    window_days: int,
) -> int:
    cutoff = _utcnow() - timedelta(days=window_days)
    result = await session.execute(
        select(func.count()).where(
            WalletTransaction.user_id == user_id,
            WalletTransaction.transaction_type == transaction_type,
            WalletTransaction.created_at >= cutoff,
        )
    )
    return int(result.scalar_one() or 0)


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

    await sync_wallet_transactions_for_payment(session, payment, itn_payload=payload)
    return payment


async def get_payment_by_reference(session: AsyncSession, reference: str) -> Optional[Payment]:
    result = await session.execute(
        select(Payment).where(Payment.provider_reference == reference)
    )
    return result.scalar_one_or_none()


async def get_payfast_status_details(
    session: AsyncSession,
    reference: str,
    *,
    expected_user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    payment = await get_payment_by_reference(session, reference)
    if not payment:
        return None
    if expected_user_id and payment.user_id and payment.user_id != expected_user_id:
        raise PermissionError("Payment does not belong to the requesting user")

    credit_query = await session.execute(
        select(WalletTransaction).where(
            and_(
                WalletTransaction.payment_id == payment.id,
                WalletTransaction.entry_type == "credit",
            )
        )
    )
    credit_entry = credit_query.scalar_one_or_none()
    wallet_credited = credit_entry is not None

    if payment.status in {PaymentStatus.PENDING, PaymentStatus.PROCESSING}:
        message = "Waiting for PayFast confirmation"
    elif payment.status == PaymentStatus.COMPLETE and wallet_credited:
        message = "Wallet credited"
    elif payment.status == PaymentStatus.CANCELLED:
        message = "Payment cancelled"
    elif payment.status == PaymentStatus.FAILED:
        message = "Payment failed"
    else:
        message = f"PayFast status: {payment.pf_status}" if payment.pf_status else None

    return {
        "reference": reference,
        "status": payment.status,
        "pf_status": payment.pf_status,
        "provider_payment_id": payment.provider_payment_id,
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "wallet_credited": wallet_credited,
        "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
        "message": message,
    }


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

    return await _create_wallet_transaction(
        session,
        user_id=payment.user_id,
        user_email=payment.user_email,
        payment_id=payment.id,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        entry_type="credit",
        transaction_type="top_up",
        note=f"PayFast payment {payment.provider_reference}",
        external_reference=payment.provider_payment_id or payment.provider_reference,
        initiated_by=payment.user_email,
        context={"provider": payment.provider},
    )


async def ensure_wallet_debit_for_payment(
    session: AsyncSession,
    payment: Payment,
    *,
    amount_minor: Optional[int] = None,
    reason: Optional[str] = None,
    transaction_type: str = "chargeback",
    external_reference: Optional[str] = None,
    initiated_by: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[WalletTransaction]:
    """Record a debit against the wallet when a chargeback/refund occurs.

    The debit is only created if the payment has previously produced a credit entry.
    """
    if not payment.user_id:
        return None

    existing_debit = await session.execute(
        select(WalletTransaction).where(
            and_(
                WalletTransaction.payment_id == payment.id,
                WalletTransaction.entry_type == "debit",
            )
        )
    )
    debit_txn = existing_debit.scalar_one_or_none()
    if debit_txn:
        return debit_txn

    credit_exists = await session.execute(
        select(WalletTransaction).where(
            and_(
                WalletTransaction.payment_id == payment.id,
                WalletTransaction.entry_type == "credit",
            )
        )
    )
    if credit_exists.scalar_one_or_none() is None:
        # Nothing to reverse; skip creating a debit entry.
        return None

    amt = amount_minor if amount_minor is not None else payment.amount_minor
    note_text = reason or f"Chargeback for {payment.provider_reference}"
    return await _create_wallet_transaction(
        session,
        user_id=payment.user_id,
        user_email=payment.user_email,
        payment_id=payment.id,
        amount_minor=amt,
        currency=payment.currency,
        entry_type="debit",
        transaction_type=transaction_type,
        note=note_text,
        external_reference=external_reference or payment.provider_payment_id,
        initiated_by=initiated_by,
        context=context,
    )


async def sync_wallet_transactions_for_payment(
    session: AsyncSession,
    payment: Payment,
    *,
    itn_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Ensure wallet ledger mirrors the current payment status."""
    if payment.status == PaymentStatus.COMPLETE:
        await ensure_wallet_credit_for_payment(session, payment)
        return

    if payment.status in {PaymentStatus.CANCELLED, PaymentStatus.FAILED}:
        reason = None
        if itn_payload:
            status = itn_payload.get("payment_status") or payment.pf_status or payment.status
            reason = f"Chargeback ({status})"
        await ensure_wallet_debit_for_payment(
            session,
            payment,
            reason=reason,
            transaction_type="chargeback",
            external_reference=itn_payload.get("pf_payment_id") if itn_payload else payment.provider_payment_id,
            context={"itn": itn_payload} if itn_payload else None,
        )


async def get_user_wallet_summary(
    session: AsyncSession, user_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    result = await session.execute(
        select(WalletTransaction).where(WalletTransaction.user_id == user_id)
    )
    transactions = result.scalars().all()
    account_state = await _get_account_state(session, user_id)

    if not transactions:
        return {
            "userId": user_id,
            "balanceMinor": 0,
            "currency": "ZAR",
            "transactions": [],
            "spendableMinor": 0,
            "spendBlocked": bool(account_state.spend_blocked) if account_state else False,
            "lockReason": account_state.lock_reason if account_state else None,
            "lockNote": account_state.lock_note if account_state else None,
        }

    balance = 0
    currency = "ZAR"
    for txn in transactions:
        currency = txn.currency or currency
        if txn.entry_type == "credit":
            balance += txn.amount_minor
        else:
            balance -= txn.amount_minor

    is_negative = balance < 0
    spend_blocked = is_negative or (account_state.spend_blocked if account_state else False)
    lock_reason = "negative_balance" if is_negative else (account_state.lock_reason if account_state else None)
    lock_note = None
    if account_state and account_state.spend_blocked and not is_negative:
        lock_note = account_state.lock_note
    spendable_minor = max(balance, 0)

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
                "transactionType": txn.transaction_type,
                "note": txn.note,
                "createdAt": txn.created_at.isoformat(),
                "paymentId": str(txn.payment_id) if txn.payment_id else None,
                "externalReference": txn.external_reference,
                "initiatedBy": txn.initiated_by,
                "context": txn.context,
            }
            for txn in sorted(transactions, key=lambda t: t.created_at, reverse=True)
        ],
        "spendableMinor": spendable_minor,
        "spendBlocked": spend_blocked,
        "lockReason": lock_reason,
        "lockNote": lock_note,
    }


async def record_chargeback(
    session: AsyncSession,
    *,
    reference: str,
    amount_minor: Optional[int],
    note: Optional[str],
    external_reference: Optional[str],
    actor_email: Optional[str],
) -> Dict[str, Any]:
    payment = await get_payment_by_reference(session, reference)
    if not payment:
        raise ValueError("Payment not found")

    txn = await ensure_wallet_debit_for_payment(
        session,
        payment,
        amount_minor=amount_minor,
        reason=note or f"Chargeback for {reference}",
        transaction_type="chargeback",
        external_reference=external_reference,
        initiated_by=actor_email,
        context={"status": "recorded", "recordedBy": actor_email},
    )

    await _evaluate_chargeback_flags(session, payment.user_id)
    summary = await get_user_wallet_summary(session, payment.user_id)

    balance = summary["balanceMinor"] if summary else 0
    spend_blocked = summary["spendBlocked"] if summary else False
    lock_reason = summary.get("lockReason") if summary else None
    lock_note = summary.get("lockNote") if summary else None

    return {
        "paymentReference": reference,
        "debitTransactionId": str(txn.id) if txn else None,
        "balanceMinor": balance,
        "spendBlocked": spend_blocked,
        "lockReason": lock_reason,
        "lockNote": lock_note,
    }


async def _evaluate_chargeback_flags(session: AsyncSession, user_id: Optional[str]) -> None:
    if not user_id:
        return
    count = await _recent_transaction_count(
        session,
        user_id=user_id,
        transaction_type="chargeback",
        window_days=CHARGEBACK_WINDOW_DAYS,
    )
    if count >= CHARGEBACK_LIMIT_PER_WINDOW:
        await _upsert_account_state(
            session,
            user_id,
            spend_blocked=True,
            lock_reason="review",
            lock_note=f"{count} chargebacks in {CHARGEBACK_WINDOW_DAYS} days",
        )


async def request_wallet_refund(
    session: AsyncSession,
    *,
    user_id: str,
    user_email: Optional[str],
    amount_minor: int,
    reason: Optional[str],
    actor_email: Optional[str],
) -> Dict[str, Any]:
    summary = await get_user_wallet_summary(session, user_id)
    if summary is None:
        raise ValueError("Wallet not found")
    if summary["spendBlocked"]:
        raise ValueError("Wallet is locked; please add funds")
    if amount_minor > summary["spendableMinor"]:
        raise ValueError("Refund exceeds spendable balance")

    recent_refunds = await _recent_transaction_count(
        session,
        user_id=user_id,
        transaction_type="refund",
        window_days=REFUND_WINDOW_DAYS,
    )
    if recent_refunds >= REFUND_LIMIT_PER_WINDOW:
        await _upsert_account_state(
            session,
            user_id,
            spend_blocked=True,
            lock_reason="review",
            lock_note=f"Exceeded refund limit ({REFUND_LIMIT_PER_WINDOW} in {REFUND_WINDOW_DAYS} days)",
        )
        raise ValueError("Refund limit exceeded")

    txn = await _create_wallet_transaction(
        session,
        user_id=user_id,
        user_email=user_email,
        payment_id=None,
        amount_minor=amount_minor,
        currency=summary["currency"],
        entry_type="debit",
        transaction_type="refund",
        note=reason or "User requested refund",
        initiated_by=actor_email or user_email,
        context={
            "status": "pending",
            "requestedAt": _utcnow().isoformat(),
            "requestedBy": actor_email or user_email,
        },
    )

    updated = await get_user_wallet_summary(session, user_id)
    return {
        "transaction": txn,
        "summary": updated,
    }


async def get_wallet_transaction(session: AsyncSession, transaction_id: str) -> Optional[WalletTransaction]:
    try:
        txn_id = uuid.UUID(transaction_id)
    except ValueError:
        return None
    return await session.get(WalletTransaction, txn_id)


async def update_refund_status(
    session: AsyncSession,
    *,
    transaction_id: str,
    status: str,
    note: Optional[str],
    actor_email: Optional[str],
) -> Dict[str, Any]:
    txn = await get_wallet_transaction(session, transaction_id)
    if txn is None:
        raise ValueError("Refund not found")
    if txn.transaction_type != "refund":
        raise ValueError("Transaction is not a refund")

    context = txn.context or {}
    context.update(
        {
            "status": status,
            "reviewedAt": _utcnow().isoformat(),
            "reviewedBy": actor_email,
            "reviewNote": note,
        }
    )
    txn.context = context
    if note:
        txn.note = note

    try:
        await session.commit()
    except Exception:  # pragma: no cover
        await session.rollback()
        raise
    await session.refresh(txn)

    if status == "denied":
        await _create_wallet_transaction(
            session,
            user_id=txn.user_id,
            user_email=txn.user_email,
            payment_id=None,
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            entry_type="credit",
            transaction_type="refund_reversal",
            note="Refund denied; funds returned",
            initiated_by=actor_email,
            context={"sourceTransaction": transaction_id},
        )

    summary = await get_user_wallet_summary(session, txn.user_id)
    return {
        "transaction": txn,
        "summary": summary,
    }
