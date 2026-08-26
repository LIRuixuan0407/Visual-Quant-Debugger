import asyncio
from pathlib import Path

import httpx
import pytest

from app.backtest import BacktestParameters
from app.data import load_pair_csv
from app.forward.engine import ForwardSession
from app.forward.feed import HistoricalBarFeed
from app.main import app
from app.models import MarketBar
from app.strategies import PairsTradingParameters


def _parameters() -> BacktestParameters:
    return BacktestParameters(strategy=PairsTradingParameters(lookback=5, entry_z=1.0, exit_z=0.8))


def _bars() -> tuple[MarketBar, ...]:
    return load_pair_csv(Path(__file__).parents[2] / "sample_data" / "forward_pairs_daily.csv")


def test_feed_exposes_only_revealed_history_and_rejects_future_access() -> None:
    bars = _bars()
    feed = HistoricalBarFeed(bars)
    assert feed.available_bars == ()
    assert feed.next_bar() == bars[0]
    assert feed.available_bars == bars[:1]
    assert feed.watermark == bars[0].timestamp
    with pytest.raises(RuntimeError, match="Future market data is unavailable"):
        feed.future_bar()


def test_future_mutation_cannot_change_processed_state() -> None:
    bars = _bars()
    changed = bars[:17] + tuple(
        MarketBar(timestamp=bar.timestamp, asset_a=bar.asset_a * 10, asset_b=bar.asset_b / 10)
        for bar in bars[17:]
    )
    left = ForwardSession("left", "pairs-trading", "forward-demo-v1", bars, _parameters())
    right = ForwardSession("right", "pairs-trading", "forward-demo-v1", changed, _parameters())
    left.start()
    right.start()
    for _ in range(17):
        left.step()
        right.step()
    assert left.rows == right.rows
    assert left.events == right.events
    assert left.snapshot().equity == pytest.approx(right.snapshot().equity)


def test_forward_trace_is_append_only_and_batch_equivalent() -> None:
    session = ForwardSession(
        "equivalence", "pairs-trading", "forward-demo-v1", _bars(), _parameters()
    )
    session.start()
    previous = ()
    while session.status == "RUNNING":
        session.step()
        assert tuple(session.events[: len(previous)]) == previous
        previous = tuple(session.events)
    assert session.status == "COMPLETED"
    assert len(session.events) == len(_bars())
    assert session.trace().diagnostics == ()
    batch = session.same_path_batch()
    assert batch is not None
    summary = session.summary()
    assert summary.execution_count == sum(
        len(event.execution_events) for event in batch.trace.timeline
    )
    assert summary.fees == pytest.approx(batch.metrics.total_fees, abs=1e-9)
    assert summary.slippage == pytest.approx(batch.metrics.total_slippage, abs=1e-9)
    assert summary.final_equity == pytest.approx(
        batch.trace.timeline[-1].pnl_snapshot.equity, abs=1e-9
    )
    assert summary.expired_order_count == 1


def test_signal_becomes_pending_then_fills_on_next_bar() -> None:
    session = ForwardSession("timing", "pairs-trading", "forward-demo-v1", _bars(), _parameters())
    session.start()
    while session.status == "RUNNING" and not any(
        event.signal_evaluation.signal_id for event in session.events
    ):
        session.step()
    signal_event = session.events[-1]
    pending = next(
        item
        for item in session.pending
        if item.source_signal_id == signal_event.signal_evaluation.signal_id
    )
    assert pending.status == "PENDING"
    assert signal_event.execution_events == ()
    session.step()
    assert session.events[-1].execution_events
    resolved = next(item for item in session.pending if item.pending_id == pending.pending_id)
    assert resolved.status == "FILLED"


async def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_forward_api_lifecycle_and_comparison() -> None:
    created = asyncio.run(
        _request(
            "POST",
            "/api/forward-sessions",
            json={
                "strategy_id": "pairs-trading",
                "dataset_id": "forward-demo-v1",
                "parameters": {
                    "lookback": 5,
                    "entry_z": 1.0,
                    "exit_z": 0.8,
                    "fee_bps": 5,
                    "slippage_bps": 5,
                },
            },
        )
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert created.json()["status"] == "CREATED"
    assert (
        asyncio.run(_request("POST", f"/api/forward-sessions/{session_id}/start")).status_code
        == 200
    )
    first = asyncio.run(_request("POST", f"/api/forward-sessions/{session_id}/step")).json()
    assert first["processed_bar_count"] == 1
    paused = asyncio.run(_request("POST", f"/api/forward-sessions/{session_id}/pause"))
    assert paused.json()["status"] == "PAUSED"
    blocked = asyncio.run(_request("POST", f"/api/forward-sessions/{session_id}/step"))
    assert blocked.status_code == 409
    assert (
        asyncio.run(_request("POST", f"/api/forward-sessions/{session_id}/resume")).json()["status"]
        == "RUNNING"
    )
    snapshot = first
    while snapshot["status"] == "RUNNING":
        snapshot = asyncio.run(_request("POST", f"/api/forward-sessions/{session_id}/step")).json()
    assert snapshot["status"] == "COMPLETED"
    trace = asyncio.run(_request("GET", f"/api/forward-sessions/{session_id}/trace")).json()
    assert len(trace["timeline"]) == snapshot["processed_bar_count"]
    comparison = asyncio.run(
        _request("GET", f"/api/forward-sessions/{session_id}/comparison")
    ).json()
    assert comparison["different_evaluation_periods"] is True
    assert comparison["consistency_status"] == "MATCH"
    assert all(item["status"] == "MATCH" for item in comparison["consistency"])


def test_unknown_forward_session_is_404() -> None:
    response = asyncio.run(_request("GET", "/api/forward-sessions/forward-missing"))
    assert response.status_code == 404
