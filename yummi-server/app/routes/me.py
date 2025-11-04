from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import get_current_principal
from ..db import get_session
from ..services.payments import get_user_wallet_summary

router = APIRouter()


@router.get("/me")
async def me(principal=Depends(get_current_principal)):
    async with get_session() as session:
        wallet = await get_user_wallet_summary(session, principal.get("sub"))
    return {
        "sub": principal.get("sub"),
        "email": principal.get("email"),
        "claims": principal.get("claims"),
        "wallet": wallet,
    }
