#!/usr/bin/env python3
"""Thin CLI for wallet chargebacks/refund moderation until a UI exists."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

import httpx


def _default_api_base() -> str:
    return os.environ.get("YUMMI_API_BASE", "http://localhost:8000/v1")


def _default_token() -> str:
    return os.environ.get("YUMMI_ADMIN_TOKEN", "")


def _request(
    endpoint: str,
    payload: Dict[str, Any],
    *,
    api_base: str,
    token: str,
) -> Dict[str, Any]:
    url = endpoint if endpoint.startswith("http") else f"{api_base.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(url, headers=headers, json=payload)
    try:
        data = resp.json()
    except Exception:
        data = {"text": resp.text}
    if resp.status_code >= 400:
        raise SystemExit(f"[{resp.status_code}] {json.dumps(data, indent=2)}")
    return data


def cmd_chargeback(args: argparse.Namespace, api_base: str, token: str) -> None:
    payload = {
        "reference": args.reference,
        "amountMinor": args.amount_minor,
        "note": args.note,
        "externalReference": args.external_reference,
    }
    data = _request(
        "/admin/wallet/chargebacks",
        payload,
        api_base=api_base,
        token=token,
    )
    print(json.dumps(data, indent=2))


def cmd_refund_update(args: argparse.Namespace, api_base: str, token: str) -> None:
    payload = {"status": args.status, "note": args.note}
    endpoint = f"/admin/wallet/refunds/{args.transaction_id}/status"
    data = _request(endpoint, payload, api_base=api_base, token=token)
    print(json.dumps(data, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wallet admin helper for chargebacks/refunds.")
    parser.add_argument(
        "--api-base",
        default=_default_api_base(),
        help="Base API URL (default: %(default)s or YUMMI_API_BASE).",
    )
    parser.add_argument(
        "--token",
        default=_default_token(),
        help="Admin bearer token (default: YUMMI_ADMIN_TOKEN env).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cb = sub.add_parser("chargeback", help="Record a chargeback for a PayFast reference.")
    cb.add_argument("reference", help="PayFast reference (custom_str2).")
    cb.add_argument(
        "--amount-minor",
        type=int,
        default=None,
        help="Override amount (in minor units). Defaults to payment amount.",
    )
    cb.add_argument("--note", default=None, help="Reason/note to store with the debit.")
    cb.add_argument(
        "--external-reference",
        default=None,
        help="Issuer reference (optional).",
    )

    ru = sub.add_parser("refund-update", help="Update a pending refund transaction status.")
    ru.add_argument("transaction_id", help="Refund wallet transaction ID (UUID).")
    ru.add_argument(
        "status",
        choices=["approved", "paid", "denied"],
        help="New refund state.",
    )
    ru.add_argument("--note", default=None, help="Optional reviewer note.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    token = args.token or _default_token()
    if not token:
        parser.error("Missing admin bearer token. Pass --token or set YUMMI_ADMIN_TOKEN.")
    api_base = args.api_base or _default_api_base()

    if args.command == "chargeback":
        cmd_chargeback(args, api_base, token)
    elif args.command == "refund-update":
        cmd_refund_update(args, api_base, token)
    else:  # pragma: no cover
        parser.error(f"Unknown command {args.command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
