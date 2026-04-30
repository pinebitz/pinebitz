from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConnectionStatus(StrEnum):
    active = "active"
    paused = "paused"
    error = "error"
    deleted = "deleted"


class PlanStatus(StrEnum):
    active = "active"
    paused = "paused"
    deleted = "deleted"


class InstrumentKind(StrEnum):
    futures = "futures"
    spot = "spot"


class TradeDirection(StrEnum):
    long = "long"
    short = "short"


class StartOrderType(StrEnum):
    market = "market"
    limit = "limit"


class MarginMode(StrEnum):
    cross = "cross"
    isolated = "isolated"


class EntryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_order_usdt: float = Field(default=25, ge=0)
    leverage: int = Field(default=5, ge=1, le=125)
    start_order_type: StartOrderType = StartOrderType.market
    margin_mode: MarginMode = MarginMode.cross


class AveragingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_orders: int = Field(default=3, ge=0, le=50)
    first_deviation_pct: float = Field(default=3, ge=0)
    deviation_multiplier: float = Field(default=1.5, ge=0)
    safety_order_size: float = Field(default=25, ge=0)
    order_size_multiplier: float = Field(default=1, ge=0)


class ExitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tp_pct: float = Field(default=1.0, ge=0)
    stop_loss_pct: float = Field(default=0, ge=0)


class TradeStartConditionRow(BaseModel):
    """Single AND-clause for when a plan may start trading (UI + future evaluator)."""

    model_config = ConfigDict(extra="forbid")

    kind: str = "tv_webhook"
    timeframe: str | None = None  # legacy mirror for tv_screener; prefer params
    signal_value: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)  # kind-specific fields from dashboard


class TradeStartConditionsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    conditions: list[TradeStartConditionRow] = Field(default_factory=list)


class RiskPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_open_positions: int = Field(default=3, ge=0, le=1000)
    max_open_per_symbol: int = Field(default=1, ge=0, le=1000)
    daily_loss_limit_pct: float = Field(default=0, ge=0, le=100)
    time_window_enabled: bool = False
    start_hour_utc: int = Field(default=0, ge=0, le=23)
    end_hour_utc: int = Field(default=23, ge=0, le=23)
    allowed_symbols: list[str] = Field(default_factory=list)
    allow_weekends: bool = True
    require_price: bool = True
    require_volume_or_risk: bool = True


class DcaBotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair: str = Field(min_length=1, max_length=64)
    direction: TradeDirection = TradeDirection.long
    notes: str = ""
    entry: EntryConfig = Field(default_factory=EntryConfig)
    averaging: AveragingConfig = Field(default_factory=AveragingConfig)
    exit: ExitConfig = Field(default_factory=ExitConfig)
    risk_policy: RiskPolicyConfig = Field(default_factory=RiskPolicyConfig)
    trade_start_conditions: TradeStartConditionsConfig = Field(default_factory=TradeStartConditionsConfig)


class ConnectionCreate(BaseModel):
    label: str = Field(min_length=1, max_length=512)
    venue: str = Field(min_length=1, max_length=64)
    market_lane: str = Field(min_length=1, max_length=64)
    environment: str = Field(min_length=1, max_length=64)
    credential_ref: str | None = None
    status: ConnectionStatus = ConnectionStatus.active
    extra: dict[str, Any] | None = None


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_key: str | None
    label: str
    venue: str
    market_lane: str
    environment: str
    credential_ref: str | None
    status: ConnectionStatus
    extra: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class PlanCreate(BaseModel):
    connection_id: uuid.UUID
    name: str = Field(min_length=1, max_length=512)
    instrument_kind: InstrumentKind | None = None
    enabled: bool = False
    plan_version: int = 1
    config_json: DcaBotConfig
    status: PlanStatus = PlanStatus.active


class ConnectionPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=512)
    credential_ref: str | None = None
    status: ConnectionStatus | None = None
    extra: dict[str, Any] | None = None


class PlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    instrument_kind: InstrumentKind | None = None
    enabled: bool | None = None
    plan_version: int | None = None
    config_json: DcaBotConfig | None = None
    status: PlanStatus | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connection_id: uuid.UUID
    name: str
    instrument_kind: InstrumentKind | None
    status: PlanStatus
    enabled: bool
    plan_version: int
    config_json: DcaBotConfig
    created_at: datetime
    updated_at: datetime


class DashboardBotRow(BaseModel):
    plan_id: uuid.UUID
    plan_name: str
    enabled: bool
    instrument_kind: InstrumentKind | None
    venue: str
    market_lane: str
    environment: str
    connection_label: str


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class ConnectionListOut(BaseModel):
    items: list[ConnectionOut]
    meta: PageMeta


class PlanListOut(BaseModel):
    items: list[PlanOut]
    meta: PageMeta


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class PreviewStepOut(BaseModel):
    index: int
    kind: str
    deviation_pct: float
    order_size_usdt: float
    cumulative_usdt: float
    trigger_price: float
    avg_price: float
    take_profit_price: float


class PreviewSummaryOut(BaseModel):
    entry_price: float
    total_steps: int
    total_usdt: float
    max_deviation_pct: float
    estimated_tp_price: float


class DcaPreviewOut(BaseModel):
    summary: PreviewSummaryOut
    steps: list[PreviewStepOut]


class SimulationEventOut(BaseModel):
    tick_index: int
    price: float
    action: str
    step_index: int | None = None
    avg_price: float | None = None
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    cumulative_usdt: float | None = None
    realized_pnl: float | None = None
    roi_pct: float | None = None


class SimulationSummaryOut(BaseModel):
    finished: bool
    close_reason: str
    ticks_processed: int
    filled_steps: int
    total_usdt: float
    close_price: float
    final_avg_price: float
    final_take_profit_price: float
    final_stop_loss_price: float
    realized_pnl: float
    roi_pct: float


class DcaSimulationOut(BaseModel):
    summary: SimulationSummaryOut
    events: list[SimulationEventOut]


class SignalSide(StrEnum):
    buy = "buy"
    sell = "sell"
    close = "close"
    close_all = "close_all"


class SignalSource(StrEnum):
    tradingview = "tradingview"
    gateway_text = "gateway_text"


class TradingViewSignalOut(BaseModel):
    signal_id: str
    source: SignalSource
    received_at: datetime
    owner_key: str | None = None
    secret_valid: bool
    symbol: str
    side: SignalSide
    timeframe: str | None = None
    strategy: str | None = None
    price: float | None = None
    volume: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    risk_pct: float | None = None
    max_lag_seconds: int | None = None
    raw_payload: dict[str, Any]


class TradingViewSignalListOut(BaseModel):
    items: list[TradingViewSignalOut]
    meta: PageMeta


class ExecutionJobStatus(StrEnum):
    queued = "queued"
    approved = "approved"
    rejected = "rejected"
    sent = "sent"
    filled = "filled"
    failed = "failed"


class ExecutionAdapter(StrEnum):
    paper = "paper"
    binance = "binance"
    mt5_bridge = "mt5_bridge"


class ExecutionJobOut(BaseModel):
    job_id: str
    signal_id: str
    owner_key: str | None = None
    status: ExecutionJobStatus
    symbol: str
    side: SignalSide
    adapter: ExecutionAdapter = ExecutionAdapter.paper
    venue: str
    market_lane: str
    connection_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    risk_checks: dict[str, Any]
    execution_payload: dict[str, Any]
    dispatch_result: dict[str, Any] | None = None
    notes: str | None = None


class ExecutionJobListOut(BaseModel):
    items: list[ExecutionJobOut]
    meta: PageMeta


class ExecutionJobActionIn(BaseModel):
    status: ExecutionJobStatus
    notes: str | None = None


class ExecutionAuditEventOut(BaseModel):
    event_id: str
    job_id: str
    signal_id: str
    owner_key: str | None = None
    event_type: str
    status: ExecutionJobStatus | None = None
    occurred_at: datetime
    details: dict[str, Any]


class ExecutionAuditEventListOut(BaseModel):
    items: list[ExecutionAuditEventOut]
    meta: PageMeta


class RuntimeGuardStateOut(BaseModel):
    owner_key: str | None = None
    as_of: datetime
    open_jobs: int
    open_symbols: list[str]
    open_positions: int
    filled_jobs_today: int
    realized_pnl_today: float
    unrealized_pnl: float
    daily_loss_limit_pct: float | None = None
    equity_estimate: float
    daily_loss_limit_value: float | None = None
    halted: bool
    halt_reasons: list[str]


class PaperPositionOut(BaseModel):
    owner_key: str | None = None
    symbol: str
    side: SignalSide
    quantity: float
    avg_entry_price: float
    mark_price: float | None = None
    unrealized_pnl: float | None = None
    opened_at: datetime
    updated_at: datetime


class PaperPositionListOut(BaseModel):
    items: list[PaperPositionOut]
    meta: PageMeta
