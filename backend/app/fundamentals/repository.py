from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from app.workspace import default_workspace_root

from .models import (
    STANDARD_FUNDAMENTAL_FIELDS,
    FundamentalDataset,
    FundamentalFieldSnapshot,
    FundamentalObservation,
    FundamentalSnapshot,
    FundamentalStatus,
)


class FundamentalRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "fundamentals"
        self._cache: dict[str, FundamentalDataset] = {}

    def _path(self, dataset_id: str) -> Path:
        return self.root / dataset_id / "fundamentals.json"

    def save(self, dataset: FundamentalDataset) -> FundamentalDataset:
        path = self._path(dataset.fundamental_dataset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(dataset.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        self._cache[dataset.fundamental_dataset_id] = dataset
        return dataset

    def create_dataset(
        self,
        *,
        name: str,
        provider: str,
        observations: tuple[FundamentalObservation, ...],
        start: datetime,
        end: datetime,
        retrieved_at: datetime,
        point_in_time_safe: bool,
        restatement_safe: bool,
        disclosure: str,
    ) -> FundamentalDataset:
        semantic = json.dumps(
            [item.model_dump(mode="json") for item in observations],
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(semantic.encode()).hexdigest()
        dataset = FundamentalDataset(
            fundamental_dataset_id=f"fundamental-{digest[:16]}",
            name=name,
            provider=provider,
            symbols=tuple(sorted({item.symbol for item in observations})),
            fields=tuple(sorted({item.field for item in observations})),
            start_time=start,
            end_time=end,
            retrieved_at=retrieved_at,
            content_fingerprint=f"sha256:{digest}",
            observations=observations,
            point_in_time_safe=point_in_time_safe,
            restatement_safe=restatement_safe,
            disclosure=disclosure,
        )
        return self.save(dataset)

    def get(self, dataset_id: str) -> FundamentalDataset | None:
        cached = self._cache.get(dataset_id)
        if cached is not None:
            return cached
        path = self._path(dataset_id)
        dataset = (
            FundamentalDataset.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )
        if dataset is not None:
            self._cache[dataset_id] = dataset
        return dataset

    def list(self) -> tuple[FundamentalDataset, ...]:
        if not self.root.exists():
            return ()
        datasets = tuple(
            sorted(
                (
                    FundamentalDataset.model_validate_json(path.read_text(encoding="utf-8"))
                    for path in self.root.glob("*/fundamentals.json")
                ),
                key=lambda item: item.retrieved_at,
                reverse=True,
            )
        )
        self._cache.update({item.fundamental_dataset_id: item for item in datasets})
        return datasets

    @staticmethod
    def latest_available(
        dataset: FundamentalDataset,
        *,
        symbol: str,
        field: str,
        used_at: datetime,
        period_type: str | None = None,
        count: int = 1,
    ) -> tuple[FundamentalObservation, ...]:
        eligible = [
            item
            for item in dataset.observations
            if item.symbol == symbol
            and item.field == field
            and item.available_at <= used_at
            and (period_type is None or item.period_type == period_type)
        ]
        by_period: dict[tuple[datetime, str], FundamentalObservation] = {}
        for item in sorted(eligible, key=lambda value: (value.available_at, value.accession)):
            by_period[(item.period_end, item.period_type)] = item
        return tuple(
            sorted(by_period.values(), key=lambda value: value.period_end, reverse=True)[:count]
        )

    @classmethod
    def snapshot(
        cls,
        dataset: FundamentalDataset,
        *,
        symbol: str,
        used_at: datetime,
        max_age_days: int = 550,
    ) -> FundamentalSnapshot:
        rows: list[FundamentalFieldSnapshot] = []
        for field in STANDARD_FUNDAMENTAL_FIELDS:
            selected = cls.latest_available(dataset, symbol=symbol, field=field, used_at=used_at)
            future_exists = any(
                item.symbol == symbol and item.field == field and item.available_at > used_at
                for item in dataset.observations
            )
            if not selected:
                rows.append(
                    FundamentalFieldSnapshot(
                        field=field,
                        status="NOT_YET_REPORTED" if future_exists else "MISSING",
                        value=None,
                        unit=None,
                        fiscal_period=None,
                        report_date=None,
                        filed_at=None,
                        available_at=None,
                        used_at=used_at,
                        age_days=None,
                        form=None,
                        accession=None,
                    )
                )
                continue
            item = selected[0]
            age_days = max(0, (used_at.date() - item.available_at.date()).days)
            status: FundamentalStatus = (
                "RESTATED"
                if item.is_restatement
                else "STALE"
                if age_days > max_age_days
                else "AVAILABLE"
            )
            rows.append(
                FundamentalFieldSnapshot(
                    field=field,
                    status=status,
                    value=item.value,
                    unit=item.unit,
                    fiscal_period=item.fiscal_period,
                    report_date=item.report_date,
                    filed_at=item.filed_at,
                    available_at=item.available_at,
                    used_at=used_at,
                    age_days=age_days,
                    form=item.form,
                    accession=item.accession,
                    is_restatement=item.is_restatement,
                )
            )
        return FundamentalSnapshot(
            fundamental_dataset_id=dataset.fundamental_dataset_id,
            provider=dataset.provider,
            symbol=symbol,
            used_at=used_at,
            restatement_safe=dataset.restatement_safe,
            fields=tuple(rows),
        )


fundamental_repository = FundamentalRepository()
