"""Async Alembic migrations (SQLAlchemy 2.x + asyncpg)."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

from pinebitz.db.base import Base
from pinebitz.db.models import BotPlanRow  # noqa: F401 — register metadata
from pinebitz.db.models import ExchangeConnectionRow  # noqa: F401
from pinebitz.db.session import get_database_url

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    raise RuntimeError(
        "Offline migrations are not configured for this repo; use DATABASE_URL "
        "and alembic upgrade head (online)."
    )


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online_async() -> None:
    url = get_database_url()

    connectable = create_async_engine(url, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_migrations_online_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
