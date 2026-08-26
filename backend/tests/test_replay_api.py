import asyncio

import httpx
import pytest

from app.main import app

GOLDEN_REQUEST = {
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


def test_create_backtest_and_get_complete_trace() -> None:
    created = asyncio.run(_request("POST", "/api/backtests", json=GOLDEN_REQUEST))
    assert created.status_code == 201
    body = created.json()
    assert body["trace_version"] == "1.0"
    assert body["summary"]["timeline_events"] == 40
    assert body["summary"]["signals"] == 10

    response = asyncio.run(_request("GET", f"/api/traces/{body['trace_id']}"))
    assert response.status_code == 200
    trace = response.json()
    assert trace["trace_version"] == "1.0"
    assert len(trace["timeline"]) == 40


def test_api_trace_contains_real_first_signal_lineage() -> None:
    created = asyncio.run(_request("POST", "/api/backtests", json=GOLDEN_REQUEST)).json()
    trace = asyncio.run(_request("GET", f"/api/traces/{created['trace_id']}")).json()
    first_signal = next(
        event for event in trace["timeline"] if event["signal_evaluation"]["signal_id"]
    )
    features = {item["name"]: item for item in first_signal["feature_snapshots"]}
    assert first_signal["timestamp"] == "2024-01-17T16:00:00Z"
    assert first_signal["signal_evaluation"]["signal"] == "SHORT_SPREAD"
    assert features["zscore"]["value"] == pytest.approx(1.9937661959305428, abs=1e-12)
    assert features["zscore"]["inputs"] == [
        features["spread"]["feature_id"],
        features["rolling_mean"]["feature_id"],
        features["rolling_std"]["feature_id"],
    ]


def test_unknown_trace_returns_404() -> None:
    response = asyncio.run(_request("GET", "/api/traces/trace-does-not-exist"))
    assert response.status_code == 404
    assert response.json()["detail"] == "Trace 'trace-does-not-exist' was not found"
