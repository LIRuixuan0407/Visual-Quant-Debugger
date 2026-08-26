from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from app.datasets import DatasetRegistry
from app.factor_relationships.models import FactorRelationshipRecord
from app.factor_relationships.repository import FactorRelationshipRepository
from app.factors.models import FactorResearchRecord, HorizonEvaluation, ResearchStage
from app.factors.repository import FactorResearchRepository
from app.portfolio_lab import (
    CreatePortfolioResearch,
    PortfolioResearchEngine,
    PortfolioResearchRecord,
    PortfolioStrategyFactory,
)
from app.portfolio_lab.models import PortfolioConstruction, PortfolioFactorRef, PortfolioFilters
from app.portfolio_lab.repository import PortfolioResearchRepository
from app.research_ledger import ResearchLedgerEntry, ResearchLedgerRepository
from app.runs import RunRepository, run_store
from app.walk_forward.repository import WalkForwardRepository

from .models import (
    CandidateStrategyTemplate,
    CreateHypothesis,
    CreateHypothesisRevision,
    DiscoverySuggestion,
    EvidenceStance,
    HypothesisEvidence,
    HypothesisLineage,
    HypothesisStatus,
    OutcomeClassification,
    ResearchHypothesis,
)
from .repository import HypothesisRepository

_STAGE_ORDER: dict[ResearchStage, int] = {"RESEARCH": 0, "VALIDATION": 1, "HOLDOUT": 2}
_STATUS_STAGE: dict[HypothesisStatus, ResearchStage] = {
    "DRAFT": "RESEARCH",
    "RESEARCHED": "RESEARCH",
    "VALIDATED": "VALIDATION",
    "HOLDOUT_REVEALED": "HOLDOUT",
    "STRATEGY_CREATED": "HOLDOUT",
}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stance(*, positive: bool, negative: bool) -> EvidenceStance:
    if positive and not negative:
        return "SUPPORTING"
    if negative and not positive:
        return "CONTRADICTING"
    return "NEUTRAL"


class DiscoveryEngine:
    def __init__(
        self,
        datasets: DatasetRegistry,
        factors: FactorResearchRepository,
        relationships: FactorRelationshipRepository,
        walk_forward: WalkForwardRepository,
        portfolios: PortfolioResearchRepository,
        hypotheses: HypothesisRepository,
        portfolio_engine: PortfolioResearchEngine,
        strategy_factory: PortfolioStrategyFactory,
        ledger: ResearchLedgerRepository,
        runs: RunRepository | None = None,
    ) -> None:
        self.datasets = datasets
        self.factors = factors
        self.relationships = relationships
        self.walk_forward = walk_forward
        self.portfolios = portfolios
        self.hypotheses = hypotheses
        self.portfolio_engine = portfolio_engine
        self.strategy_factory = strategy_factory
        self.ledger = ledger
        self.runs = run_store.repository if runs is None else runs

    def _factor_records(self, research_ids: tuple[str, ...]) -> tuple[FactorResearchRecord, ...]:
        records: list[FactorResearchRecord] = []
        for research_id in research_ids:
            record = self.factors.get(research_id)
            if record is None:
                raise KeyError(f"Factor research '{research_id}' was not found")
            records.append(record)
        first = records[0]
        for record in records[1:]:
            if record.dataset_id != first.dataset_id:
                raise ValueError("Hypothesis Factors must use one market dataset")
            if record.dataset_revision != first.dataset_revision:
                raise ValueError("Hypothesis Factors must use one dataset revision")
            if record.universe != first.universe:
                raise ValueError("Hypothesis Factors must use one research universe")
            if record.periods != first.periods:
                raise ValueError("Hypothesis Factors must use matching stage boundaries")
        dataset = self.datasets.get(first.dataset_id)
        if dataset is None:
            raise KeyError(f"Dataset '{first.dataset_id}' was not found")
        if dataset.content_fingerprint != first.dataset_revision:
            raise ValueError("Hypothesis dataset fingerprint no longer matches the Factor research")
        return tuple(records)

    @staticmethod
    def _universe(
        requested: tuple[str, ...],
        records: tuple[FactorResearchRecord, ...],
    ) -> tuple[str, ...]:
        source = records[0].universe
        if not requested:
            return source
        requested_set = set(requested)
        unknown = sorted(requested_set - set(source))
        if unknown:
            detail = ", ".join(unknown)
            raise ValueError(
                f"Hypothesis universe contains symbols outside Factor research: {detail}"
            )
        return tuple(symbol for symbol in source if symbol in requested_set)

    @staticmethod
    def _known_stage(records: tuple[FactorResearchRecord, ...]) -> ResearchStage:
        return max((record.revealed_stage for record in records), key=_STAGE_ORDER.__getitem__)

    @staticmethod
    def _horizon(value: str) -> int:
        match = re.search(r"\b(1|5|20)\b", value)
        return int(match.group(1)) if match else 20

    @staticmethod
    def _evaluation(
        record: FactorResearchRecord,
        stage: ResearchStage,
        horizon: int,
    ) -> HorizonEvaluation | None:
        period = next((item for item in record.evaluations if item.stage == stage), None)
        if period is None:
            return None
        return next((item for item in period.horizons if item.horizon == horizon), None)

    @staticmethod
    def _factor_evidence(
        records: tuple[FactorResearchRecord, ...],
        allowed_stages: dict[str, ResearchStage],
        horizon: int,
    ) -> list[HypothesisEvidence]:
        evidence: list[HypothesisEvidence] = []
        stages: tuple[ResearchStage, ...] = ("RESEARCH", "VALIDATION", "HOLDOUT")
        for record in records:
            allowed_stage = allowed_stages[record.research_id]
            for stage in stages:
                if _STAGE_ORDER[stage] > _STAGE_ORDER[allowed_stage]:
                    continue
                evaluation = DiscoveryEngine._evaluation(record, stage, horizon)
                if evaluation is None:
                    continue
                positive = (
                    evaluation.rank_ic is not None
                    and evaluation.rank_ic > 0
                    and evaluation.long_short_spread is not None
                    and evaluation.long_short_spread > 0
                    and evaluation.monotonic
                )
                negative = (evaluation.rank_ic is not None and evaluation.rank_ic < 0) or (
                    evaluation.long_short_spread is not None and evaluation.long_short_spread < 0
                )
                evidence.append(
                    HypothesisEvidence(
                        evidence_id=f"factor:{record.research_id}:{stage}:{horizon}",
                        source_type="FACTOR",
                        source_id=record.research_id,
                        stage=stage,
                        stance=_stance(positive=positive, negative=negative),
                        label=f"{record.factor.name} · {stage}",
                        detail=(
                            f"{horizon}D Factor evidence known when this hypothesis revision was "
                            f"created, from the saved {stage} evaluation; no metric is recomputed "
                            "in Discovery."
                        ),
                        metrics={
                            "ic": evaluation.ic,
                            "rank_ic": evaluation.rank_ic,
                            "q5_minus_q1": evaluation.long_short_spread,
                            "coverage": evaluation.coverage,
                            "turnover": evaluation.turnover,
                            "monotonic": evaluation.monotonic,
                        },
                    )
                )
        return evidence

    @staticmethod
    def _relationship_relevant(
        record: FactorRelationshipRecord,
        research_ids: tuple[str, ...],
        source_stages: dict[str, ResearchStage],
    ) -> bool:
        selected = set(research_ids)
        overlap = set(record.factor_research_ids) & selected
        if len(overlap) < 2:
            return False
        return all(
            research_id in source_stages
            and _STAGE_ORDER[source_stages[research_id]] >= _STAGE_ORDER[record.stage]
            for research_id in overlap
        )

    def _relationship_evidence(
        self,
        research_ids: tuple[str, ...],
        source_stages: dict[str, ResearchStage],
    ) -> tuple[list[HypothesisEvidence], tuple[str, ...]]:
        selected = set(research_ids)
        evidence: list[HypothesisEvidence] = []
        lineage_ids: list[str] = []
        for record in self.relationships.list():
            if not self._relationship_relevant(record, research_ids, source_stages):
                continue
            lineage_ids.append(record.relationship_id)
            for redundancy in record.redundancy:
                if {redundancy.left_research_id, redundancy.right_research_id} <= selected:
                    evidence.append(
                        HypothesisEvidence(
                            evidence_id=(
                                f"relationship:{record.relationship_id}:redundancy:"
                                f"{redundancy.left_research_id}:{redundancy.right_research_id}"
                            ),
                            source_type="RELATIONSHIP",
                            source_id=record.relationship_id,
                            stage=record.stage,
                            stance=(
                                "CONTRADICTING"
                                if redundancy.status == "HIGH_REDUNDANCY"
                                else "NEUTRAL"
                            ),
                            label=f"{redundancy.status.replace('_', ' ')} · Factor pair",
                            detail=redundancy.reason,
                            metrics={
                                "rank_correlation": redundancy.rank_correlation,
                                "top_quantile_overlap": redundancy.top_quantile_overlap,
                            },
                        )
                    )
            for incremental in record.incremental_information:
                if {incremental.base_research_id, incremental.added_research_id} <= selected:
                    positive = (
                        incremental.rank_ic_delta is not None
                        and incremental.rank_ic_delta > 0
                        and incremental.spread_delta is not None
                        and incremental.spread_delta > 0
                    )
                    negative = (
                        incremental.rank_ic_delta is not None
                        and incremental.rank_ic_delta < 0
                        and incremental.spread_delta is not None
                        and incremental.spread_delta < 0
                    )
                    evidence.append(
                        HypothesisEvidence(
                            evidence_id=(
                                f"relationship:{record.relationship_id}:incremental:"
                                f"{incremental.base_research_id}:{incremental.added_research_id}"
                            ),
                            source_type="RELATIONSHIP",
                            source_id=record.relationship_id,
                            stage=record.stage,
                            stance=_stance(positive=positive, negative=negative),
                            label="Incremental Information",
                            detail=(
                                "Direction-adjusted percentile Rank Average comparison from "
                                "Factor Relationship research; association only, not causality."
                            ),
                            metrics={
                                "rank_ic_delta": incremental.rank_ic_delta,
                                "spread_delta": incremental.spread_delta,
                                "coverage_delta": incremental.coverage_delta,
                                "turnover_delta": incremental.turnover_delta,
                                "portfolio_effect": incremental.portfolio_effect,
                            },
                        )
                    )
        return evidence, tuple(dict.fromkeys(lineage_ids))

    def _walk_forward_evidence(
        self,
        research_ids: tuple[str, ...],
    ) -> tuple[list[HypothesisEvidence], tuple[str, ...]]:
        selected = set(research_ids)
        evidence: list[HypothesisEvidence] = []
        lineage_ids: list[str] = []
        for record in self.walk_forward.list():
            if record.factor_research_id not in selected:
                continue
            lineage_ids.append(record.walk_forward_id)
            stability = record.stability
            positive = (
                stability.positive_ic_window_ratio >= 0.60
                and stability.factor_sign_consistency >= 0.60
                and stability.quantile_monotonicity_stability >= 0.50
            )
            negative = (
                stability.positive_ic_window_ratio < 0.40
                or stability.factor_sign_consistency < 0.40
            )
            evidence.append(
                HypothesisEvidence(
                    evidence_id=f"walk-forward:{record.walk_forward_id}",
                    source_type="WALK_FORWARD",
                    source_id=record.walk_forward_id,
                    stage="WALK_FORWARD",
                    stance=_stance(positive=positive, negative=negative),
                    label=f"Walk-Forward Stability · {record.factor_id}",
                    detail=(
                        "Fixed-definition rolling stability evidence. Discovery does not optimize "
                        "windows or parameters from this result."
                    ),
                    metrics={
                        "positive_ic_window_ratio": stability.positive_ic_window_ratio,
                        "factor_sign_consistency": stability.factor_sign_consistency,
                        "quantile_monotonicity_stability": (
                            stability.quantile_monotonicity_stability
                        ),
                        "turnover_stability": stability.turnover_stability,
                        "rank_ic_mean": stability.rank_ic_distribution.mean,
                        "strategy_return_mean": (
                            None
                            if stability.strategy_return_distribution is None
                            else stability.strategy_return_distribution.mean
                        ),
                    },
                )
            )
        return evidence, tuple(dict.fromkeys(lineage_ids))

    @staticmethod
    def _portfolio_evidence(
        portfolio: PortfolioResearchRecord | None,
        allowed_stage: ResearchStage,
    ) -> list[HypothesisEvidence]:
        if portfolio is None:
            return []
        evidence: list[HypothesisEvidence] = []
        for result in portfolio.stages:
            if _STAGE_ORDER[result.stage] > _STAGE_ORDER[allowed_stage]:
                continue
            preview = result.cost_preview
            evidence.append(
                HypothesisEvidence(
                    evidence_id=f"portfolio:{portfolio.portfolio_research_id}:{result.stage}",
                    source_type="PORTFOLIO",
                    source_id=portfolio.portfolio_research_id,
                    stage=result.stage,
                    stance=(
                        "SUPPORTING"
                        if preview.net_return > 0
                        else "CONTRADICTING"
                        if preview.net_return < 0
                        else "NEUTRAL"
                    ),
                    label=f"Candidate Portfolio · {result.stage}",
                    detail=(
                        "Native Portfolio Lab result using the fixed hypothesis template and the "
                        "real Execution Engine cost model."
                    ),
                    metrics={
                        "gross_return": preview.gross_return,
                        "fees": preview.fees,
                        "slippage": preview.slippage,
                        "net_return": preview.net_return,
                        "turnover": preview.turnover,
                        "max_drawdown": preview.max_drawdown,
                        "positions": preview.positions,
                        "rebalance_count": preview.rebalance_count,
                    },
                )
            )
        return evidence

    def _source_evidence(
        self,
        record: ResearchHypothesis,
        records: tuple[FactorResearchRecord, ...],
    ) -> tuple[tuple[HypothesisEvidence, ...], tuple[str, ...], tuple[str, ...]]:
        factor_evidence = self._factor_evidence(
            records,
            record.source_revealed_stages,
            self._horizon(record.holding_horizon),
        )
        relationship_evidence, relationship_ids = self._relationship_evidence(
            record.factor_research_ids,
            record.source_revealed_stages,
        )
        walk_forward_evidence, walk_forward_ids = self._walk_forward_evidence(
            record.factor_research_ids
        )
        return (
            tuple([*factor_evidence, *relationship_evidence, *walk_forward_evidence]),
            relationship_ids,
            walk_forward_ids,
        )

    @staticmethod
    def _outcome(
        status: HypothesisStatus,
        evidence: tuple[HypothesisEvidence, ...],
    ) -> OutcomeClassification:
        if status in {"DRAFT", "RESEARCHED"}:
            return "INSUFFICIENT_EVIDENCE"
        directional = [item for item in evidence if item.stance != "NEUTRAL"]
        if not directional:
            return "INSUFFICIENT_EVIDENCE"
        supporting = any(item.stance == "SUPPORTING" for item in directional)
        contradicting = any(item.stance == "CONTRADICTING" for item in directional)
        if supporting and contradicting:
            return "MIXED"
        if supporting:
            return "SUPPORTED"
        return "NOT_SUPPORTED"

    def _refresh(self, record: ResearchHypothesis) -> ResearchHypothesis:
        allowed_stage = _STATUS_STAGE[record.status]
        portfolio = (
            None
            if record.lineage.portfolio_research_id is None
            else self.portfolios.get(record.lineage.portfolio_research_id)
        )
        source_evidence = tuple(item for item in record.evidence if item.source_type != "PORTFOLIO")
        portfolio_evidence = tuple(self._portfolio_evidence(portfolio, allowed_stage))
        evidence = (*source_evidence, *portfolio_evidence)
        return record.model_copy(
            update={
                "evidence": evidence,
                "outcome": self._outcome(record.status, evidence),
            }
        )

    def _initialize_source_evidence(
        self,
        record: ResearchHypothesis,
        records: tuple[FactorResearchRecord, ...],
    ) -> ResearchHypothesis:
        evidence, relationship_ids, walk_forward_ids = self._source_evidence(record, records)
        lineage = record.lineage.model_copy(
            update={
                "relationship_ids": relationship_ids,
                "walk_forward_ids": walk_forward_ids,
            }
        )
        return record.model_copy(update={"evidence": evidence, "lineage": lineage})

    def _factor_revisions(self, record: ResearchHypothesis) -> tuple[str, ...]:
        revisions: list[str] = []
        for research_id in record.factor_research_ids:
            factor_record = self.factors.get(research_id)
            if factor_record is None:
                raise KeyError(f"Factor research '{research_id}' was not found")
            revisions.append(
                factor_record.factor.source_fingerprint or factor_record.factor.version
            )
        return tuple(revisions)

    def _strategy_revision(self, record: ResearchHypothesis) -> str | None:
        if record.lineage.strategy_id is None:
            return None
        registration = self.strategy_factory.strategy_registry.get_registration(
            record.lineage.strategy_id
        )
        return None if registration is None else registration.source_fingerprint

    @staticmethod
    def _known_evidence(record: ResearchHypothesis) -> tuple[str, ...]:
        source_max = max(
            (_STAGE_ORDER[stage] for stage in record.source_revealed_stages.values()),
            default=0,
        )
        known_max = max(source_max, _STAGE_ORDER[_STATUS_STAGE[record.status]])
        stages: tuple[ResearchStage, ...] = ("RESEARCH", "VALIDATION", "HOLDOUT")
        return tuple(stage for stage in stages if _STAGE_ORDER[stage] <= known_max)

    def _ledger_event(self, record: ResearchHypothesis, event: str) -> None:
        result_refs = [f"hypothesis:{record.hypothesis_id}"]
        result_refs.extend(f"relationship:{item}" for item in record.lineage.relationship_ids)
        result_refs.extend(f"walk-forward:{item}" for item in record.lineage.walk_forward_ids)
        if record.lineage.portfolio_research_id is not None:
            result_refs.append(f"portfolio:{record.lineage.portfolio_research_id}")
        if record.lineage.strategy_id is not None:
            result_refs.append(f"strategy:{record.lineage.strategy_id}")
        result_refs.extend(f"run:{run_id}" for run_id in record.lineage.run_ids)
        self.ledger.save(
            ResearchLedgerEntry.new(
                entry_id=f"ledger-{secrets.token_hex(10)}",
                kind="HYPOTHESIS",
                artifact_id=record.hypothesis_id,
                revision=record.revision,
                dataset_ids=(record.dataset_id,),
                dataset_fingerprints=(record.dataset_fingerprint,),
                factor_ids=record.lineage.factor_ids,
                factor_revisions=self._factor_revisions(record),
                strategy_id=record.lineage.strategy_id,
                strategy_revision=self._strategy_revision(record),
                known_evidence=self._known_evidence(record),
                result_refs=tuple(result_refs),
                metadata={
                    "event": event,
                    "status": record.status,
                    "outcome": record.outcome,
                    "family_id": record.family_id,
                    "created_with_known_stage": record.created_with_known_stage,
                    "source_revealed_stages": ",".join(
                        f"{key}:{value}" for key, value in record.source_revealed_stages.items()
                    ),
                },
                hypothesis_id=record.hypothesis_id,
                portfolio_research_id=record.lineage.portfolio_research_id,
            )
        )

    def create(self, request: CreateHypothesis) -> ResearchHypothesis:
        records = self._factor_records(request.factor_research_ids)
        dataset = self.datasets.get(records[0].dataset_id)
        if dataset is None:
            raise KeyError(records[0].dataset_id)
        universe = self._universe(request.universe, records)
        hypothesis_id = f"hypothesis-{secrets.token_hex(10)}"
        family_id = f"hypothesis-family-{secrets.token_hex(8)}"
        record = ResearchHypothesis(
            hypothesis_id=hypothesis_id,
            family_id=family_id,
            revision=1,
            title=request.title,
            description=request.description,
            dataset_id=dataset.dataset_id,
            dataset_fingerprint=dataset.content_fingerprint,
            universe=universe,
            factor_research_ids=request.factor_research_ids,
            expected_relationship=request.expected_relationship,
            holding_horizon=request.holding_horizon,
            rebalance_idea=request.rebalance_idea,
            risk_assumptions=request.risk_assumptions,
            created_at=datetime.now(UTC),
            created_with_known_stage=self._known_stage(records),
            source_revealed_stages={item.research_id: item.revealed_stage for item in records},
            candidate=CandidateStrategyTemplate(rebalance=request.rebalance_idea),
            lineage=HypothesisLineage(
                factor_research_ids=request.factor_research_ids,
                factor_ids=tuple(item.factor.factor_id for item in records),
            ),
        )
        record = self._initialize_source_evidence(record, records)
        record = self._refresh(record)
        self.hypotheses.save(record)
        self._ledger_event(record, "CREATE_HYPOTHESIS")
        return record

    def create_revision(
        self,
        parent: ResearchHypothesis,
        request: CreateHypothesisRevision,
    ) -> ResearchHypothesis:
        current = self.hypotheses.family(parent.family_id)
        next_revision = max((item.revision for item in current), default=parent.revision) + 1
        factor_ids = request.factor_research_ids or parent.factor_research_ids
        records = self._factor_records(factor_ids)
        dataset = self.datasets.get(records[0].dataset_id)
        if dataset is None:
            raise KeyError(records[0].dataset_id)
        universe = self._universe(
            parent.universe if request.universe is None else request.universe,
            records,
        )
        rebalance = request.rebalance_idea or parent.rebalance_idea
        record = ResearchHypothesis(
            hypothesis_id=f"hypothesis-{secrets.token_hex(10)}",
            family_id=parent.family_id,
            parent_hypothesis_id=parent.hypothesis_id,
            revision=next_revision,
            title=request.title or parent.title,
            description=request.description or parent.description,
            dataset_id=dataset.dataset_id,
            dataset_fingerprint=dataset.content_fingerprint,
            universe=universe,
            factor_research_ids=factor_ids,
            expected_relationship=request.expected_relationship or parent.expected_relationship,
            holding_horizon=request.holding_horizon or parent.holding_horizon,
            rebalance_idea=rebalance,
            risk_assumptions=(
                parent.risk_assumptions
                if request.risk_assumptions is None
                else request.risk_assumptions
            ),
            created_at=datetime.now(UTC),
            created_with_known_stage=self._known_stage(records),
            source_revealed_stages={item.research_id: item.revealed_stage for item in records},
            candidate=CandidateStrategyTemplate(rebalance=rebalance),
            lineage=HypothesisLineage(
                factor_research_ids=factor_ids,
                factor_ids=tuple(item.factor.factor_id for item in records),
            ),
            revision_reason=request.revision_reason,
        )
        record = self._initialize_source_evidence(record, records)
        record = self._refresh(record)
        self.hypotheses.save(record)
        self._ledger_event(record, "CREATE_REVISION")
        return record

    def build_candidate(self, record: ResearchHypothesis) -> ResearchHypothesis:
        if record.lineage.portfolio_research_id is not None:
            return record
        records = self._factor_records(record.factor_research_ids)
        if any(
            item.revealed_stage not in {"RESEARCH", "VALIDATION", "HOLDOUT"} for item in records
        ):
            raise ValueError("Invalid Factor research stage")
        count = len(records)
        portfolio = self.portfolio_engine.create(
            CreatePortfolioResearch(
                name=f"Hypothesis · {record.title}",
                factors=tuple(
                    PortfolioFactorRef(research_id=item.research_id, weight=1 / count)
                    for item in records
                ),
                combination="RANK_AVERAGE",
                filters=PortfolioFilters(
                    require_factor_availability=True,
                    include_symbols=record.universe,
                ),
                construction=PortfolioConstruction(
                    selection="TOP_PERCENT",
                    top_percent=record.candidate.top_percent,
                    weighting="EQUAL_WEIGHT",
                    max_single_position_weight=record.candidate.max_single_position_weight,
                ),
                rebalance=record.rebalance_idea,
            )
        )
        self.portfolios.save(portfolio)
        updated = record.model_copy(
            update={
                "status": "RESEARCHED",
                "candidate": record.candidate.model_copy(
                    update={"portfolio_research_id": portfolio.portfolio_research_id}
                ),
                "lineage": record.lineage.model_copy(
                    update={"portfolio_research_id": portfolio.portfolio_research_id}
                ),
            }
        )
        updated = self._refresh(updated)
        self.hypotheses.save(updated)
        self._ledger_event(updated, "CREATE_CANDIDATE")
        return updated

    def validate(self, record: ResearchHypothesis) -> ResearchHypothesis:
        if record.lineage.portfolio_research_id is None:
            raise ValueError("Create the Candidate Strategy before Validation")
        if record.status in {"VALIDATED", "HOLDOUT_REVEALED", "STRATEGY_CREATED"}:
            return record
        portfolio = self.portfolios.get(record.lineage.portfolio_research_id)
        if portfolio is None:
            raise KeyError(record.lineage.portfolio_research_id)
        portfolio = self.portfolio_engine.reveal(portfolio, "VALIDATION")
        self.portfolios.save(portfolio)
        updated = self._refresh(record.model_copy(update={"status": "VALIDATED"}))
        self.hypotheses.save(updated)
        self._ledger_event(updated, "VALIDATE")
        return updated

    def reveal_holdout(self, record: ResearchHypothesis) -> ResearchHypothesis:
        if record.status == "RESEARCHED" or record.status == "DRAFT":
            raise ValueError("Validation must be completed before Holdout can be revealed")
        if record.status in {"HOLDOUT_REVEALED", "STRATEGY_CREATED"}:
            return record
        if record.lineage.portfolio_research_id is None:
            raise ValueError("Candidate Portfolio is missing")
        portfolio = self.portfolios.get(record.lineage.portfolio_research_id)
        if portfolio is None:
            raise KeyError(record.lineage.portfolio_research_id)
        portfolio = self.portfolio_engine.reveal(portfolio, "HOLDOUT")
        self.portfolios.save(portfolio)
        updated = self._refresh(record.model_copy(update={"status": "HOLDOUT_REVEALED"}))
        self.hypotheses.save(updated)
        self._ledger_event(updated, "REVEAL_HOLDOUT")
        return updated

    def create_strategy(self, record: ResearchHypothesis) -> ResearchHypothesis:
        if record.status == "STRATEGY_CREATED":
            return record
        if record.status != "HOLDOUT_REVEALED":
            raise ValueError("Explicitly reveal Holdout before creating the final Native Strategy")
        if record.lineage.portfolio_research_id is None:
            raise ValueError("Candidate Portfolio is missing")
        portfolio = self.portfolios.get(record.lineage.portfolio_research_id)
        if portfolio is None:
            raise KeyError(record.lineage.portfolio_research_id)
        artifact = self.strategy_factory.create(portfolio)
        self.portfolios.save(portfolio.model_copy(update={"strategy": artifact}))
        updated = self._refresh(
            record.model_copy(
                update={
                    "status": "STRATEGY_CREATED",
                    "lineage": record.lineage.model_copy(
                        update={"strategy_id": artifact.strategy_id}
                    ),
                }
            )
        )
        self.hypotheses.save(updated)
        self._ledger_event(updated, "CREATE_NATIVE_STRATEGY")
        return updated

    def attach_run(
        self,
        record: ResearchHypothesis,
        run_id: str,
        trace_id: str,
    ) -> ResearchHypothesis:
        if record.lineage.strategy_id is None:
            raise ValueError("Create the Native Strategy before attaching a run")
        manifest = self.runs.get_manifest(run_id)
        if manifest.strategy.strategy_id != record.lineage.strategy_id:
            raise ValueError("Run strategy does not match this hypothesis Native Strategy")
        if manifest.dataset.dataset_id != record.dataset_id:
            raise ValueError("Run dataset does not match this hypothesis dataset")
        if manifest.dataset.content_fingerprint != record.dataset_fingerprint:
            raise ValueError("Run dataset fingerprint does not match this hypothesis revision")
        if manifest.trace_id != trace_id or self.runs.run_id_for_trace(trace_id) != run_id:
            raise ValueError("Run and Trace do not belong to the same persisted execution")
        runs = tuple(dict.fromkeys((*record.lineage.run_ids, run_id)))
        traces = tuple(dict.fromkeys((*record.lineage.trace_ids, trace_id)))
        updated = record.model_copy(
            update={
                "lineage": record.lineage.model_copy(update={"run_ids": runs, "trace_ids": traces})
            }
        )
        self.hypotheses.save(updated)
        self._ledger_event(updated, "ATTACH_RUN")
        return updated

    def suggestions(self) -> tuple[DiscoverySuggestion, ...]:
        suggestions: list[DiscoverySuggestion] = []
        seen: set[tuple[str, str]] = set()
        for relationship in self.relationships.list():
            if relationship.stage == "HOLDOUT":
                continue
            redundancy = {
                frozenset((item.left_research_id, item.right_research_id)): item
                for item in relationship.redundancy
            }
            for item in relationship.incremental_information:
                left_research_id, right_research_id = sorted(
                    (item.base_research_id, item.added_research_id)
                )
                pair = (left_research_id, right_research_id)
                if pair in seen:
                    continue
                assessment = redundancy.get(frozenset(pair))
                if assessment is None or assessment.status == "HIGH_REDUNDANCY":
                    continue
                if (
                    item.rank_ic_delta is None
                    or item.rank_ic_delta <= 0
                    or item.spread_delta is None
                    or item.spread_delta <= 0
                ):
                    continue
                records = self._factor_records(pair)
                research_rank_ics = [
                    evaluation.rank_ic
                    for record in records
                    if (evaluation := self._evaluation(record, "RESEARCH", relationship.horizon))
                    is not None
                    and evaluation.rank_ic is not None
                ]
                if len(research_rank_ics) != 2 or not all(value > 0 for value in research_rank_ics):
                    continue
                seen.add(pair)
                suggestions.append(
                    DiscoverySuggestion(
                        factor_research_ids=pair,
                        rationale=(
                            "Both Factors have positive Research Rank IC, and the selected "
                            "relationship study does not classify them as HIGH REDUNDANCY. The "
                            "deterministic Rank Average comparison increases both Rank IC and "
                            "Q5−Q1. Investigate the combination as a new hypothesis; this is not "
                            "a recommendation."
                        ),
                        source_relationship_id=relationship.relationship_id,
                    )
                )
        return tuple(suggestions)
