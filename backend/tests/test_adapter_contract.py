from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.adapters.fake import FakeFrameworkAdapter
from app.adapters.models import (
    AdapterDataRequirements,
    AdapterDataset,
    AdapterMarketPoint,
    AdapterRunRequest,
    AdapterStrategyManifest,
    RuntimeDescriptor,
    TraceCapabilitySet,
    derive_trace_fidelity,
)
from app.adapters.trace_builder import build_adapter_trace
from app.runs.models import RunManifest


def _capabilities(**updates: str) -> TraceCapabilitySet:
    return TraceCapabilitySet.model_validate(updates)


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    (
        (
            TraceCapabilitySet.model_validate(
                {name: "AVAILABLE" for name in TraceCapabilitySet.model_fields}
            ),
            "FULL",
        ),
        (
            _capabilities(
                market_timeline="AVAILABLE",
                feature_values="AVAILABLE",
                orders="PARTIAL",
                trades="AVAILABLE",
                equity="AVAILABLE",
                pnl="AVAILABLE",
            ),
            "STANDARD",
        ),
        (
            _capabilities(
                market_timeline="AVAILABLE",
                trades="AVAILABLE",
                equity="AVAILABLE",
                pnl="AVAILABLE",
            ),
            "BASIC",
        ),
        (
            _capabilities(market_timeline="AVAILABLE", equity="AVAILABLE", pnl="AVAILABLE"),
            "BASIC",
        ),
    ),
)
def test_trace_fidelity_is_derived_from_captured_capabilities(
    capabilities: TraceCapabilitySet, expected: str
) -> None:
    assert derive_trace_fidelity(capabilities) == expected


def test_fake_adapter_builds_basic_trace_without_invented_provenance() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    dataset = AdapterDataset(
        dataset_id="dataset-test",
        name="Test",
        revision="sha256:test",
        symbols=("DEMO",),
        fields=("close",),
        points=tuple(
            AdapterMarketPoint(
                timestamp=start + timedelta(days=index),
                values={"DEMO": {"close": 100.0 + index}},
            )
            for index in range(3)
        ),
    )
    manifest = AdapterStrategyManifest(
        strategy_id="fake-strategy",
        name="Fake",
        data_requirements=AdapterDataRequirements(required_fields=("close",), symbol_count=1),
    )
    result = FakeFrameworkAdapter().execute(
        AdapterRunRequest(
            adapter_id="fake",
            source_path="/trusted/fake.py",
            entrypoint="run",
            manifest=manifest,
            dataset=dataset,
            parameters={},
        )
    )
    trace = build_adapter_trace(result, dataset.name)
    assert trace.metadata.runtime.trace_fidelity == "BASIC"
    assert trace.metadata.runtime.trace_capabilities.data_dependencies == "UNAVAILABLE"
    assert trace.timeline[0].data_dependencies == ()
    assert trace.diagnostics == ()


def test_old_run_manifest_defaults_to_native_full_runtime() -> None:
    payload = {
        "run_version": "1.0",
        "run_id": "run-000000000000000000000000",
        "run_fingerprint": "sha256:old",
        "status": "FAILED",
        "created_at": "2024-01-01T00:00:00Z",
        "completed_at": "2024-01-01T00:00:01Z",
        "strategy": {
            "strategy_id": "old-native",
            "name": "Old Native",
            "version": "1",
            "class_name": "OldStrategy",
            "source_fingerprint": "sha256:source",
            "original_source_path": "/old.py",
        },
        "dataset": {
            "dataset_id": "old-data",
            "name": "Old Data",
            "content_fingerprint": "sha256:data",
            "source_timezone": "UTC",
        },
        "period": {"start": None, "end": None, "cutoff": None},
        "parameters": {},
        "execution_model": {},
        "engine": {"python_version": "3.12", "platform": "test", "vqd_version": "0.1"},
        "artifacts": {"strategy_source_sha256": "sha256:source"},
    }
    manifest = RunManifest.model_validate(payload)
    assert manifest.runtime == RuntimeDescriptor()
    assert manifest.runtime.trace_fidelity == "FULL"


def test_adapter_result_rejects_claimed_fidelity_that_exceeds_evidence() -> None:
    with pytest.raises(ValidationError, match="must be derived"):
        from app.adapters.models import AdapterEquityPoint, AdapterRunResult

        timestamp = datetime(2024, 1, 1, tzinfo=UTC)
        AdapterRunResult(
            adapter_id="fake",
            adapter_version="1",
            framework_name="fake",
            framework_version="1",
            execution_owner="fake",
            strategy_id="fake",
            strategy_name="Fake",
            parameters={},
            dataset_revision="sha256:data",
            execution_semantics={},
            initial_equity=100.0,
            market_timeline=(
                AdapterMarketPoint(timestamp=timestamp, values={"X": {"close": 1.0}}),
            ),
            equity=(AdapterEquityPoint(timestamp=timestamp, equity=100.0),),
            capabilities=TraceCapabilitySet(),
            fidelity="FULL",
        )
