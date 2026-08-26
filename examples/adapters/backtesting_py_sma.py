from backtesting import Strategy

from app.adapters.models import (
    AdapterDataRequirements,
    AdapterDiagnosticCapabilities,
    AdapterParameterSpec,
    AdapterStrategyManifest,
)


def rolling_mean(values, window):
    return values.s.rolling(window).mean()


class FrameworkSmaCross(Strategy):
    fast_window = 5
    slow_window = 12

    def init(self):
        self.fast = self.I(rolling_mean, self.data.Close, self.fast_window, name="fast_ma")
        self.slow = self.I(rolling_mean, self.data.Close, self.slow_window, name="slow_ma")

    def next(self):
        if self.fast[-1] > self.slow[-1] and not self.position.is_long:
            self.position.close()
            self.buy()
        elif self.fast[-1] < self.slow[-1] and not self.position.is_short:
            self.position.close()
            self.sell()


VQD_ADAPTER_MANIFEST = AdapterStrategyManifest(
    strategy_id="framework-sma-cross-bt",
    name="Framework SMA Cross",
    description="A single-symbol SMA crossover executed entirely by backtesting.py.",
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
        required_fields=("open", "high", "low", "close"),
        symbol_count=1,
        minimum_history=15,
    ),
    diagnostic_capabilities=AdapterDiagnosticCapabilities(
        train_test=True,
        parameter_sensitivity="fast_window",
    ),
    execution_config={
        "cash": 100_000.0,
        "commission": 0.001,
        "trade_on_close": False,
        "exclusive_orders": True,
        "finalize_trades": True,
    },
)
