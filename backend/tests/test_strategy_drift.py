from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_discovery import _assets
from test_forward import _bars, _parameters
from test_paper_live import _bar, _service

from app.forward.engine import ForwardSession
from app.paper import PaperSessionRepository
from app.strategy_drift import (
    CreateStrategyDriftReport,
    DriftMetric,
    DriftSource,
    StrategyDriftEngine,
    StrategyDriftIntegrityError,
    StrategyDriftRepository,
)
from app.strategy_drift.engine import (
    _dimension_status,
    _exposure_metrics,
    _FactorEvidence,
    _performance_metrics,
    _ResolvedEvidence,
    _signal_metrics,
    _turnover_metrics,
)
from app.trace.models import TimelineEvent


def _events() -> tuple[TimelineEvent, ...]:
    session = ForwardSession(
        "drift-fixture",
        "pairs-trading",
        "forward-demo-v1",
        _bars(),
        _parameters(),
        strategy_fingerprint="strategy-fingerprint",
        dataset_revision="dataset-revision",
    )
    session.start()
    while session.status == "RUNNING":
        session.step()
    return session.trace().timeline


def _source(
    *,
    source_type: str = "RUN",
    source_id: str = "run-baseline",
    fingerprint: str | None = "strategy-fingerprint",
    parameters: dict[str, int | float] | None = None,
    execution_model: str = "next-close@1.0",
    runtime: str = "runtime",
    sample_size: int = 40,
) -> DriftSource:
    return DriftSource(
        source_type=source_type,  # type: ignore[arg-type]
        source_id=source_id,
        resolved_run_id=source_id if source_type in {"RUN", "PAPER_RUN"} else None,
        trace_id="trace-fixture" if source_type in {"RUN", "PAPER_RUN"} else None,
        strategy_id="pairs-trading",
        strategy_fingerprint=fingerprint,
        parameters=parameters or {"lookback": 5},
        execution_model=execution_model,
        runtime=runtime,
        dataset_id="dataset-fixture",
        dataset_revision="dataset-revision",
        sample_size=sample_size,
        observed_until=datetime(2025, 1, 1, tzinfo=UTC),
        status="COMPLETED",
    )


def test_strategy_revision_and_parameter_changes_block_drift_claims() -> None:
    baseline = _source()
    changed_revision = _source(source_type="FORWARD_SESSION", fingerprint="other")
    comparability, checks = StrategyDriftEngine._comparability(baseline, changed_revision)
    assert comparability == "CONFIGURATION_CHANGED"
    assert next(item for item in checks if item.field == "strategy_fingerprint").same is False

    changed_parameters = _source(
        source_type="FORWARD_SESSION",
        parameters={"lookback": 20},
    )
    comparability, _ = StrategyDriftEngine._comparability(baseline, changed_parameters)
    assert comparability == "CONFIGURATION_CHANGED"


def test_same_strategy_is_comparable_and_runtime_difference_is_contextual() -> None:
    baseline = _source()
    observed = _source(source_type="FORWARD_SESSION")
    comparability, _ = StrategyDriftEngine._comparability(baseline, observed)
    assert comparability == "STRICTLY_COMPARABLE"

    numeric_equivalent = _source(
        source_type="FORWARD_SESSION",
        parameters={"lookback": 5.0},
    )
    comparability, _ = StrategyDriftEngine._comparability(baseline, numeric_equivalent)
    assert comparability == "STRICTLY_COMPARABLE"

    contextual = _source(source_type="FORWARD_SESSION", runtime="different-runtime")
    comparability, _ = StrategyDriftEngine._comparability(baseline, contextual)
    assert comparability == "CONTEXTUALLY_COMPARABLE"


def test_signal_metrics_detect_frequency_and_state_distribution_changes() -> None:
    events = _events()
    baseline = _signal_metrics(events)
    changed = tuple(
        event.model_copy(
            update={
                "signal_evaluation": event.signal_evaluation.model_copy(
                    update={
                        "signal_id": f"forced-{index}",
                        "previous_state": "FLAT",
                        "next_state": "LONG" if index % 2 == 0 else "SHORT",
                        "target_position": 1 if index % 2 == 0 else -1,
                    }
                )
            }
        )
        for index, event in enumerate(events)
    )
    observed = _signal_metrics(changed)
    assert observed["signal_frequency"] == 1.0
    assert observed["state_transition_rate"] > baseline["state_transition_rate"]


def test_turnover_is_normalized_by_equity_not_absolute_notional() -> None:
    events = _events()[:10]
    baseline = _turnover_metrics(events)
    scaled = tuple(
        event.model_copy(
            update={
                "execution_events": tuple(
                    execution.model_copy(
                        update={
                            "quantity": execution.quantity * 10,
                            "traded_notional": execution.traded_notional * 10,
                        }
                    )
                    for execution in event.execution_events
                ),
                "position_snapshot": event.position_snapshot.model_copy(
                    update={
                        "asset_positions": tuple(
                            position.model_copy(
                                update={
                                    "quantity": position.quantity * 10,
                                    "market_value": position.market_value * 10,
                                }
                            )
                            for position in event.position_snapshot.asset_positions
                        ),
                        "gross_exposure": event.position_snapshot.gross_exposure * 10,
                        "net_exposure": event.position_snapshot.net_exposure * 10,
                    }
                ),
                "pnl_snapshot": event.pnl_snapshot.model_copy(
                    update={"equity": event.pnl_snapshot.equity * 10}
                ),
            }
        )
        for event in events
    )
    observed = _turnover_metrics(scaled)
    assert observed["traded_notional_to_average_equity"] == pytest.approx(
        baseline["traded_notional_to_average_equity"]
    )
    assert observed["turnover_per_bar"] == pytest.approx(baseline["turnover_per_bar"])


def test_exposure_concentration_uses_multi_asset_positions() -> None:
    events = _events()[:10]
    baseline = _exposure_metrics(events)
    concentrated = tuple(
        event.model_copy(
            update={
                "position_snapshot": event.position_snapshot.model_copy(
                    update={
                        "asset_positions": tuple(
                            position.model_copy(
                                update={
                                    "market_value": position.market_value
                                    * (10 if index == 0 else 0.1)
                                }
                            )
                            for index, position in enumerate(
                                event.position_snapshot.asset_positions
                            )
                        )
                    }
                )
            }
        )
        for event in events
    )
    observed = _exposure_metrics(concentrated)
    assert observed["exposure_concentration"] >= baseline["exposure_concentration"]
    assert "average_position_count" in observed


def test_short_performance_window_is_insufficient_evidence() -> None:
    raw = _performance_metrics(_events()[:5], minimum_bars=20)
    assert raw
    assert all(value is None for value in raw.values())


def test_factor_without_explicit_lineage_is_insufficient_evidence() -> None:
    engine = StrategyDriftEngine(  # type: ignore[arg-type]
        runs=None,
        snapshots=None,
        forward_lookup=lambda _session_id: None,
    )
    metrics = engine._dimension_metrics(
        "FACTOR",
        _events(),
        _events(),
        window_bars=20,
        factor=None,
    )
    assert _dimension_status(metrics) == "INSUFFICIENT_EVIDENCE"
    assert metrics[0].metric == "factor_distribution"


def test_factor_drift_reuses_canonical_factor_engine(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    _, canonical, factors, _, _, datasets, _, research_ids = assets
    record = factors.get(research_ids[0])
    assert record is not None

    class SpyFactorEngine:
        def __init__(self) -> None:
            self.datasets = datasets
            self.calls = 0

        def observations(self, item):  # type: ignore[no-untyped-def]
            self.calls += 1
            return canonical.observations(item)

    spy = SpyFactorEngine()
    engine = StrategyDriftEngine(  # type: ignore[arg-type]
        runs=None,
        snapshots=None,
        forward_lookup=lambda _session_id: None,
        factor_research=factors,
        factor_engine=spy,
    )
    engine._factor_lineage = lambda _baseline: (record.research_id,)  # type: ignore[method-assign]
    source = _source()
    source = source.model_copy(
        update={
            "dataset_id": record.dataset_id,
            "dataset_revision": record.dataset_revision,
        }
    )
    baseline = _ResolvedEvidence(source=source, timeline=())
    observed = _ResolvedEvidence(
        source=source.model_copy(update={"source_type": "FORWARD_SESSION"}),
        timeline=(
            SimpleNamespace(timestamp=record.periods.research.start),
            SimpleNamespace(timestamp=record.periods.research.end),
        ),  # type: ignore[arg-type]
    )
    factor = engine._factor_evidence(baseline, observed)
    assert factor is not None
    assert spy.calls >= 2
    assert factor.observed
    assert all("Canonical Factor Engine" in item for item in factor.evidence)


def test_factor_drift_uses_each_observed_window_instead_of_full_period(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    _, canonical, factors, _, _, _, _, research_ids = assets
    record = factors.get(research_ids[0])
    assert record is not None
    observations = canonical.observations(record)
    assert observations
    template = observations[0]
    start = template.timestamp
    times = tuple(start + timedelta(days=index) for index in range(10))
    baseline_values = tuple(
        template.model_copy(
            update={
                "timestamp": timestamp,
                "window_start": timestamp,
                "window_end": timestamp,
                "available_at": timestamp,
                "value": 1.0,
            }
        )
        for timestamp in times
    )
    observed_values = tuple(
        template.model_copy(
            update={
                "timestamp": timestamp,
                "window_start": timestamp,
                "window_end": timestamp,
                "available_at": timestamp,
                "value": 1.0 if index < 5 else 10.0,
            }
        )
        for index, timestamp in enumerate(times)
    )
    factor = _FactorEvidence((record,), baseline_values, observed_values, ("canonical",))
    engine = StrategyDriftEngine(None, None, lambda _session_id: None)  # type: ignore[arg-type]
    first = engine._dimension_metrics(
        "FACTOR",
        (),
        (
            SimpleNamespace(timestamp=times[0]),
            SimpleNamespace(timestamp=times[4]),
        ),  # type: ignore[arg-type]
        window_bars=5,
        factor=factor,
    )
    second = engine._dimension_metrics(
        "FACTOR",
        (),
        (
            SimpleNamespace(timestamp=times[5]),
            SimpleNamespace(timestamp=times[9]),
        ),  # type: ignore[arg-type]
        window_bars=5,
        factor=factor,
    )
    first_mean = next(item for item in first if item.metric == "mean")
    second_mean = next(item for item in second if item.metric == "mean")
    assert first_mean.observed_value == pytest.approx(1.0)
    assert second_mean.observed_value == pytest.approx(10.0)


def test_factor_evidence_never_reads_future_targets(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    _, canonical, factors, _, _, datasets, _, research_ids = assets
    record = factors.get(research_ids[0])
    assert record is not None

    class GuardedEngine:
        def __init__(self) -> None:
            self.datasets = datasets

        def observations(self, item):  # type: ignore[no-untyped-def]
            values = canonical.observations(item)
            return tuple(
                value.model_copy(
                    update={
                        "future_returns": {1: 999999.0, 5: 999999.0, 20: 999999.0},
                    }
                )
                for value in values
            )

    engine = StrategyDriftEngine(  # type: ignore[arg-type]
        runs=None,
        snapshots=None,
        forward_lookup=lambda _session_id: None,
        factor_research=factors,
        factor_engine=GuardedEngine(),
    )
    engine._factor_lineage = lambda _baseline: (record.research_id,)  # type: ignore[method-assign]
    source = _source().model_copy(
        update={"dataset_id": record.dataset_id, "dataset_revision": record.dataset_revision}
    )
    observed = _ResolvedEvidence(
        source=source.model_copy(update={"source_type": "FORWARD_SESSION"}),
        timeline=(
            SimpleNamespace(timestamp=record.periods.research.start),
            SimpleNamespace(timestamp=record.periods.research.end),
        ),  # type: ignore[arg-type]
    )
    factor = engine._factor_evidence(_ResolvedEvidence(source=source, timeline=()), observed)
    assert factor is not None
    raw = engine._factor_raw_from_observations(
        factor.observed,
        expected=max(1, len(factor.observed)),
    )
    assert all(value != 999999.0 for value in raw.values() if value is not None)


class _FirstDriftEngine(StrategyDriftEngine):
    def __init__(
        self,
        baseline: _ResolvedEvidence,
        observed: _ResolvedEvidence,
    ) -> None:
        super().__init__(None, None, lambda _session_id: None)  # type: ignore[arg-type]
        self._baseline_evidence = baseline
        self._observed_evidence = observed

    def _baseline(self, request: CreateStrategyDriftReport) -> _ResolvedEvidence:
        return self._baseline_evidence

    def _observed(self, request: CreateStrategyDriftReport) -> _ResolvedEvidence:
        return self._observed_evidence

    def _factor_evidence(self, baseline, observed):  # type: ignore[no-untyped-def]
        return None

    def _dimension_metrics(self, dimension, baseline, observed, **kwargs):  # type: ignore[no-untyped-def]
        is_second_window = observed and observed[0].event_id == "observed-6"
        status = "DRIFT" if dimension == "SIGNAL" and is_second_window else "STABLE"
        distance = 2.5 if status == "DRIFT" else 0.0
        return (
            DriftMetric(
                metric=f"{dimension.lower()}-fixture",
                baseline_value=0.0,
                observed_value=distance,
                relative_change=None,
                normalized_distance=distance,
                status=status,
            ),
        )


def test_first_drift_locates_exact_window_event_for_replay() -> None:
    base_events = _events()[:10]
    observed_events = tuple(
        event.model_copy(update={"event_id": f"observed-{index}"})
        for index, event in enumerate(base_events, start=1)
    )
    baseline = _ResolvedEvidence(source=_source(sample_size=10), timeline=base_events)
    observed = _ResolvedEvidence(
        source=_source(source_type="FORWARD_SESSION", source_id="forward-fixture", sample_size=10),
        timeline=observed_events,
    )
    report = _FirstDriftEngine(baseline, observed).build(
        CreateStrategyDriftReport(
            baseline_type="RUN",
            baseline_id="run-baseline",
            observed_type="FORWARD_SESSION",
            observed_id="forward-fixture",
            window_bars=5,
        )
    )
    assert report.first_drift_dimension == "SIGNAL"
    assert report.first_drift_event_id == "observed-10"
    assert report.first_drift_at == observed_events[-1].timestamp


def test_running_paper_session_records_observed_until_and_sample_size(tmp_path: Path) -> None:
    service, request, _ = _service(tmp_path)
    created = service.create(request)
    asyncio.run(service.start(created.session_id, launch_task=False))
    asyncio.run(service.ingest(created.session_id, _bar(30, 100.0)))
    asyncio.run(service.ingest(created.session_id, _bar(31, 101.0)))

    def lookup(session_id: str):
        repository = PaperSessionRepository(tmp_path)
        return repository.load_manifest(session_id), service.trace(session_id)

    engine = StrategyDriftEngine(  # type: ignore[arg-type]
        runs=None,
        snapshots=None,
        forward_lookup=lambda _session_id: None,
        paper_lookup=lookup,
    )
    evidence = engine._observed(
        CreateStrategyDriftReport(
            baseline_type="RUN",
            baseline_id="run-unused",
            observed_type="PAPER_SESSION",
            observed_id=created.session_id,
            window_bars=5,
        )
    )
    assert evidence.source.status == "PARTIAL"
    assert evidence.source.sample_size == len(evidence.timeline)
    assert evidence.source.observed_until == evidence.timeline[-1].timestamp
    assert evidence.source.dataset_id == f"recorded-feed:{created.session_id}"


def _report_for_repository() -> object:
    events = _events()[:5]
    baseline = _ResolvedEvidence(source=_source(sample_size=5), timeline=events)
    observed = _ResolvedEvidence(
        source=_source(source_type="FORWARD_SESSION", source_id="forward-fixture", sample_size=5),
        timeline=events,
    )
    return _FirstDriftEngine(baseline, observed).build(
        CreateStrategyDriftReport(
            baseline_type="RUN",
            baseline_id="run-baseline",
            observed_type="FORWARD_SESSION",
            observed_id="forward-fixture",
            window_bars=5,
        )
    )


def test_report_is_immutable_and_survives_restart(tmp_path: Path) -> None:
    report = _report_for_repository()
    repository = StrategyDriftRepository(tmp_path)
    saved = repository.save(report)  # type: ignore[arg-type]
    restarted = StrategyDriftRepository(tmp_path)
    assert restarted.get(saved.drift_report_id) == saved
    with pytest.raises(StrategyDriftIntegrityError, match="immutable"):
        repository.save(saved.model_copy(update={"overall_status": "WATCH"}))


def test_strategy_drift_api_is_registered() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/strategy-drift" in paths
    assert "/api/strategy-drift/{report_id}" in paths


async def _api_request(method: str, path: str, **kwargs: object) -> httpx.Response:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_api_builds_real_backtest_vs_forward_drift_report() -> None:
    baseline = asyncio.run(
        _api_request(
            "POST",
            "/api/backtests",
            json={
                "strategy": "pairs-trading",
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
    assert baseline.status_code == 201
    forward = asyncio.run(
        _api_request(
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
    assert forward.status_code == 201
    session_id = forward.json()["session_id"]
    assert (
        asyncio.run(_api_request("POST", f"/api/forward-sessions/{session_id}/start")).status_code
        == 200
    )
    while True:
        snapshot = asyncio.run(
            _api_request("POST", f"/api/forward-sessions/{session_id}/step")
        ).json()
        if snapshot["status"] != "RUNNING":
            break
    created = asyncio.run(
        _api_request(
            "POST",
            "/api/strategy-drift",
            json={
                "baseline_type": "RUN",
                "baseline_id": baseline.json()["run_id"],
                "observed_type": "FORWARD_SESSION",
                "observed_id": session_id,
                "window_bars": 20,
            },
        )
    )
    assert created.status_code == 201
    report = created.json()
    assert report["comparability"] == "STRICTLY_COMPARABLE"
    assert report["observed"]["sample_size"] == snapshot["processed_bar_count"]
    assert report["timeline"]
    if report["first_drift_event_id"] is not None:
        trace = asyncio.run(_api_request("GET", f"/api/forward-sessions/{session_id}/trace")).json()
        assert report["first_drift_event_id"] in {event["event_id"] for event in trace["timeline"]}
