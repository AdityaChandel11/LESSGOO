"""Database engine, session factory, and declarative base."""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings

def _server_settings() -> dict[str, str]:
    """Per-connection Postgres settings, applied when the connection opens.

    `statement_timeout` is the one that matters here. It is set on the server
    rather than enforced in Python because a client-side timeout abandons the
    request while the query keeps running — and a query that keeps running
    keeps its temporary files. The server cancels the statement and releases
    them.
    """
    settings_map: dict[str, str] = {}
    if settings.db_statement_timeout_ms > 0:
        settings_map["statement_timeout"] = str(settings.db_statement_timeout_ms)
    return settings_map


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Recycle before Cloud SQL / proxies drop idle connections.
    pool_recycle=1800,
    connect_args={"server_settings": _server_settings()},
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def ping() -> bool:
    """Cheap connectivity probe for health checks. Never raises."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
