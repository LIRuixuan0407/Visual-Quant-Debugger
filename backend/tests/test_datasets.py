from pathlib import Path

import pytest

from app.datasets import DatasetImportRequest, DatasetRegistry, DatasetValidationError


def _request(preview_id: str, *, timezone: str | None = "Asia/Hong_Kong") -> DatasetImportRequest:
    return DatasetImportRequest(
        preview_id=preview_id,
        name="My mapped market data",
        mapping={"timestamp": "date", "symbol": "ticker", "close": "price"},
        timezone=timezone,
    )


def test_csv_preview_mapping_timezone_sort_alignment_fingerprint_and_restore(
    tmp_path: Path,
) -> None:
    content = (
        b"date,ticker,price\n"
        b"2025-01-02,AAPL,102\n"
        b"2025-01-01,AAPL,101\n"
        b"2025-01-01,MSFT,201\n"
        b"2025-01-02,MSFT,202\n"
        b"2025-01-03,AAPL,103\n"
    )
    registry = DatasetRegistry(tmp_path)
    preview = registry.preview("prices.csv", content)
    assert preview.columns == ("date", "ticker", "price")
    assert preview.candidate_mapping == {
        "timestamp": "date",
        "symbol": "ticker",
        "close": "price",
    }
    assert preview.detected_types["price"] == "number"
    definition = registry.commit(_request(preview.preview_id))
    assert definition.dataset_id.startswith("dataset-")
    assert definition.timezone == "UTC"
    assert definition.source_timezone == "Asia/Hong_Kong"
    assert definition.symbols == ("AAPL", "MSFT")
    assert definition.quality.rows_reordered > 0
    assert definition.quality.alignment_gaps == 1
    assert definition.quality.status == "WARNING"
    assert definition.synchronized_bar_count == 2
    frames = registry.load_frames(definition.dataset_id, ("AAPL", "MSFT"))
    assert len(frames) == 2
    assert frames[0].timestamp.isoformat() == "2024-12-31T16:00:00+00:00"
    assert frames[0].value("AAPL") == 101

    duplicate_import = registry.commit(_request(preview.preview_id))
    assert duplicate_import.dataset_id == definition.dataset_id
    restored = DatasetRegistry(tmp_path)
    assert restored.get(definition.dataset_id) == definition
    assert restored.load_frames(definition.dataset_id, ("AAPL", "MSFT")) == frames


def test_naive_timestamp_requires_explicit_timezone(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path)
    preview = registry.preview("naive.csv", b"date,ticker,price\n2025-01-01,AAPL,100\n")
    with pytest.raises(DatasetValidationError, match="explicit IANA timezone"):
        registry.commit(_request(preview.preview_id, timezone=None))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            b"date,ticker,price\n2025-01-01T00:00:00Z,AAPL,100\n2025-01-01T00:00:00Z,AAPL,101\n",
            "Duplicate",
        ),
        (
            b"date,ticker,price\n2025-01-01T00:00:00Z,AAPL,\n",
            "close is missing",
        ),
    ],
)
def test_duplicate_bars_and_missing_close_are_rejected(
    tmp_path: Path, content: bytes, message: str
) -> None:
    registry = DatasetRegistry(tmp_path)
    preview = registry.preview("invalid.csv", content)
    with pytest.raises(DatasetValidationError, match=message):
        registry.commit(_request(preview.preview_id, timezone=None))
