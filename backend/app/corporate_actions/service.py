from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from app.datasets import DatasetRegistry
from app.models import MarketFrame
from app.portfolio import Portfolio

from .adjustment import adjust_market_frames
from .models import (
    AdjustedMarketView,
    CorporateAction,
    CorporateActionApplication,
    CorporateActionDataset,
    CorporateActionEvent,
    CorporateActionEventStatus,
    CreateCorporateActionDataset,
    PriceAdjustmentPolicy,
)
from .repository import CorporateActionRepository


def _fingerprint_payload(request: CreateCorporateActionDataset) -> str:
    payload = json.dumps(request.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


class CorporateActionService:
    def __init__(
        self,
        repository: CorporateActionRepository,
        datasets: DatasetRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.datasets = datasets

    def create(self, request: CreateCorporateActionDataset) -> CorporateActionDataset:
        actions = tuple(
            sorted(request.actions, key=lambda item: (item.effective_at, item.action_id))
        )
        semantic = request.model_copy(update={"actions": actions})
        fingerprint = _fingerprint_payload(semantic)
        identity = fingerprint.removeprefix("sha256:")[:20]
        record_id = f"corporate-actions-{identity}"
        existing = self.repository.get(record_id)
        if existing is not None:
            if existing.content_fingerprint != fingerprint:
                raise ValueError(f"Corporate Action dataset '{record_id}' has conflicting content")
            return existing
        now = datetime.now(UTC)
        record = CorporateActionDataset(
            corporate_action_dataset_id=record_id,
            name=request.name.strip(),
            provider=request.provider.strip(),
            symbols=tuple(sorted({item.symbol for item in actions})),
            start_time=min((item.effective_at for item in actions), default=now),
            end_time=max((item.effective_at for item in actions), default=now),
            retrieved_at=now,
            content_fingerprint=fingerprint,
            actions=actions,
            point_in_time_safe=all(item.available_at <= item.effective_at for item in actions),
            disclosure=request.disclosure.strip(),
        )
        return self.repository.save(record)

    def adjusted_frames(
        self,
        dataset_id: str,
        corporate_action_dataset_id: str | None,
        policy: PriceAdjustmentPolicy,
        required_symbols: tuple[str, ...] = (),
        *,
        allow_partial: bool = False,
    ) -> tuple[MarketFrame, ...]:
        if self.datasets is None:
            raise RuntimeError("Dataset Registry is required to build an adjusted market view")
        frames = self.datasets.load_frames(
            dataset_id,
            required_symbols,
            allow_partial=allow_partial,
        )
        actions = (
            None
            if corporate_action_dataset_id is None
            else self.repository.get(corporate_action_dataset_id)
        )
        if corporate_action_dataset_id is not None and actions is None:
            raise KeyError(
                f"Corporate Action dataset '{corporate_action_dataset_id}' was not found"
            )
        return adjust_market_frames(frames, actions, policy)

    def market_view(
        self,
        dataset_id: str,
        corporate_action_dataset_id: str | None,
        policy: PriceAdjustmentPolicy,
    ) -> AdjustedMarketView:
        if self.datasets is None:
            raise RuntimeError("Dataset Registry is required to inspect a market view")
        dataset = self.datasets.get(dataset_id)
        if dataset is None:
            raise KeyError(f"Dataset '{dataset_id}' was not found")
        actions = (
            None
            if corporate_action_dataset_id is None
            else self.repository.get(corporate_action_dataset_id)
        )
        if corporate_action_dataset_id is not None and actions is None:
            raise KeyError(
                f"Corporate Action dataset '{corporate_action_dataset_id}' was not found"
            )
        frames = self.adjusted_frames(dataset_id, corporate_action_dataset_id, policy)
        return AdjustedMarketView(
            dataset_id=dataset_id,
            corporate_action_dataset_id=corporate_action_dataset_id,
            price_adjustment_policy=policy,
            raw_dataset_fingerprint=dataset.content_fingerprint,
            frame_count=len(frames),
            split_count=(
                0
                if actions is None
                else sum(item.action_type == "SPLIT" for item in actions.actions)
            ),
        )

    @staticmethod
    def apply_action(
        portfolio: Portfolio,
        action: CorporateAction,
        policy: PriceAdjustmentPolicy,
    ) -> CorporateActionEvent:
        before = portfolio.positions.get(action.symbol, 0.0)
        after = before
        cash = 0.0
        status: CorporateActionEventStatus = "APPLIED"
        if action.action_type == "SPLIT":
            if policy == "RAW":
                after = before * (action.split_ratio or 1.0)
                portfolio.positions[action.symbol] = after
                if action.symbol == "ASSET_A":
                    portfolio.quantity_a = after
                elif action.symbol == "ASSET_B":
                    portfolio.quantity_b = after
            else:
                status = "REFLECTED_IN_PRICE_VIEW"
        elif action.action_type == "CASH_DIVIDEND":
            cash = before * (action.cash_amount or 0.0)
            portfolio.cash += cash
        elif action.settlement_price is None:
            status = "UNRESOLVED"
        else:
            cash = before * action.settlement_price
            portfolio.cash += cash
            after = 0.0
            portfolio.positions[action.symbol] = 0.0
            if action.symbol == "ASSET_A":
                portfolio.quantity_a = 0.0
            elif action.symbol == "ASSET_B":
                portfolio.quantity_b = 0.0
        return CorporateActionEvent(
            action_id=action.action_id,
            symbol=action.symbol,
            action_type=action.action_type,
            timestamp=action.effective_at,
            status=status,
            quantity_before=before,
            quantity_after=after,
            cash_amount=cash,
            settlement_price=action.settlement_price,
            evidence=action.evidence,
        )

    def apply(
        self,
        corporate_action_dataset_id: str,
        *,
        positions: dict[str, float],
        cash: float,
        policy: PriceAdjustmentPolicy = "RAW",
    ) -> CorporateActionApplication:
        dataset = self.repository.get(corporate_action_dataset_id)
        if dataset is None:
            raise KeyError(
                f"Corporate Action dataset '{corporate_action_dataset_id}' was not found"
            )
        portfolio = Portfolio(cash=cash, positions=dict(positions))
        events = tuple(self.apply_action(portfolio, action, policy) for action in dataset.actions)
        return CorporateActionApplication(
            corporate_action_dataset_id=corporate_action_dataset_id,
            price_adjustment_policy=policy,
            starting_cash=cash,
            ending_cash=portfolio.cash,
            starting_positions=dict(positions),
            ending_positions=dict(portfolio.positions),
            events=events,
            unresolved_action_ids=tuple(
                item.action_id for item in events if item.status == "UNRESOLVED"
            ),
        )
