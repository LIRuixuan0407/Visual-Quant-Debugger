from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.runs.models import RunComparisonReport

ArtifactKind = Literal[
    "DATASET",
    "UNIVERSE",
    "CORPORATE_ACTION_DATASET",
    "FACTOR_RESEARCH",
    "FACTOR_RELATIONSHIP",
    "WALK_FORWARD",
    "HYPOTHESIS",
    "PORTFOLIO_RESEARCH",
    "STRATEGY_SOURCE",
    "RUN_MANIFEST",
    "TRACE",
]
ParameterOwner = Literal["HYPOTHESIS", "FACTOR", "PORTFOLIO", "STRATEGY", "RUN"]
SnapshotScalar = str | int | float | bool | None
ExperimentComparability = Literal[
    "STRICTLY_COMPARABLE",
    "CONTEXTUALLY_COMPARABLE",
    "DESCRIPTIVE_ONLY",
]
ContextSignificance = Literal["STRICT_CONTROL", "CONTEXT", "INFORMATIONAL"]


def sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Research Snapshot timestamps must be timezone-aware")
    return value


class CreateResearchSnapshot(SnapshotModel):
    name: str = Field(min_length=1, max_length=200)
    hypothesis_id: str = Field(min_length=1)


class FrozenArtifact(SnapshotModel):
    kind: ArtifactKind
    artifact_id: str
    source_revision: str
    payload_sha256: str
    payload_json: str

    @model_validator(mode="after")
    def payload_is_valid_and_verified(self) -> FrozenArtifact:
        try:
            json.loads(self.payload_json)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Frozen {self.kind} payload is not valid JSON") from exc
        if sha256_text(self.payload_json) != self.payload_sha256:
            raise ValueError(f"Frozen {self.kind} payload fingerprint does not match")
        return self


class SnapshotParameterValue(SnapshotModel):
    key: str
    value: SnapshotScalar


class SnapshotParameterSet(SnapshotModel):
    owner_type: ParameterOwner
    owner_id: str
    values: tuple[SnapshotParameterValue, ...]


class SnapshotPeriod(SnapshotModel):
    label: str
    source_id: str
    start: datetime | None = None
    end: datetime | None = None
    cutoff: datetime | None = None

    @field_validator("start", "end", "cutoff")
    @classmethod
    def aware_when_present(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)


class SnapshotTimeBoundaries(SnapshotModel):
    research: SnapshotPeriod
    validation: SnapshotPeriod
    holdout: SnapshotPeriod
    runs: tuple[SnapshotPeriod, ...]


class EnvironmentDependency(SnapshotModel):
    name: str
    version: str


class SnapshotEnvironment(SnapshotModel):
    python_version: str
    python_implementation: str
    platform: str
    machine: str
    vqd_version: str
    dependencies: tuple[EnvironmentDependency, ...]


class SnapshotLineage(SnapshotModel):
    dataset_id: str
    dataset_family_id: str | None = None
    dataset_revision: int = Field(default=1, ge=1)
    universe_ids: tuple[str, ...] = ()
    corporate_action_dataset_ids: tuple[str, ...] = ()
    factor_research_ids: tuple[str, ...]
    factor_ids: tuple[str, ...]
    relationship_ids: tuple[str, ...]
    walk_forward_ids: tuple[str, ...]
    hypothesis_id: str
    hypothesis_revision: int = Field(ge=1)
    portfolio_research_id: str
    strategy_id: str
    run_ids: tuple[str, ...]
    trace_ids: tuple[str, ...]


class ResearchSnapshot(SnapshotModel):
    snapshot_version: Literal["1.0"] = "1.0"
    snapshot_id: str
    name: str
    created_at: datetime
    content_fingerprint: str
    lineage: SnapshotLineage
    dataset: FrozenArtifact
    universes: tuple[FrozenArtifact, ...] = ()
    corporate_actions: tuple[FrozenArtifact, ...] = ()
    factors: tuple[FrozenArtifact, ...]
    relationships: tuple[FrozenArtifact, ...]
    walk_forward: tuple[FrozenArtifact, ...]
    hypothesis: FrozenArtifact
    portfolio: FrozenArtifact
    strategy: FrozenArtifact
    runs: tuple[FrozenArtifact, ...]
    traces: tuple[FrozenArtifact, ...]
    parameters: tuple[SnapshotParameterSet, ...]
    time_boundaries: SnapshotTimeBoundaries
    environment: SnapshotEnvironment
    immutability_disclosure: str = (
        "This Research Snapshot is append-only and content-verified. Source records may evolve "
        "only through new revisions; this frozen record is never updated in place."
    )

    _aware_created = field_validator("created_at")(_aware)

    @model_validator(mode="after")
    def complete_research_chain(self) -> ResearchSnapshot:
        if self.dataset.kind != "DATASET" or self.hypothesis.kind != "HYPOTHESIS":
            raise ValueError("Snapshot dataset and hypothesis artifacts have invalid kinds")
        if not self.factors:
            raise ValueError("Research Snapshot requires at least one Factor revision")
        if not self.runs or len(self.runs) != len(self.traces):
            raise ValueError("Research Snapshot requires matched Run and Trace artifacts")
        if tuple(item.artifact_id for item in self.runs) != self.lineage.run_ids:
            raise ValueError("Frozen Run artifacts do not match Snapshot lineage")
        if tuple(item.artifact_id for item in self.traces) != self.lineage.trace_ids:
            raise ValueError("Frozen Trace artifacts do not match Snapshot lineage")
        return self


class ResearchSnapshotSummary(SnapshotModel):
    snapshot_id: str
    name: str
    created_at: datetime
    content_fingerprint: str
    hypothesis_id: str
    hypothesis_revision: int
    dataset_id: str
    dataset_family_id: str | None = None
    dataset_revision: int = Field(default=1, ge=1)
    factor_count: int
    strategy_id: str
    run_count: int
    trace_count: int

    _aware_created = field_validator("created_at")(_aware)


class ExperimentComparisonRequest(SnapshotModel):
    snapshot_ids: tuple[str, ...] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def distinct_snapshots(self) -> ExperimentComparisonRequest:
        if len(self.snapshot_ids) != len(set(self.snapshot_ids)):
            raise ValueError("Select distinct Research Snapshots for comparison")
        return self


class ExperimentSnapshotIdentity(SnapshotModel):
    snapshot_id: str
    name: str
    content_fingerprint: str
    hypothesis_id: str
    hypothesis_revision: int
    run_id: str
    trace_id: str


class ExperimentContextComparison(SnapshotModel):
    field: Literal[
        "dataset_revision",
        "universe_revisions",
        "corporate_action_revisions",
        "research_periods",
        "run_period",
        "execution_model",
        "runtime",
        "creation_environment",
    ]
    same: bool
    significance: ContextSignificance
    values: tuple[str, ...]


class ExperimentArtifactComparison(SnapshotModel):
    kind: ArtifactKind
    semantic_key: str
    artifact_ids: tuple[str | None, ...]
    source_revisions: tuple[str | None, ...]
    payload_fingerprints: tuple[str | None, ...]
    same_revision: bool


class ExperimentParameterComparison(SnapshotModel):
    owner_type: ParameterOwner
    owner_key: str
    parameter: str
    values: tuple[SnapshotScalar, ...]
    changed: Literal[True] = True


class ExperimentMetricComparison(SnapshotModel):
    scope: str
    metric: str
    values: tuple[float | int | None, ...]
    differences_from_first: tuple[float | None, ...]


class ExperimentHypothesisState(SnapshotModel):
    snapshot_id: str
    status: str
    outcome: str
    supporting_evidence: int
    contradicting_evidence: int
    neutral_evidence: int


class ExperimentComparisonReport(SnapshotModel):
    comparison_version: Literal["1.0"] = "1.0"
    snapshot_ids: tuple[str, ...]
    snapshots: tuple[ExperimentSnapshotIdentity, ...]
    comparability: ExperimentComparability
    context_diff: tuple[ExperimentContextComparison, ...]
    artifact_diff: tuple[ExperimentArtifactComparison, ...]
    parameter_diff: tuple[ExperimentParameterComparison, ...]
    metric_diff: tuple[ExperimentMetricComparison, ...]
    hypothesis_states: tuple[ExperimentHypothesisState, ...]
    primary_run_comparison: RunComparisonReport
    comparison_disclosure: str = (
        "Experiment Compare describes controlled context, treatment, result, and recorded "
        "behavior differences. It does not select a winner, optimize parameters, infer "
        "causality from correlation, or make an investment recommendation."
    )


def snapshot_content_fingerprint(snapshot: ResearchSnapshot) -> str:
    payload = json.dumps(
        snapshot.model_dump(mode="json", exclude={"content_fingerprint"}),
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256_text(payload)
