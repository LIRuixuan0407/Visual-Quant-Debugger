from __future__ import annotations

import json
from pathlib import Path

from app.datasets import DatasetImportRequest, DatasetRegistry, DatasetValidationError


def _import_csv(
    registry: DatasetRegistry,
    content: bytes,
    *,
    name: str = "Versioned prices",
    family_id: str | None = None,
    reason: str | None = None,
):
    preview = registry.preview("prices.csv", content)
    return registry.commit(
        DatasetImportRequest(
            preview_id=preview.preview_id,
            name=name,
            mapping={"timestamp": "date", "symbol": "ticker", "close": "price"},
            timezone="UTC",
            dataset_family_id=family_id,
            revision_reason=reason,
        )
    )


def test_new_dataset_creates_family_and_explicit_revision_chain(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    r1 = _import_csv(
        registry,
        b"date,ticker,price\n2025-01-01,AAPL,100\n2025-01-02,AAPL,101\n",
    )
    assert r1.dataset_family_id is not None
    assert r1.revision == 1
    assert r1.parent_dataset_id is None
    family = registry.get_family(r1.dataset_family_id)
    assert family is not None
    assert family.latest_dataset_id == r1.dataset_id
    assert family.revision_count == 1

    data_before = (tmp_path / ".vqd" / "datasets" / r1.dataset_id / "data.csv").read_bytes()
    metadata_before = (
        tmp_path / ".vqd" / "datasets" / r1.dataset_id / "metadata.json"
    ).read_bytes()
    r2 = _import_csv(
        registry,
        b"date,ticker,price\n2025-01-01,AAPL,100\n2025-01-02,AAPL,101\n2025-01-03,AAPL,102\n",
        family_id=r1.dataset_family_id,
        reason="Extend coverage through January 3",
    )
    assert r2.dataset_id != r1.dataset_id
    assert r2.dataset_family_id == r1.dataset_family_id
    assert r2.revision == 2
    assert r2.parent_dataset_id == r1.dataset_id
    assert r2.revision_reason == "Extend coverage through January 3"
    assert (tmp_path / ".vqd" / "datasets" / r1.dataset_id / "data.csv").read_bytes() == data_before
    assert (
        tmp_path / ".vqd" / "datasets" / r1.dataset_id / "metadata.json"
    ).read_bytes() == metadata_before
    history = registry.family_history(r1.dataset_family_id)
    assert history is not None
    assert [item.dataset_id for item in history.revisions] == [r1.dataset_id, r2.dataset_id]
    assert [item.revision for item in history.revisions] == [1, 2]
    assert history.family.latest_dataset_id == r2.dataset_id
    assert history.family.revision_count == 2

    restarted = DatasetRegistry(tmp_path)
    restarted_history = restarted.family_history(r1.dataset_family_id)
    assert restarted_history is not None
    assert [item.dataset_id for item in restarted_history.revisions] == [
        r1.dataset_id,
        r2.dataset_id,
    ]
    assert restarted_history.family.latest_dataset_id == r2.dataset_id
    assert restarted_history.family.revision_count == 2


def test_same_fingerprint_does_not_create_fake_revision(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    content = b"date,ticker,price\n2025-01-01,AAPL,100\n"
    r1 = _import_csv(registry, content)
    repeated = _import_csv(registry, content, family_id=r1.dataset_family_id)
    assert repeated.dataset_id == r1.dataset_id
    assert repeated.revision == 1
    assert registry.get_family(r1.dataset_family_id or "").revision_count == 1


def test_csv_revision_requires_explicit_family_and_never_guesses_from_filename(
    tmp_path: Path,
) -> None:
    registry = DatasetRegistry(tmp_path)
    first = _import_csv(
        registry,
        b"date,ticker,price\n2025-01-01,AAPL,100\n",
        name="Same display name",
    )
    second = _import_csv(
        registry,
        b"date,ticker,price\n2025-01-02,AAPL,101\n",
        name="Same display name",
    )
    assert second.dataset_family_id != first.dataset_family_id
    assert second.revision == 1

    try:
        _import_csv(
            registry,
            b"date,ticker,price\n2025-01-03,AAPL,102\n",
            family_id="dataset-family-missing",
        )
    except DatasetValidationError as exc:
        assert "was not found" in str(exc)
    else:
        raise AssertionError("Unknown family must be rejected")


def test_revision_diff_reports_factual_changes(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    left = _import_csv(
        registry,
        b"date,ticker,price\n2025-01-01,AAPL,100\n2025-01-02,AAPL,101\n",
    )
    right = _import_csv(
        registry,
        (
            b"date,ticker,price,volume\n"
            b"2025-01-01,AAPL,100,10\n"
            b"2025-01-02,AAPL,101,11\n"
            b"2025-01-03,AAPL,102,12\n"
            b"2025-01-03,MSFT,200,13\n"
        ),
        family_id=left.dataset_family_id,
    )
    # Import mapping intentionally excludes volume; the diff remains a structural summary of
    # canonical revision metadata rather than a row-by-row comparison.
    diff = registry.compare(left.dataset_id, right.dataset_id)
    assert diff.same_family is True
    assert diff.fingerprint_changed is True
    assert diff.symbols_added == ("MSFT",)
    assert diff.rows_delta == 2
    assert diff.end_changed is True


def test_legacy_migration_is_idempotent_and_preserves_data_identity(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    current = _import_csv(
        registry,
        b"date,ticker,price\n2025-01-01,AAPL,100\n",
    )
    metadata_path = tmp_path / ".vqd" / "datasets" / current.dataset_id / "metadata.json"
    data_path = tmp_path / ".vqd" / "datasets" / current.dataset_id / "data.csv"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    for key in ("dataset_family_id", "revision", "parent_dataset_id", "revision_reason"):
        payload.pop(key, None)
    metadata_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for family_path in (tmp_path / ".vqd" / "dataset-families").glob("*/family.json"):
        family_path.unlink()
        family_path.parent.rmdir()
    data_before = data_path.read_bytes()
    fingerprint = current.content_fingerprint

    migrated_once = DatasetRegistry(tmp_path).get(current.dataset_id)
    assert migrated_once is not None
    family_id = migrated_once.dataset_family_id
    assert family_id is not None
    assert migrated_once.dataset_id == current.dataset_id
    assert migrated_once.content_fingerprint == fingerprint
    assert data_path.read_bytes() == data_before

    migrated_twice = DatasetRegistry(tmp_path).get(current.dataset_id)
    assert migrated_twice is not None
    assert migrated_twice.dataset_family_id == family_id
    assert len(DatasetRegistry(tmp_path).family_repository.list()) == 1


def test_built_in_dataset_keeps_exact_identity_and_fixed_family(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    built_in = registry.get("pairs-sample-v1")
    assert built_in is not None
    assert built_in.dataset_id == "pairs-sample-v1"
    assert built_in.dataset_family_id == "dataset-family-pairs-sample"
    assert built_in.revision == 1
    history = registry.family_history("dataset-family-pairs-sample")
    assert history is not None
    assert history.family.latest_dataset_id == "pairs-sample-v1"
    assert tuple(item.dataset_id for item in history.revisions) == ("pairs-sample-v1",)


def test_built_in_family_is_not_a_user_revision_target(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    try:
        _import_csv(
            registry,
            b"date,ticker,price\n2025-01-01,AAPL,100\n",
            family_id="dataset-family-pairs-sample",
        )
    except DatasetValidationError as exc:
        assert "built-in sample Dataset Family is immutable" in str(exc)
    else:
        raise AssertionError("Built-in Dataset Family must remain read-only")

    assert registry.family_history("dataset-family-pairs-sample").family.revision_count == 1
