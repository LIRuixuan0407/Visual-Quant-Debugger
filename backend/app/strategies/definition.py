from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.models import RuntimeDescriptor, TraceCapabilitySet, full_trace_capabilities
from app.adapters.registry import adapter_registry
from app.backtest import BacktestParameters
from app.sdk.loader import LoadedStrategy
from app.sdk.strategy import VQDStrategy

if TYPE_CHECKING:
    from app.sdk.registry import StrategyRegistration

ParameterValue = int | float


class StrategyParameterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    description: str
    value_type: Literal["integer", "number"]
    default_value: ParameterValue
    minimum: ParameterValue
    exclusive_minimum: bool = False
    maximum: ParameterValue | None = None
    step: ParameterValue
    unit: str
    impact_hint: str


class StrategyPreset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preset_id: str
    name: str
    description: str
    parameters: dict[str, ParameterValue]


class ParameterValidationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_parameter: str
    operator: Literal["less_than"]
    right_parameter: str
    message: str


class PipelineNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    label: str
    category: Literal["DATA", "FEATURE", "DECISION", "POSITION", "EXECUTION"]
    description: str
    formula: str | None = None
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    related_parameters: tuple[str, ...] = ()
    used_by: tuple[str, ...] = ()


class ExecutionAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    value: str
    description: str


class StrategyDataRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_fields: tuple[str, ...]
    symbol_count: int | None
    symbols: tuple[str, ...]
    minimum_history: int


class DiagnosticCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parameter_sensitivity: str | None
    train_test: bool = True
    cost_stress: bool = True
    execution_delay: bool = True


class StrategyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    name: str
    description: str
    version: str
    parameters: tuple[StrategyParameterDefinition, ...]
    validation_rules: tuple[ParameterValidationRule, ...]
    presets: tuple[StrategyPreset, ...]
    pipeline: tuple[PipelineNode, ...]
    execution_assumptions: tuple[ExecutionAssumption, ...]
    data_requirements: StrategyDataRequirements
    diagnostic_capabilities: DiagnosticCapabilityDefinition
    trace_fidelity: Literal["FULL", "STANDARD", "BASIC"] = "FULL"
    trace_capabilities: TraceCapabilitySet = Field(default_factory=full_trace_capabilities)
    runtime: RuntimeDescriptor = RuntimeDescriptor()
    source_type: Literal["BUILT_IN", "LOCAL_PYTHON", "FRAMEWORK_PYTHON"] = "BUILT_IN"
    source_fingerprint: str | None = None
    available: bool = True
    unavailable_reason: str | None = None
    historical_research_only: bool = False


def _parameter_values() -> dict[str, ParameterValue]:
    defaults = BacktestParameters()
    return {
        "lookback": defaults.strategy.lookback,
        "entry_z": defaults.strategy.entry_z,
        "exit_z": defaults.strategy.exit_z,
        "fee_bps": defaults.fee_bps,
        "slippage_bps": defaults.slippage_bps,
    }


def build_pairs_trading_definition() -> StrategyDefinition:
    defaults = _parameter_values()
    return StrategyDefinition(
        strategy_id="pairs-trading",
        name="Pairs Trading",
        description=(
            "Models the changing price relationship between two assets, then trades temporary "
            "departures from that relationship with explicit next-bar execution."
        ),
        version="0.1",
        parameters=(
            StrategyParameterDefinition(
                key="lookback",
                label="Lookback",
                description=(
                    "Number of historical observations used to estimate the pair relationship "
                    "and rolling statistics."
                ),
                value_type="integer",
                default_value=defaults["lookback"],
                minimum=2,
                step=1,
                unit="bars",
                impact_hint="Shorter is more reactive; longer is steadier but slower to adapt.",
            ),
            StrategyParameterDefinition(
                key="entry_z",
                label="Entry Z",
                description=(
                    "How far the spread must move from its recent mean before opening a position."
                ),
                value_type="number",
                default_value=defaults["entry_z"],
                minimum=0,
                exclusive_minimum=True,
                step=0.1,
                unit="σ",
                impact_hint=(
                    "Lower thresholds react sooner; higher thresholds wait for larger moves."
                ),
            ),
            StrategyParameterDefinition(
                key="exit_z",
                label="Exit Z",
                description=(
                    "How close the spread must return toward its mean before closing the position."
                ),
                value_type="number",
                default_value=defaults["exit_z"],
                minimum=0,
                step=0.1,
                unit="σ",
                impact_hint="Must remain below Entry Z; it defines the mean-reversion exit zone.",
            ),
            StrategyParameterDefinition(
                key="fee_bps",
                label="Fee",
                description="Simulated transaction fee applied to traded notional.",
                value_type="number",
                default_value=defaults["fee_bps"],
                minimum=0,
                step=1,
                unit="bps",
                impact_hint="A cost assumption, not a forecast of a specific venue's fee.",
            ),
            StrategyParameterDefinition(
                key="slippage_bps",
                label="Slippage",
                description="Simulated execution price deterioration from the next close.",
                value_type="number",
                default_value=defaults["slippage_bps"],
                minimum=0,
                step=1,
                unit="bps",
                impact_hint="Higher values apply a more conservative fill-price assumption.",
            ),
        ),
        validation_rules=(
            ParameterValidationRule(
                left_parameter="exit_z",
                operator="less_than",
                right_parameter="entry_z",
                message="Exit Z must be smaller than Entry Z.",
            ),
        ),
        presets=(
            StrategyPreset(
                preset_id="strategy-default",
                name="Strategy Default",
                description="The production defaults defined by the current Quant Engine.",
                parameters=defaults,
            ),
            StrategyPreset(
                preset_id="demo-active-signals",
                name="Demo: Active Signals",
                description=(
                    "A short-window sample preset that preserves the golden Replay walkthrough."
                ),
                parameters={
                    "lookback": 5,
                    "entry_z": 1.0,
                    "exit_z": 0.8,
                    "fee_bps": defaults["fee_bps"],
                    "slippage_bps": defaults["slippage_bps"],
                },
            ),
        ),
        pipeline=(
            PipelineNode(
                node_id="market-data",
                label="Market Data",
                category="DATA",
                description=(
                    "The two synchronized, timezone-aware close prices available at each bar."
                ),
                outputs=("rolling-regression", "spread"),
                used_by=("Rolling Regression", "Spread"),
            ),
            PipelineNode(
                node_id="rolling-regression",
                label="Rolling Regression",
                category="FEATURE",
                description=(
                    "Estimates the recent relative price relationship using a rolling OLS "
                    "regression through the origin."
                ),
                formula="dot(price_B, price_A) / dot(price_B, price_B)",
                inputs=("market-data",),
                outputs=("hedge-ratio",),
                related_parameters=("lookback",),
                used_by=("Hedge Ratio",),
            ),
            PipelineNode(
                node_id="hedge-ratio",
                label="Hedge Ratio",
                category="FEATURE",
                description=(
                    "The estimated quantity relationship used to place the two asset prices on "
                    "a comparable basis."
                ),
                formula="beta = rolling OLS coefficient",
                inputs=("rolling-regression",),
                outputs=("spread",),
                related_parameters=("lookback",),
                used_by=("Spread", "Position sizing"),
            ),
            PipelineNode(
                node_id="spread",
                label="Spread",
                category="FEATURE",
                description=(
                    "The relative price relationship after adjusting Asset B by the recorded "
                    "hedge ratio."
                ),
                formula="price_A - hedge_ratio * price_B",
                inputs=("market-data", "hedge-ratio"),
                outputs=("rolling-mean", "rolling-std", "zscore"),
                used_by=("Rolling Mean", "Rolling Std", "Z-score"),
            ),
            PipelineNode(
                node_id="rolling-mean",
                label="Rolling Mean",
                category="FEATURE",
                description="The recent average spread used as the strategy's local center.",
                formula="mean(spread_window)",
                inputs=("spread",),
                outputs=("zscore",),
                related_parameters=("lookback",),
                used_by=("Z-score",),
            ),
            PipelineNode(
                node_id="rolling-std",
                label="Rolling Std",
                category="FEATURE",
                description="The population standard deviation of the recent spread window.",
                formula="population_std(spread_window, ddof=0)",
                inputs=("spread",),
                outputs=("zscore",),
                related_parameters=("lookback",),
                used_by=("Z-score",),
            ),
            PipelineNode(
                node_id="zscore",
                label="Z-score",
                category="FEATURE",
                description=(
                    "Measures how far the current spread is from its recent mean in units of "
                    "recent spread volatility."
                ),
                formula="(spread - rolling_mean) / rolling_std",
                inputs=("spread", "rolling-mean", "rolling-std"),
                outputs=("signal-rules",),
                related_parameters=("lookback",),
                used_by=("Entry decisions", "Exit decisions"),
            ),
            PipelineNode(
                node_id="signal-rules",
                label="Signal Rules",
                category="DECISION",
                description=(
                    "Compares the recorded Z-score with entry or exit thresholds and emits a "
                    "signal only when target state changes."
                ),
                inputs=("zscore",),
                outputs=("target-position",),
                related_parameters=("entry_z", "exit_z"),
                used_by=("Target Position",),
            ),
            PipelineNode(
                node_id="target-position",
                label="Target Position",
                category="POSITION",
                description=(
                    "The desired flat, long-spread, or short-spread state produced by the "
                    "decision engine."
                ),
                inputs=("signal-rules", "hedge-ratio"),
                outputs=("execution",),
                used_by=("Execution",),
            ),
            PipelineNode(
                node_id="execution",
                label="Execution",
                category="EXECUTION",
                description=(
                    "Translates a state transition into orders and fills them at the next "
                    "available sample close."
                ),
                inputs=("target-position",),
                related_parameters=("fee_bps", "slippage_bps"),
                used_by=("Portfolio accounting", "Replay"),
            ),
        ),
        execution_assumptions=(
            ExecutionAssumption(
                key="signal_timing",
                label="Signal timing",
                value="close(t)",
                description=(
                    "Features and decisions use information available at the current close."
                ),
            ),
            ExecutionAssumption(
                key="execution_timing",
                label="Execution timing",
                value="close(t+1)",
                description=(
                    "Orders execute on the next available close because the sample has close "
                    "prices only."
                ),
            ),
            ExecutionAssumption(
                key="position_sizing",
                label="Position sizing",
                value="$20,000 gross target",
                description="The fixed gross notional is split using the signal-time hedge ratio.",
            ),
        ),
        data_requirements=StrategyDataRequirements(
            required_fields=("close",),
            symbol_count=2,
            symbols=(),
            minimum_history=3,
        ),
        diagnostic_capabilities=DiagnosticCapabilityDefinition(parameter_sensitivity="lookback"),
    )


PAIRS_TRADING_DEFINITION = build_pairs_trading_definition()


def build_native_strategy_definition(
    strategy: VQDStrategy, loaded: LoadedStrategy
) -> StrategyDefinition:
    metadata = strategy.metadata
    engine_defaults = BacktestParameters()
    parameters = tuple(
        StrategyParameterDefinition(
            key=item.name,
            label=item.label,
            description=item.description,
            value_type=item.value_type,
            default_value=item.default,
            minimum=item.minimum,
            maximum=item.maximum,
            step=item.step,
            unit=item.unit,
            impact_hint="Declared by the registered native strategy.",
        )
        for item in strategy.parameter_definitions()
    ) + (
        StrategyParameterDefinition(
            key="fee_bps",
            label="Fee",
            description="Simulated transaction fee applied to traded notional.",
            value_type="number",
            default_value=engine_defaults.fee_bps,
            minimum=0,
            step=1,
            unit="bps",
            impact_hint="A run-level execution assumption.",
        ),
        StrategyParameterDefinition(
            key="slippage_bps",
            label="Slippage",
            description="Simulated execution price deterioration from the next close.",
            value_type="number",
            default_value=engine_defaults.slippage_bps,
            minimum=0,
            step=1,
            unit="bps",
            impact_hint="A run-level execution assumption.",
        ),
    )

    defaults = {item.key: item.default_value for item in parameters}
    requirements = metadata.data_requirements
    return StrategyDefinition(
        strategy_id=metadata.strategy_id,
        name=metadata.name,
        description=metadata.description,
        version=metadata.version,
        parameters=parameters,
        validation_rules=(),
        presets=(
            StrategyPreset(
                preset_id="strategy-default",
                name="Strategy Default",
                description="Defaults declared by the registered native strategy.",
                parameters=defaults,
            ),
        ),
        pipeline=(
            PipelineNode(
                node_id="market-data",
                label="Market Data",
                category="DATA",
                description="Point-in-time synchronized fields requested through StrategyContext.",
                outputs=("observed-features",),
                used_by=("Observed runtime features",),
            ),
            PipelineNode(
                node_id="observed-features",
                label="Observed Runtime Features",
                category="FEATURE",
                description="Feature graph is populated from actual ctx.feature calls after a run.",
                inputs=("market-data",),
                outputs=("decision",),
                used_by=("Decision", "Replay"),
            ),
            PipelineNode(
                node_id="decision",
                label="Decision",
                category="DECISION",
                description="Structured reason, conditions, dependencies, and target intent.",
                inputs=("observed-features",),
                outputs=("target-portfolio",),
                used_by=("Target Portfolio",),
            ),
            PipelineNode(
                node_id="target-portfolio",
                label="Target Portfolio",
                category="POSITION",
                description="Symbol quantities or gross-normalized weights requested by strategy.",
                inputs=("decision",),
                outputs=("execution",),
                used_by=("Execution",),
            ),
            PipelineNode(
                node_id="execution",
                label="Execution",
                category="EXECUTION",
                description="Current-to-target delta orders filled at close(t+1).",
                inputs=("target-portfolio",),
                related_parameters=("fee_bps", "slippage_bps"),
                used_by=("Portfolio accounting", "Replay"),
            ),
        ),
        execution_assumptions=(
            ExecutionAssumption(
                key="signal_timing",
                label="Signal timing",
                value="close(t)",
                description="Only data available through the current watermark can be used.",
            ),
            ExecutionAssumption(
                key="execution_timing",
                label="Execution timing",
                value="close(t+1)",
                description="Target transitions execute at the next synchronized close.",
            ),
        ),
        data_requirements=StrategyDataRequirements(
            required_fields=requirements.required_fields,
            symbol_count=requirements.symbol_count,
            symbols=requirements.symbols,
            minimum_history=requirements.minimum_history,
        ),
        diagnostic_capabilities=DiagnosticCapabilityDefinition(
            parameter_sensitivity=metadata.diagnostic_capabilities.parameter_sensitivity
        ),
        trace_fidelity=metadata.trace_fidelity,
        source_type="LOCAL_PYTHON",
        source_fingerprint=loaded.source_fingerprint,
    )


def build_framework_strategy_definition(
    registration: "StrategyRegistration",
) -> StrategyDefinition:
    manifest = registration.adapter_manifest
    if manifest is None or registration.adapter_id is None or registration.adapter_version is None:
        raise ValueError("Framework strategy registration is missing its typed adapter manifest")
    installed_version = adapter_registry.installed_version(registration.adapter_id)
    available = installed_version is not None
    if registration.adapter_id == "backtesting.py":
        fidelity: Literal["STANDARD", "BASIC"] = "STANDARD"
        capabilities = TraceCapabilitySet(
            market_timeline="AVAILABLE",
            feature_values="PARTIAL",
            decision_events="PARTIAL",
            orders="PARTIAL",
            executions="PARTIAL",
            positions="AVAILABLE",
            trades="AVAILABLE",
            equity="AVAILABLE",
            pnl="AVAILABLE",
            gross_pnl="PARTIAL",
            fees="PARTIAL",
            trade_attribution="AVAILABLE",
            drawdowns="AVAILABLE",
        )
    else:
        fidelity = "BASIC"
        capabilities = TraceCapabilitySet(
            market_timeline="AVAILABLE",
            orders="AVAILABLE",
            executions="AVAILABLE",
            positions="AVAILABLE",
            trades="AVAILABLE",
            equity="AVAILABLE",
            pnl="AVAILABLE",
            gross_pnl="PARTIAL",
            fees="AVAILABLE",
            trade_attribution="AVAILABLE",
            drawdowns="AVAILABLE",
        )
    runtime = RuntimeDescriptor(
        kind="framework",
        adapter_id=registration.adapter_id,
        adapter_version=registration.adapter_version,
        framework_name=registration.framework_name,
        framework_version=installed_version or registration.framework_version,
        execution_owner=registration.framework_name or registration.adapter_id,
        trace_fidelity=fidelity,
        trace_capabilities=capabilities,
        determinism="SEEDED" if manifest.random_seed is not None else "UNVERIFIED",
        random_seed=manifest.random_seed,
        historical_research_only=True,
    )
    parameters = tuple(
        StrategyParameterDefinition(
            key=item.name,
            label=item.label,
            description=item.description,
            value_type=item.value_type,
            default_value=item.default,
            minimum=item.minimum,
            maximum=item.maximum,
            step=item.step,
            unit=item.unit,
            impact_hint="Declared in the typed framework adapter manifest.",
        )
        for item in manifest.parameters
    )
    defaults = {item.key: item.default_value for item in parameters}
    return StrategyDefinition(
        strategy_id=manifest.strategy_id,
        name=manifest.name,
        description=manifest.description,
        version=manifest.version,
        parameters=parameters,
        validation_rules=(),
        presets=(
            StrategyPreset(
                preset_id="strategy-default",
                name="Strategy Default",
                description="Defaults declared in the framework adapter manifest.",
                parameters=defaults,
            ),
        ),
        pipeline=(
            PipelineNode(
                node_id="market-data",
                label="VQD Dataset",
                category="DATA",
                description="The immutable VQD dataset revision supplied to the framework.",
                outputs=("framework-engine",),
                used_by=(registration.framework_name or registration.adapter_id,),
            ),
            PipelineNode(
                node_id="framework-engine",
                label=registration.framework_name or registration.adapter_id,
                category="EXECUTION",
                description=(
                    "The external framework owns execution and portfolio accounting; VQD "
                    "normalizes only the artifacts it returns."
                ),
                inputs=("market-data",),
                used_by=("Replay", "P&L Autopsy", "Run comparison"),
            ),
        ),
        execution_assumptions=tuple(
            ExecutionAssumption(
                key=key,
                label=key.replace("_", " ").title(),
                value=str(value),
                description="Recorded framework execution configuration.",
            )
            for key, value in manifest.execution_config.items()
        ),
        data_requirements=StrategyDataRequirements(
            required_fields=manifest.data_requirements.required_fields,
            symbol_count=manifest.data_requirements.symbol_count,
            symbols=(),
            minimum_history=manifest.data_requirements.minimum_history,
        ),
        diagnostic_capabilities=DiagnosticCapabilityDefinition(
            parameter_sensitivity=manifest.diagnostic_capabilities.parameter_sensitivity,
            train_test=manifest.diagnostic_capabilities.train_test,
            cost_stress=manifest.diagnostic_capabilities.cost_stress,
            execution_delay=manifest.diagnostic_capabilities.execution_delay,
        ),
        trace_fidelity=fidelity,
        trace_capabilities=capabilities,
        runtime=runtime,
        source_type="FRAMEWORK_PYTHON",
        source_fingerprint=registration.source_fingerprint,
        available=available,
        unavailable_reason=(
            None
            if available
            else f"{registration.framework_name or registration.adapter_id} package not installed"
        ),
        historical_research_only=True,
    )


def get_strategy_definition(strategy_id: str) -> StrategyDefinition | None:
    if strategy_id == PAIRS_TRADING_DEFINITION.strategy_id:
        return PAIRS_TRADING_DEFINITION
    from app.sdk.registry import strategy_registry

    registration = strategy_registry.get_registration(strategy_id)
    if registration is None:
        return None
    if registration.runtime_kind == "framework":
        return build_framework_strategy_definition(registration)
    strategy, loaded = strategy_registry.instantiate(strategy_id)
    return build_native_strategy_definition(strategy, loaded)


def list_strategy_definitions() -> tuple[StrategyDefinition, ...]:
    from app.sdk.registry import strategy_registry

    definitions = [PAIRS_TRADING_DEFINITION]
    for registration in strategy_registry.list():
        if registration.runtime_kind == "framework":
            definitions.append(build_framework_strategy_definition(registration))
        else:
            strategy, loaded = strategy_registry.instantiate(registration.strategy_id)
            definitions.append(build_native_strategy_definition(strategy, loaded))
    return tuple(definitions)


def demo_parameters() -> dict[str, ParameterValue]:
    return dict(PAIRS_TRADING_DEFINITION.presets[1].parameters)


def assert_definition_matches_engine_defaults() -> None:
    definition_defaults = {
        parameter.key: parameter.default_value for parameter in PAIRS_TRADING_DEFINITION.parameters
    }
    if definition_defaults != _parameter_values():
        raise RuntimeError("Strategy Definition defaults do not match the Quant Engine defaults")


assert_definition_matches_engine_defaults()
