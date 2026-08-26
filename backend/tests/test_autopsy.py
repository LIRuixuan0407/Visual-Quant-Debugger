import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.autopsy import detect_drawdown_episodes
from app.autopsy.models import EquityPoint
from app.main import app

REQUEST = {
    "strategy": "pairs-trading",
    "parameters": {
        "lookback": 5,
        "entry_z": 1.0,
        "exit_z": 0.8,
        "fee_bps": 5,
        "slippage_bps": 5,
    },
}


async def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def _trace_and_report() -> tuple[dict[str, object], dict[str, object]]:
    created = asyncio.run(_request("POST", "/api/backtests", json=REQUEST)).json()
    trace_id = created["trace_id"]
    trace_response = asyncio.run(_request("GET", f"/api/traces/{trace_id}"))
    report_response = asyncio.run(_request("GET", f"/api/traces/{trace_id}/pnl-autopsy"))
    assert trace_response.status_code == 200
    assert report_response.status_code == 200
    return trace_response.json(), report_response.json()


def test_summary_is_aggregated_from_trace_and_reconciles_both_equations() -> None:
    trace, report = _trace_and_report()
    summary = report["summary"]
    reconciliation = report["reconciliation"]

    assert summary["gross_pnl"] - summary["fees"] - summary["slippage"] == pytest.approx(
        summary["net_pnl"]
    )
    assert summary["initial_equity"] + summary["net_pnl"] == pytest.approx(summary["final_equity"])
    assert summary["final_equity"] == pytest.approx(trace["timeline"][-1]["pnl_snapshot"]["equity"])
    assert summary["fees"] == pytest.approx(trace["metrics"]["total_fees"])
    assert summary["slippage"] == pytest.approx(trace["metrics"]["total_slippage"])
    assert reconciliation["reconciled"] is True
    assert reconciliation["pnl_difference"] == pytest.approx(0, abs=1e-8)
    assert reconciliation["equity_difference"] == pytest.approx(0, abs=1e-8)


def test_month_quarter_and_year_periods_use_utc_trace_events_and_reconcile() -> None:
    _, report = _trace_and_report()
    summary = report["summary"]

    for granularity in ("monthly", "quarterly", "yearly"):
        periods = report["periods"][granularity]
        assert periods
        assert sum(period["net_pnl"] for period in periods) == pytest.approx(summary["net_pnl"])
        assert sum(period["gross_pnl"] for period in periods) == pytest.approx(summary["gross_pnl"])
        for period in periods:
            assert period["end_equity"] / period["start_equity"] - 1 == pytest.approx(
                period["period_return"]
            )
            assert period["start"].endswith("Z")
            assert period["end"].endswith("Z")


def test_trade_attribution_is_explicit_ranked_and_replay_linkable() -> None:
    trace, report = _trace_and_report()
    attribution = report["trades"]
    events = {event["event_id"] for event in trace["timeline"]}
    closed = attribution["closed_trades"]
    opened = attribution["open_trades"]

    assert all(trade["status"] == "CLOSED" for trade in closed)
    assert all(trade["status"] == "OPEN" for trade in opened)
    assert [trade["net_pnl"] for trade in attribution["best_closed"]] == sorted(
        (trade["net_pnl"] for trade in closed), reverse=True
    )[:5]
    assert [trade["net_pnl"] for trade in attribution["worst_closed"]] == sorted(
        trade["net_pnl"] for trade in closed
    )[:5]
    assert all(trade["entry_event_id"] in events for trade in (*closed, *opened))
    assert attribution["attributed_net_pnl"] + attribution["unattributed_net_pnl"] == pytest.approx(
        report["summary"]["net_pnl"]
    )
    assert attribution["reconciliation_status"] in {"RECONCILED", "UNATTRIBUTED_REMAINS"}


def test_drawdown_state_machine_handles_recovery_and_unrecovered_tail() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    points = tuple(
        EquityPoint(
            event_id=f"event-{index}",
            timestamp=start + timedelta(days=index),
            equity=equity,
        )
        for index, equity in enumerate([100, 110, 105, 90, 95, 111, 108])
    )

    episodes = detect_drawdown_episodes(points)

    assert len(episodes) == 2
    recovered, tail = episodes
    assert recovered.peak_event_id == "event-1"
    assert recovered.drawdown_start_event_id == "event-2"
    assert recovered.trough_event_id == "event-3"
    assert recovered.recovery_event_id == "event-5"
    assert recovered.max_drawdown == pytest.approx(90 / 110 - 1)
    assert recovered.duration_bars == 4
    assert recovered.recovery_bars == 2
    assert recovered.recovered is True
    assert recovered.rank_by_depth == 1
    assert tail.peak_event_id == "event-5"
    assert tail.trough_event_id == "event-6"
    assert tail.recovery_event_id is None
    assert tail.recovered is False
    assert tail.rank_by_depth == 2


def test_trace_drawdown_matches_recorded_maximum_and_unknown_trace_is_404() -> None:
    trace, report = _trace_and_report()
    assert min(episode["max_drawdown"] for episode in report["drawdowns"]) == pytest.approx(
        trace["metrics"]["max_drawdown"]
    )

    missing = asyncio.run(_request("GET", "/api/traces/trace-missing/pnl-autopsy"))
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Trace 'trace-missing' was not found"
