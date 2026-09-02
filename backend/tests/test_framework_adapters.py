from __future__ import annotations

from pathlib import Path

import pytest

from app.autopsy.engine import build_pnl_autopsy
from app.datasets import DatasetImportRequest, DatasetRegistry
from app.runs.comparison import compare_runs
from app.runs.repository import RunRepository
from app.runs.service import RunLedger
from app.sdk.registry import StrategyRegistry

pytestmark = pytest.mark.framework
pytest.importorskip("backtesting")
pytest.importorskip("vectorbt")

ROOT = Path(__file__).parents[2]


def _dataset(registry: DatasetRegistry) -> str:
    content = (ROOT / "sample_data" / "single_ohlcv_daily.csv").read_bytes()
    preview = registry.preview("single.csv", content)
    definition = registry.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name="Single OHLCV",
            mapping={
                "timestamp": "timestamp",
                "symbol": "symbol",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            },
        )
    )
    return definition.dataset_id


def test_real_backtesting_py_adapter_persists_standard_trace_and_restarts(
    tmp_path: Path,
) -> None:
    strategies = StrategyRegistry(tmp_path)
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _dataset(datasets)
    registration = strategies.add(
        ROOT / "examples" / "adapters" / "backtesting_py_sma.py",
        framework="backtesting.py",
        class_name="FrameworkSmaCross",
    )
    result = RunLedger().create(
        strategy_id=registration.strategy_id,
        dataset_id=dataset_id,
        parameters={"fast_window": 4, "slow_window": 9},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    assert result.manifest.status == "COMPLETED"
    assert result.trace is not None
    assert result.manifest.runtime.framework_version == "0.6.6"
    assert result.manifest.runtime.execution_owner == "backtesting.py"
    assert result.manifest.runtime.trace_fidelity == "STANDARD"
    assert result.manifest.runtime.trace_capabilities.point_in_time_proven == "UNAVAILABLE"
    assert result.trace.timeline[-1].pnl_snapshot.equity == pytest.approx(
        result.manifest.metrics.final_equity
    )
    assert len(result.trace.trades) > 0
    restarted = RunRepository(tmp_path)
    assert restarted.get_manifest(result.manifest.run_id).runtime.trace_fidelity == "STANDARD"
    assert restarted.load_trace_for_run(result.manifest.run_id) == result.trace
    assert restarted.adapter_manifest_path(result.manifest.run_id).is_file()
    autopsy = build_pnl_autopsy(result.manifest.trace_id or "", result.trace)
    assert autopsy.summary.final_equity == pytest.approx(result.manifest.metrics.final_equity)
    reproduced = RunLedger().reproduce(
        result.manifest.run_id,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    assert reproduced.manifest.status == "COMPLETED"
    assert reproduced.manifest.reproduced_from_run_id == result.manifest.run_id
    assert reproduced.manifest.run_fingerprint == result.manifest.run_fingerprint


def test_backtesting_py_rejects_close_only_multi_symbol_dataset_as_failed_run(
    tmp_path: Path,
) -> None:
    strategies = StrategyRegistry(tmp_path)
    datasets = DatasetRegistry(tmp_path)
    registration = strategies.add(
        ROOT / "examples" / "adapters" / "backtesting_py_sma.py",
        framework="backtesting.py",
        class_name="FrameworkSmaCross",
    )
    result = RunLedger().create(
        strategy_id=registration.strategy_id,
        dataset_id="pairs-sample-v1",
        parameters={},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    assert result.manifest.status == "FAILED"
    assert result.manifest.failure is not None
    assert result.manifest.failure.exception_type == "ADAPTER_VALIDATION_FAILED"
    assert "missing required framework fields" in result.manifest.failure.message.lower()


def test_real_vectorbt_portfolio_only_is_basic_and_explicit_arrays_are_standard(
    tmp_path: Path,
) -> None:
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _dataset(datasets)
    ledger = RunLedger()

    portfolio_strategies = StrategyRegistry(tmp_path / "portfolio")
    portfolio_registration = portfolio_strategies.add(
        ROOT / "examples" / "adapters" / "vectorbt_portfolio_only.py",
        framework="vectorbt",
        entrypoint="build_portfolio",
    )
    portfolio_run = ledger.create(
        strategy_id=portfolio_registration.strategy_id,
        dataset_id=dataset_id,
        parameters={},
        research_cutoff=None,
        strategy_registry_override=portfolio_strategies,
        dataset_registry_override=datasets,
    )
    assert portfolio_run.trace is not None
    assert portfolio_run.manifest.runtime.trace_fidelity == "BASIC"
    assert portfolio_run.manifest.runtime.trace_capabilities.feature_values == "UNAVAILABLE"
    assert portfolio_run.manifest.runtime.trace_capabilities.decision_events == "UNAVAILABLE"

    explicit_strategies = StrategyRegistry(tmp_path / "explicit")
    explicit_registration = explicit_strategies.add(
        ROOT / "examples" / "adapters" / "vectorbt_sma.py",
        framework="vectorbt",
        entrypoint="build_strategy",
    )
    explicit_run = ledger.create(
        strategy_id=explicit_registration.strategy_id,
        dataset_id=dataset_id,
        parameters={"fast_window": 4, "slow_window": 9},
        research_cutoff=None,
        strategy_registry_override=explicit_strategies,
        dataset_registry_override=datasets,
    )
    assert explicit_run.trace is not None
    assert explicit_run.manifest.runtime.framework_version == "1.0.0"
    assert explicit_run.manifest.runtime.trace_fidelity == "STANDARD"
    assert explicit_run.manifest.runtime.trace_capabilities.feature_values == "AVAILABLE"
    assert explicit_run.manifest.runtime.trace_capabilities.decision_events == "AVAILABLE"
    assert explicit_run.manifest.runtime.trace_capabilities.feature_lineage == "UNAVAILABLE"
    assert explicit_run.manifest.runtime.trace_capabilities.point_in_time_proven == "UNAVAILABLE"


def test_framework_comparison_respects_runtime_context_and_finds_trading_divergence(
    tmp_path: Path,
) -> None:
    strategies = StrategyRegistry(tmp_path)
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _dataset(datasets)
    registration = strategies.add(
        ROOT / "examples" / "adapters" / "backtesting_py_sma.py",
        framework="backtesting.py",
        class_name="FrameworkSmaCross",
    )
    ledger = RunLedger()
    first = ledger.create(
        strategy_id=registration.strategy_id,
        dataset_id=dataset_id,
        parameters={"fast_window": 3, "slow_window": 8},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    second = ledger.create(
        strategy_id=registration.strategy_id,
        dataset_id=dataset_id,
        parameters={"fast_window": 5, "slow_window": 11},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    report = compare_runs(ledger.repository, (first.manifest.run_id, second.manifest.run_id))
    assert report.comparability == "STRICTLY_COMPARABLE"
    assert {item.parameter for item in report.parameter_diff} == {"fast_window", "slow_window"}
    assert report.first_trading_divergence is not None
    assert report.first_trading_divergence.status == "DIVERGENCE"


def test_vectorbt_future_shift_never_becomes_verified_point_in_time(tmp_path: Path) -> None:
    source = tmp_path / "future_vectorbt.py"
    source.write_text(
        """
import vectorbt as vbt
from app.adapters.models import AdapterDataRequirements, AdapterStrategyManifest
def build(ctx):
    close = ctx.close()
    future = close.shift(-1)
    portfolio = vbt.Portfolio.from_holding(close, init_cash=100000.0)
    return ctx.result(portfolio=portfolio, features={'future_close': future})
VQD_ADAPTER_MANIFEST = AdapterStrategyManifest(
    strategy_id='future-vectorbt', name='Future Vectorbt',
    data_requirements=AdapterDataRequirements(required_fields=('close',), symbol_count=1))
""",
        encoding="utf-8",
    )
    strategies = StrategyRegistry(tmp_path)
    datasets = DatasetRegistry(tmp_path)
    dataset_id = _dataset(datasets)
    registration = strategies.add(source, framework="vectorbt", entrypoint="build")
    result = RunLedger().create(
        strategy_id=registration.strategy_id,
        dataset_id=dataset_id,
        parameters={},
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    assert result.manifest.runtime.trace_capabilities.point_in_time_proven == "UNAVAILABLE"
    assert result.trace is not None
    assert result.trace.diagnostics == ()
