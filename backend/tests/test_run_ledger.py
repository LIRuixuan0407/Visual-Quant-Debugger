import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

import app.api.replay as replay_api
import app.api.runs as runs_api
import app.diagnostics.engine as diagnostics_engine
from app.datasets import DatasetImportRequest, DatasetRegistry
from app.main import app
from app.runs import (
    AnnotationUpdate,
    ArtifactIntegrityError,
    RunRepository,
    compare_runs,
    run_ledger,
    run_store,
)
from app.sdk.registry import StrategyRegistry
from app.trace import trace_to_json


def _csv(*, price_offset: float = 0.0) -> bytes:
    prices = (
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
    )
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = ["date,ticker,price"]
    rows.extend(
        f"{(start + timedelta(days=index)).isoformat()},AAPL,{price + price_offset}"
        for index, price in enumerate(prices)
    )
    return ("\n".join(rows) + "\n").encode()


def _registries(
    tmp_path: Path, *, source: str | None = None
) -> tuple[StrategyRegistry, DatasetRegistry, Path, str, str]:
    source_path = tmp_path / "sma_cross.py"
    source_path.write_text(
        source
        if source is not None
        else (Path(__file__).parents[2] / "examples" / "sma_cross.py").read_text(),
        encoding="utf-8",
    )
    strategies = StrategyRegistry(tmp_path)
    strategy = strategies.add(source_path)
    datasets = DatasetRegistry(tmp_path)
    preview = datasets.preview("prices.csv", _csv())
    dataset = datasets.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name="AAPL Research",
            mapping={"timestamp": "date", "symbol": "ticker", "close": "price"},
        )
    )
    return strategies, datasets, source_path, strategy.strategy_id, dataset.dataset_id


def _run(
    strategies: StrategyRegistry,
    datasets: DatasetRegistry,
    strategy_id: str,
    dataset_id: str,
    *,
    fast_window: int = 3,
) -> object:
    return run_ledger.create(
        strategy_id=strategy_id,
        dataset_id=dataset_id,
        parameters={
            "fast_window": fast_window,
            "slow_window": 5,
            "quantity": 100,
            "fee_bps": 5,
            "slippage_bps": 5,
        },
        research_cutoff=None,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )


async def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_sqlite_manifest_trace_snapshot_restart_and_integrity(tmp_path: Path) -> None:
    strategies, datasets, source_path, strategy_id, dataset_id = _registries(tmp_path)
    result = _run(strategies, datasets, strategy_id, dataset_id)
    manifest = result.manifest
    assert manifest.status == "COMPLETED"
    assert manifest.run_version == "1.1"
    assert manifest.trace_version == "1.0"
    assert manifest.dataset.content_fingerprint == datasets.get(dataset_id).content_fingerprint
    assert manifest.run_fingerprint.startswith("sha256:")
    assert manifest.artifacts.trace_sha256
    assert manifest.artifacts.strategy_source_sha256
    run_directory = tmp_path / ".vqd" / "runs" / manifest.run_id
    assert (run_directory / "manifest.json").is_file()
    assert (run_directory / "strategy.py").read_bytes() == source_path.read_bytes()
    assert (run_directory / "trace.json").is_file()

    restarted = RunRepository(tmp_path)
    assert restarted.schema_version() == 4
    restored = restarted.get_manifest(manifest.run_id)
    assert restored == manifest
    assert trace_to_json(restarted.load_trace_for_run(manifest.run_id)) == trace_to_json(
        result.trace
    )

    (run_directory / "trace.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="trace.json hash mismatch"):
        restarted.get_manifest(manifest.run_id)


def test_source_change_is_detected_and_exact_rerun_uses_snapshot(tmp_path: Path) -> None:
    strategies, datasets, source_path, strategy_id, dataset_id = _registries(tmp_path)
    original = _run(strategies, datasets, strategy_id, dataset_id)
    source_path.write_text(
        source_path.read_text() + "\n# current revision changed\n", encoding="utf-8"
    )

    detail = run_ledger.detail(
        original.manifest.run_id,
        strategy_registry_override=strategies,
    )
    assert detail.current_source_matches is False
    assert (
        "current revision changed"
        not in run_ledger.repository.strategy_source(original.manifest.run_id).source
    )

    reproduced = run_ledger.reproduce(
        original.manifest.run_id,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    assert reproduced.manifest.run_id != original.manifest.run_id
    assert reproduced.manifest.reproduced_from_run_id == original.manifest.run_id
    assert reproduced.manifest.run_fingerprint == original.manifest.run_fingerprint
    assert reproduced.manifest.metrics == original.manifest.metrics
    assert reproduced.manifest.trace_id != original.manifest.trace_id
    assert trace_to_json(reproduced.trace) == trace_to_json(original.trace)


def test_failed_and_partial_runs_are_durable(tmp_path: Path) -> None:
    strategies, datasets, _, strategy_id, dataset_id = _registries(tmp_path)
    with pytest.raises(ValueError):
        _run(strategies, datasets, strategy_id, dataset_id, fast_window=0)
    failed = run_ledger.repository.list_runs(status="FAILED")
    assert failed.total == 1
    failed_manifest = run_ledger.repository.get_manifest(failed.items[0].run_id)
    assert failed_manifest.failure is not None
    assert failed_manifest.trace_id is None
    assert run_ledger.repository.strategy_path(failed_manifest.run_id).is_file()

    source = (Path(__file__).parents[2] / "examples" / "sma_cross.py").read_text()
    source = source.replace(
        "self._target = 0.0",
        "self._target = 0.0\n        self._bar_count = 0",
        1,
    ).replace(
        "def on_bar(self, context: StrategyContext) -> TargetPortfolioIntent:\n",
        "def on_bar(self, context: StrategyContext) -> TargetPortfolioIntent:\n"
        "        self._bar_count += 1\n"
        "        if self._bar_count == 10:\n"
        "            raise RuntimeError('deliberate research failure')\n",
        1,
    )
    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    partial_strategies, partial_datasets, _, partial_strategy_id, partial_dataset_id = _registries(
        partial_root, source=source
    )
    run_store.use_workspace(partial_root)
    partial = _run(
        partial_strategies,
        partial_datasets,
        partial_strategy_id,
        partial_dataset_id,
    )
    assert partial.manifest.status == "PARTIAL"
    assert partial.manifest.failure is not None
    assert partial.manifest.failure.event_index == 9
    assert partial.trace is not None
    assert len(partial.trace.timeline) == 9
    assert RunRepository(partial_root).load_trace_for_run(partial.manifest.run_id) == partial.trace


def test_list_filter_annotations_delete_and_path_validation(tmp_path: Path) -> None:
    strategies, datasets, _, strategy_id, dataset_id = _registries(tmp_path)
    first = _run(strategies, datasets, strategy_id, dataset_id)
    second = _run(strategies, datasets, strategy_id, dataset_id, fast_window=4)
    repository = run_ledger.repository

    page = repository.list_runs(limit=1, strategy_id=strategy_id, dataset_id=dataset_id)
    assert page.total == 2
    assert len(page.items) == 1
    annotations = repository.update_annotations(
        first.manifest.run_id,
        AnnotationUpdate(
            display_name="SMA baseline",
            note="Shorter fast window baseline.",
            tags=("baseline", "cost-test", "baseline"),
        ),
    )
    assert annotations.tags == ("baseline", "cost-test")
    assert repository.list_runs(search="baseline").total == 1
    repository.delete(second.manifest.run_id)
    assert repository.list_runs().total == 1
    assert not (tmp_path / ".vqd" / "runs" / second.manifest.run_id).exists()
    with pytest.raises(ValueError, match="Invalid run id"):
        repository.get_manifest("../../outside")


def test_comparison_classification_diffs_and_no_divergence(tmp_path: Path) -> None:
    strategies, datasets, _, strategy_id, dataset_id = _registries(tmp_path)
    run_a = _run(strategies, datasets, strategy_id, dataset_id, fast_window=3)
    exact = run_ledger.reproduce(
        run_a.manifest.run_id,
        strategy_registry_override=strategies,
        dataset_registry_override=datasets,
    )
    run_c = _run(strategies, datasets, strategy_id, dataset_id, fast_window=2)

    no_divergence = compare_runs(
        run_ledger.repository,
        (run_a.manifest.run_id, exact.manifest.run_id),
    )
    assert no_divergence.comparability == "STRICTLY_COMPARABLE"
    assert no_divergence.first_behavioral_divergence is not None
    assert no_divergence.first_behavioral_divergence.status == "NO_BEHAVIORAL_DIVERGENCE"

    strict = compare_runs(
        run_ledger.repository,
        (run_a.manifest.run_id, run_c.manifest.run_id),
    )
    assert strict.comparability == "STRICTLY_COMPARABLE"
    assert [(item.parameter, item.values) for item in strict.parameter_diff] == [
        ("fast_window", (3, 2))
    ]
    assert strict.signal_comparison
    assert strict.first_behavioral_divergence is not None
    assert strict.first_behavioral_divergence.status == "DIVERGENCE"
    assert strict.first_behavioral_divergence.kind in {"FEATURE", "CONDITION", "SIGNAL"}
    assert strict.first_behavioral_divergence.associated_parameter_differences == ("fast_window",)

    preview = datasets.preview("different.csv", _csv(price_offset=0.25))
    other_dataset = datasets.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name="Shifted AAPL Research",
            mapping={"timestamp": "date", "symbol": "ticker", "close": "price"},
        )
    )
    contextual_run = _run(
        strategies,
        datasets,
        strategy_id,
        other_dataset.dataset_id,
    )
    contextual = compare_runs(
        run_ledger.repository,
        (run_a.manifest.run_id, contextual_run.manifest.run_id),
    )
    assert contextual.comparability == "CONTEXTUALLY_COMPARABLE"
    assert contextual.first_behavioral_divergence is None

    built_in = run_ledger.create(
        strategy_id="pairs-trading",
        dataset_id="pairs-sample-v1",
        parameters={"lookback": 5, "entry_z": 1.0, "exit_z": 0.25},
        research_cutoff=None,
    )
    descriptive = compare_runs(
        run_ledger.repository,
        (run_a.manifest.run_id, built_in.manifest.run_id),
    )
    assert descriptive.comparability == "DESCRIPTIVE_ONLY"
    assert descriptive.equity_comparison == ()
    assert descriptive.signal_comparison == ()


def test_diagnostics_and_autopsy_are_persisted_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    strategies, datasets, _, strategy_id, dataset_id = _registries(tmp_path)
    monkeypatch.setattr(diagnostics_engine, "strategy_registry", strategies)
    monkeypatch.setattr(diagnostics_engine, "dataset_registry", datasets)
    result = _run(strategies, datasets, strategy_id, dataset_id)
    trace_id = result.manifest.trace_id
    assert trace_id is not None

    diagnosis = asyncio.run(_request("POST", "/api/diagnostics", json={"trace_id": trace_id}))
    autopsy = asyncio.run(_request("GET", f"/api/traces/{trace_id}/pnl-autopsy"))
    assert diagnosis.status_code == 200
    assert autopsy.status_code == 200
    manifest = run_ledger.repository.get_manifest(result.manifest.run_id)
    assert manifest.artifacts.diagnostics_sha256
    assert manifest.artifacts.pnl_autopsy_sha256
    run_directory = tmp_path / ".vqd" / "runs" / manifest.run_id
    assert (run_directory / "diagnostics.json").is_file()
    assert (run_directory / "pnl-autopsy.json").is_file()

    assert (
        asyncio.run(_request("POST", "/api/diagnostics", json={"trace_id": trace_id})).json()
        == diagnosis.json()
    )
    assert asyncio.run(_request("GET", f"/api/traces/{trace_id}/pnl-autopsy")).json() == (
        autopsy.json()
    )


def test_run_api_history_annotations_rerun_comparison_and_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    strategies, datasets, _, strategy_id, dataset_id = _registries(tmp_path)
    monkeypatch.setattr(replay_api, "strategy_registry", strategies)
    monkeypatch.setattr(replay_api, "dataset_registry", datasets)
    monkeypatch.setattr(runs_api, "strategy_registry", strategies)
    monkeypatch.setattr(runs_api, "dataset_registry", datasets)
    payload = {
        "strategy_id": strategy_id,
        "dataset_id": dataset_id,
        "parameters": {
            "fast_window": 3,
            "slow_window": 5,
            "quantity": 100,
            "fee_bps": 5,
            "slippage_bps": 5,
        },
    }
    created = asyncio.run(_request("POST", "/api/backtests", json=payload))
    assert created.status_code == 201
    run_id = created.json()["run_id"]
    listing = asyncio.run(
        _request(
            "GET",
            f"/api/runs?strategy_id={strategy_id}&dataset_id={dataset_id}&status=COMPLETED",
        )
    )
    assert listing.status_code == 200
    assert [item["run_id"] for item in listing.json()["items"]] == [run_id]
    detail = asyncio.run(_request("GET", f"/api/runs/{run_id}"))
    source = asyncio.run(_request("GET", f"/api/runs/{run_id}/strategy-source"))
    assert detail.json()["integrity"] == "VERIFIED"
    assert "class MovingAverageCross" in source.json()["source"]

    annotations = asyncio.run(
        _request(
            "PATCH",
            f"/api/runs/{run_id}/annotations",
            json={
                "display_name": "API baseline",
                "note": "Persistent annotation.",
                "tags": ["baseline", "api"],
            },
        )
    )
    assert annotations.status_code == 200
    assert annotations.json()["tags"] == ["baseline", "api"]
    rerun = asyncio.run(_request("POST", f"/api/runs/{run_id}/rerun"))
    assert rerun.status_code == 201
    rerun_id = rerun.json()["run_id"]
    assert rerun_id != run_id
    comparison = asyncio.run(
        _request(
            "POST",
            "/api/run-comparisons",
            json={"run_ids": [run_id, rerun_id]},
        )
    )
    assert comparison.status_code == 200
    assert comparison.json()["comparability"] == "STRICTLY_COMPARABLE"
    assert comparison.json()["first_behavioral_divergence"]["status"] == "NO_BEHAVIORAL_DIVERGENCE"
    deleted = asyncio.run(_request("DELETE", f"/api/runs/{rerun_id}"))
    assert deleted.status_code == 204
    assert asyncio.run(_request("GET", f"/api/runs/{rerun_id}")).status_code == 404
    assert asyncio.run(_request("GET", "/api/runs/not-a-valid-run-id")).status_code == 422
