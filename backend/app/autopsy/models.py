from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


def _finite(value: float) -> float:
    if value != value or value in (float("inf"), -float("inf")):
        raise ValueError("Autopsy values must be finite")
    return value


class AutopsyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PnLSummary(AutopsyModel):
    initial_equity: float
    gross_pnl: float
    fees: float
    slippage: float
    total_cost: float
    net_pnl: float
    final_equity: float

    _finite_values = field_validator(
        "initial_equity",
        "gross_pnl",
        "fees",
        "slippage",
        "total_cost",
        "net_pnl",
        "final_equity",
    )(_finite)


class PnLReconciliation(AutopsyModel):
    gross_less_costs: float
    reported_net_pnl: float
    pnl_difference: float
    initial_plus_net: float
    reported_final_equity: float
    equity_difference: float
    reconciled: bool

    _finite_values = field_validator(
        "gross_less_costs",
        "reported_net_pnl",
        "pnl_difference",
        "initial_plus_net",
        "reported_final_equity",
        "equity_difference",
    )(_finite)


class PeriodAttribution(AutopsyModel):
    label: str
    start: datetime
    end: datetime
    gross_pnl: float
    fees: float
    slippage: float
    net_pnl: float
    start_equity: float
    end_equity: float
    period_return: float
    event_count: int

    _finite_values = field_validator(
        "gross_pnl",
        "fees",
        "slippage",
        "net_pnl",
        "start_equity",
        "end_equity",
        "period_return",
    )(_finite)


class PeriodBreakdown(AutopsyModel):
    monthly: tuple[PeriodAttribution, ...]
    quarterly: tuple[PeriodAttribution, ...]
    yearly: tuple[PeriodAttribution, ...]


class TradeAttribution(AutopsyModel):
    trade_id: str
    direction: str
    status: Literal["OPEN", "CLOSED"]
    opened_at: datetime
    closed_at: datetime | None
    entry_event_id: str
    exit_event_id: str | None
    gross_pnl: float
    fees: float
    slippage: float
    net_pnl: float
    trade_return: float
    event_count: int

    _finite_values = field_validator("gross_pnl", "fees", "slippage", "net_pnl", "trade_return")(
        _finite
    )


class TradeAttributionReport(AutopsyModel):
    method: str
    closed_trades: tuple[TradeAttribution, ...]
    open_trades: tuple[TradeAttribution, ...]
    best_closed: tuple[TradeAttribution, ...]
    worst_closed: tuple[TradeAttribution, ...]
    attributed_net_pnl: float
    unattributed_net_pnl: float
    reconciliation_status: Literal["RECONCILED", "UNATTRIBUTED_REMAINS"]

    _finite_values = field_validator("attributed_net_pnl", "unattributed_net_pnl")(_finite)


class EquityPoint(AutopsyModel):
    event_id: str
    timestamp: datetime
    equity: float

    _finite_equity = field_validator("equity")(_finite)


class DrawdownEpisode(AutopsyModel):
    episode_id: str
    rank_by_depth: int
    peak_event_id: str
    drawdown_start_event_id: str
    trough_event_id: str
    recovery_event_id: str | None
    peak_time: datetime
    drawdown_start_time: datetime
    trough_time: datetime
    recovery_time: datetime | None
    peak_equity: float
    trough_equity: float
    max_drawdown: float
    duration_bars: int
    recovery_bars: int | None
    recovered: bool

    _finite_values = field_validator("peak_equity", "trough_equity", "max_drawdown")(_finite)


class AutopsySourceRun(AutopsyModel):
    trace_id: str
    trace_version: Literal["1.0"]
    strategy_id: str
    dataset_id: str
    dataset_name: str
    bar_count: int


class PnLAutopsyReport(AutopsyModel):
    report_version: Literal["1.0"] = "1.0"
    source_run: AutopsySourceRun
    summary: PnLSummary
    reconciliation: PnLReconciliation
    periods: PeriodBreakdown
    trades: TradeAttributionReport
    drawdowns: tuple[DrawdownEpisode, ...]
