from pathlib import Path

import pytest

from app.data import load_pair_csv


def test_load_sample_data() -> None:
    path = Path(__file__).parents[2] / "sample_data" / "pairs_daily.csv"
    bars = load_pair_csv(path)
    assert len(bars) == 40
    assert bars[0].timestamp.isoformat() == "2024-01-02T16:00:00+00:00"
    assert bars[-1].asset_a > 0


def test_rejects_non_monotonic_timestamps(tmp_path: Path) -> None:
    source = tmp_path / "bad.csv"
    source.write_text(
        "timestamp,asset_a_close,asset_b_close\n"
        "2024-01-02T16:00:00+00:00,100,50\n"
        "2024-01-01T16:00:00+00:00,101,51\n"
        "2024-01-03T16:00:00+00:00,102,52\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        load_pair_csv(source)
