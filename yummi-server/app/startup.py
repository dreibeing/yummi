from __future__ import annotations

import logging
from typing import Iterable, Tuple

from .config import Settings

logger = logging.getLogger(__name__)


def _collect_missing(settings: Settings, pairs: Iterable[Tuple[str, str]]) -> list[str]:
    missing: list[str] = []
    for attr, label in pairs:
        value = getattr(settings, attr, None)
        if value in (None, "", [], {}):
            missing.append(label)
    return missing


def validate_settings(settings: Settings) -> None:
    """Fail fast when mandatory secrets/config values are missing for non-dev envs."""
    environment = (settings.environment or "dev").lower()

    # Always warn in dev if critical secrets are absent to encourage local coverage.
    dev_missing = _collect_missing(
        settings,
        [
            ("redis_url", "REDIS_URL"),
            ("openai_api_key", "OPENAI_API_KEY"),
            ("payfast_merchant_id", "PAYFAST_MERCHANT_ID"),
            ("payfast_merchant_key", "PAYFAST_MERCHANT_KEY"),
        ],
    )
    if environment == "dev":
        if dev_missing:
            logger.warning(
                "Running in dev without recommended secrets; some features may be disabled",
                missing=dev_missing,
            )
        return

    # Non-dev environments must have the following.
    required_pairs: list[Tuple[str, str]] = [
        ("redis_url", "REDIS_URL"),
        ("openai_api_key", "OPENAI_API_KEY"),
        ("payfast_merchant_id", "PAYFAST_MERCHANT_ID"),
        ("payfast_merchant_key", "PAYFAST_MERCHANT_KEY"),
        ("payfast_notify_url", "PAYFAST_NOTIFY_URL"),
        ("payfast_return_url", "PAYFAST_RETURN_URL"),
        ("payfast_cancel_url", "PAYFAST_CANCEL_URL"),
    ]

    if not settings.auth_disable_verification:
        required_pairs.extend(
            [
                ("clerk_issuer", "CLERK_ISSUER"),
                ("clerk_audience", "CLERK_AUDIENCE"),
            ]
        )

    missing = _collect_missing(settings, required_pairs)
    if missing:
        raise RuntimeError(
            f"Missing required configuration for environment '{environment}': {', '.join(sorted(missing))}"
        )
