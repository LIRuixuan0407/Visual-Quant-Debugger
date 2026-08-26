import csv
from datetime import datetime
from pathlib import Path

from app.models import MarketBar

REQUIRED_COLUMNS = {"timestamp", "asset_a_close", "asset_b_close"}


def load_pair_csv(path: str | Path) -> tuple[MarketBar, ...]:
    source = Path(path)
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            raise ValueError(f"CSV must contain columns: {sorted(REQUIRED_COLUMNS)}")

        bars: list[MarketBar] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                timestamp = datetime.fromisoformat(row["timestamp"])
                asset_a = float(row["asset_a_close"])
                asset_b = float(row["asset_b_close"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid market data at line {line_number}") from exc
            if asset_a <= 0 or asset_b <= 0:
                raise ValueError(f"Prices must be positive at line {line_number}")
            bars.append(MarketBar(timestamp=timestamp, asset_a=asset_a, asset_b=asset_b))

    if len(bars) < 3:
        raise ValueError("At least three rows are required")
    if any(
        current.timestamp <= previous.timestamp
        for previous, current in zip(bars, bars[1:], strict=False)
    ):
        raise ValueError("Timestamps must be strictly increasing")
    return tuple(bars)
