from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.responses import FileResponse
from sqlalchemy import Select, func, select, text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession

from pinebitz.api.errors import ErrorCode
from pinebitz.api.logging_utils import configure_logging, emit_request_log
from pinebitz.api.metrics import InMemoryMetrics
from pinebitz.api.schemas import (
    ConnectionListOut,
    ConnectionCreate,
    ConnectionPatch,
    ConnectionOut,
    ConnectionStatus,
    DcaBotConfig,
    DcaPreviewOut,
    DcaSimulationOut,
    DashboardBotRow,
    ErrorResponse,
    PlanListOut,
    PlanCreate,
    PlanPatch,
    PlanOut,
    PlanStatus,
    SignalSide,
    SignalSource,
    ExecutionJobActionIn,
    ExecutionAdapter,
    ExecutionAuditEventListOut,
    ExecutionAuditEventOut,
    ExecutionJobListOut,
    ExecutionJobOut,
    ExecutionJobStatus,
    RuntimeGuardStateOut,
    PaperPositionListOut,
    PaperPositionOut,
    TradingViewSignalListOut,
    TradingViewSignalOut,
)
from pinebitz.db.models import BotPlanRow, ExchangeConnectionRow
from pinebitz.db.session import get_database_url, make_async_sessionmaker

app = FastAPI(title="pinebitz API", version="0.1.0")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

from sqlalchemy.ext.asyncio import create_async_engine

_engine = create_async_engine(get_database_url(), poolclass=NullPool, pool_pre_ping=True)
_Session = make_async_sessionmaker(_engine)
_logger = configure_logging()
_metrics = InMemoryMetrics()
_signal_inbox: list[TradingViewSignalOut] = []
_execution_jobs: list[ExecutionJobOut] = []
_execution_audit: list[ExecutionAuditEventOut] = []
_paper_positions: dict[str, PaperPositionOut] = {}

app.mount("/assets", StaticFiles(directory=str(WEB_DIR)), name="assets")

ERROR_RESPONSES = {
    401: {
        "model": ErrorResponse,
        "content": {
            "application/json": {
                "example": {
                    "code": ErrorCode.missing_owner_key,
                    "message": "missing X-Owner-Key",
                    "details": None,
                    "request_id": "4cf8e6d6-18f6-4f47-b6d2-4bd71f97191c",
                }
            }
        },
    },
    404: {
        "model": ErrorResponse,
        "content": {
            "application/json": {
                "example": {
                    "code": ErrorCode.connection_not_found,
                    "message": "connection not found",
                    "details": {"connection_id": "00000000-0000-0000-0000-000000000000"},
                    "request_id": "4cf8e6d6-18f6-4f47-b6d2-4bd71f97191c",
                }
            }
        },
    },
    422: {
        "model": ErrorResponse,
        "content": {
            "application/json": {
                "example": {
                    "code": ErrorCode.validation_error,
                    "message": "request validation failed",
                    "details": {"errors": []},
                    "request_id": "4cf8e6d6-18f6-4f47-b6d2-4bd71f97191c",
                }
            }
        },
    },
}


def build_dca_preview(config: DcaBotConfig, reference_price: float) -> DcaPreviewOut:
    entry_price = max(reference_price, 0.00000001)
    direction_mult = -1 if config.direction.value == "long" else 1

    steps = []
    total_qty = 0.0
    total_cost = 0.0

    base_qty = config.entry.base_order_usdt / entry_price if entry_price else 0.0
    total_qty += base_qty
    total_cost += config.entry.base_order_usdt
    avg_price = total_cost / total_qty if total_qty else entry_price
    tp_price = avg_price * (1 + (config.exit.tp_pct / 100.0) * (-direction_mult))
    steps.append(
        {
            "index": 0,
            "kind": "base",
            "deviation_pct": 0.0,
            "order_size_usdt": round(config.entry.base_order_usdt, 4),
            "cumulative_usdt": round(total_cost, 4),
            "trigger_price": round(entry_price, 8),
            "avg_price": round(avg_price, 8),
            "take_profit_price": round(tp_price, 8),
        }
    )

    if config.averaging.enabled and config.averaging.max_orders > 0:
        gap = config.averaging.first_deviation_pct
        deviation_pct = 0.0
        for idx in range(1, config.averaging.max_orders + 1):
            deviation_pct += gap
            trigger_price = entry_price * (1 + direction_mult * (deviation_pct / 100.0))
            order_size = config.averaging.safety_order_size * (config.averaging.order_size_multiplier ** (idx - 1))
            qty = order_size / max(trigger_price, 0.00000001)
            total_qty += qty
            total_cost += order_size
            avg_price = total_cost / total_qty if total_qty else trigger_price
            tp_price = avg_price * (1 + (config.exit.tp_pct / 100.0) * (-direction_mult))
            steps.append(
                {
                    "index": idx,
                    "kind": "averaging",
                    "deviation_pct": round(deviation_pct, 4),
                    "order_size_usdt": round(order_size, 4),
                    "cumulative_usdt": round(total_cost, 4),
                    "trigger_price": round(trigger_price, 8),
                    "avg_price": round(avg_price, 8),
                    "take_profit_price": round(tp_price, 8),
                }
            )
            gap *= config.averaging.deviation_multiplier

    summary = {
        "entry_price": round(entry_price, 8),
        "total_steps": len(steps),
        "total_usdt": round(total_cost, 4),
        "max_deviation_pct": round(max(s["deviation_pct"] for s in steps), 4),
        "estimated_tp_price": round(steps[-1]["take_profit_price"], 8),
    }
    return DcaPreviewOut(summary=summary, steps=steps)


def build_dca_simulation(config: DcaBotConfig, price_path: list[float]) -> DcaSimulationOut:
    if not price_path:
        raise ValueError("price_path must not be empty")

    preview = build_dca_preview(config, price_path[0])
    preview_steps = [step.model_dump() for step in preview.steps]
    events = []
    filled_steps: list[dict] = []
    next_step_idx = 0

    def calc_pnl(exit_price: float, avg_price: float, cumulative_usdt: float) -> tuple[float, float]:
        if avg_price <= 0 or cumulative_usdt <= 0:
            return (0.0, 0.0)
        position_qty = cumulative_usdt / avg_price
        if config.direction.value == "long":
            pnl = (exit_price - avg_price) * position_qty
        else:
            pnl = (avg_price - exit_price) * position_qty
        roi = (pnl / cumulative_usdt) * 100.0 if cumulative_usdt > 0 else 0.0
        return (pnl, roi)

    def calc_levels() -> tuple[float, float, float]:
        if not filled_steps:
            return (0.0, 0.0, 0.0)
        avg_price = filled_steps[-1]["avg_price"]
        tp_price = filled_steps[-1]["take_profit_price"]
        sl_pct = config.exit.stop_loss_pct
        if config.direction.value == "long":
            sl_price = avg_price * (1 - sl_pct / 100.0) if sl_pct > 0 else 0.0
        else:
            sl_price = avg_price * (1 + sl_pct / 100.0) if sl_pct > 0 else 0.0
        return avg_price, tp_price, sl_price

    for tick_idx, price in enumerate(price_path):
        price = float(price)

        while next_step_idx < len(preview_steps):
            step = preview_steps[next_step_idx]
            should_fill = False
            if step["kind"] == "base" and tick_idx == 0:
                should_fill = True
            elif config.direction.value == "long":
                should_fill = price <= step["trigger_price"]
            else:
                should_fill = price >= step["trigger_price"]
            if not should_fill:
                break

            filled_steps.append(step)
            avg_price, tp_price, sl_price = calc_levels()
            events.append(
                {
                    "tick_index": tick_idx,
                    "price": round(price, 8),
                    "action": "fill",
                    "step_index": step["index"],
                    "avg_price": round(avg_price, 8),
                    "take_profit_price": round(tp_price, 8),
                    "stop_loss_price": round(sl_price, 8),
                    "cumulative_usdt": round(step["cumulative_usdt"], 4),
                }
            )
            next_step_idx += 1

        if not filled_steps:
            continue

        avg_price, tp_price, sl_price = calc_levels()
        hit_tp = price >= tp_price if config.direction.value == "long" else price <= tp_price
        hit_sl = sl_price > 0 and (price <= sl_price if config.direction.value == "long" else price >= sl_price)
        if hit_tp:
            cumulative_usdt = filled_steps[-1]["cumulative_usdt"]
            pnl, roi = calc_pnl(price, avg_price, cumulative_usdt)
            events.append(
                {
                    "tick_index": tick_idx,
                    "price": round(price, 8),
                    "action": "take_profit",
                    "step_index": None,
                    "avg_price": round(avg_price, 8),
                    "take_profit_price": round(tp_price, 8),
                    "stop_loss_price": round(sl_price, 8),
                    "cumulative_usdt": round(cumulative_usdt, 4),
                    "realized_pnl": round(pnl, 4),
                    "roi_pct": round(roi, 4),
                }
            )
            summary = {
                "finished": True,
                "close_reason": "take_profit",
                "ticks_processed": tick_idx + 1,
                "filled_steps": len(filled_steps),
                "total_usdt": round(cumulative_usdt, 4),
                "close_price": round(price, 8),
                "final_avg_price": round(avg_price, 8),
                "final_take_profit_price": round(tp_price, 8),
                "final_stop_loss_price": round(sl_price, 8),
                "realized_pnl": round(pnl, 4),
                "roi_pct": round(roi, 4),
            }
            return DcaSimulationOut(summary=summary, events=events)
        if hit_sl:
            cumulative_usdt = filled_steps[-1]["cumulative_usdt"]
            pnl, roi = calc_pnl(price, avg_price, cumulative_usdt)
            events.append(
                {
                    "tick_index": tick_idx,
                    "price": round(price, 8),
                    "action": "stop_loss",
                    "step_index": None,
                    "avg_price": round(avg_price, 8),
                    "take_profit_price": round(tp_price, 8),
                    "stop_loss_price": round(sl_price, 8),
                    "cumulative_usdt": round(cumulative_usdt, 4),
                    "realized_pnl": round(pnl, 4),
                    "roi_pct": round(roi, 4),
                }
            )
            summary = {
                "finished": True,
                "close_reason": "stop_loss",
                "ticks_processed": tick_idx + 1,
                "filled_steps": len(filled_steps),
                "total_usdt": round(cumulative_usdt, 4),
                "close_price": round(price, 8),
                "final_avg_price": round(avg_price, 8),
                "final_take_profit_price": round(tp_price, 8),
                "final_stop_loss_price": round(sl_price, 8),
                "realized_pnl": round(pnl, 4),
                "roi_pct": round(roi, 4),
            }
            return DcaSimulationOut(summary=summary, events=events)

    avg_price, tp_price, sl_price = calc_levels()
    cumulative = filled_steps[-1]["cumulative_usdt"] if filled_steps else 0.0
    close_price = float(price_path[-1])
    pnl, roi = calc_pnl(close_price, avg_price, cumulative)
    summary = {
        "finished": False,
        "close_reason": "open" if filled_steps else "not_opened",
        "ticks_processed": len(price_path),
        "filled_steps": len(filled_steps),
        "total_usdt": round(cumulative, 4),
        "close_price": round(close_price, 8),
        "final_avg_price": round(avg_price, 8),
        "final_take_profit_price": round(tp_price, 8),
        "final_stop_loss_price": round(sl_price, 8),
        "realized_pnl": round(pnl, 4),
        "roi_pct": round(roi, 4),
    }
    return DcaSimulationOut(summary=summary, events=events)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _Session() as session:
        yield session


def require_owner_key(x_owner_key: str = Header(alias="X-Owner-Key")) -> str:
    owner = (x_owner_key or "").strip()
    if not owner:
        raise api_error(401, ErrorCode.missing_owner_key, "missing X-Owner-Key")
    return owner


def require_metrics_access(
    request: Request,
    x_owner_key: str | None = Header(default=None, alias="X-Owner-Key"),
    x_metrics_token: str | None = Header(default=None, alias="X-Metrics-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    if not _metrics_enabled():
        raise api_error(404, ErrorCode.metrics_disabled, "metrics endpoint disabled")
    if not _is_client_in_allowlist(request):
        raise api_error(401, ErrorCode.metrics_auth_failed, "metrics client ip not allowed", {"ip": _client_ip(request)})

    mode = _metrics_auth_mode()
    if mode == "owner_key":
        owner = (x_owner_key or "").strip()
        if owner:
            return "owner_key"
    elif mode == "token":
        if _match_metrics_token(x_metrics_token, authorization):
            return "token"
    elif mode == "localhost":
        if _is_local_client(request):
            return "localhost"
    elif mode == "token_or_localhost":
        if _match_metrics_token(x_metrics_token, authorization) or _is_local_client(request):
            return "token_or_localhost"
    raise api_error(
        401,
        ErrorCode.metrics_auth_failed,
        "metrics auth failed",
        {"mode": mode, "hint": "use X-Owner-Key or X-Metrics-Token based on METRICS_PROM_AUTH_MODE"},
    )


def require_webhook_access(
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> bool:
    expected = (os.environ.get("TRADINGVIEW_WEBHOOK_SECRET") or "").strip()
    if not expected:
        return True
    provided = (x_webhook_secret or "").strip()
    if provided == expected:
        return True
    auth = (authorization or "").strip()
    if auth.lower().startswith("bearer ") and auth[7:].strip() == expected:
        return True
    raise api_error(401, ErrorCode.webhook_auth_failed, "webhook auth failed")


def api_error(status_code: int, code: ErrorCode | str, message: str, details: dict | None = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ErrorResponse(code=code, message=message, details=details).model_dump(),
    )


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


def _is_local_client(request: Request) -> bool:
    ip = _client_ip(request)
    return ip in ("127.0.0.1", "::1", "localhost")


def _metrics_enabled() -> bool:
    return (os.environ.get("METRICS_PROM_ENABLED") or "true").strip().lower() in ("1", "true", "yes")


def _metrics_allowlist_networks() -> list[ipaddress._BaseNetwork]:
    raw = (os.environ.get("METRICS_PROM_ALLOWLIST") or "").strip()
    if not raw:
        return []
    networks: list[ipaddress._BaseNetwork] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        try:
            if "/" not in token:
                addr = ipaddress.ip_address(token)
                token = f"{addr}/{32 if addr.version == 4 else 128}"
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            # ignore malformed entries instead of crashing the app
            continue
    return networks


def _is_client_in_allowlist(request: Request) -> bool:
    networks = _metrics_allowlist_networks()
    if not networks:
        return True
    ip_text = _client_ip(request)
    try:
        addr = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def _match_metrics_token(x_metrics_token: str | None, authorization: str | None) -> bool:
    expected = (os.environ.get("METRICS_PROM_TOKEN") or "").strip()
    if not expected:
        return False
    provided = (x_metrics_token or "").strip()
    if provided and provided == expected:
        return True
    auth = (authorization or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() == expected
    return False


def _metrics_auth_mode() -> str:
    # owner_key | token | localhost | token_or_localhost
    return (os.environ.get("METRICS_PROM_AUTH_MODE") or "owner_key").strip().lower()


def _auto_dispatch_enabled() -> bool:
    return (os.environ.get("EXECUTION_AUTO_DISPATCH") or "false").strip().lower() in ("1", "true", "yes")


def _parse_key_value_payload(text_payload: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in text_payload.replace("\r", "\n").split("\n"):
        line = raw_line.strip().strip(",")
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip().lower()] = value.strip().strip('"').strip("'")
    return parsed


def _payload_number(payload: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _payload_text(payload: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value:
            return text_value
    return None


def _normalize_signal_side(raw_value: str | None) -> SignalSide:
    token = (raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in ("buy", "long", "entry_long"):
        return SignalSide.buy
    if token in ("sell", "short", "entry_short"):
        return SignalSide.sell
    if token in ("close", "exit"):
        return SignalSide.close
    if token in ("closeall", "close_all", "flat"):
        return SignalSide.close_all
    raise api_error(422, ErrorCode.validation_error, "unsupported signal side", {"side": raw_value})


def normalize_tradingview_signal(raw_payload: dict[str, object], *, owner_key: str | None, secret_valid: bool, source: SignalSource) -> TradingViewSignalOut:
    effective_owner_key = owner_key or _payload_text(raw_payload, "owner_key", "owner", "account", "terminal_id")
    symbol = str(
        raw_payload.get("symbol")
        or raw_payload.get("ticker")
        or raw_payload.get("pair")
        or raw_payload.get("instrument")
        or ""
    ).strip()
    if not symbol:
        raise api_error(422, ErrorCode.validation_error, "signal symbol is required")
    side = _normalize_signal_side(str(raw_payload.get("side") or raw_payload.get("action") or raw_payload.get("signal") or ""))
    signal_id = str(raw_payload.get("signal_id") or raw_payload.get("id") or uuid.uuid4())
    return TradingViewSignalOut(
        signal_id=signal_id,
        source=source,
        received_at=datetime.now(timezone.utc),
        owner_key=effective_owner_key,
        secret_valid=secret_valid,
        symbol=symbol,
        side=side,
        timeframe=str(raw_payload.get("timeframe") or raw_payload.get("tf") or "") or None,
        strategy=str(raw_payload.get("strategy") or raw_payload.get("strategy_name") or raw_payload.get("bot") or "") or None,
        price=_payload_number(raw_payload, "price", "close", "entry_price"),
        volume=_payload_number(raw_payload, "volume", "qty", "quantity", "contracts"),
        take_profit=_payload_number(raw_payload, "tp", "take_profit", "tp_price"),
        stop_loss=_payload_number(raw_payload, "sl", "stop_loss", "sl_price"),
        risk_pct=_payload_number(raw_payload, "risk", "risk_pct", "risk_percent"),
        max_lag_seconds=int(_payload_number(raw_payload, "max_lag_seconds", "max_lag_sec") or 0) or None,
        raw_payload=raw_payload,
    )


TECHNICAL_TRADE_START_KINDS = frozenset({
    "rsi",
    "ultimate_oscillator",
    "bollinger_pctb",
    "ma",
    "adx",
    "stochastic",
    "macd",
    "parabolic_sar",
    "mfi",
    "cci",
    "heikin_ashi",
})


def _norm_timeframe_token(raw: object | None) -> str:
    if raw is None:
        return ""
    x = str(raw).strip().lower().replace(" ", "")
    if not x:
        return ""
    aliases = {"60m": "1h", "240m": "4h", "1440m": "1d", "daily": "1d"}
    return aliases.get(x, x)


def _signal_timeframe_tokens(signal: TradingViewSignalOut) -> set[str]:
    out: set[str] = set()
    candidates: list[object] = [signal.timeframe]
    if isinstance(signal.raw_payload, dict):
        for k in ("timeframe", "tf", "interval", "resolution"):
            if k in signal.raw_payload:
                candidates.append(signal.raw_payload.get(k))
    for cand in candidates:
        if cand is None:
            continue
        if isinstance(cand, str) and cand.strip().isdigit():
            out.add(_norm_timeframe_token(f"{cand.strip()}m"))
        t = _norm_timeframe_token(cand)
        if t:
            out.add(t)
    return {x for x in out if x}


def _desired_screener_side(params: dict[str, object]) -> str | None:
    sv = params.get("signal_value")
    if sv is None or not isinstance(sv, str):
        return None
    v = sv.strip().lower()
    return v if v else None


def _side_matches_screener(signal_side: SignalSide, want_side: str | None) -> bool:
    if not want_side:
        return True
    if want_side in ("buy", "long"):
        return signal_side == SignalSide.buy
    if want_side in ("sell", "short"):
        return signal_side == SignalSide.sell
    return False


def _trade_start_proof_for_row(raw_payload: dict[str, object], index: int, kind: str) -> bool | None:
    payload = raw_payload.get("pinebitz_tsc")
    if not isinstance(payload, dict):
        return None
    by_idx = payload.get("by_index")
    if isinstance(by_idx, dict) and str(index) in by_idx:
        return bool(by_idx[str(index)])
    by_kind = payload.get("by_kind")
    if isinstance(by_kind, dict) and kind in by_kind:
        return bool(by_kind[kind])
    results = payload.get("results")
    if isinstance(results, list) and index < len(results):
        entry = results[index]
        if isinstance(entry, dict):
            ek = str(entry.get("kind") or "")
            if ek == kind:
                return bool(entry.get("ok"))
    return None


def evaluate_trade_start_conditions(signal: TradingViewSignalOut, plan: BotPlanRow | None) -> dict[str, object]:
    """AND-chain from plan ``config_json.trade_start_conditions``.
    Proof-based for technical indicators: alert JSON may include ``pinebitz_tsc``.
    ``tv_webhook`` / aligned ``tv_screener`` / ``qfl_long`` can be satisfied from the signal alone.
    """
    out: dict[str, object] = {
        "enabled": False,
        "passed": True,
        "reject_reasons": [],
        "detail_rows": [],
    }
    if not plan:
        return out

    cfg = plan.config_json if isinstance(plan.config_json, dict) else {}
    tsc_raw = cfg.get("trade_start_conditions")
    if not isinstance(tsc_raw, dict):
        return out

    enabled = bool(tsc_raw.get("enabled"))
    out["enabled"] = enabled
    if not enabled:
        return out

    conditions_raw = tsc_raw.get("conditions") or []
    if not isinstance(conditions_raw, list):
        reasons = ["trade_start_conditions_invalid"]
        out["reject_reasons"] = reasons
        out["passed"] = False
        return out

    if len(conditions_raw) == 0:
        # enabled with zero clauses → no extra gate
        out["detail_rows"] = [{"note": "no_conditions_defined"}]
        return out

    detail_rows: list[dict[str, object]] = []
    reject_reasons: list[str] = []

    for index, row in enumerate(conditions_raw):
        if not isinstance(row, dict):
            reject_reasons.append(f"trade_start_bad_row[{index}]")
            detail_rows.append({"index": index, "kind": None, "ok": False, "reason": "not_object"})
            continue

        kind = str(row.get("kind") or "tv_webhook")
        params = row.get("params") if isinstance(row.get("params"), dict) else {}

        proof = _trade_start_proof_for_row(signal.raw_payload, index, kind)
        if proof is True:
            detail_rows.append({"index": index, "kind": kind, "ok": True, "reason": "pinebitz_tsc"})
            continue
        if proof is False:
            reject_reasons.append(f"trade_start_proof_false[{index}]_{kind}")
            detail_rows.append({"index": index, "kind": kind, "ok": False, "reason": "pinebitz_tsc_false"})
            continue

        ok, reason = False, ""

        if kind == "tv_webhook":
            ok, reason = True, "webhook_received"
        elif kind == "tv_screener":
            want_tf = _norm_timeframe_token(params.get("timeframe"))
            sig_tfs = _signal_timeframe_tokens(signal)
            tf_ok = (not want_tf) or (want_tf in sig_tfs)
            want_side = _desired_screener_side(params)
            side_ok = _side_matches_screener(signal.side, want_side)
            ok = tf_ok and side_ok
            if not tf_ok:
                reason = "screener_timeframe_mismatch"
            elif not side_ok:
                reason = "screener_side_mismatch"
            else:
                reason = "screener_aligned"
        elif kind == "qfl_long":
            ok = signal.side == SignalSide.buy
            reason = "qfl_long_buy" if ok else "qfl_long_requires_buy"
        elif kind in TECHNICAL_TRADE_START_KINDS:
            ok = False
            reason = "technical_requires_pinebitz_tsc"
        else:
            ok = False
            reason = f"unknown_kind_{kind}"

        if not ok:
            reject_reasons.append(f"trade_start[{index}]_{kind}:{reason}")
        detail_rows.append({"index": index, "kind": kind, "ok": ok, "reason": reason})

    out["detail_rows"] = detail_rows
    out["reject_reasons"] = reject_reasons
    out["passed"] = len(reject_reasons) == 0
    return out


def build_execution_job(signal: TradingViewSignalOut, plan: BotPlanRow | None, risk_checks: dict[str, object]) -> ExecutionJobOut:
    auto_reject_reasons = list(risk_checks.get("auto_reject_reasons", []))
    now = datetime.now(timezone.utc)
    status = ExecutionJobStatus.rejected if auto_reject_reasons else ExecutionJobStatus.queued
    return ExecutionJobOut(
        job_id=str(uuid.uuid4()),
        signal_id=signal.signal_id,
        owner_key=signal.owner_key,
        status=status,
        symbol=signal.symbol,
        side=signal.side,
        adapter=ExecutionAdapter.paper,
        venue="binance" if not plan else str((plan.connection.venue if hasattr(plan, "connection") and plan.connection else "binance")),
        market_lane="futures_um" if not plan else str((plan.connection.market_lane if hasattr(plan, "connection") and plan.connection else "futures_um")),
        connection_id=plan.connection_id if plan else None,
        created_at=now,
        updated_at=now,
        risk_checks=risk_checks,
        execution_payload={
            "symbol": signal.symbol,
            "side": signal.side.value,
            "price": signal.price,
            "volume": signal.volume,
            "take_profit": signal.take_profit,
            "stop_loss": signal.stop_loss,
            "risk_pct": signal.risk_pct,
            "strategy": signal.strategy,
            "timeframe": signal.timeframe,
            "plan_id": str(plan.id) if plan else None,
        },
        dispatch_result=None,
        notes="auto-rejected by risk policy" if auto_reject_reasons else None,
    )


def _hour_in_window(hour: int, start_hour: int, end_hour: int) -> bool:
    if start_hour <= end_hour:
        return start_hour <= hour <= end_hour
    return hour >= start_hour or hour <= end_hour


def evaluate_risk_policy(signal: TradingViewSignalOut, plan: BotPlanRow | None) -> dict[str, object]:
    policy = ((plan.config_json or {}).get("risk_policy") if plan else None) or {}
    allowed_symbols = [str(s).upper() for s in policy.get("allowed_symbols", []) if str(s).strip()]
    symbol_upper = signal.symbol.upper()
    active_jobs = [job for job in _execution_jobs if job.status in {ExecutionJobStatus.queued, ExecutionJobStatus.approved, ExecutionJobStatus.sent}]
    active_for_symbol = [job for job in active_jobs if job.symbol.upper() == symbol_upper]

    time_window_enabled = bool(policy.get("time_window_enabled", False))
    now = datetime.now(timezone.utc)
    within_time_window = True
    if time_window_enabled:
        within_time_window = _hour_in_window(
            now.hour,
            int(policy.get("start_hour_utc", 0)),
            int(policy.get("end_hour_utc", 23)),
        )

    allow_weekends = bool(policy.get("allow_weekends", True))
    weekend_allowed = allow_weekends or now.weekday() < 5
    symbol_allowed = not allowed_symbols or symbol_upper in allowed_symbols
    require_price = bool(policy.get("require_price", True))
    require_volume_or_risk = bool(policy.get("require_volume_or_risk", True))
    is_entry = signal.side in {SignalSide.buy, SignalSide.sell}
    has_price = signal.price is not None or not require_price
    # For close/close_all, we don't need volume/risk inputs.
    has_volume_or_risk = (signal.volume is not None or signal.risk_pct is not None) or not require_volume_or_risk or not is_entry
    max_open_positions = int(policy.get("max_open_positions", 3))
    # Default more permissive to avoid rejecting legitimate bursts.
    max_open_per_symbol = int(policy.get("max_open_per_symbol", 3))
    below_global_limit = max_open_positions <= 0 or len(active_jobs) < max_open_positions
    below_symbol_limit = max_open_per_symbol <= 0 or len(active_for_symbol) < max_open_per_symbol

    reasons = []
    if not symbol_allowed:
        reasons.append("symbol_not_allowed")
    if not within_time_window:
        reasons.append("outside_time_window")
    if not weekend_allowed:
        reasons.append("weekend_blocked")
    if not has_price:
        reasons.append("missing_price")
    if not has_volume_or_risk:
        reasons.append("missing_volume_or_risk")
    if not below_global_limit:
        reasons.append("max_open_positions_reached")
    if not below_symbol_limit:
        reasons.append("max_open_per_symbol_reached")
    daily_loss_limit_pct = float(policy.get("daily_loss_limit_pct", 0) or 0)
    runtime_state = compute_runtime_guard_state(signal.owner_key, daily_loss_limit_pct if daily_loss_limit_pct > 0 else None)
    if runtime_state.halted:
        reasons.extend(runtime_state.halt_reasons)

    trade_start = evaluate_trade_start_conditions(signal, plan)
    tsrj = trade_start.get("reject_reasons") or []
    if isinstance(tsrj, list):
        reasons.extend(str(x) for x in tsrj)

    return {
        "matched_plan_id": str(plan.id) if plan else None,
        "matched_plan_name": plan.name if plan else None,
        "matched_connection_id": str(plan.connection_id) if plan else None,
        "matched_owner_key": plan.connection.owner_key if plan and hasattr(plan, "connection") and plan.connection else signal.owner_key,
        "requested_plan_id": _payload_text(signal.raw_payload, "plan_id"),
        "requested_plan_name": _payload_text(signal.raw_payload, "plan_name", "bot", "strategy_name"),
        "symbol_allowed": symbol_allowed,
        "within_time_window": within_time_window,
        "weekend_allowed": weekend_allowed,
        "has_price": has_price,
        "has_volume_or_risk": has_volume_or_risk,
        "active_jobs_total": len(active_jobs),
        "active_jobs_for_symbol": len(active_for_symbol),
        "max_open_positions": max_open_positions,
        "max_open_per_symbol": max_open_per_symbol,
        "runtime_guard": runtime_state.model_dump(mode="json"),
        "trade_start": trade_start,
        "auto_reject_reasons": reasons,
    }


async def match_plan_for_signal(session: AsyncSession, signal: TradingViewSignalOut) -> BotPlanRow | None:
    stmt: Select = (
        select(BotPlanRow)
        .join(ExchangeConnectionRow, ExchangeConnectionRow.id == BotPlanRow.connection_id)
        .where(BotPlanRow.enabled.is_(True))
        .where(BotPlanRow.status == PlanStatus.active.value)
        .where(ExchangeConnectionRow.status == ConnectionStatus.active.value)
        .order_by(BotPlanRow.updated_at.desc())
    )
    if signal.owner_key:
        stmt = stmt.where(ExchangeConnectionRow.owner_key == signal.owner_key)
    rows = (await session.execute(stmt)).scalars().all()
    requested_plan_id = _payload_text(signal.raw_payload, "plan_id")
    requested_plan_name = _payload_text(signal.raw_payload, "plan_name", "bot", "strategy_name")
    if requested_plan_id:
        for row in rows:
            if str(row.id) == requested_plan_id:
                await session.refresh(row, attribute_names=["connection"])
                return row
    if requested_plan_name:
        for row in rows:
            if row.name.strip().lower() == requested_plan_name.strip().lower():
                await session.refresh(row, attribute_names=["connection"])
                return row
    for row in rows:
        pair = str((row.config_json or {}).get("pair") or "").upper()
        if pair == signal.symbol.upper():
            await session.refresh(row, attribute_names=["connection"])
            return row
    if not signal.owner_key:
        stmt_any_owner: Select = (
            select(BotPlanRow)
            .join(ExchangeConnectionRow, ExchangeConnectionRow.id == BotPlanRow.connection_id)
            .where(BotPlanRow.enabled.is_(True))
            .where(BotPlanRow.status == PlanStatus.active.value)
            .where(ExchangeConnectionRow.status == ConnectionStatus.active.value)
            .order_by(BotPlanRow.updated_at.desc())
        )
        all_rows = (await session.execute(stmt_any_owner)).scalars().all()
        pair_matches = [row for row in all_rows if str((row.config_json or {}).get("pair") or "").upper() == signal.symbol.upper()]
        if len(pair_matches) == 1:
            await session.refresh(pair_matches[0], attribute_names=["connection"])
            return pair_matches[0]
    return None


def append_execution_audit(
    *,
    job: ExecutionJobOut,
    event_type: str,
    details: dict[str, object] | None = None,
) -> None:
    _execution_audit.insert(
        0,
        ExecutionAuditEventOut(
            event_id=str(uuid.uuid4()),
            job_id=job.job_id,
            signal_id=job.signal_id,
            owner_key=job.owner_key,
            event_type=event_type,
            status=job.status,
            occurred_at=datetime.now(timezone.utc),
            details=details or {},
        ),
    )
    del _execution_audit[2000:]


def compute_runtime_guard_state(owner_key: str | None, daily_loss_limit_pct: float | None = None) -> RuntimeGuardStateOut:
    now = datetime.now(timezone.utc)
    equity_estimate = float(os.environ.get("RUNTIME_GUARD_EQUITY_ESTIMATE") or 1000.0)
    relevant_jobs = [job for job in _execution_jobs if job.owner_key in (None, owner_key)]
    open_jobs = [job for job in relevant_jobs if job.status in {ExecutionJobStatus.queued, ExecutionJobStatus.approved, ExecutionJobStatus.sent}]
    open_positions = [position for position in _paper_positions.values() if position.owner_key in (None, owner_key)]
    filled_today = []
    for event in _execution_audit:
        if event.owner_key not in (None, owner_key):
            continue
        if event.event_type != "dispatch_filled":
            continue
        if event.occurred_at.date() != now.date():
            continue
        filled_today.append(event)
    realized_pnl_today = 0.0
    for event in filled_today:
        realized_pnl_today += float(event.details.get("realized_pnl") or 0.0)
    unrealized_pnl = 0.0
    for position in open_positions:
        unrealized_pnl += float(position.unrealized_pnl or 0.0)
    halt_reasons: list[str] = []
    daily_loss_limit_value = None
    if daily_loss_limit_pct is not None and daily_loss_limit_pct > 0:
        daily_loss_limit_value = equity_estimate * (daily_loss_limit_pct / 100.0)
        if realized_pnl_today <= -daily_loss_limit_value:
            halt_reasons.append("daily_loss_limit_reached")
    return RuntimeGuardStateOut(
        owner_key=owner_key,
        as_of=now,
        open_jobs=len(open_jobs),
        open_symbols=sorted({job.symbol for job in open_jobs}),
        open_positions=len(open_positions),
        filled_jobs_today=len(filled_today),
        realized_pnl_today=round(realized_pnl_today, 4),
        unrealized_pnl=round(unrealized_pnl, 4),
        daily_loss_limit_pct=daily_loss_limit_pct,
        equity_estimate=round(equity_estimate, 4),
        daily_loss_limit_value=round(daily_loss_limit_value, 4) if daily_loss_limit_value is not None else None,
        halted=bool(halt_reasons),
        halt_reasons=halt_reasons,
    )


def _paper_position_key(owner_key: str | None, symbol: str) -> str:
    return f"{owner_key or 'public'}::{symbol.upper()}"


def _position_realized_pnl(position: PaperPositionOut, exit_price: float) -> float:
    if position.side == SignalSide.buy:
        return (exit_price - position.avg_entry_price) * position.quantity
    return (position.avg_entry_price - exit_price) * position.quantity


def _position_unrealized_pnl(position: PaperPositionOut, mark_price: float) -> float:
    if position.side == SignalSide.buy:
        return (mark_price - position.avg_entry_price) * position.quantity
    return (position.avg_entry_price - mark_price) * position.quantity


def apply_paper_fill(job: ExecutionJobOut, fill_price: float) -> tuple[dict[str, object], float]:
    key = _paper_position_key(job.owner_key, job.symbol)
    now = datetime.now(timezone.utc)
    qty = float(job.execution_payload.get("volume") or 0.0)
    if qty <= 0:
        qty = float(job.execution_payload.get("risk_pct") or 0.0) / 100.0
    qty = max(qty, 0.001)
    position = _paper_positions.get(key)
    realized_pnl = 0.0

    if job.side in {SignalSide.close, SignalSide.close_all}:
        if position:
            realized_pnl = _position_realized_pnl(position, fill_price)
            _paper_positions.pop(key, None)
            return (
                {
                    "position_action": "closed",
                    "previous_side": position.side.value,
                    "closed_quantity": position.quantity,
                },
                realized_pnl,
            )
        return ({"position_action": "noop_close", "closed_quantity": 0.0}, 0.0)

    if not position:
        _paper_positions[key] = PaperPositionOut(
            owner_key=job.owner_key,
            symbol=job.symbol,
            side=job.side,
            quantity=qty,
            avg_entry_price=fill_price,
            opened_at=now,
            updated_at=now,
        )
        return (
            {
                "position_action": "opened",
                "new_side": job.side.value,
                "quantity": qty,
                "avg_entry_price": fill_price,
            },
            0.0,
        )

    if position.side == job.side:
        new_qty = position.quantity + qty
        new_avg = ((position.avg_entry_price * position.quantity) + (fill_price * qty)) / new_qty
        _paper_positions[key] = position.model_copy(
            update={"quantity": new_qty, "avg_entry_price": new_avg, "updated_at": now}
        )
        return (
            {
                "position_action": "added",
                "new_side": job.side.value,
                "quantity": new_qty,
                "avg_entry_price": new_avg,
            },
            0.0,
        )

    realized_pnl = _position_realized_pnl(position, fill_price)
    _paper_positions[key] = PaperPositionOut(
        owner_key=job.owner_key,
        symbol=job.symbol,
        side=job.side,
        quantity=qty,
        avg_entry_price=fill_price,
        opened_at=now,
        updated_at=now,
    )
    return (
        {
            "position_action": "reversed",
            "previous_side": position.side.value,
            "new_side": job.side.value,
            "quantity": qty,
            "avg_entry_price": fill_price,
        },
        realized_pnl,
    )


def dispatch_execution_job(job: ExecutionJobOut) -> ExecutionJobOut:
    now = datetime.now(timezone.utc)
    if job.status not in {ExecutionJobStatus.approved, ExecutionJobStatus.queued}:
        raise api_error(
            422,
            ErrorCode.validation_error,
            "execution job must be queued or approved before dispatch",
            {"job_id": job.job_id, "status": job.status.value},
        )

    if job.adapter == ExecutionAdapter.paper:
        sent_job = job.model_copy(
            update={
                "status": ExecutionJobStatus.sent,
                "updated_at": now,
                "dispatch_result": {
                    "adapter": job.adapter.value,
                    "dispatched_at": now,
                    "mode": "paper",
                    "accepted": True,
                },
            }
        )
        append_execution_audit(
            job=sent_job,
            event_type="dispatch_sent",
            details={"adapter": sent_job.adapter.value, "mode": "paper"},
        )
        price = sent_job.execution_payload.get("price")
        if price is not None:
            filled_at = datetime.now(timezone.utc)
            position_result, realized_pnl = apply_paper_fill(sent_job, float(price))
            filled_job = sent_job.model_copy(
                update={
                    "status": ExecutionJobStatus.filled,
                    "updated_at": filled_at,
                    "dispatch_result": {
                        **(sent_job.dispatch_result or {}),
                        "filled_at": filled_at,
                        "fill_price": price,
                        "realized_pnl": round(realized_pnl, 4),
                        "position_result": position_result,
                    },
                    "notes": sent_job.notes or "paper adapter auto-filled job",
                }
            )
            append_execution_audit(
                job=filled_job,
                event_type="dispatch_filled",
                details={
                    "adapter": filled_job.adapter.value,
                    "fill_price": price,
                    "realized_pnl": float(filled_job.dispatch_result.get("realized_pnl") or 0.0)
                    if filled_job.dispatch_result
                    else 0.0,
                },
            )
            return filled_job
        return sent_job

    dispatched = job.model_copy(
        update={
            "status": ExecutionJobStatus.sent,
            "updated_at": now,
            "dispatch_result": {
                "adapter": job.adapter.value,
                "dispatched_at": now,
                "accepted": True,
                "mode": "stub",
            },
        }
    )
    append_execution_audit(
        job=dispatched,
        event_type="dispatch_sent",
        details={"adapter": dispatched.adapter.value, "mode": "stub"},
    )
    return dispatched


@app.middleware("http")
async def request_id_middleware(request: Request, call_next) -> Response:
    started = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    owner = request.headers.get("X-Owner-Key")
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = str(duration_ms)
    emit_request_log(
        _logger,
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
        owner_key=owner,
    )
    route_key = f"{request.method} {request.url.path}"
    _metrics.record(route_key=route_key, duration_ms=duration_ms, status_code=response.status_code)
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    payload = exc.detail
    if isinstance(payload, dict) and {"code", "message"} <= set(payload.keys()):
        body = {**payload, "request_id": request_id}
    else:
        body = ErrorResponse(code=ErrorCode.http_error, message=str(payload), details=None, request_id=request_id).model_dump()
    return JSONResponse(status_code=exc.status_code, content=body, headers={"X-Request-ID": request_id or ""})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        code=ErrorCode.validation_error,
        message="request validation failed",
        details={"errors": exc.errors()},
        request_id=request_id,
    ).model_dump()
    return JSONResponse(status_code=422, content=body, headers={"X-Request-ID": request_id or ""})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, _: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        code=ErrorCode.internal_server_error,
        message="internal server error",
        details=None,
        request_id=request_id,
    ).model_dump()
    return JSONResponse(status_code=500, content=body, headers={"X-Request-ID": request_id or ""})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def dashboard_index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/dashboard")
async def dashboard_page() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
async def health_ready(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        raise api_error(503, ErrorCode.internal_server_error, "not ready", {"dependency": "postgres"})
    return {"status": "ready"}


@app.get("/metrics")
async def metrics(owner_key: str = Depends(require_owner_key)) -> dict:
    # Keep this simple and local-only for now; can be promoted to Prometheus later.
    return {"owner_key": owner_key, **_metrics.snapshot()}


@app.get("/metrics/prometheus")
async def metrics_prometheus(_: str = Depends(require_metrics_access)) -> Response:
    return Response(content=_metrics.snapshot_prometheus(), media_type="text/plain; version=0.0.4")


@app.post("/dca/preview", response_model=DcaPreviewOut, responses=ERROR_RESPONSES)
async def dca_preview(
    config: DcaBotConfig,
    reference_price: float = Query(default=1.0, gt=0),
    _: str = Depends(require_owner_key),
) -> DcaPreviewOut:
    return build_dca_preview(config, reference_price)


@app.post("/dca/simulate", response_model=DcaSimulationOut, responses=ERROR_RESPONSES)
async def dca_simulate(
    config: DcaBotConfig,
    price_path: str = Query(..., min_length=1),
    _: str = Depends(require_owner_key),
) -> DcaSimulationOut:
    try:
        prices = [float(x.strip()) for x in price_path.split(",") if x.strip()]
    except ValueError:
        raise api_error(422, ErrorCode.validation_error, "price_path must contain only numbers")
    if not prices:
        raise api_error(422, ErrorCode.validation_error, "price_path must not be empty")
    return build_dca_simulation(config, prices)


@app.post("/signals/tradingview/webhook", response_model=TradingViewSignalOut, status_code=202, responses=ERROR_RESPONSES)
async def tradingview_webhook(
    request: Request,
    owner_key: str | None = Header(default=None, alias="X-Owner-Key"),
    secret_valid: bool = Depends(require_webhook_access),
    session: AsyncSession = Depends(get_session),
) -> TradingViewSignalOut:
    content_type = (request.headers.get("content-type") or "").lower()
    raw_text = (await request.body()).decode("utf-8", errors="ignore").strip()
    if not raw_text:
        raise api_error(422, ErrorCode.validation_error, "webhook body is empty")

    raw_payload: dict[str, object]
    source = SignalSource.tradingview
    if "application/json" in content_type:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            raise api_error(422, ErrorCode.validation_error, "invalid json payload")
        if not isinstance(parsed, dict):
            raise api_error(422, ErrorCode.validation_error, "json payload must be an object")
        raw_payload = parsed
    else:
        raw_payload = _parse_key_value_payload(raw_text)
        source = SignalSource.gateway_text
        if not raw_payload:
            raise api_error(422, ErrorCode.validation_error, "text payload must contain key=value pairs")

    signal = normalize_tradingview_signal(raw_payload, owner_key=owner_key, secret_valid=secret_valid, source=source)
    _signal_inbox.insert(0, signal)
    del _signal_inbox[200:]
    plan = await match_plan_for_signal(session, signal)
    risk_checks = evaluate_risk_policy(signal, plan)
    job = build_execution_job(signal, plan, risk_checks)
    _execution_jobs.insert(0, job)
    del _execution_jobs[500:]
    append_execution_audit(
        job=job,
        event_type="job_created",
        details={
            "matched_plan_id": risk_checks.get("matched_plan_id"),
            "matched_plan_name": risk_checks.get("matched_plan_name"),
            "matched_connection_id": risk_checks.get("matched_connection_id"),
            "matched_owner_key": risk_checks.get("matched_owner_key"),
            "requested_plan_id": risk_checks.get("requested_plan_id"),
            "requested_plan_name": risk_checks.get("requested_plan_name"),
            "trade_start": risk_checks.get("trade_start"),
            "auto_reject_reasons": risk_checks.get("auto_reject_reasons", []),
        },
    )
    return signal


@app.get("/signals/tradingview/inbox", response_model=TradingViewSignalListOut, responses=ERROR_RESPONSES)
async def tradingview_inbox(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    owner_key: str = Depends(require_owner_key),
) -> TradingViewSignalListOut:
    filtered = [signal for signal in _signal_inbox if signal.owner_key in (None, owner_key)]
    items = filtered[offset : offset + limit]
    return TradingViewSignalListOut(items=items, meta={"total": len(filtered), "limit": limit, "offset": offset})


@app.get("/execution/jobs", response_model=ExecutionJobListOut, responses=ERROR_RESPONSES)
async def list_execution_jobs(
    status: ExecutionJobStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    owner_key: str = Depends(require_owner_key),
) -> ExecutionJobListOut:
    filtered = [job for job in _execution_jobs if job.owner_key in (None, owner_key)]
    if status is not None:
        filtered = [job for job in filtered if job.status == status]
    items = filtered[offset : offset + limit]
    return ExecutionJobListOut(items=items, meta={"total": len(filtered), "limit": limit, "offset": offset})


@app.post("/execution/jobs/purge-test", responses=ERROR_RESPONSES)
async def purge_execution_jobs_for_test(
    statuses: str = Query(default="queued,rejected"),
    owner_key: str = Depends(require_owner_key),
) -> dict[str, object]:
    tokens = [part.strip().lower() for part in statuses.split(",") if part.strip()]
    target_statuses: set[ExecutionJobStatus] = set()
    for token in tokens:
        try:
            target_statuses.add(ExecutionJobStatus(token))
        except ValueError:
            raise api_error(422, ErrorCode.validation_error, "invalid execution status in purge request", {"status": token})
    if not target_statuses:
        raise api_error(422, ErrorCode.validation_error, "no statuses provided for purge")

    before = len(_execution_jobs)
    kept: list[ExecutionJobOut] = []
    removed = 0
    for job in _execution_jobs:
        if job.owner_key not in (None, owner_key):
            kept.append(job)
            continue
        if job.status in target_statuses:
            removed += 1
            continue
        kept.append(job)
    _execution_jobs[:] = kept
    return {
        "owner_key": owner_key,
        "requested_statuses": [status.value for status in sorted(target_statuses, key=lambda item: item.value)],
        "before": before,
        "removed": removed,
        "after": len(_execution_jobs),
    }


@app.get("/execution/audit", response_model=ExecutionAuditEventListOut, responses=ERROR_RESPONSES)
async def list_execution_audit(
    job_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    owner_key: str = Depends(require_owner_key),
) -> ExecutionAuditEventListOut:
    filtered = [event for event in _execution_audit if event.owner_key in (None, owner_key)]
    if job_id:
        filtered = [event for event in filtered if event.job_id == job_id]
    items = filtered[offset : offset + limit]
    return ExecutionAuditEventListOut(items=items, meta={"total": len(filtered), "limit": limit, "offset": offset})


@app.get("/execution/runtime-state", response_model=RuntimeGuardStateOut, responses=ERROR_RESPONSES)
async def execution_runtime_state(
    daily_loss_limit_pct: float | None = Query(default=None, ge=0, le=100),
    owner_key: str = Depends(require_owner_key),
) -> RuntimeGuardStateOut:
    return compute_runtime_guard_state(owner_key, daily_loss_limit_pct)


@app.get("/execution/paper/positions", response_model=PaperPositionListOut, responses=ERROR_RESPONSES)
async def list_paper_positions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    owner_key: str = Depends(require_owner_key),
) -> PaperPositionListOut:
    items = [position for position in _paper_positions.values() if position.owner_key in (None, owner_key)]
    paged = items[offset : offset + limit]
    return PaperPositionListOut(items=paged, meta={"total": len(items), "limit": limit, "offset": offset})


@app.get("/execution/paper/mark-to-market", response_model=PaperPositionListOut, responses=ERROR_RESPONSES)
async def paper_mark_to_market(
    symbol: str,
    mark_price: float = Query(..., gt=0),
    owner_key: str = Depends(require_owner_key),
) -> PaperPositionListOut:
    updated: list[PaperPositionOut] = []
    target_symbol = symbol.upper()
    for key, position in list(_paper_positions.items()):
        if position.owner_key not in (None, owner_key):
            continue
        if position.symbol.upper() != target_symbol:
            continue
        unrealized = _position_unrealized_pnl(position, mark_price)
        next_position = position.model_copy(
            update={
                "mark_price": mark_price,
                "unrealized_pnl": round(unrealized, 4),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        _paper_positions[key] = next_position
        updated.append(next_position)
    return PaperPositionListOut(items=updated, meta={"total": len(updated), "limit": len(updated), "offset": 0})


@app.get("/execution/jobs/{job_id}", response_model=ExecutionJobOut, responses=ERROR_RESPONSES)
async def get_execution_job(
    job_id: str,
    owner_key: str = Depends(require_owner_key),
) -> ExecutionJobOut:
    for job in _execution_jobs:
        if job.job_id == job_id and job.owner_key in (None, owner_key):
            return job
    raise api_error(404, ErrorCode.job_not_found, "execution job not found", {"job_id": job_id})


@app.post("/execution/jobs/{job_id}/dispatch", response_model=ExecutionJobOut, responses=ERROR_RESPONSES)
async def dispatch_execution_job_endpoint(
    job_id: str,
    owner_key: str = Depends(require_owner_key),
) -> ExecutionJobOut:
    for idx, job in enumerate(_execution_jobs):
        if job.job_id != job_id or job.owner_key not in (None, owner_key):
            continue
        dispatched = dispatch_execution_job(job)
        _execution_jobs[idx] = dispatched
        return dispatched
    raise api_error(404, ErrorCode.job_not_found, "execution job not found", {"job_id": job_id})


@app.patch("/execution/jobs/{job_id}", response_model=ExecutionJobOut, responses=ERROR_RESPONSES)
async def patch_execution_job(
    job_id: str,
    payload: ExecutionJobActionIn,
    owner_key: str = Depends(require_owner_key),
) -> ExecutionJobOut:
    valid_transitions = {
        ExecutionJobStatus.queued: {ExecutionJobStatus.approved, ExecutionJobStatus.rejected, ExecutionJobStatus.failed},
        ExecutionJobStatus.approved: {ExecutionJobStatus.sent, ExecutionJobStatus.rejected, ExecutionJobStatus.failed},
        ExecutionJobStatus.sent: {ExecutionJobStatus.filled, ExecutionJobStatus.failed},
        ExecutionJobStatus.rejected: set(),
        ExecutionJobStatus.filled: set(),
        ExecutionJobStatus.failed: set(),
    }
    for idx, job in enumerate(_execution_jobs):
        if job.job_id != job_id or job.owner_key not in (None, owner_key):
            continue
        if payload.status != job.status and payload.status not in valid_transitions[job.status]:
            raise api_error(
                422,
                ErrorCode.validation_error,
                "invalid execution job status transition",
                {"from": job.status.value, "to": payload.status.value},
            )
        updated = job.model_copy(
            update={
                "status": payload.status,
                "notes": payload.notes if payload.notes is not None else job.notes,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        append_execution_audit(
            job=updated,
            event_type="status_changed",
            details={"from": job.status.value, "to": payload.status.value, "notes": payload.notes},
        )
        if payload.status == ExecutionJobStatus.approved and _auto_dispatch_enabled():
            updated = dispatch_execution_job(updated)
        _execution_jobs[idx] = updated
        return updated
    raise api_error(404, ErrorCode.job_not_found, "execution job not found", {"job_id": job_id})


@app.post("/connections", response_model=ConnectionOut, status_code=201, responses=ERROR_RESPONSES)
async def create_connection(
    payload: ConnectionCreate,
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> ExchangeConnectionRow:
    row = ExchangeConnectionRow(**payload.model_dump(mode="json"), owner_key=owner_key)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@app.get("/connections", response_model=ConnectionListOut, responses=ERROR_RESPONSES)
async def list_connections(
    include_deleted: bool = Query(default=False),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|label|venue|status)$"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> ConnectionListOut:
    stmt: Select = select(ExchangeConnectionRow).where(ExchangeConnectionRow.owner_key == owner_key)
    count_stmt: Select = select(func.count()).select_from(ExchangeConnectionRow).where(
        ExchangeConnectionRow.owner_key == owner_key
    )
    if not include_deleted:
        stmt = stmt.where(ExchangeConnectionRow.status != ConnectionStatus.deleted.value)
        count_stmt = count_stmt.where(ExchangeConnectionRow.status != ConnectionStatus.deleted.value)

    sort_map = {
        "created_at": ExchangeConnectionRow.created_at,
        "updated_at": ExchangeConnectionRow.updated_at,
        "label": ExchangeConnectionRow.label,
        "venue": ExchangeConnectionRow.venue,
        "status": ExchangeConnectionRow.status,
    }
    col = sort_map[sort_by]
    stmt = stmt.order_by(col.asc() if sort_dir == "asc" else col.desc()).limit(limit).offset(offset)

    rows = (await session.execute(stmt)).scalars().all()
    total = int((await session.execute(count_stmt)).scalar_one())
    return ConnectionListOut(items=rows, meta={"total": total, "limit": limit, "offset": offset})


@app.get(
    "/connections/{connection_id}",
    response_model=ConnectionOut,
    responses=ERROR_RESPONSES,
)
async def get_connection(
    connection_id: uuid.UUID,
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> ExchangeConnectionRow:
    row = await session.get(ExchangeConnectionRow, connection_id)
    if not row or row.owner_key != owner_key or row.status == ConnectionStatus.deleted.value:
        raise api_error(404, ErrorCode.connection_not_found, "connection not found", {"connection_id": str(connection_id)})
    return row


@app.patch("/connections/{connection_id}", response_model=ConnectionOut, responses=ERROR_RESPONSES)
async def patch_connection(
    connection_id: uuid.UUID,
    payload: ConnectionPatch,
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> ExchangeConnectionRow:
    row = await session.get(ExchangeConnectionRow, connection_id)
    if not row or row.owner_key != owner_key or row.status == ConnectionStatus.deleted.value:
        raise api_error(404, ErrorCode.connection_not_found, "connection not found", {"connection_id": str(connection_id)})
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return row


@app.delete("/connections/{connection_id}", response_model=ConnectionOut, responses=ERROR_RESPONSES)
async def delete_connection(
    connection_id: uuid.UUID,
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> ExchangeConnectionRow:
    row = await session.get(ExchangeConnectionRow, connection_id)
    if not row or row.owner_key != owner_key or row.status == ConnectionStatus.deleted.value:
        raise api_error(404, ErrorCode.connection_not_found, "connection not found", {"connection_id": str(connection_id)})
    row.status = ConnectionStatus.deleted.value
    plan_stmt = select(BotPlanRow).where(BotPlanRow.connection_id == row.id, BotPlanRow.status != PlanStatus.deleted.value)
    plans = (await session.execute(plan_stmt)).scalars().all()
    for p in plans:
        p.enabled = False
        p.status = PlanStatus.deleted.value
    await session.commit()
    await session.refresh(row)
    return row


@app.post("/bot-plans", response_model=PlanOut, status_code=201, responses=ERROR_RESPONSES)
async def create_bot_plan(
    payload: PlanCreate,
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> BotPlanRow:
    connection = await session.get(ExchangeConnectionRow, payload.connection_id)
    if not connection or connection.owner_key != owner_key or connection.status == ConnectionStatus.deleted.value:
        raise api_error(
            404,
            ErrorCode.connection_not_found,
            "connection not found for owner",
            {"connection_id": str(payload.connection_id)},
        )

    row = BotPlanRow(**payload.model_dump(mode="json"))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@app.get("/bot-plans", response_model=PlanListOut, responses=ERROR_RESPONSES)
async def list_bot_plans(
    connection_id: uuid.UUID | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|name|status)$"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> PlanListOut:
    stmt: Select = (
        select(BotPlanRow)
        .join(ExchangeConnectionRow, BotPlanRow.connection_id == ExchangeConnectionRow.id)
        .where(ExchangeConnectionRow.owner_key == owner_key, ExchangeConnectionRow.status != ConnectionStatus.deleted.value)
    )
    count_stmt: Select = (
        select(func.count())
        .select_from(BotPlanRow)
        .join(ExchangeConnectionRow, BotPlanRow.connection_id == ExchangeConnectionRow.id)
        .where(ExchangeConnectionRow.owner_key == owner_key, ExchangeConnectionRow.status != ConnectionStatus.deleted.value)
    )
    if connection_id:
        stmt = stmt.where(BotPlanRow.connection_id == connection_id)
        count_stmt = count_stmt.where(BotPlanRow.connection_id == connection_id)
    if not include_deleted:
        stmt = stmt.where(BotPlanRow.status != PlanStatus.deleted.value)
        count_stmt = count_stmt.where(BotPlanRow.status != PlanStatus.deleted.value)

    sort_map = {
        "created_at": BotPlanRow.created_at,
        "updated_at": BotPlanRow.updated_at,
        "name": BotPlanRow.name,
        "status": BotPlanRow.status,
    }
    col = sort_map[sort_by]
    stmt = stmt.order_by(col.asc() if sort_dir == "asc" else col.desc()).limit(limit).offset(offset)

    rows = (await session.execute(stmt)).scalars().all()
    total = int((await session.execute(count_stmt)).scalar_one())
    return PlanListOut(items=rows, meta={"total": total, "limit": limit, "offset": offset})


@app.get(
    "/bot-plans/{plan_id}",
    response_model=PlanOut,
    responses=ERROR_RESPONSES,
)
async def get_bot_plan(
    plan_id: uuid.UUID,
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> BotPlanRow:
    row = await session.get(BotPlanRow, plan_id)
    if not row or row.status == PlanStatus.deleted.value:
        raise api_error(404, ErrorCode.plan_not_found, "plan not found", {"plan_id": str(plan_id)})
    connection = await session.get(ExchangeConnectionRow, row.connection_id)
    if not connection or connection.owner_key != owner_key or connection.status == ConnectionStatus.deleted.value:
        raise api_error(404, ErrorCode.plan_not_found, "plan not found", {"plan_id": str(plan_id)})
    return row


@app.patch("/bot-plans/{plan_id}", response_model=PlanOut, responses=ERROR_RESPONSES)
async def patch_bot_plan(
    plan_id: uuid.UUID,
    payload: PlanPatch,
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> BotPlanRow:
    row = await session.get(BotPlanRow, plan_id)
    if not row or row.status == PlanStatus.deleted.value:
        raise api_error(404, ErrorCode.plan_not_found, "plan not found", {"plan_id": str(plan_id)})
    connection = await session.get(ExchangeConnectionRow, row.connection_id)
    if not connection or connection.owner_key != owner_key or connection.status == ConnectionStatus.deleted.value:
        raise api_error(404, ErrorCode.plan_not_found, "plan not found", {"plan_id": str(plan_id)})
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return row


@app.delete("/bot-plans/{plan_id}", response_model=PlanOut, responses=ERROR_RESPONSES)
async def delete_bot_plan(
    plan_id: uuid.UUID,
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> BotPlanRow:
    row = await session.get(BotPlanRow, plan_id)
    if not row or row.status == PlanStatus.deleted.value:
        raise api_error(404, ErrorCode.plan_not_found, "plan not found", {"plan_id": str(plan_id)})
    connection = await session.get(ExchangeConnectionRow, row.connection_id)
    if not connection or connection.owner_key != owner_key or connection.status == ConnectionStatus.deleted.value:
        raise api_error(404, ErrorCode.plan_not_found, "plan not found", {"plan_id": str(plan_id)})
    row.status = PlanStatus.deleted.value
    row.enabled = False
    await session.commit()
    await session.refresh(row)
    return row


@app.get("/dashboard/bots", response_model=list[DashboardBotRow], responses=ERROR_RESPONSES)
async def list_dashboard_bots(
    market_lane: str | None = Query(default=None),
    venue: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    owner_key: str = Depends(require_owner_key),
    session: AsyncSession = Depends(get_session),
) -> list[DashboardBotRow]:
    stmt: Select = (
        select(
            BotPlanRow.id.label("plan_id"),
            BotPlanRow.name.label("plan_name"),
            BotPlanRow.enabled,
            BotPlanRow.instrument_kind,
            ExchangeConnectionRow.venue,
            ExchangeConnectionRow.market_lane,
            ExchangeConnectionRow.environment,
            ExchangeConnectionRow.label.label("connection_label"),
        )
        .join(ExchangeConnectionRow, BotPlanRow.connection_id == ExchangeConnectionRow.id)
        .where(
            ExchangeConnectionRow.owner_key == owner_key,
            ExchangeConnectionRow.status != ConnectionStatus.deleted.value,
            BotPlanRow.status != PlanStatus.deleted.value,
        )
        .order_by(BotPlanRow.created_at.desc())
    )

    if market_lane:
        stmt = stmt.where(ExchangeConnectionRow.market_lane == market_lane)
    if venue:
        stmt = stmt.where(ExchangeConnectionRow.venue == venue)
    if enabled is not None:
        stmt = stmt.where(BotPlanRow.enabled == enabled)

    rows = (await session.execute(stmt)).mappings().all()
    return [DashboardBotRow(**dict(r)) for r in rows]
