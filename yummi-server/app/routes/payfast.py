from __future__ import annotations

import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from ..auth import get_current_principal
from ..config import get_settings
from ..db import get_session
from ..payments.payfast import (
    build_checkout_params,
    build_signature,
    validate_itn_payload,
)
from ..schemas import (
    PayFastInitiateRequest,
    PayFastInitiateResponse,
    PayFastStatusResponse,
)
from ..services.payments import (
    create_payfast_payment,
    get_payment_by_reference,
    update_payfast_payment_from_itn,
)

router = APIRouter(prefix="/payments/payfast", tags=["payments"])

logger = logging.getLogger(__name__)


@router.post("/initiate", response_model=PayFastInitiateResponse)
async def initiate_payfast_payment(
    payload: PayFastInitiateRequest,
    principal=Depends(get_current_principal),
):
    try:
        host, params = build_checkout_params(
            amount_minor=payload.amountMinor,
            currency=payload.currency,
            item_name=payload.itemName,
            item_description=payload.itemDescription,
            user_email=principal.get("email"),
            user_reference=principal.get("sub"),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to build PayFast checkout params")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    reference = params.get("custom_str2", "")
    async with get_session() as session:
        await create_payfast_payment(
            session,
            reference=reference,
            user_id=principal.get("sub"),
            user_email=principal.get("email"),
            amount_minor=payload.amountMinor,
            currency=payload.currency,
            item_name=payload.itemName,
            item_description=payload.itemDescription,
            checkout_payload=params,
        )
    return PayFastInitiateResponse(url=host, params=params, reference=reference)


@router.post("/itn", response_class=PlainTextResponse)
async def payfast_itn(request: Request):
    form = await request.form()
    payload: Dict[str, str] = {k: v for k, v in form.items()}
    signature = payload.get("signature", "")

    settings = get_settings()
    check_signature = build_signature(
        {k: v for k, v in payload.items() if k != "signature"},
        settings.payfast_passphrase,
    )
    if signature != check_signature:
        logger.warning("PayFast ITN signature mismatch", extra={"payload": payload})
        return PlainTextResponse("INVALID", status_code=400)

    try:
        is_valid = await validate_itn_payload(payload)
    except Exception as exc:
        logger.exception("Failed to validate PayFast ITN with remote service")
        return PlainTextResponse("ERROR", status_code=500)

    if not is_valid:
        logger.warning("PayFast ITN validation failed", extra={"payload": payload})
        return PlainTextResponse("INVALID", status_code=400)

    payment_status = payload.get("payment_status", "").upper()
    reference = payload.get("custom_str2")
    logger.info("PayFast ITN received", extra={"status": payment_status, "reference": reference})

    async with get_session() as session:
        await update_payfast_payment_from_itn(session, payload)

    return PlainTextResponse("OK", status_code=200)


@router.get("/status", response_model=PayFastStatusResponse)
async def payfast_status(reference: str):
    async with get_session() as session:
        payment = await get_payment_by_reference(session, reference)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    message = None
    if payment.pf_status:
        message = f"PayFast status: {payment.pf_status}"
    return PayFastStatusResponse(reference=reference, status=payment.status, message=message)
