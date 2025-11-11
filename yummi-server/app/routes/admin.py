from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_principal
from ..config import get_settings
from ..db import get_session
from ..redis_util import get_redis
from ..schemas import (
    AdminChargebackRequest,
    AdminChargebackResponse,
    WalletRefundAdminActionRequest,
    WalletRefundResponse,
)
from ..services.payments import record_chargeback, update_refund_status


router = APIRouter()


def _require_admin(principal: Dict[str, Any]):
    s = get_settings()
    email = principal.get("email")
    if s.admin_emails:
        if not email or email.lower() not in [e.lower() for e in s.admin_emails]:
            raise HTTPException(status_code=403, detail="Admin only")
    else:
        # If no admin list configured and not dev, block
        if s.environment not in ("dev", "development"):
            raise HTTPException(status_code=403, detail="Admin not configured")


@router.post("/admin/catalog/import")
def catalog_import(payload: Any, principal=Depends(get_current_principal)):
    _require_admin(principal)
    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis not configured")
    data = payload
    if not isinstance(data, (list, dict)):
        raise HTTPException(status_code=400, detail="Expected list or object with items[]")
    try:
        # Validate minimal shape
        items = data if isinstance(data, list) else data.get("items", [])
        if not isinstance(items, list) or len(items) == 0:
            raise HTTPException(status_code=400, detail="No items to import")
        r.set("catalog:data", json.dumps(data))
        return {"ok": True, "count": len(items), "source": "redis"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")


@router.get("/admin/catalog/source")
def catalog_source(principal=Depends(get_current_principal)):
    _require_admin(principal)
    s = get_settings()
    r = get_redis()
    src = "file"
    count = None
    if r is not None:
        raw = r.get("catalog:data")
        if raw:
            try:
                data = json.loads(raw)
                items = data if isinstance(data, list) else data.get("items", [])
                src = "redis"
                count = len(items)
            except Exception:
                pass
    return {"source": src, "redis": bool(r is not None), "file": s.catalog_path, "count": count}


@router.post("/admin/wallet/chargebacks", response_model=AdminChargebackResponse)
async def admin_record_chargeback(
    payload: AdminChargebackRequest,
    principal=Depends(get_current_principal),
):
    _require_admin(principal)
    async with get_session() as session:
        try:
            result = await record_chargeback(
                session,
                reference=payload.reference,
                amount_minor=payload.amountMinor,
                note=payload.note,
                external_reference=payload.externalReference,
                actor_email=principal.get("email"),
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(exc))
    return result


@router.post(
    "/admin/wallet/refunds/{transaction_id}/status",
    response_model=WalletRefundResponse,
)
async def admin_update_refund(
    transaction_id: str,
    payload: WalletRefundAdminActionRequest,
    principal=Depends(get_current_principal),
):
    _require_admin(principal)
    async with get_session() as session:
        try:
            result = await update_refund_status(
                session,
                transaction_id=transaction_id,
                status=payload.status,
                note=payload.note,
                actor_email=principal.get("email"),
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(exc))

    txn = result["transaction"]
    summary = result["summary"]
    context = txn.context or {}
    return WalletRefundResponse(
        refundId=str(txn.id),
        status=context.get("status", payload.status),
        debitedMinor=txn.amount_minor,
        balanceMinor=summary["balanceMinor"] if summary else 0,
        spendBlocked=summary["spendBlocked"] if summary else False,
        lockReason=summary.get("lockReason") if summary else None,
        lockNote=summary.get("lockNote") if summary else None,
    )
