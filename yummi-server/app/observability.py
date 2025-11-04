from __future__ import annotations

import logging
import sys

import structlog
from structlog import dev

from .config import Settings

_LOGGING_CONFIGURED = False
_SENTRY_CONFIGURED = False


def configure_logging(json_logs: bool, level: str = "INFO") -> None:
    """Configure structlog-backed logging with optional JSON output."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_logs:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer(
            sort_keys=True
        )
    else:
        renderer = dev.ConsoleRenderer(colors=False)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                timestamper,
            ],
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Align common dependency loggers with our formatting.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn"):
        logging.getLogger(logger_name).handlers.clear()
        logging.getLogger(logger_name).propagate = True
        logging.getLogger(logger_name).setLevel(log_level)

    _LOGGING_CONFIGURED = True


def init_sentry(settings: Settings) -> None:
    """Initialise Sentry, capturing breadcrumbs from logging if configured."""
    global _SENTRY_CONFIGURED
    if _SENTRY_CONFIGURED:
        return

    dsn = getattr(settings, "sentry_dsn", None)
    if not dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:  # pragma: no cover - defensive
        logging.getLogger(__name__).warning("sentry-sdk not installed; skipping Sentry init")
        return

    sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        integrations=[FastApiIntegration(), sentry_logging],
        send_default_pii=False,
    )

    _SENTRY_CONFIGURED = True
