from __future__ import annotations

import logging
import os
from typing import List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from .config import get_settings
from .db import init_engine
from .observability import configure_logging, init_sentry
from .startup import validate_settings
from .routes import health, me, catalog, orders, ai, admin, thin, payfast, wallet
from .ratelimit import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(json_logs=s.log_json, level=s.log_level)
    init_sentry(s)
    validate_settings(s)
    app = FastAPI(title=s.app_name)

    # Initialize DB engine if configured
    init_engine()

    # CORS
    origins: List[str] = s.cors_allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware)
    # Compression
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # Routers
    prefix = "/v1"
    app.include_router(health.router, prefix=prefix)
    app.include_router(me.router, prefix=prefix)
    app.include_router(catalog.router, prefix=prefix)
    app.include_router(orders.router, prefix=prefix)
    app.include_router(ai.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)
    app.include_router(thin.router, prefix=f"{prefix}/thin")
    app.include_router(payfast.router, prefix=prefix)
    app.include_router(wallet.router, prefix=prefix)

    # Rate limit handling
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Metrics
    Instrumentator().instrument(app).expose(app, include_in_schema=False)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
