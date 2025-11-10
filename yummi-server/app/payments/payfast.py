from __future__ import annotations

import hashlib
import logging
import uuid
from collections import OrderedDict
from typing import Dict, Tuple
from urllib.parse import quote_plus

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

PAYFAST_PROCESS_URLS = {
    "live": "https://www.payfast.co.za/eng/process",
    "sandbox": "https://sandbox.payfast.co.za/eng/process",
}

PAYFAST_VALIDATE_URLS = {
    "live": "https://www.payfast.co.za/eng/query/validate",
    "sandbox": "https://sandbox.payfast.co.za/eng/query/validate",
}


def _clean_value(value: str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _build_signature_payload(params: Dict[str, str], passphrase: str | None = None) -> str:
    ordered_pairs = sorted((k, _clean_value(v)) for k, v in params.items())
    encoded = "&".join(f"{k}={quote_plus(v)}" for k, v in ordered_pairs)
    if passphrase:
        encoded = f"{encoded}&passphrase={quote_plus(passphrase.strip())}"
    return encoded


def build_signature(params: Dict[str, str], passphrase: str | None = None) -> str:
    payload = _build_signature_payload(params, passphrase)
    signature = hashlib.md5(payload.encode("utf-8")).hexdigest()
    return signature


def get_checkout_host(mode: str) -> str:
    return PAYFAST_PROCESS_URLS.get(mode.lower(), PAYFAST_PROCESS_URLS["sandbox"])


def get_validation_host(mode: str) -> str:
    return PAYFAST_VALIDATE_URLS.get(mode.lower(), PAYFAST_VALIDATE_URLS["sandbox"])


def build_checkout_params(
    *,
    amount_minor: int,
    currency: str,
    item_name: str,
    item_description: str | None,
    user_email: str | None,
    user_reference: str | None,
) -> Tuple[str, Dict[str, str], str]:
    settings = get_settings()
    mode = settings.payfast_mode or "sandbox"
    amount = f"{amount_minor / 100:.2f}"
    reference = user_reference or f"yummi-{uuid.uuid4().hex[:12]}"

    raw_params: OrderedDict[str, str] = OrderedDict(
        {
        "merchant_id": _clean_value(settings.payfast_merchant_id),
        "merchant_key": _clean_value(settings.payfast_merchant_key),
        "amount": amount,
        "item_name": _clean_value(item_name) or "Wallet Top-up",
        "item_description": _clean_value(item_description or ""),
        "currency": currency.upper(),
        "return_url": _clean_value(settings.payfast_return_url),
        "cancel_url": _clean_value(settings.payfast_cancel_url),
        "notify_url": _clean_value(settings.payfast_notify_url),
        "email_address": _clean_value(user_email) or "",
        "custom_str1": _clean_value(user_reference) or "",
        "custom_str2": reference,
    }
    )
    params: Dict[str, str] = OrderedDict(
        (k, v) for k, v in raw_params.items() if _clean_value(v) != ""
    )

    signature_payload = _build_signature_payload(params, settings.payfast_passphrase)
    params["signature"] = hashlib.md5(signature_payload.encode("utf-8")).hexdigest()
    host = get_checkout_host(mode)
    return host, params, signature_payload


async def validate_itn_payload(payload: Dict[str, str]) -> bool:
    settings = get_settings()
    mode = settings.payfast_mode or "sandbox"
    if settings.environment.lower() == "dev":
        logger.info("Skipping ITN remote validation in dev environment")
        return True

    validation_url = get_validation_host(mode)
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(validation_url, data=payload)
        response.raise_for_status()
        return response.text.strip() == "VALID"
