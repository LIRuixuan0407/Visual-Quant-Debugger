import asyncio
import json
import math
from typing import cast

import httpx
import numpy as np
import pytest

from app.diagnostics.engine import diagnose_run
from app.diagnostics.models import DiagnosisReport
from app.diagnostics.statistical import (
    calculate_pair_mean_reversion,
    calculate_return_diagnostics,
)
from app.diagnostics.volatility import (
    annualization_factor_for_frequency,
    build_volatility_diagnostics,
    classify_volatility_regime,
    ewma_volatility,
    rolling_historical_volatility,
)
from app.main import app
from app.runs import run_ledger

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


def _what_if(
    trace_id: str,
    *,
    fee_bps: float = 5.0,
    slippage_bps: float = 5.0,
    delay: int = 0,
    lookback: int = 5,
) -> httpx.Response:
    return asyncio.run(
        _request(
            "POST",
            "/api/diagnostics/what-if",
            json={
                "trace_id": trace_id,
                "inputs": {
                    "fee_bps": fee_bps,
                    "slippage_bps": slippage_bps,
                    "additional_execution_delay_bars": delay,
                    "strategy_parameters": {"lookback": lookback},
                },
            },
        )
    )


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


def test_return_diagnostics_use_trace_equity_and_report_acf_lags() -> None:
    trace_id = _create_trace_id()
    report = _diagnose(trace_id)
    trace = asyncio.run(_request("GET", f"/api/traces/{trace_id}")).json()
    diagnostics = report["statistical_diagnostics"]["returns"]
    equity = np.asarray(
        [event["pnl_snapshot"]["equity"] for event in trace["timeline"]], dtype=np.float64
    )
    returns = equity[1:] / equity[:-1] - 1.0
    centered = returns - np.mean(returns)
    expected_lag_1 = float(np.dot(centered[:-1], centered[1:]) / np.dot(centered, centered))

    assert diagnostics["observation_count"] == len(trace["timeline"]) - 1
    assert [point["lag"] for point in diagnostics["return_acf"]] == list(range(1, 11))
    assert [point["lag"] for point in diagnostics["squared_return_acf"]] == list(range(1, 11))
    assert diagnostics["lag_1_return_autocorrelation"] == pytest.approx(expected_lag_1)


def test_pairs_mean_reversion_reads_recorded_trace_features() -> None:
    trace_id = _create_trace_id()
    report = _diagnose(trace_id)
    trace = asyncio.run(_request("GET", f"/api/traces/{trace_id}")).json()
    evidence = report["statistical_diagnostics"]["pair_mean_reversion"]
    spreads = [
        feature["value"]
        for event in trace["timeline"]
        for feature in event["feature_snapshots"]
        if feature["name"] == "spread" and feature["value"] is not None
    ]
    hedge_ratios = [
        feature["value"]
        for event in trace["timeline"]
        for feature in event["feature_snapshots"]
        if feature["name"] == "hedge_ratio" and feature["value"] is not None
    ]
    previous = np.asarray(spreads[:-1], dtype=np.float64)
    current = np.asarray(spreads[1:], dtype=np.float64)
    expected_phi = float(
        np.dot(previous - np.mean(previous), current - np.mean(current))
        / np.dot(previous - np.mean(previous), previous - np.mean(previous))
    )

    assert evidence["observation_count"] == len(spreads)
    assert evidence["consecutive_pair_count"] == len(spreads) - 1
    assert evidence["hedge_ratio_observation_count"] == len(hedge_ratios)
    assert evidence["phi"] == pytest.approx(expected_phi)
    assert evidence["hedge_ratio_mean"] == pytest.approx(np.mean(hedge_ratios))
    assert evidence["hedge_ratio_std"] == pytest.approx(np.std(hedge_ratios, ddof=0))
    if 0 < expected_phi < 1:
        assert evidence["half_life_bars"] == pytest.approx(-math.log(2) / math.log(expected_phi))
    else:
        assert evidence["half_life_bars"] is None


def test_statistical_boundaries_do_not_fabricate_values_or_half_life() -> None:
    returns = calculate_return_diagnostics((100.0,) * 15)
    short_returns = calculate_return_diagnostics((100.0, 101.0, 100.0, 102.0))
    explosive_spread = tuple(float(2**index) for index in range(25))
    explosive_pair = calculate_pair_mean_reversion(explosive_spread, (1.0,) * 25)
    short_pair = calculate_pair_mean_reversion((1.0, 1.0), ())

    assert returns.status == "INSUFFICIENT_DATA"
    assert returns.lag_1_return_autocorrelation is None
    assert returns.lag_1_squared_return_autocorrelation is None
    assert all(point.value is None for point in returns.return_acf)
    assert short_returns.status == "INSUFFICIENT_DATA"
    assert short_returns.return_acf[0].value is not None
    assert short_returns.return_acf[-1].value is None
    assert explosive_pair.phi == pytest.approx(2.0)
    assert explosive_pair.half_life_bars is None
    assert short_pair.status == "INSUFFICIENT_DATA"
    assert short_pair.phi is None
    assert short_pair.half_life_bars is None


def test_pair_ar1_requires_time_adjacent_pairs_and_a_larger_sample() -> None:
    spreads = (
        *tuple(float(index) for index in range(12)),
        None,
        *tuple(float(100 + index) for index in range(12)),
    )
    evidence = calculate_pair_mean_reversion(spreads, (1.0,) * len(spreads))
    too_short = calculate_pair_mean_reversion(tuple(float(index) for index in range(20)), ())

    previous = np.asarray([*range(11), *range(100, 111)], dtype=np.float64)
    current = np.asarray([*range(1, 12), *range(101, 112)], dtype=np.float64)
    expected_phi = float(
        np.dot(previous - np.mean(previous), current - np.mean(current))
        / np.dot(previous - np.mean(previous), previous - np.mean(previous))
    )

    assert evidence.observation_count == 24
    assert evidence.consecutive_pair_count == 22
    assert evidence.phi == pytest.approx(expected_phi)
    assert evidence.status == "OK"
    assert too_short.consecutive_pair_count == 19
    assert too_short.status == "INSUFFICIENT_DATA"
    assert too_short.phi is None


def test_diagnosis_report_accepts_legacy_cache_without_new_diagnostic_fields() -> None:
    trace_id = _create_trace_id()
    record = run_ledger.execution_record(trace_id)
    assert record is not None
    payload = diagnose_run(trace_id, record).model_dump()
    payload.pop("statistical_diagnostics")
    payload.pop("volatility_diagnostics")
    payload.pop("what_if")

    restored = DiagnosisReport.model_validate(payload)

    assert restored.statistical_diagnostics is None
    assert restored.volatility_diagnostics is None
    assert restored.what_if is None


def test_stale_diagnostic_cache_is_recomputed_for_frequency_and_pair_contracts() -> None:
    trace_id = _create_trace_id()
    payload = _diagnose(trace_id)
    record = run_ledger.execution_record(trace_id)
    assert record is not None
    volatility = cast(dict[str, object], payload["volatility_diagnostics"])
    statistical = cast(dict[str, object], payload["statistical_diagnostics"])
    pair = cast(dict[str, object], statistical["pair_mean_reversion"])
    del volatility["dataset_frequency"]
    del pair["consecutive_pair_count"]
    run_ledger.repository.save_derived(
        record.run_id, "diagnostics", json.dumps(payload).encode()
    )

    refreshed = _diagnose(trace_id)
    refreshed_volatility = cast(dict[str, object], refreshed["volatility_diagnostics"])
    refreshed_statistical = cast(dict[str, object], refreshed["statistical_diagnostics"])
    refreshed_pair = cast(dict[str, object], refreshed_statistical["pair_mean_reversion"])

    assert refreshed_volatility["dataset_frequency"] == "1D"
    assert cast(int, refreshed_pair["consecutive_pair_count"]) >= 20


def test_historical_volatility_annualization_ewma_and_regime_boundaries() -> None:
    returns = (0.01, -0.02, 0.03)
    historical = rolling_historical_volatility(returns, window=3, annualization_factor=252)
    unannualized = rolling_historical_volatility(returns, window=3, annualization_factor=1)
    ewma = ewma_volatility((0.10, 0.20), decay=0.5, annualization_factor=1)

    assert historical[:2] == (None, None)
    assert historical[2] == pytest.approx(np.std(returns, ddof=1) * math.sqrt(252))
    assert historical[2] == pytest.approx(unannualized[2] * math.sqrt(252))
    assert ewma == pytest.approx((0.10, math.sqrt(0.5 * 0.10**2 + 0.5 * 0.20**2)))
    assert classify_volatility_regime(0.1499) == "LOW"
    assert classify_volatility_regime(0.15) == "NORMAL"
    assert classify_volatility_regime(0.2999) == "NORMAL"
    assert classify_volatility_regime(0.30) == "HIGH"
    assert annualization_factor_for_frequency("1D") == 252
    assert annualization_factor_for_frequency("1Day") == 252
    assert annualization_factor_for_frequency("86400s") == 252
    assert annualization_factor_for_frequency("1Hour") is None
    assert annualization_factor_for_frequency("unknown") is None


def test_volatility_diagnostics_use_recorded_market_prices_and_drawdown_evidence() -> None:
    trace_id = _create_trace_id()
    report = _diagnose(trace_id)
    trace = asyncio.run(_request("GET", f"/api/traces/{trace_id}")).json()
    diagnostics = report["volatility_diagnostics"]
    points = diagnostics["points"]
    previous = {
        item["symbol"]: item["value"] for item in trace["timeline"][0]["market_snapshot"]["values"]
    }
    current = {
        item["symbol"]: item["value"] for item in trace["timeline"][1]["market_snapshot"]["values"]
    }
    expected_return = np.mean([current[symbol] / previous[symbol] - 1 for symbol in current])
    first_window = [point["market_return"] for point in points[1:22]]

    assert diagnostics["status"] == "OK"
    assert diagnostics["rolling_window"] == 21
    assert diagnostics["ewma_decay"] == pytest.approx(0.94)
    assert diagnostics["dataset_frequency"] == "1D"
    assert diagnostics["annualization_factor"] == 252
    assert points[1]["market_return"] == pytest.approx(expected_return)
    assert points[21]["rolling_historical_vol"] == pytest.approx(
        np.std(first_window, ddof=1) * math.sqrt(252)
    )
    assert len(diagnostics["drawdown_overlap"]) <= 4
    assert "caus" not in diagnostics["summary"].lower()


def test_volatility_calculations_return_clean_unavailable_values_for_short_data() -> None:
    historical = rolling_historical_volatility((0.01, -0.01), window=3)
    ewma = ewma_volatility((None, 0.01), decay=0.94)

    assert historical == (None, None)
    assert ewma[0] is None
    assert ewma[1] == pytest.approx(0.01 * math.sqrt(252))


def test_volatility_diagnostics_reject_unreliable_frequency_annualization() -> None:
    trace_id = _create_trace_id()
    record = run_ledger.execution_record(trace_id)
    assert record is not None

    diagnostics = build_volatility_diagnostics(
        record.trace, dataset_frequency="1Hour"
    )

    assert diagnostics.status == "UNSUPPORTED"
    assert diagnostics.verdict == "UNSUPPORTED"
    assert diagnostics.dataset_frequency == "1Hour"
    assert diagnostics.annualization_factor is None
    assert diagnostics.current_historical_vol is None
    assert diagnostics.current_ewma_vol is None
    assert all(point.rolling_historical_vol is None for point in diagnostics.points)
    assert "unsupported" in diagnostics.summary.lower()


def test_what_if_preserves_baseline_and_higher_cost_does_not_improve_net_pnl() -> None:
    trace_id = _create_trace_id()
    report = _diagnose(trace_id)
    response = _what_if(trace_id, fee_bps=25.0)

    assert response.status_code == 200
    scenario = response.json()
    baseline = report["what_if"]
    assert scenario["baseline_inputs"] == baseline["baseline_inputs"]
    assert scenario["baseline_metrics"] == baseline["baseline_metrics"]
    assert scenario["baseline_inputs"]["fee_bps"] == 5.0
    assert scenario["inputs"]["fee_bps"] == 25.0
    assert scenario["inputs"]["strategy_parameters"] == {"lookback": 5}
    assert (
        scenario["stressed_metrics"]["trade_count"] == scenario["baseline_metrics"]["trade_count"]
    )
    assert scenario["stressed_metrics"]["net_pnl"] <= scenario["baseline_metrics"]["net_pnl"]
    assert scenario["deltas"]["net_pnl"] <= 0


def test_what_if_delay_and_cost_scenario_is_deterministic_and_validated() -> None:
    trace_id = _create_trace_id()
    first = _what_if(trace_id, fee_bps=15.0, slippage_bps=12.0, delay=2, lookback=8)
    second = _what_if(trace_id, fee_bps=15.0, slippage_bps=12.0, delay=2, lookback=8)
    invalid = asyncio.run(
        _request(
            "POST",
            "/api/diagnostics/what-if",
            json={
                "trace_id": trace_id,
                "inputs": {
                    "fee_bps": 5,
                    "slippage_bps": 5,
                    "additional_execution_delay_bars": 0,
                    "strategy_parameters": {"entry_z": 1.5},
                },
            },
        )
    )

    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["inputs"]["additional_execution_delay_bars"] == 2
    assert first.json()["unfilled_signal_count"] >= 0
    assert invalid.status_code == 422
    assert "Unsupported What-if strategy parameters" in invalid.json()["detail"]


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
