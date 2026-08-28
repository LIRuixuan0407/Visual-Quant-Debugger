from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.corporate_actions import (
    CorporateAction,
    CorporateActionRepository,
    CorporateActionService,
    CreateCorporateActionDataset,
    adjust_market_frames,
)
from app.datasets import DatasetImportRequest, DatasetRegistry
from app.main import app
from app.models import MarketFrame
from app.sdk import DataRequirements, StrategyContext, StrategyMetadata, VQDStrategy
from app.sdk.loader import load_strategy
from app.sdk.runtime import StrategyRuntime
from app.sdk.tracing import RuntimeTraceConfiguration, build_runtime_trace
from app.universes import (
    CreateHistoricalUniverse,
    HistoricalUniverse,
    UniverseMembershipProvenance,
    UniverseRepository,
    UniverseSnapshot,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


class _UniverseVisibilityStrategy(VQDStrategy):
    metadata = StrategyMetadata(
        strategy_id="universe-visibility",
        name="Universe Visibility",
        version="1.0",
        description="Records point-in-time universe visibility.",
        data_requirements=DataRequirements(
            required_fields=("close",),
            symbols=("OLD", "NEW"),
            minimum_history=1,
        ),
    )

    def __init__(self) -> None:
        self.seen_symbols: list[tuple[str, ...]] = []

    def on_bar(self, context: StrategyContext) -> None:
        self.seen_symbols.append(context.symbols)
        return None


def test_corporate_action_and_universe_apis_are_registered() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/corporate-actions" in paths
    assert "/api/corporate-actions/{dataset_id}" in paths
    assert "/api/corporate-actions/{dataset_id}/market-view/{market_dataset_id}" in paths
    assert "/api/universes" in paths
    assert "/api/universes/{universe_id}" in paths


def _action(action_type: str, **updates: object) -> CorporateAction:
    payload: dict[str, object] = {
        "action_id": f"action-{action_type.lower()}",
        "symbol": "AAPL",
        "action_type": action_type,
        "effective_at": NOW + timedelta(days=2),
        "announced_at": NOW,
        "available_at": NOW + timedelta(days=1),
        "source": "exchange bulletin",
        "evidence": "Archived exchange notice",
    }
    payload.update(updates)
    return CorporateAction.model_validate(payload)


def _frames() -> tuple[MarketFrame, ...]:
    return (
        MarketFrame(
            timestamp=NOW,
            values={
                "AAPL": {
                    "open": 100.0,
                    "high": 110.0,
                    "low": 90.0,
                    "close": 104.0,
                    "volume": 1_000.0,
                }
            },
        ),
        MarketFrame(
            timestamp=NOW + timedelta(days=2),
            values={
                "AAPL": {
                    "open": 52.0,
                    "high": 55.0,
                    "low": 50.0,
                    "close": 53.0,
                    "volume": 2_000.0,
                }
            },
        ),
    )


def test_action_type_contracts_require_explicit_evidence() -> None:
    with pytest.raises(ValidationError, match="split_ratio"):
        _action("SPLIT")
    with pytest.raises(ValidationError, match="cash_amount and currency"):
        _action("CASH_DIVIDEND")
    with pytest.raises(ValidationError, match="timezone-aware"):
        _action("SPLIT", effective_at=datetime(2025, 1, 3), split_ratio=2.0)


def test_split_adjusts_all_price_fields_and_volume_without_mutating_raw(
    tmp_path: Path,
) -> None:
    raw = _frames()
    raw_snapshot = tuple(dict(frame.values["AAPL"]) for frame in raw)
    split = _action("SPLIT", split_ratio=2.0)
    actions = CreateCorporateActionDataset(
        name="Actions",
        provider="Exchange",
        actions=(split,),
        disclosure="Official actions only.",
    )
    record = CorporateActionService(CorporateActionRepository(tmp_path)).create(actions)

    adjusted = adjust_market_frames(raw, record, "SPLIT_ADJUSTED")

    assert dict(adjusted[0].values["AAPL"]) == {
        "open": 50.0,
        "high": 55.0,
        "low": 45.0,
        "close": 52.0,
        "volume": 2_000.0,
    }
    assert dict(adjusted[1].values["AAPL"]) == dict(raw[1].values["AAPL"])
    assert tuple(dict(frame.values["AAPL"]) for frame in raw) == raw_snapshot


def test_dividend_is_cash_flow_and_never_mutates_close(tmp_path: Path) -> None:
    repository = CorporateActionRepository(tmp_path)
    service = CorporateActionService(repository)
    dividend = _action("CASH_DIVIDEND", cash_amount=1.25, currency="USD")
    dataset = service.create(
        CreateCorporateActionDataset(
            name="Dividend evidence",
            provider="Exchange",
            actions=(dividend,),
            disclosure="Cash distribution.",
        )
    )
    adjusted = adjust_market_frames(_frames(), dataset, "SPLIT_ADJUSTED")
    application = service.apply(
        dataset.corporate_action_dataset_id,
        positions={"AAPL": 20.0},
        cash=100.0,
    )

    assert adjusted == _frames()
    assert application.ending_cash == 125.0
    assert application.ending_positions == {"AAPL": 20.0}
    assert application.events[0].cash_amount == 25.0


@pytest.mark.parametrize(
    ("settlement_price", "expected_cash", "expected_quantity", "expected_status"),
    [
        (12.5, 150.0, 0.0, "APPLIED"),
        (None, 100.0, 4.0, "UNRESOLVED"),
    ],
)
def test_delisting_settlement_is_explicit_and_never_guessed(
    tmp_path: Path,
    settlement_price: float | None,
    expected_cash: float,
    expected_quantity: float,
    expected_status: str,
) -> None:
    service = CorporateActionService(CorporateActionRepository(tmp_path))
    delisting = _action(
        "DELISTING",
        delisting_reason="Acquisition",
        settlement_price=settlement_price,
    )
    dataset = service.create(
        CreateCorporateActionDataset(
            name="Delisting evidence",
            provider="Exchange",
            actions=(delisting,),
            disclosure="No inferred settlement.",
        )
    )
    result = service.apply(
        dataset.corporate_action_dataset_id,
        positions={"AAPL": 4.0},
        cash=100.0,
    )

    assert result.ending_cash == expected_cash
    assert result.ending_positions["AAPL"] == expected_quantity
    assert result.events[0].status == expected_status
    assert result.unresolved_action_ids == (
        () if settlement_price is not None else (delisting.action_id,)
    )


def test_repository_is_immutable_and_survives_restart(tmp_path: Path) -> None:
    service = CorporateActionService(CorporateActionRepository(tmp_path))
    request = CreateCorporateActionDataset(
        name="Official actions",
        provider="Exchange",
        actions=(_action("SPLIT", split_ratio=2.0),),
        disclosure="Source evidence retained.",
    )
    saved = service.create(request)
    restored = CorporateActionRepository(tmp_path).get(saved.corporate_action_dataset_id)

    assert restored == saved
    assert service.create(request).corporate_action_dataset_id == saved.corporate_action_dataset_id


def test_market_data_file_is_immutable_when_building_adjusted_view(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    content = (
        b"timestamp,symbol,open,high,low,close,volume\n"
        b"2025-01-01T00:00:00Z,AAPL,100,110,90,104,1000\n"
        b"2025-01-03T00:00:00Z,AAPL,52,55,50,53,2000\n"
    )
    preview = registry.preview("prices.csv", content)
    market = registry.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name="Raw market evidence",
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
    action_service = CorporateActionService(CorporateActionRepository(tmp_path), registry)
    actions = action_service.create(
        CreateCorporateActionDataset(
            name="Split",
            provider="Exchange",
            actions=(_action("SPLIT", split_ratio=2.0),),
            disclosure="Official split.",
        )
    )
    raw_path = tmp_path / ".vqd" / "datasets" / market.dataset_id / "data.csv"
    before = hashlib.sha256(raw_path.read_bytes()).hexdigest()

    adjusted = action_service.adjusted_frames(
        market.dataset_id,
        actions.corporate_action_dataset_id,
        "SPLIT_ADJUSTED",
    )

    assert adjusted[0].value("AAPL") == 52.0
    assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == before


def _provenance(symbol: str, effective_from: datetime = NOW) -> UniverseMembershipProvenance:
    return UniverseMembershipProvenance(
        symbol=symbol,
        source="index provider",
        effective_from=effective_from,
        evidence="Archived constituent file",
    )


def test_point_in_time_universe_timeline_and_provenance(tmp_path: Path) -> None:
    repository = UniverseRepository(tmp_path)
    saved = repository.create(
        CreateHistoricalUniverse(
            name="Historical index",
            source="Index provider",
            mode="POINT_IN_TIME",
            snapshots=(
                UniverseSnapshot(
                    effective_date=NOW,
                    symbols=("OLD",),
                    membership_provenance=(_provenance("OLD"),),
                ),
                UniverseSnapshot(
                    effective_date=NOW + timedelta(days=10),
                    symbols=("NEW",),
                    membership_provenance=(_provenance("NEW", NOW + timedelta(days=10)),),
                ),
            ),
            disclosure="Historical membership with source evidence.",
        )
    )

    assert saved.survivorship_bias_free is True
    assert saved.symbols_at(NOW + timedelta(days=5)) == ("OLD",)
    assert saved.symbols_at(NOW + timedelta(days=11)) == ("NEW",)
    assert UniverseRepository(tmp_path).get(saved.universe_id) == saved


def test_missing_provenance_cannot_claim_survivorship_safe(tmp_path: Path) -> None:
    snapshot = UniverseSnapshot(
        effective_date=NOW,
        symbols=("AAPL",),
        membership_provenance=(),
    )
    saved = UniverseRepository(tmp_path).create(
        CreateHistoricalUniverse(
            name="Incomplete history",
            source="Unknown",
            mode="POINT_IN_TIME",
            snapshots=(snapshot,),
            disclosure="Membership evidence is missing.",
        )
    )
    assert saved.survivorship_bias_free is False

    with pytest.raises(ValidationError, match="survivorship-bias free"):
        HistoricalUniverse(
            universe_id="universe-invalid",
            name="Invalid claim",
            source="Unknown",
            mode="POINT_IN_TIME",
            created_at=NOW,
            snapshots=(snapshot,),
            survivorship_bias_free=True,
            disclosure="Invalid safe claim.",
        )


def test_runtime_records_dividend_and_unresolved_delisting_in_trace() -> None:
    loaded = load_strategy(
        Path(__file__).parents[2] / "examples" / "sma_cross.py",
        "MovingAverageCross",
    )
    dividend = _action(
        "CASH_DIVIDEND",
        action_id="action-dividend-runtime",
        effective_at=NOW + timedelta(days=1),
        cash_amount=2.0,
        currency="USD",
    )
    delisting = _action(
        "DELISTING",
        action_id="action-delisting-runtime",
        effective_at=NOW + timedelta(days=2),
        delisting_reason="Bankruptcy",
    )
    runtime = StrategyRuntime(
        strategy=loaded.strategy_class(),
        parameters={"fast_window": 2, "slow_window": 3, "quantity": 1},
        initial_cash=100.0,
        corporate_actions=(dividend, delisting),
    )
    runtime.portfolio.positions["AAPL"] = 10.0

    result = runtime.run(_frames())
    trace = build_runtime_trace(
        result.rows,
        RuntimeTraceConfiguration(
            dataset_id="dataset-runtime",
            dataset_name="Runtime evidence",
            strategy_id="sma-cross",
            strategy_name="SMA Cross",
            parameters={},
            initial_cash=100.0,
            corporate_action_events=tuple(runtime.corporate_action_events),
        ),
    )

    assert result.status == "COMPLETED"
    assert result.rows[-1].portfolio.cash == 120.0
    assert tuple(item.status for item in trace.corporate_action_events) == (
        "APPLIED",
        "UNRESOLVED",
    )
    assert trace.corporate_action_events[-1].quantity_after == 10.0


def test_runtime_uses_point_in_time_universe_for_strategy_visibility() -> None:
    universe = HistoricalUniverse(
        universe_id="universe-runtime",
        name="Runtime membership",
        source="Index provider",
        mode="POINT_IN_TIME",
        created_at=NOW,
        snapshots=(
            UniverseSnapshot(
                effective_date=NOW,
                symbols=("OLD",),
                membership_provenance=(_provenance("OLD"),),
            ),
            UniverseSnapshot(
                effective_date=NOW + timedelta(days=1),
                symbols=("NEW",),
                membership_provenance=(_provenance("NEW", NOW + timedelta(days=1)),),
            ),
        ),
        survivorship_bias_free=True,
        disclosure="Historical membership drives strategy visibility.",
    )
    frames = (
        MarketFrame(
            timestamp=NOW,
            values={
                "OLD": {"close": 10.0},
                "NEW": {"close": 20.0},
            },
        ),
        MarketFrame(
            timestamp=NOW + timedelta(days=1),
            values={
                "OLD": {"close": 11.0},
                "NEW": {"close": 21.0},
            },
        ),
    )
    strategy = _UniverseVisibilityStrategy()
    runtime = StrategyRuntime(
        strategy=strategy,
        parameters={},
        historical_universe=universe,
    )

    result = runtime.run(frames)

    assert result.status == "COMPLETED"
    assert strategy.seen_symbols == [("OLD",), ("NEW",)]
    assert {item.symbol for item in result.rows[0].data_dependencies} == {"OLD"}
    assert {item.symbol for item in result.rows[1].data_dependencies} == {"NEW"}
