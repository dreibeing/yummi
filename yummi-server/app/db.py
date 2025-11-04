from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings


engine: Optional[AsyncEngine] = None
SessionLocal: Optional[async_sessionmaker[AsyncSession]] = None


def init_engine() -> None:
    global engine, SessionLocal
    url = get_settings().database_url
    if not url:
        engine = None
        SessionLocal = None
        return
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, future=True, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    if not SessionLocal:
        raise RuntimeError("Database not configured")
    async with SessionLocal() as session:
        yield session

