from __future__ import annotations

from .models import FactorDefinition, FactorParameter


def _lookback(default: int, description: str = "Historical observations used") -> FactorParameter:
    return FactorParameter(
        key="lookback",
        label="Lookback",
        description=description,
        default_value=default,
        minimum=2,
        maximum=252,
        step=1,
        unit="bars",
    )


def _max_age(default: int = 550) -> FactorParameter:
    return FactorParameter(
        key="max_age_days",
        label="Maximum fundamental age",
        description="Reject filings older than this many calendar days at the factor timestamp.",
        default_value=default,
        minimum=30,
        maximum=1_500,
        step=1,
        unit="days",
    )


FACTOR_CATALOG: tuple[FactorDefinition, ...] = (
    FactorDefinition(
        factor_id="momentum",
        name="Momentum",
        formula="close(t) / close(t-lookback) - 1",
        description="Measures trailing price persistence over a fixed historical window.",
        parameters=(_lookback(20),),
        required_fields=("close",),
        lookback=20,
    ),
    FactorDefinition(
        factor_id="reversal",
        name="Reversal",
        formula="-(close(t) / close(t-lookback) - 1)",
        description="Tests whether recent relative losers subsequently recover.",
        parameters=(_lookback(5),),
        required_fields=("close",),
        lookback=5,
    ),
    FactorDefinition(
        factor_id="volatility",
        name="Volatility",
        formula="std(daily_return[t-lookback+1:t])",
        description="Measures realized variability using trailing close-to-close returns.",
        parameters=(_lookback(20),),
        required_fields=("close",),
        lookback=20,
        direction="LOW",
    ),
    FactorDefinition(
        factor_id="volume-change",
        name="Volume Change",
        formula="volume(t) / mean(volume[t-lookback:t-1]) - 1",
        description="Compares current activity with its trailing baseline.",
        parameters=(_lookback(20),),
        required_fields=("volume",),
        lookback=20,
    ),
    FactorDefinition(
        factor_id="liquidity-proxy",
        name="Liquidity Proxy",
        formula="log(mean(close * volume, lookback))",
        description="Uses trailing average dollar volume as a transparent liquidity proxy.",
        parameters=(_lookback(20),),
        required_fields=("close", "volume"),
        lookback=20,
    ),
    FactorDefinition(
        factor_id="price-range",
        name="Price Range",
        formula="(max(high, lookback) - min(low, lookback)) / close(t)",
        description="Measures the size of the recent trading range relative to current price.",
        parameters=(_lookback(20),),
        required_fields=("high", "low", "close"),
        lookback=20,
    ),
    FactorDefinition(
        factor_id="moving-average-distance",
        name="Moving Average Distance",
        formula="close(t) / mean(close, lookback) - 1",
        description="Locates current price relative to its trailing moving average.",
        parameters=(_lookback(20),),
        required_fields=("close",),
        lookback=20,
    ),
    FactorDefinition(
        factor_id="breakout",
        name="Breakout",
        formula="close(t) / max(high[t-lookback:t-1]) - 1",
        description="Measures distance through or below the prior trailing high.",
        parameters=(_lookback(20),),
        required_fields=("close", "high"),
        lookback=20,
    ),
    FactorDefinition(
        factor_id="earnings-yield",
        name="Earnings Yield",
        formula="latest_annual(net_income) / (close(t) * shares_outstanding)",
        description="Annual earnings available at t relative to market capitalization at t.",
        parameters=(_max_age(),),
        required_fields=("close",),
        required_fundamental_fields=("net_income", "shares_outstanding"),
        lookback=0,
        category="VALUE",
        data_source="FUNDAMENTAL",
    ),
    FactorDefinition(
        factor_id="book-to-price",
        name="Book-to-Price",
        formula="latest(equity) / (close(t) * shares_outstanding)",
        description="Book equity available at t relative to market capitalization at t.",
        parameters=(_max_age(),),
        required_fields=("close",),
        required_fundamental_fields=("equity", "shares_outstanding"),
        lookback=0,
        category="VALUE",
        data_source="FUNDAMENTAL",
    ),
    FactorDefinition(
        factor_id="free-cash-flow-yield",
        name="Free Cash Flow Yield",
        formula="latest_annual(free_cash_flow) / (close(t) * shares_outstanding)",
        description="Filed free cash flow relative to market capitalization at t.",
        parameters=(_max_age(),),
        required_fields=("close",),
        required_fundamental_fields=("free_cash_flow", "shares_outstanding"),
        lookback=0,
        category="VALUE",
        data_source="FUNDAMENTAL",
    ),
    FactorDefinition(
        factor_id="roe",
        name="Return on Equity",
        formula="latest_annual(net_income) / latest(equity)",
        description="Filed annual net income relative to the latest available equity.",
        parameters=(_max_age(),),
        required_fields=(),
        required_fundamental_fields=("net_income", "equity"),
        lookback=0,
        category="QUALITY",
        data_source="FUNDAMENTAL",
    ),
    FactorDefinition(
        factor_id="roa",
        name="Return on Assets",
        formula="latest_annual(net_income) / latest(assets)",
        description="Filed annual net income relative to the latest available assets.",
        parameters=(_max_age(),),
        required_fields=(),
        required_fundamental_fields=("net_income", "assets"),
        lookback=0,
        category="QUALITY",
        data_source="FUNDAMENTAL",
    ),
    FactorDefinition(
        factor_id="operating-margin",
        name="Operating Margin",
        formula="latest_annual(operating_income) / latest_annual(revenue)",
        description="Filed annual operating income as a share of filed annual revenue.",
        parameters=(_max_age(),),
        required_fields=(),
        required_fundamental_fields=("operating_income", "revenue"),
        lookback=0,
        category="QUALITY",
        data_source="FUNDAMENTAL",
    ),
    FactorDefinition(
        factor_id="revenue-growth",
        name="Revenue Growth",
        formula="latest_annual(revenue) / prior_annual(revenue) - 1",
        description="Growth between the two latest annual revenue filings available at t.",
        parameters=(_max_age(),),
        required_fields=(),
        required_fundamental_fields=("revenue",),
        lookback=0,
        category="GROWTH",
        data_source="FUNDAMENTAL",
    ),
    FactorDefinition(
        factor_id="earnings-growth",
        name="Earnings Growth",
        formula="latest_annual(net_income) / prior_annual(net_income) - 1",
        description="Growth between the two latest annual earnings filings available at t.",
        parameters=(_max_age(),),
        required_fields=(),
        required_fundamental_fields=("net_income",),
        lookback=0,
        category="GROWTH",
        data_source="FUNDAMENTAL",
    ),
    FactorDefinition(
        factor_id="debt-to-equity",
        name="Debt to Equity",
        formula="latest(debt) / latest(equity)",
        description="Latest filed debt relative to latest filed shareholders' equity.",
        parameters=(_max_age(),),
        required_fields=(),
        required_fundamental_fields=("debt", "equity"),
        lookback=0,
        category="LEVERAGE",
        data_source="FUNDAMENTAL",
        direction="LOW",
    ),
    FactorDefinition(
        factor_id="mixed",
        name="Explicit Mixed Factor",
        formula="sum(weight_i * cross_sectional_zscore(component_i(t)))",
        description="A user-declared weighted combination; VQD never searches formulas or weights.",
        parameters=(),
        required_fields=(),
        required_fundamental_fields=(),
        lookback=0,
        category="MIXED",
        data_source="MIXED",
    ),
)


def factor_definition(factor_id: str) -> FactorDefinition:
    definition = next((item for item in FACTOR_CATALOG if item.factor_id == factor_id), None)
    if definition is None:
        raise KeyError(f"Factor '{factor_id}' was not found")
    return definition


def parameter_values(
    definition: FactorDefinition, supplied: dict[str, int | float]
) -> dict[str, int | float]:
    known = {item.key: item for item in definition.parameters}
    unknown = sorted(set(supplied) - set(known))
    if unknown:
        raise ValueError(f"Unknown factor parameters: {', '.join(unknown)}")
    values: dict[str, int | float] = {}
    for key, parameter in known.items():
        value = supplied.get(key, parameter.default_value)
        if value < parameter.minimum or (
            parameter.maximum is not None and value > parameter.maximum
        ):
            raise ValueError(f"Factor parameter '{key}' is outside its supported range")
        values[key] = value
    return values
