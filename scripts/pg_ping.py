"""``SELECT 1`` against Postgres using DATABASE_URL normalization (same as workers)."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text


async def main() -> None:
    from pinebitz.db.session import make_async_engine

    engine = make_async_engine()
    try:
        async with engine.connect() as conn:
            one = await conn.scalar(text("SELECT 1"))
        print(one)
    except Exception as exc:  # pragma: no cover - smoke path
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
