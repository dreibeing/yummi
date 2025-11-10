from __future__ import annotations

import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse

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
    get_payfast_status_details,
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
        host, params, signature_payload = build_checkout_params(
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

    if logger.isEnabledFor(logging.INFO):
        preview = {k: params[k] for k in sorted(params.keys()) if k != "signature"}
        logger.info(
            "PayFast checkout params built host=%s reference=%s payload=%s signature=%s base=%s",
            host,
            params.get("custom_str2"),
            preview,
            params.get("signature"),
            signature_payload,
        )
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
        payload = await get_payfast_status_details(session, reference)
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return PayFastStatusResponse(**payload)


def _bridge_html(destination: str | None, fallback_message: str) -> str:
    if not destination:
        return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>PayFast Return</title>
  </head>
  <body>
    <p>{fallback_message}</p>
  </body>
</html>"""
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Redirecting…</title>
    <meta http-equiv="refresh" content="0;url={destination}">
    <script>window.location.replace("{destination}");</script>
    <style>
      body {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        padding: 2rem;
        text-align: center;
      }}
    </style>
  </head>
  <body>
    <p>Redirecting back to the app…</p>
    <p>If nothing happens <a href="{destination}">tap here</a>.</p>
  </body>
</html>"""


@router.get("/return-bridge", response_class=HTMLResponse)
async def payfast_return_bridge():
    settings = get_settings()
    target = settings.payfast_return_deeplink or settings.payfast_return_url
    html = _bridge_html(target, "Return URL is not configured.")
    return HTMLResponse(content=html)


@router.get("/cancel-bridge", response_class=HTMLResponse)
async def payfast_cancel_bridge():
    settings = get_settings()
    target = settings.payfast_cancel_deeplink or settings.payfast_cancel_url
    html = _bridge_html(target, "Cancel URL is not configured.")
    return HTMLResponse(content=html)
