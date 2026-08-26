import vectorbt as vbt

from app.adapters.models import (
    AdapterDataRequirements,
    AdapterDiagnosticCapabilities,
    AdapterParameterSpec,
    AdapterStrategyManifest,
)


def build_strategy(ctx, fast_window=5, slow_window=12):
    close = ctx.close()
    fast = close.rolling(fast_window).mean()
    slow = close.rolling(slow_window).mean()
    entries = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    exits = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    portfolio = vbt.Portfolio.from_signals(
        close,
        entries.shift(1, fill_value=False),
        exits.shift(1, fill_value=False),
        init_cash=100_000.0,
        fees=0.001,
        freq="1D",
    )
    return ctx.result(
        portfolio=portfolio,
        signals={"entries": entries, "exits": exits},
        features={"fast_ma": fast, "slow_ma": slow},
    )


VQD_ADAPTER_MANIFEST = AdapterStrategyManifest(
    strategy_id="framework-sma-cross-vbt",
    name="Vectorized SMA Cross",
    description="Explicit vectorbt signals and feature arrays over a VQD dataset revision.",
    parameters=(
        AdapterParameterSpec(
            name="fast_window",
            label="Fast window",
            value_type="integer",
            default=5,
            minimum=2,
            maximum=50,
            step=1,
        ),
        AdapterParameterSpec(
            name="slow_window",
            label="Slow window",
            value_type="integer",
            default=12,
            minimum=3,
            maximum=100,
            step=1,
        ),
    ),
    data_requirements=AdapterDataRequirements(
        required_fields=("close",),
        symbol_count=1,
        minimum_history=15,
    ),
    diagnostic_capabilities=AdapterDiagnosticCapabilities(
        train_test=True,
        parameter_sensitivity="fast_window",
    ),
)
