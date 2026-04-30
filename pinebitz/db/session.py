from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def normalize_database_url(url: str) -> str:
    """Ensure ``postgresql[+asyncpg]`` form for AsyncEngine."""
    u = url.strip().strip('"').strip("'")
    scheme, sep, remainder = u.partition("://")
    if not sep:
        raise ValueError(f"DATABASE_URL missing scheme ({url!r}).")
    dialect, has_plus, driver = scheme.partition("+")
    # already asyncpg — keep dialect name if supported
    if dialect.startswith("postgres"):
        if has_plus and driver == "asyncpg":
            return u
        if has_plus and driver in ("psycopg", "psycopg2"):
            return "postgresql+asyncpg://" + remainder
        # postgres:// or postgresql:// without explicit driver
        if not has_plus:
            return "postgresql+asyncpg://" + remainder
    raise ValueError(f"DATABASE_URL dialect not supported ({scheme!r}). Use postgresql+asyncpg.")


@lru_cache(maxsize=1)
def get_database_url() -> str:
    raw = (
        os.environ.get("DATABASE_URL")
        or "postgresql+asyncpg://pinebitz:pinebitz@127.0.0.1:15432/pinebitz"
    )
    return normalize_database_url(raw)


def make_async_engine(echo_sql: bool | None = None) -> AsyncEngine:
    if echo_sql is None:
        echo_sql = os.environ.get("SQL_ECHO", "").lower() in ("1", "true", "yes")
    url = get_database_url()
    return create_async_engine(url, echo=echo_sql, pool_pre_ping=True)


AsyncSessionMaker = async_sessionmaker[AsyncSession]


def make_async_sessionmaker(engine: AsyncEngine) -> AsyncSessionMaker:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
        class_=AsyncSession,
    )
