import asyncio

import httpx
import pytest

from app.backtest import BacktestParameters
from app.main import app
from app.strategies.definition import PAIRS_TRADING_DEFINITION


async def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_strategy_list_and_definition_are_structured_and_stable() -> None:
    response = asyncio.run(_request("GET", "/api/strategies"))
    assert response.status_code == 200
    strategy_ids = [item["strategy_id"] for item in response.json()]
    assert strategy_ids[0] == "pairs-trading"
    assert len(strategy_ids) == len(set(strategy_ids))

    detail = asyncio.run(_request("GET", "/api/strategies/pairs-trading"))
    assert detail.status_code == 200
    body = detail.json()
    assert body["strategy_id"] == "pairs-trading"
    assert body["version"] == "0.1"
    assert [node["node_id"] for node in body["pipeline"]] == [
        "market-data",
        "rolling-regression",
        "hedge-ratio",
        "spread",
        "rolling-mean",
        "rolling-std",
        "zscore",
        "signal-rules",
        "target-position",
        "execution",
    ]
    assert body["execution_assumptions"][0]["value"] == "close(t)"
    assert body["execution_assumptions"][1]["value"] == "close(t+1)"


def test_strategy_definition_defaults_match_engine_defaults() -> None:
    engine = BacktestParameters()
    definition_defaults = {
        item.key: item.default_value for item in PAIRS_TRADING_DEFINITION.parameters
    }
    assert definition_defaults == {
        "lookback": engine.strategy.lookback,
        "entry_z": engine.strategy.entry_z,
        "exit_z": engine.strategy.exit_z,
        "fee_bps": engine.fee_bps,
        "slippage_bps": engine.slippage_bps,
    }


def test_demo_preset_preserves_first_golden_signal() -> None:
    demo = next(
        preset
        for preset in PAIRS_TRADING_DEFINITION.presets
        if preset.preset_id == "demo-active-signals"
    )
    created = asyncio.run(
        _request(
            "POST",
            "/api/backtests",
            json={"strategy": "pairs-trading", "parameters": demo.parameters},
        )
    ).json()
    trace = asyncio.run(_request("GET", f"/api/traces/{created['trace_id']}")).json()
    first_signal = next(
        event for event in trace["timeline"] if event["signal_evaluation"]["signal_id"]
    )
    zscore = next(
        feature["value"]
        for feature in first_signal["feature_snapshots"]
        if feature["name"] == "zscore"
    )
    assert first_signal["timestamp"] == "2024-01-17T16:00:00Z"
    assert first_signal["signal_evaluation"]["signal_id"] == "signal-0001"
    assert zscore == pytest.approx(1.9937661959305428, abs=1e-12)


def test_custom_parameters_are_recorded_and_traces_remain_independent() -> None:
    first_parameters = {
        "lookback": 10,
        "entry_z": 1.5,
        "exit_z": 0.5,
        "fee_bps": 4,
        "slippage_bps": 7,
    }
    second_parameters = {**first_parameters, "lookback": 12}
    first = asyncio.run(
        _request(
            "POST",
            "/api/backtests",
            json={"strategy": "pairs-trading", "parameters": first_parameters},
        )
    ).json()
    second = asyncio.run(
        _request(
            "POST",
            "/api/backtests",
            json={"strategy": "pairs-trading", "parameters": second_parameters},
        )
    ).json()

    assert first["trace_id"] != second["trace_id"]
    first_trace = asyncio.run(_request("GET", f"/api/traces/{first['trace_id']}"))
    second_trace = asyncio.run(_request("GET", f"/api/traces/{second['trace_id']}"))
    assert first_trace.json()["parameters"]["lookback"] == 10
    assert first_trace.json()["parameters"]["entry_z"] == 1.5
    assert first_trace.json()["parameters"]["fee_bps"] == 4
    assert second_trace.json()["parameters"]["lookback"] == 12


def test_backtest_api_rejects_invalid_strategy_parameters() -> None:
    invalid_parameters = (
        {"lookback": 1, "entry_z": 2, "exit_z": 0.5, "fee_bps": 5, "slippage_bps": 5},
        {"lookback": 10, "entry_z": 2, "exit_z": 0.5, "fee_bps": -1, "slippage_bps": 5},
        {"lookback": 10, "entry_z": 1, "exit_z": 1, "fee_bps": 5, "slippage_bps": 5},
    )
    for parameters in invalid_parameters:
        response = asyncio.run(
            _request(
                "POST",
                "/api/backtests",
                json={"strategy": "pairs-trading", "parameters": parameters},
            )
        )
        assert response.status_code == 422


def test_unknown_strategy_definition_returns_404() -> None:
    response = asyncio.run(_request("GET", "/api/strategies/not-a-strategy"))
    assert response.status_code == 404
    assert response.json()["detail"] == "Strategy 'not-a-strategy' was not found"
