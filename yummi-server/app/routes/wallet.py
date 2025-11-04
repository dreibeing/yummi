from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_principal
from ..db import get_session
from ..services.payments import get_user_wallet_summary
from ..schemas import WalletSummary

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/balance", response_model=WalletSummary)
async def wallet_balance(principal=Depends(get_current_principal)):
    async with get_session() as session:
        summary = await get_user_wallet_summary(session, principal.get("sub"))
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found")
    return summary
