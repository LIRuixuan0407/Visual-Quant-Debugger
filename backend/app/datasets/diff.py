from __future__ import annotations

from .models import DatasetDefinition, DatasetRevisionDiff


def _quality_changes(left: DatasetDefinition, right: DatasetDefinition) -> tuple[str, ...]:
    changes: list[str] = []
    if left.quality.status != right.quality.status:
        changes.append(f"status: {left.quality.status} -> {right.quality.status}")
    for field in ("duplicates", "missing_required_values", "rows_reordered", "alignment_gaps"):
        before = getattr(left.quality, field)
        after = getattr(right.quality, field)
        if before != after:
            changes.append(f"{field}: {before} -> {after}")
    if left.quality.issues != right.quality.issues:
        changes.append("validation issues changed")
    return tuple(changes)


def _provenance_changes(left: DatasetDefinition, right: DatasetDefinition) -> tuple[str, ...]:
    if left.provenance == right.provenance:
        return ()
    if left.provenance is None or right.provenance is None:
        return ("provider provenance availability changed",)
    changes: list[str] = []
    for field in (
        "provider",
        "feed",
        "requested_symbols",
        "requested_start",
        "requested_end",
        "retrieved_at",
        "market_timestamp_start",
        "market_timestamp_end",
    ):
        if getattr(left.provenance, field) != getattr(right.provenance, field):
            changes.append(field)
    return tuple(changes)


def compare_datasets(left: DatasetDefinition, right: DatasetDefinition) -> DatasetRevisionDiff:
    return DatasetRevisionDiff(
        left_dataset_id=left.dataset_id,
        right_dataset_id=right.dataset_id,
        same_family=(
            left.dataset_family_id is not None
            and left.dataset_family_id == right.dataset_family_id
        ),
        fingerprint_changed=left.content_fingerprint != right.content_fingerprint,
        symbols_added=tuple(sorted(set(right.symbols) - set(left.symbols))),
        symbols_removed=tuple(sorted(set(left.symbols) - set(right.symbols))),
        fields_added=tuple(sorted(set(right.fields) - set(left.fields))),
        fields_removed=tuple(sorted(set(left.fields) - set(right.fields))),
        start_changed=left.start_time != right.start_time,
        end_changed=left.end_time != right.end_time,
        rows_delta=right.row_count - left.row_count,
        synchronized_bars_delta=right.synchronized_bar_count - left.synchronized_bar_count,
        quality_changes=_quality_changes(left, right),
        provenance_changes=_provenance_changes(left, right),
    )
