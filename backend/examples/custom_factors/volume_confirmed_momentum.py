from __future__ import annotations

import statistics

from app.factor_sdk import (
    FactorContext,
    FactorMetadata,
    FactorResult,
    VQDFactor,
    factor_parameter,
)


class VolumeConfirmedMomentum(VQDFactor):
    """Transparent example factor used by the Phase 22 real-data verification."""

    metadata = FactorMetadata(
        factor_id="volume-confirmed-momentum",
        name="Volume-Confirmed Momentum",
        version="1.0.0",
        description=(
            "Scales trailing price momentum by current volume relative to its trailing mean."
        ),
        formula=("(close(t) / close(t-lookback) - 1) * volume(t) / mean(volume[t-lookback:t])"),
        required_fields=("close", "volume"),
        lookback=20,
        direction="HIGH",
        category="PRICE_VOLUME",
        data_source="MARKET",
    )
    lookback = factor_parameter(
        default=20,
        minimum=2,
        maximum=252,
        step=1,
        description="Trailing price and volume observations",
        unit="bars",
    )

    def compute(self, context: FactorContext, symbol: str) -> FactorResult:
        bars = int(self.lookback) + 1
        closes = context.history(symbol, "close", bars)
        volumes = context.history(symbol, "volume", bars)
        if len(closes) < bars or len(volumes) < bars:
            value = None
        else:
            baseline_volume = statistics.fmean(volumes[:-1])
            value = (
                None
                if baseline_volume == 0
                else (closes[-1] / closes[0] - 1) * volumes[-1] / baseline_volume
            )
        return context.result(
            value,
            inputs=(closes, volumes),
            formula=self.metadata.formula,
        )
