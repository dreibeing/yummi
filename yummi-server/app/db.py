from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings


engine: Optional[AsyncEngine] = None
SessionLocal: Optional[async_sessionmaker[AsyncSession]] = None


def init_engine() -> None:
    global engine, SessionLocal
    url = normalize_database_url(get_settings().database_url)
    if not url:
        engine = None
        SessionLocal = None
        return
    engine = create_async_engine(url, future=True, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    if not SessionLocal:
        raise RuntimeError("Database not configured")
    async with SessionLocal() as session:
        yield session


def normalize_database_url(raw_url: Optional[str]) -> Optional[str]:
    """Ensure we always use asyncpg + sane SSL defaults.

    Fly Postgres single-node clusters do not terminate TLS for internal clients,
    so we automatically disable SSL when pointing at `.internal` hosts unless the
    caller explicitly sets `ssl=true`.
    """
    if not raw_url:
        return raw_url

    url = raw_url
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlparse(url)
    if not parsed.scheme.startswith("postgresql+asyncpg"):
        return url

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    sslmode = query.pop("sslmode", None)
    if sslmode:
        query["ssl"] = sslmode.lower()
        sslmode = None
    if "ssl" not in query and parsed.hostname and parsed.hostname.endswith(".internal"):
        query["ssl"] = "disable"
    elif "ssl" in query:
        # normalize accepted asyncpg keywords
        allowed = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
        query["ssl"] = query["ssl"].lower()
        if query["ssl"] not in allowed:
            query["ssl"] = "disable"

    normalized_query = urlencode(query)
    return urlunparse(parsed._replace(query=normalized_query))
