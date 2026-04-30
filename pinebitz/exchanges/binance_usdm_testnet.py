"""
Binance USD-M perpetual (USDT) — TESTNET only.

Keys: https://testnet.binancefuture.com/
"""

from __future__ import annotations

import os
from typing import Any


def _require_testnet_credentials() -> tuple[str, str]:
    key = (os.environ.get("BINANCE_USDM_TESTNET_API_KEY") or "").strip()
    secret = (os.environ.get("BINANCE_USDM_TESTNET_SECRET") or "").strip()
    if not key or not secret:
        raise RuntimeError(
            "Set BINANCE_USDM_TESTNET_API_KEY and BINANCE_USDM_TESTNET_SECRET "
            "(USD-M Futures testnet keys)."
        )
    return key, secret


def binance_usdm_testnet_exchange(overrides: dict[str, Any] | None = None):
    """Return configured async ``ccxt.binanceusdm`` pointing at testnet endpoints."""
    import ccxt.async_support as ccxt

    key, secret = _require_testnet_credentials()
    opts: dict[str, Any] = {
        "apiKey": key,
        "secret": secret,
        "enableRateLimit": True,
    }
    if overrides:
        opts.update(overrides)
    ex = ccxt.binanceusdm(opts)
    ex.set_sandbox_mode(True)
    return ex
