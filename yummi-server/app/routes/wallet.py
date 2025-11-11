from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_principal
from ..db import get_session
from ..services.payments import get_user_wallet_summary, request_wallet_refund
from ..schemas import WalletSummary, WalletRefundRequest, WalletRefundResponse

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/balance", response_model=WalletSummary)
async def wallet_balance(principal=Depends(get_current_principal)):
    async with get_session() as session:
        summary = await get_user_wallet_summary(session, principal.get("sub"))
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found")
    return summary


@router.post("/refunds", response_model=WalletRefundResponse)
async def wallet_refund(
    payload: WalletRefundRequest,
    principal=Depends(get_current_principal),
):
    async with get_session() as session:
        try:
            result = await request_wallet_refund(
                session,
                user_id=principal.get("sub"),
                user_email=principal.get("email"),
                amount_minor=payload.amountMinor,
                reason=payload.reason,
                actor_email=principal.get("email"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    txn = result["transaction"]
    summary = result["summary"]
    context = txn.context or {}
    lock_reason = summary.get("lockReason") if summary else None
    lock_note = summary.get("lockNote") if summary else None
    return WalletRefundResponse(
        refundId=str(txn.id),
        status=context.get("status", "pending"),
        debitedMinor=txn.amount_minor,
        balanceMinor=summary["balanceMinor"] if summary else 0,
        spendBlocked=summary["spendBlocked"] if summary else False,
        lockReason=lock_reason,
        lockNote=lock_note,
    )
