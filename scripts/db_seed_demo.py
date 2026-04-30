from __future__ import annotations

import asyncio
from sqlalchemy import select

from pinebitz.db.models import BotPlanRow, ExchangeConnectionRow
from pinebitz.db.session import make_async_engine, make_async_sessionmaker


async def main() -> None:
    engine = make_async_engine()
    Session = make_async_sessionmaker(engine)
    try:
        async with Session() as s:
            conn = ExchangeConnectionRow(
                owner_key="demo-user",
                label="Demo Binance USDT-M Testnet",
                venue="binance",
                market_lane="futures_um",
                environment="testnet",
                status="active",
                credential_ref="vault://demo/binance/usdm/testnet",
                extra={"notes": "seeded by scripts/db_seed_demo.py"},
            )
            s.add(conn)
            await s.flush()

            plan = BotPlanRow(
                connection_id=conn.id,
                name="USDM DCA Demo",
                instrument_kind="futures",
                enabled=False,
                plan_version=1,
                config_json={
                    "pair": "BTC/USDT:USDT",
                    "direction": "long",
                    "entry": {"base_order_usdt": 25},
                    "averaging": {"enabled": True, "max_orders": 3},
                    "exit": {"tp_pct": 1.0},
                },
            )
            s.add(plan)
            await s.commit()

            q = await s.execute(
                select(BotPlanRow, ExchangeConnectionRow)
                .join(ExchangeConnectionRow, BotPlanRow.connection_id == ExchangeConnectionRow.id)
                .order_by(BotPlanRow.created_at.desc())
                .limit(1)
            )
            row = q.first()
            if not row:
                print("no rows")
                return
            p, c = row
            print(f"connection={c.label} ({c.venue}/{c.market_lane}/{c.environment})")
            print(f"plan={p.name} enabled={p.enabled} pair={p.config_json.get('pair')}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
