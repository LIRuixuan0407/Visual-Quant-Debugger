import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

import app.api.datasets as datasets_api
import app.api.forward as forward_api
import app.api.replay as replay_api
import app.diagnostics.engine as diagnostics_engine
import app.sdk.registry as registry_module
from app.datasets import DatasetImportRequest, DatasetRegistry
from app.main import app
from app.sdk.registry import StrategyRegistry


async def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def _csv() -> bytes:
    prices = [
        100,
        101,
        103,
        106,
        109,
        108,
        105,
        101,
        97,
        95,
        98,
        102,
        107,
        111,
        110,
        106,
        101,
        96,
        94,
        97,
        102,
        108,
        112,
        109,
        104,
        99,
        95,
        98,
        103,
        107,
    ]
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = ["date,ticker,price"]
    rows.extend(
        f"{(start + timedelta(days=index)).isoformat()},AAPL,{price}"
        for index, price in enumerate(prices)
    )
    return ("\n".join(rows) + "\n").encode()


def _install_test_registries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    strategy_registry = StrategyRegistry(tmp_path)
    strategy = strategy_registry.add(Path(__file__).parents[2] / "examples" / "sma_cross.py")
    dataset_registry = DatasetRegistry(tmp_path)
    preview = dataset_registry.preview("market.csv", _csv())
    dataset = dataset_registry.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name="AAPL Research and Holdout",
            mapping={"timestamp": "date", "symbol": "ticker", "close": "price"},
        )
    )
    monkeypatch.setattr(registry_module, "strategy_registry", strategy_registry)
    monkeypatch.setattr(replay_api, "strategy_registry", strategy_registry)
    monkeypatch.setattr(replay_api, "dataset_registry", dataset_registry)
    monkeypatch.setattr(datasets_api, "dataset_registry", dataset_registry)
    monkeypatch.setattr(diagnostics_engine, "strategy_registry", strategy_registry)
    monkeypatch.setattr(diagnostics_engine, "dataset_registry", dataset_registry)
    monkeypatch.setattr(forward_api, "strategy_registry", strategy_registry)
    monkeypatch.setattr(forward_api, "dataset_registry", dataset_registry)
    return strategy.strategy_id, dataset.dataset_id


def test_external_strategy_user_csv_open_run_diagnose_autopsy_and_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    strategy_id, dataset_id = _install_test_registries(tmp_path, monkeypatch)
    strategies = asyncio.run(_request("GET", "/api/strategies"))
    assert strategy_id in {item["strategy_id"] for item in strategies.json()}
    datasets = asyncio.run(_request("GET", "/api/datasets"))
    assert dataset_id in {item["dataset_id"] for item in datasets.json()}

    parameters = {
        "fast_window": 3,
        "slow_window": 5,
        "quantity": 100,
        "fee_bps": 5,
        "slippage_bps": 5,
    }
    compatible = asyncio.run(
        _request(
            "POST",
            "/api/compatibility-checks",
            json={
                "strategy_id": strategy_id,
                "dataset_id": dataset_id,
                "parameters": parameters,
            },
        )
    )
    assert compatible.status_code == 200
    assert compatible.json()["compatible"] is True
    cutoff = datetime(2025, 1, 20, tzinfo=UTC).isoformat()
    created = asyncio.run(
        _request(
            "POST",
            "/api/backtests",
            json={
                "strategy_id": strategy_id,
                "dataset_id": dataset_id,
                "parameters": parameters,
                "research_cutoff": cutoff,
            },
        )
    )
    assert created.status_code == 201
    assert created.json()["status"] == "COMPLETED"
    trace_id = created.json()["trace_id"]
    trace_response = asyncio.run(_request("GET", f"/api/traces/{trace_id}"))
    trace = trace_response.json()
    assert trace["strategy"]["strategy_id"] == strategy_id
    assert trace["metadata"]["dataset_id"] == dataset_id
    signal = next(event for event in trace["timeline"] if event["signal_evaluation"]["signal_id"])
    assert {item["name"] for item in signal["feature_snapshots"]} == {
        "fast_ma",
        "slow_ma",
    }
    assert signal["data_dependencies"]
    assert any(event["order_events"] for event in trace["timeline"])
    assert any(event["execution_events"] for event in trace["timeline"])

    context = asyncio.run(_request("GET", f"/api/traces/{trace_id}/context")).json()
    assert context["strategy_fingerprint"].startswith("sha256:")
    assert context["dataset_fingerprint"].startswith("sha256:")
    diagnosis = asyncio.run(_request("POST", "/api/diagnostics", json={"trace_id": trace_id}))
    assert diagnosis.status_code == 200
    assert diagnosis.json()["source_run"]["sensitivity_parameter"] == "slow_window"
    assert diagnosis.json()["cost_stress"]
    assert diagnosis.json()["execution_delay"]
    autopsy = asyncio.run(_request("GET", f"/api/traces/{trace_id}/pnl-autopsy"))
    assert autopsy.status_code == 200
    assert autopsy.json()["reconciliation"]["reconciled"] is True

    forward = asyncio.run(
        _request(
            "POST",
            "/api/forward-sessions",
            json={
                "strategy_id": strategy_id,
                "dataset_id": dataset_id,
                "parameters": parameters,
                "research_cutoff": cutoff,
            },
        )
    )
    assert forward.status_code == 201
    session_id = forward.json()["session_id"]
    snapshot = asyncio.run(_request("POST", f"/api/forward-sessions/{session_id}/start")).json()
    while snapshot["status"] == "RUNNING":
        snapshot = asyncio.run(_request("POST", f"/api/forward-sessions/{session_id}/step")).json()
    assert snapshot["status"] == "COMPLETED"
    assert snapshot["processed_bar_count"] == 10
    comparison = asyncio.run(_request("GET", f"/api/forward-sessions/{session_id}/comparison"))
    assert comparison.status_code == 200
    assert comparison.json()["consistency_status"] == "MATCH"
    assert all(item["status"] == "MATCH" for item in comparison.json()["consistency"])
