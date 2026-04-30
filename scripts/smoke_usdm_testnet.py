"""
Smoke test: Binance USD-M Futures TESTNET via ccxt (read-only).
Create keys at https://testnet.binancefuture.com/ — never use mainnet keys here.

From repo root (after editable install):
  pip install -e .

PowerShell:
  $env:BINANCE_USDM_TESTNET_API_KEY="..."
  $env:BINANCE_USDM_TESTNET_SECRET="..."
  python scripts/smoke_usdm_testnet.py
"""

from __future__ import annotations

import asyncio
import sys

try:
    from pinebitz.exchanges.binance_usdm_testnet import binance_usdm_testnet_exchange
except ImportError:
    print("Run from project root after: pip install -e .", file=sys.stderr)
    raise


async def main() -> None:
    try:
        exchange = binance_usdm_testnet_exchange()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    try:
        await exchange.load_markets()
        balance = await exchange.fetch_balance()

        print(f"markets loaded: {len(exchange.markets)}")
        usdt = balance.get("USDT") or {}
        print(f"USDT total: {usdt.get('total')} free: {usdt.get('free')}")

        positions = await exchange.fetch_positions()
        open_pos = [
            p
            for p in positions
            if p.get("contracts") is not None and float(p["contracts"] or 0) != 0
        ]
        print(f"open positions (non-zero): {len(open_pos)}")

        symbol = "BTC/USDT:USDT"
        if symbol in exchange.markets:
            t = await exchange.fetch_ticker(symbol)
            print(f"{symbol} last: {t.get('last')}")
        else:
            print(f"{symbol} not in markets — pick a linear symbol from testnet.")

    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())
