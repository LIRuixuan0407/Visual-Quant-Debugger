import vectorbt as vbt

from app.adapters.models import AdapterDataRequirements, AdapterStrategyManifest


def build_portfolio(ctx):
    close = ctx.close()
    portfolio = vbt.Portfolio.from_holding(close, init_cash=100_000.0, fees=0.001)
    return ctx.result(portfolio=portfolio)


VQD_ADAPTER_MANIFEST = AdapterStrategyManifest(
    strategy_id="vectorbt-portfolio-only",
    name="Vectorbt Portfolio Only",
    description="Portfolio accounting without declared signals or feature arrays.",
    data_requirements=AdapterDataRequirements(
        required_fields=("close",), symbol_count=1, minimum_history=2
    ),
)
