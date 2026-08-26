from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models import MarketFrame
from app.sdk.loader import StrategyLoadError, load_strategy
from app.sdk.registry import StrategyRegistry
from app.sdk.runtime import StrategyRuntime
from app.sdk.tracing import RuntimeTraceConfiguration, build_runtime_trace
from app.strategies import PairsTradingStrategy


def _example() -> Path:
    return Path(__file__).parents[2] / "examples" / "sma_cross.py"


def _frames(count: int = 12, changed_after: int | None = None) -> tuple[MarketFrame, ...]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return tuple(
        MarketFrame(
            timestamp=start + timedelta(days=index),
            values={
                "AAPL": {
                    "close": (
                        100.0 + index
                        if changed_after is None or index < changed_after
                        else 1_000.0 - index * 10
                    )
                }
            },
        )
        for index in range(count)
    )


def _runtime() -> StrategyRuntime:
    loaded = load_strategy(_example())
    return StrategyRuntime(
        strategy=loaded.strategy_class(),
        parameters={"fast_window": 3, "slow_window": 5, "quantity": 100.0},
    )


def test_strategy_registration_loading_fingerprint_and_restore(tmp_path: Path) -> None:
    registry = StrategyRegistry(tmp_path)
    added = registry.add(_example())
    assert added.strategy_id == "user.sma-cross"
    assert added.source_path == str(_example().resolve())
    assert added.source_fingerprint.startswith("sha256:")
    assert [
        item.name
        for item in registry.load(added.strategy_id).strategy_class.parameter_definitions()
    ] == [
        "fast_window",
        "slow_window",
        "quantity",
    ]
    restored = StrategyRegistry(tmp_path)
    assert restored.list() == (added,)
    assert restored.instantiate(added.strategy_id)[0].metadata.trace_fidelity == "FULL"
    with pytest.raises(ValueError, match="already registered"):
        restored.add(_example())
    assert restored.remove(added.strategy_id) == added
    assert StrategyRegistry(tmp_path).list() == ()


def test_invalid_strategy_errors_include_path_type_and_traceback(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.py"
    invalid.write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(StrategyLoadError) as captured:
        load_strategy(invalid)
    assert captured.value.path == invalid.resolve()
    assert captured.value.exception_type == "SyntaxError"
    assert "SyntaxError" in captured.value.traceback

    missing = tmp_path / "missing.py"
    missing.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(StrategyLoadError, match="No VQDStrategy subclass"):
        load_strategy(missing)


def test_native_runtime_is_point_in_time_safe_and_builds_full_lineage() -> None:
    left = _runtime().run(_frames())
    right = _runtime().run(_frames(changed_after=8))
    assert left.status == right.status == "COMPLETED"
    assert left.rows[:8] == right.rows[:8]

    trace = build_runtime_trace(
        left.rows,
        RuntimeTraceConfiguration(
            dataset_id="dataset-test",
            dataset_name="Test",
            strategy_id="user.sma-cross",
            strategy_name="SMA Cross",
            parameters={"fast_window": 3, "slow_window": 5, "quantity": 100.0},
            initial_cash=100_000.0,
        ),
    )
    signal = next(event for event in trace.timeline if event.signal_evaluation.signal_id)
    features = {item.name: item for item in signal.feature_snapshots}
    assert signal.signal_evaluation.dependencies == (
        features["fast_ma"].feature_id,
        features["slow_ma"].feature_id,
    )
    assert features["fast_ma"].data_dependencies
    assert all(
        dependency.available_at <= dependency.used_at
        for event in trace.timeline
        for dependency in event.data_dependencies
    )
    execution = next(event for event in trace.timeline if event.execution_events)
    assert execution.timestamp > signal.timestamp
    assert execution.order_events[0].source_signal_id == signal.signal_evaluation.signal_id


def test_pairs_strategy_uses_the_users_configured_symbols() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    frames = tuple(
        MarketFrame(
            timestamp=start + timedelta(minutes=index),
            values={"AAPL": {"close": 100.0 + index}, "MSFT": {"close": 200.0 + index * 0.8}},
        )
        for index in range(6)
    )
    result = StrategyRuntime(
        strategy=PairsTradingStrategy(),
        parameters={"lookback": 2, "entry_z": 2.0, "exit_z": 0.5},
    ).run(frames)

    assert result.status == "COMPLETED"
    assert result.failure is None
    assert all(tuple(row.market) == ("AAPL", "MSFT") for row in result.rows)
    assert all(set(row.decision.intent.target_weights) == {"AAPL", "MSFT"} for row in result.rows)


def test_runtime_exception_stops_run_and_marks_partial(tmp_path: Path) -> None:
    source = tmp_path / "explodes.py"
    source.write_text(
        """
from app.sdk import StrategyMetadata, VQDStrategy

class Explodes(VQDStrategy):
    metadata = StrategyMetadata(
        strategy_id="user.explodes", name="Explodes", version="1", description="test"
    )
    def on_bar(self, context):
        if context.current_time.day == 3:
            raise ZeroDivisionError("deliberate")
        return None
""".lstrip(),
        encoding="utf-8",
    )
    loaded = load_strategy(source)
    result = StrategyRuntime(strategy=loaded.strategy_class(), parameters={}).run(_frames(5))
    assert result.status == "PARTIAL"
    assert len(result.rows) == 2
    assert result.failure is not None
    assert result.failure.strategy_id == "user.explodes"
    assert result.failure.event_index == 2
    assert result.failure.exception_type == "ZeroDivisionError"
    assert result.failure.message == "deliberate"
