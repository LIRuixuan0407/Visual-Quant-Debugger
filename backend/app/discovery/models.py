from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.factors.models import ResearchStage
from app.portfolio_lab.models import RebalanceRule

OutcomeClassification = Literal[
    "SUPPORTED",
    "MIXED",
    "NOT_SUPPORTED",
    "INSUFFICIENT_EVIDENCE",
]
HypothesisStatus = Literal[
    "DRAFT",
    "RESEARCHED",
    "VALIDATED",
    "HOLDOUT_REVEALED",
    "STRATEGY_CREATED",
]
EvidenceStance = Literal["SUPPORTING", "CONTRADICTING", "NEUTRAL"]
EvidenceMetric = str | int | float | bool | None


class DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Discovery timestamps must be timezone-aware")
    return value


class CreateHypothesis(DiscoveryModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    universe: tuple[str, ...] = ()
    factor_research_ids: tuple[str, ...] = Field(min_length=1, max_length=12)
    expected_relationship: str = Field(min_length=1, max_length=1_000)
    holding_horizon: str = Field(min_length=1, max_length=80)
    rebalance_idea: RebalanceRule = "MONTHLY"
    risk_assumptions: tuple[str, ...] = ()

    @field_validator("universe", mode="before")
    @classmethod
    def normalize_universe(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple | set):
            raise ValueError("Hypothesis universe must be a list of symbols")
        symbols = tuple(str(item).strip().upper() for item in value if str(item).strip())
        if len(symbols) != len(set(symbols)):
            raise ValueError("Hypothesis universe cannot contain duplicate symbols")
        return symbols

    @model_validator(mode="after")
    def unique_factors(self) -> CreateHypothesis:
        if len(self.factor_research_ids) != len(set(self.factor_research_ids)):
            raise ValueError("Hypothesis Factor research references must be unique")
        return self


class CreateHypothesisRevision(DiscoveryModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2_000)
    universe: tuple[str, ...] | None = None
    factor_research_ids: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=12)
    expected_relationship: str | None = Field(default=None, min_length=1, max_length=1_000)
    holding_horizon: str | None = Field(default=None, min_length=1, max_length=80)
    rebalance_idea: RebalanceRule | None = None
    risk_assumptions: tuple[str, ...] | None = None
    revision_reason: str = Field(min_length=1, max_length=1_000)

    @field_validator("universe", mode="before")
    @classmethod
    def normalize_universe(cls, value: object) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not isinstance(value, list | tuple | set):
            raise ValueError("Hypothesis universe must be a list of symbols")
        symbols = tuple(str(item).strip().upper() for item in value if str(item).strip())
        if len(symbols) != len(set(symbols)):
            raise ValueError("Hypothesis universe cannot contain duplicate symbols")
        return symbols

    @model_validator(mode="after")
    def unique_factors(self) -> CreateHypothesisRevision:
        if self.factor_research_ids is not None and len(self.factor_research_ids) != len(
            set(self.factor_research_ids)
        ):
            raise ValueError("Hypothesis Factor research references must be unique")
        return self


class HypothesisEvidence(DiscoveryModel):
    evidence_id: str
    source_type: Literal["FACTOR", "RELATIONSHIP", "WALK_FORWARD", "PORTFOLIO"]
    source_id: str
    stage: ResearchStage | Literal["WALK_FORWARD"]
    stance: EvidenceStance
    label: str
    detail: str
    metrics: dict[str, EvidenceMetric] = Field(default_factory=dict)


class CandidateStrategyTemplate(DiscoveryModel):
    combination: Literal["RANK_AVERAGE"] = "RANK_AVERAGE"
    selection: Literal["TOP_PERCENT"] = "TOP_PERCENT"
    top_percent: float = Field(default=20.0, gt=0, le=100)
    weighting: Literal["EQUAL_WEIGHT"] = "EQUAL_WEIGHT"
    max_single_position_weight: float = Field(default=1.0, gt=0, le=1)
    rebalance: RebalanceRule
    long_only: Literal[True] = True
    portfolio_research_id: str | None = None


class HypothesisLineage(DiscoveryModel):
    factor_research_ids: tuple[str, ...]
    factor_ids: tuple[str, ...]
    relationship_ids: tuple[str, ...] = ()
    walk_forward_ids: tuple[str, ...] = ()
    portfolio_research_id: str | None = None
    strategy_id: str | None = None
    run_ids: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()


class ResearchHypothesis(DiscoveryModel):
    hypothesis_id: str
    family_id: str
    parent_hypothesis_id: str | None = None
    revision: int = Field(ge=1)
    title: str
    description: str
    dataset_id: str
    dataset_fingerprint: str
    universe: tuple[str, ...]
    factor_research_ids: tuple[str, ...]
    expected_relationship: str
    holding_horizon: str
    rebalance_idea: RebalanceRule
    risk_assumptions: tuple[str, ...]
    created_at: datetime
    status: HypothesisStatus = "DRAFT"
    outcome: OutcomeClassification = "INSUFFICIENT_EVIDENCE"
    created_with_known_stage: ResearchStage
    source_revealed_stages: dict[str, ResearchStage]
    evidence: tuple[HypothesisEvidence, ...] = ()
    candidate: CandidateStrategyTemplate
    lineage: HypothesisLineage
    revision_reason: str | None = None
    ai_boundary: str = (
        "Optional AI may summarize already revealed evidence, propose testable hypotheses, and "
        "explain Factor relationships. It cannot calculate quantitative metrics, change backend "
        "results, read sealed Holdout evidence, optimize, run unbounded experiments, or claim "
        "alpha."
    )

    _aware_created = field_validator("created_at")(_aware)


class DiscoverySuggestion(DiscoveryModel):
    label: Literal["RESEARCH IDEA"] = "RESEARCH IDEA"
    factor_research_ids: tuple[str, ...]
    rationale: str
    source_relationship_id: str


class AttachHypothesisRun(DiscoveryModel):
    run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
