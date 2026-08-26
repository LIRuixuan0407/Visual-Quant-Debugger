from datetime import UTC, datetime, timedelta

import pytest

from app.models import MarketBar
from app.strategies import PairsTradingParameters, calculate_features, evaluate_signals


def make_bars(asset_a: list[float], asset_b: list[float] | None = None) -> tuple[MarketBar, ...]:
    prices_b = asset_b or [50.0 + index for index in range(len(asset_a))]
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketBar(start + timedelta(days=index), price_a, price_b)
        for index, (price_a, price_b) in enumerate(zip(asset_a, prices_b, strict=True))
    )


def test_rolling_hedge_ratio_spread_and_zscore() -> None:
    bars = make_bars([100, 102, 104, 110, 108], [50, 51, 52, 53, 54])
    features = calculate_features(bars, lookback=2)
    assert features[0].hedge_ratio is None
    expected_beta = (50 * 100 + 51 * 102) / (50**2 + 51**2)
    assert features[1].hedge_ratio == pytest.approx(expected_beta)
    assert features[1].spread == pytest.approx(102 - expected_beta * 51)
    assert features[2].zscore is not None
    assert features[2].available_at == bars[2].timestamp
    assert features[2].window_end == bars[2].timestamp


def test_feature_at_t_does_not_read_future_bar() -> None:
    original = make_bars([100, 102, 104, 106, 108, 110])
    changed = make_bars([100, 102, 104, 106, 108, 999])
    first = calculate_features(original, lookback=2)
    second = calculate_features(changed, lookback=2)
    assert first[:5] == second[:5]


def test_signal_transitions_are_stateful() -> None:
    bars = make_bars([100, 102, 104, 114, 108, 110, 112])
    params = PairsTradingParameters(lookback=2, entry_z=0.5, exit_z=0.2)
    decisions = evaluate_signals(bars, calculate_features(bars, 2), params)
    transitions = [decision for decision in decisions if decision.signal_id]
    assert transitions
    assert all(item.action in {"LONG_SPREAD", "SHORT_SPREAD", "CLOSE"} for item in transitions)
