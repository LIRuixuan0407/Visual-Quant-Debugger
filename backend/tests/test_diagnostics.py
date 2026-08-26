import asyncio
import math

import httpx
import pytest

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


def _create_trace_id() -> str:
    response = asyncio.run(_request("POST", "/api/backtests", json=REQUEST))
    assert response.status_code == 201
    return str(response.json()["trace_id"])


def _diagnose(trace_id: str) -> dict[str, object]:
    response = asyncio.run(_request("POST", "/api/diagnostics", json={"trace_id": trace_id}))
    assert response.status_code == 200
    return response.json()


def _numbers(value: object) -> list[float]:
    if isinstance(value, dict):
        return [number for item in value.values() for number in _numbers(item)]
    if isinstance(value, list):
        return [number for item in value for number in _numbers(item)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)]
    return []


def test_diagnosis_is_trace_bound_deterministic_and_finite() -> None:
    trace_id = _create_trace_id()
    first = _diagnose(trace_id)
    second = _diagnose(trace_id)

    assert first == second
    assert first["report_version"] == "1.0"
    assert first["source_run"]["trace_id"] == trace_id
    assert all(math.isfinite(number) for number in _numbers(first))


def test_chronological_split_reuses_train_feature_history_but_isolates_test_pnl() -> None:
    report = _diagnose(_create_trace_id())
    split = report["train_test"]

    assert split["method"] == "chronological-70-30"
    assert split["train_bar_count"] == 28
    assert split["test_bar_count"] == 12
    assert "including train history" in split["feature_context_policy"]
    assert "train P&L is not counted" in split["pnl_isolation_policy"]
    assert split["train"]["bar_count"] == 28
    assert split["test"]["bar_count"] == 12
    assert split["train_end"] < split["test_start"]


def test_lookback_sensitivity_uses_valid_deterministic_candidates_and_real_metrics() -> None:
    report = _diagnose(_create_trace_id())
    points = report["lookback_sensitivity"]
    lookbacks = [point["lookback"] for point in points]

    assert 5 <= len(points) <= 9
    assert lookbacks == sorted(set(lookbacks))
    assert all(2 <= lookback <= 14 for lookback in lookbacks)
    assert next(point for point in points if point["lookback"] == 5)["is_current"] is True
    assert all(point["train"]["bar_count"] == 28 for point in points)
    assert all(point["test"]["bar_count"] == 12 for point in points)
    assert all(
        point["train"]["status"] in {"OK", "NO_TRADES", "UNDEFINED_SHARPE"} for point in points
    )


def test_cost_stress_reruns_full_engine_and_preserves_original_cost_ratio() -> None:
    trace_id = _create_trace_id()
    report = _diagnose(trace_id)
    trace = asyncio.run(_request("GET", f"/api/traces/{trace_id}")).json()
    points = report["cost_stress"]

    assert [point["total_friction_bps"] for point in points] == [0, 5, 10, 15, 20]
    assert all(point["fee_bps"] == pytest.approx(point["slippage_bps"]) for point in points)
    baseline = next(point for point in points if point["total_friction_bps"] == 10)
    assert baseline["metrics"]["final_equity"] == pytest.approx(
        trace["timeline"][-1]["pnl_snapshot"]["equity"]
    )
    assert points[-1]["metrics"]["return"] < points[0]["metrics"]["return"]


def test_execution_delay_baseline_and_end_of_data_semantics() -> None:
    trace_id = _create_trace_id()
    report = _diagnose(trace_id)
    trace = asyncio.run(_request("GET", f"/api/traces/{trace_id}")).json()
    points = report["execution_delay"]

    assert [point["execution_offset_bars"] for point in points] == [1, 2, 3]
    assert [point["additional_delay_bars"] for point in points] == [0, 1, 2]
    assert points[0]["metrics"]["final_equity"] == pytest.approx(
        trace["timeline"][-1]["pnl_snapshot"]["equity"]
    )
    assert all(point["unfilled_signal_count"] >= 0 for point in points)


def test_diagnosis_unknown_trace_and_validation_errors_are_explicit() -> None:
    missing = asyncio.run(
        _request("POST", "/api/diagnostics", json={"trace_id": "trace-does-not-exist"})
    )
    malformed = asyncio.run(_request("POST", "/api/diagnostics", json={"wrong": "field"}))

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Trace 'trace-does-not-exist' was not found"
    assert malformed.status_code == 422
