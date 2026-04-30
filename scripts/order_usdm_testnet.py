"""
Optional market order on Binance USD-M TESTNET — use for validating create_order + leverage paths.

Dry-run default: prints planned parameters (no REST write except load_markets + optional fetch).
With --execute: places ONE market order (real on testnet).

PowerShell:
  pip install -e .
  $env:BINANCE_USDM_TESTNET_API_KEY="..."
  $env:BINANCE_USDM_TESTNET_SECRET="..."
  python scripts/order_usdm_testnet.py
  python scripts/order_usdm_testnet.py --execute --symbol BTC/USDT:USDT --amount 0.002 --side buy --leverage 5

Selling without an open long can OPEN A SHORT depending on hedge/position settings — prefer --side buy first.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pinebitz.exchanges.binance_usdm_testnet import binance_usdm_testnet_exchange


async def run(
    *,
    symbol: str,
    side: str,
    amount: str,
    leverage: int | None,
    execute: bool,
) -> int:
    ex = binance_usdm_testnet_exchange()
    try:
        await ex.load_markets()
        if symbol not in ex.markets:
            print(f"Unknown symbol {symbol}", file=sys.stderr)
            return 2

        m = ex.markets[symbol]
        limits = m.get("limits") or {}
        amount_lim = limits.get("amount") or {}
        price_lim = limits.get("price") or {}
        ccxt_prec = (m.get("precision") or {}) if isinstance(m.get("precision"), dict) else {}

        print(f"{symbol}: contract size / type = {m.get('contract')}, inverse={m.get('inverse')}")

        ticker = await ex.fetch_ticker(symbol)
        last = ticker.get("last")
        cost_est = float(last) * float(amount) if last is not None else None

        bal = await ex.fetch_balance()
        usdt = (bal.get("USDT") or {})
        print(f"USDT free ~ {usdt.get('free')}")
        print(
            "market limits:",
            f"amount min={amount_lim.get('min')} max={amount_lim.get('max')}",
            f"precision amount={ccxt_prec.get('amount')}",
            f"precision price={ccxt_prec.get('price')}",
        )

        if leverage is not None:
            if execute:
                print(f"calling set_leverage({leverage}, {symbol})")
                await ex.set_leverage(leverage, symbol)
            else:
                print(f"[dry-run] would set_leverage({leverage}, {symbol})")

        print(
            f"[{'EXECUTE' if execute else 'DRY-RUN'}] "
            f"market {side} amount={amount} @ ~last {last}; notional ~= {cost_est} USDT (rough)"
        )

        if side == "sell":
            print(
                "Selling can open/add short exposure on futures. Prefer testing with --side buy first.",
                file=sys.stderr,
            )

        if not execute:
            print("Pass --execute to submit the order.")
            return 0

        if side == "buy":
            order = await ex.create_market_buy_order(symbol, amount)
        else:
            order = await ex.create_market_sell_order(symbol, amount)

        print("order:", order.get("id"), order.get("status"), order.get("info"))
        return 0
    finally:
        await ex.close()


def main() -> None:
    p = argparse.ArgumentParser(description="USD-M futures testnet order helper")
    p.add_argument("--symbol", default="BTC/USDT:USDT")
    p.add_argument("--side", choices=("buy", "sell"), default="buy")
    p.add_argument(
        "--amount",
        default="0.002",
        help="Base quantity (BTC for BTC/USDT linear). Must respect exchange minimums.",
    )
    p.add_argument(
        "--leverage",
        type=int,
        default=None,
        help="Optional: set leverage on symbol before placing the order (--execute only applies set_leverage)",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Actually place/set leverage on testnet (default is dry-run only).",
    )
    ns = p.parse_args()
    try:
        code = asyncio.run(
            run(
                symbol=ns.symbol,
                side=ns.side,
                amount=str(ns.amount),
                leverage=ns.leverage,
                execute=bool(ns.execute),
            )
        )
    except KeyboardInterrupt:
        code = 130
    except Exception as e:
        print(e, file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
