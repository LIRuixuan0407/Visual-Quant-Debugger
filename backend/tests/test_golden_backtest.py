import json
from pathlib import Path
from typing import Any

import pytest

from app.backtest import BacktestParameters, run_backtest
from app.data import load_pair_csv
from app.strategies import PairsTradingParameters


def test_golden_backtest_pipeline() -> None:
    project_root = Path(__file__).parents[2]
    fixture: dict[str, Any] = json.loads(
        (Path(__file__).parent / "fixtures" / "golden_backtest.json").read_text(encoding="utf-8")
    )
    values = fixture["parameters"]
    expected = fixture["expected"]
    parameters = BacktestParameters(
        strategy=PairsTradingParameters(
            lookback=values["lookback"],
            entry_z=values["entry_z"],
            exit_z=values["exit_z"],
        ),
        initial_cash=values["initial_cash"],
        gross_target=values["gross_target"],
        fee_bps=values["fee_bps"],
        slippage_bps=values["slippage_bps"],
    )
    result = run_backtest(
        load_pair_csv(project_root / "sample_data" / "pairs_daily.csv"), parameters
    )
    transitions = [row for row in result.timeline if row.decision.signal_id]

    assert len(result.timeline) == expected["timeline_rows"]
    assert len(transitions) == expected["transition_count"]
    assert [row.timestamp.isoformat() for row in transitions] == expected["transition_timestamps"]
    assert result.metrics.number_of_orders == expected["number_of_orders"]
    for name in (
        "net_pnl",
        "gross_pnl",
        "total_fees",
        "total_slippage",
        "total_return",
        "max_drawdown",
        "turnover",
    ):
        assert getattr(result.metrics, name) == pytest.approx(expected[name], abs=1e-10)
