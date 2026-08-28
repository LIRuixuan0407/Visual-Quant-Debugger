from __future__ import annotations

from app.models import MarketFrame

from .models import CorporateActionDataset, PriceAdjustmentPolicy

PRICE_FIELDS = ("open", "high", "low", "close")


def adjust_market_frames(
    frames: tuple[MarketFrame, ...],
    actions: CorporateActionDataset | None,
    policy: PriceAdjustmentPolicy,
) -> tuple[MarketFrame, ...]:
    """Return a derived price view while leaving immutable Dataset frames untouched."""

    if policy == "RAW" or actions is None:
        return frames
    splits = tuple(item for item in actions.actions if item.action_type == "SPLIT")
    if not splits:
        return frames
    adjusted: list[MarketFrame] = []
    for frame in frames:
        values: dict[str, dict[str, float]] = {}
        for symbol, fields in frame.values.items():
            ratio = 1.0
            for action in splits:
                if action.symbol == symbol and frame.timestamp < action.effective_at:
                    ratio *= action.split_ratio or 1.0
            updated = dict(fields)
            if ratio != 1.0:
                for field in PRICE_FIELDS:
                    if field in updated:
                        updated[field] /= ratio
                if "volume" in updated:
                    updated["volume"] *= ratio
            values[symbol] = updated
        adjusted.append(
            MarketFrame(
                timestamp=frame.timestamp,
                values=values,
                available_at=frame.available_at,
            )
        )
    return tuple(adjusted)
