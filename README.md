# Visual Quant Debugger

> Don't just backtest your strategy. Debug it.

Visual Quant Debugger is a local research environment for running, tracing, replaying, diagnosing, and
forward-validating Python quantitative strategies. It combines a point-in-time-safe Native Strategy SDK
with durable strategy, dataset, and research-run records. Pairs Trading is the built-in example, not the
product boundary.

## Current status

- Professional desktop-first React + TypeScript research workspace with persistent navigation and run context
- Native `VQDStrategy` SDK with incremental `initialize` / `on_bar` execution and FULL trace fidelity
- Optional `backtesting.py` and `vectorbt` historical-research adapters with explicit capability/fidelity labels
- Trusted local Python strategy registration with durable paths, metadata, and SHA-256 source revisions
- Data Workspace with real Alpaca US-equity search/snapshots/history plus CSV import, quality reports, provenance, and persistence
- Historical Market workspace for real provider-backed cross-sections, historical-universe evidence,
  price/volume history, and filed-fundamental snapshots at the selected market date
- Point-in-time Factor Lab with eight price/volume factors, nine standardized fundamental factors, and
  explicit user-weighted mixed factors; 1/5/20-day IC and Rank IC, Q1-Q5 returns,
  long-short spread, turnover, coverage, staged Research/Validation/Holdout reveal, and durable research records
- Open `VQDFactor` SDK with controlled market/fundamental history, local `.py` import, durable source
  fingerprints, custom/built-in catalog identity, recursive Factor lineage, and restart recovery
- Factor-to-Native-Strategy bridge using the existing backtest, Trace, Replay, Diagnose, Autopsy, Forward,
  and Paper runtime instead of a separate research execution engine
- Generic strategy + dataset + parameters + research-cutoff run configuration
- SQLite-indexed Research Runs with immutable manifests, source snapshots, persistent traces, notes, and tags
- Strict/contextual/descriptive run comparison with parameter, metric, signal, execution, and first-divergence facts
- Replay debugger with an equity timeline and synchronized inspectors
- Visual Strategy Anatomy with a backend-defined pipeline, concept inspector, presets, and parameters
- FastAPI application with `GET /health`
- Replay APIs for running the fixed sample backtest and loading its complete Trace 1.0 payload
- Strategy Definition APIs for metadata, defaults, validation rules, presets, and execution assumptions
- Strict CSV market-data validation
- Rolling pairs-trading features and stateful signal transitions
- One-bar-delayed execution, directional slippage, and fees
- Cash, holdings, exposure, equity, and P&L accounting
- Return, daily Sharpe, maximum drawdown, and turnover
- Deterministic synthetic sample data and a checked golden result
- Versioned, deterministic Quant Trace Protocol with feature and execution lineage
- Point-in-time data dependencies and trace-based look-ahead diagnostics
- Stable JSON serialization and a human-reviewable golden trace projection
- Signal/evaluation navigation, recursive feature lineage, point-in-time dependency inspection,
  and signal-to-execution navigation
- Explicit draft → validation → run → generated trace → Replay experiment flow
- Trace-bound Diagnose workspace with a chronological 70/30 split, lookback sensitivity, friction
  stress, and t+1/t+2/t+3 execution-delay reruns
- Trace-native P&L Autopsy with exact gross/cost/net reconciliation, UTC period attribution,
  closed/open trade attribution, and peak/trough/recovery drawdown episodes
- Cross-page Strategy → Replay → Diagnose / P&L Autopsy → selected Replay event navigation
- Forward Validation sessions for built-in and registered strategies with incremental historical-bar delivery,
  pending next-bar execution, persistent paper portfolio state, and append-only trace history
- Research-vs-forward generalization comparison and same-path batch-vs-streaming consistency checks
- Persistent Live Paper sessions driven by real Alpaca one-minute stock bars, with a safe choice between
  deterministic VQD execution and Alpaca Paper Broker orders, append-only lifecycle journals,
  reconnect/reconciliation, point-in-time revisions, and restart recovery
- Backtest-vs-Paper validation with frozen Recorded Feed Reference Runs, layered first-divergence evidence,
  conservative comparability labels, Replay links, and residual-preserving P&L attribution
- Python API/integration tests, frontend interaction tests, lint, typecheck, and GitHub Actions CI

## Repository layout

```text
frontend/          React + TypeScript + Vite professional research workspace
backend/app/sdk/   Native strategy contract, point-in-time context, loader, runtime, trace builder, and registry
backend/app/adapters/  Framework-neutral contract, isolated worker, and optional framework adapters
backend/app/datasets/  CSV normalization, quality checks, fingerprints, and durable local dataset registry
backend/app/runs/  Open strategy/dataset research-run orchestration
backend/app/market_data/  Provider-independent live bars, Alpaca/fake adapters, clock, and PIT store
backend/app/broker/  Paper-only broker contract and Alpaca Trading API adapter
backend/app/factors/  PIT factor catalog, cross-sectional evaluation, research ledger, and strategy factory
backend/app/factor_sdk/  Public VQDFactor contract, controlled context, loader, and typed results
backend/app/fundamentals/  Provider boundary, standardized filings, PIT snapshots, and restatement disclosure
backend/app/universes/  Static and verifiable point-in-time historical-universe records
backend/app/paper/  Persistent live paper sessions, journal replay, checkpoints, account, and SSE runtime
backend/app/       API, execution, portfolio, trace, diagnostics, autopsy, and forward modules
backend/tests/     Unit, integration, future-mutation, golden fixture, API, and CLI tests
examples/          Native SMA Cross example; not registered automatically
sample_data/       Fixed built-in research and forward fixtures
.github/           CI checks
```

## Requirements

- Python 3.12+
- Node.js 22+
- pnpm 11+

Framework integrations are optional. Core install and CI do not import either framework:

```bash
.venv/bin/python -m pip install -e 'backend[frameworks]'
```

The tested optional ranges are `backtesting>=0.6.6,<0.7` and `vectorbt>=1.1,<2`.
`backtesting.py` is AGPL-3.0-licensed. `vectorbt` is distributed under Apache 2.0 with the Commons
Clause; review that restriction before commercial redistribution or hosted-service use.

## Run locally

Backend setup and checks:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e 'backend[dev]'
cd backend
../.venv/bin/ruff check .
../.venv/bin/mypy app
../.venv/bin/pytest --cov=app --cov-report=term-missing
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --reload
```

The health endpoint is then available at `http://127.0.0.1:8000/health`. Replay uses:

```text
POST /api/backtests
GET  /api/traces/{trace_id}
GET  /api/traces/{trace_id}/context
POST /api/diagnostics
GET  /api/traces/{trace_id}/pnl-autopsy
GET  /api/strategies
GET  /api/strategies/{strategy_id}
GET  /api/datasets
GET  /api/datasets/{dataset_id}
GET  /api/datasets/{dataset_id}/preview
POST /api/datasets/import/preview
POST /api/datasets/import
POST /api/compatibility-checks
GET  /api/runs
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/strategy-source
PATCH /api/runs/{run_id}/annotations
DELETE /api/runs/{run_id}
POST /api/runs/{run_id}/rerun
POST /api/run-comparisons
POST /api/forward-sessions
GET  /api/forward-sessions/{session_id}
POST /api/forward-sessions/{session_id}/start
POST /api/forward-sessions/{session_id}/step
POST /api/forward-sessions/{session_id}/pause
POST /api/forward-sessions/{session_id}/resume
POST /api/forward-sessions/{session_id}/stop
GET  /api/forward-sessions/{session_id}/trace
GET  /api/forward-sessions/{session_id}/comparison
GET  /api/market-data/providers
GET  /api/market-data/stocks/search?q=AAPL
GET  /api/market-data/stocks/{symbol}/snapshot
POST /api/market-data/historical-datasets
GET  /api/historical-market/{dataset_id}?as_of=...
GET  /api/fundamental-providers
GET  /api/fundamental-datasets
POST /api/fundamental-datasets/sec-companyfacts
GET  /api/fundamental-datasets/{dataset_id}/snapshot
GET  /api/universes
POST /api/universes/static/{dataset_id}
GET  /api/factors
GET  /api/factor-research
POST /api/factor-research
GET  /api/factor-research/{research_id}
GET  /api/factor-research/{research_id}/inspect
POST /api/factor-research/{research_id}/validate
POST /api/factor-research/{research_id}/reveal-holdout
POST /api/factor-research/{research_id}/strategy
GET  /api/paper-sessions
POST /api/paper-sessions
GET  /api/paper-sessions/{session_id}
POST /api/paper-sessions/{session_id}/start
POST /api/paper-sessions/{session_id}/pause
POST /api/paper-sessions/{session_id}/resume
POST /api/paper-sessions/{session_id}/stop
GET  /api/paper-sessions/{session_id}/trace
GET  /api/paper-sessions/{session_id}/events
POST /api/run-validations
```

`POST /api/backtests` accepts `strategy_id`, `dataset_id`, `parameters`, and an optional
`research_cutoff`. It creates a new Research Run and returns distinct `run_id` and `trace_id` values.
Historical Forward sessions remain process-local. Research Runs and Live Paper sessions are durable; their
artifacts survive backend restarts.

Run the deterministic example from the repository root:

```bash
.venv/bin/python backend/scripts/demo.py
```

Real persisted research can be checked through one verification entry point:

```bash
# Factor Relationship research (Holdout is never selected implicitly)
.venv/bin/python backend/scripts/verify_research.py relationships \
  <momentum_research_id> <volatility_research_id> <roe_research_id> \
  <fundamental_dataset_id>

# Strategy Discovery; stops after Validation unless --reveal-holdout is explicit
.venv/bin/python backend/scripts/verify_research.py discovery \
  <factor_research_id> <factor_research_id>
```

Frontend setup and checks:

```bash
cd frontend
pnpm install
pnpm lint
pnpm test
pnpm build
pnpm dev
```

The Vite app is then available at `http://127.0.0.1:5173`.

SEC Company Facts downloads require a fair-access identity in the backend environment, for example
`SEC_USER_AGENT="Visual Quant Debugger contact@example.com"`. This is an application identity, not an API
secret. VQD treats the filing date as the earliest availability date and never substitutes the fiscal period
end for data availability. The current SEC Company Facts adapter is explicitly labelled
**NOT RESTATEMENT-SAFE** because the endpoint does not guarantee an immutable historical view of every
post-acceptance correction.

Open that URL with the backend running. The app first loads the Pairs Trading Strategy Definition.
Edit a draft and select **Run Backtest** to create a new trace, then **Open Replay** to inspect that
exact trace. Opening Replay before a Strategy run uses the named **Demo: Active Signals** preset from
the backend definition. Diagnose and P&L Autopsy never substitute demo data: without an active trace
they show an explicit empty state. Vite proxies `/api` to `http://127.0.0.1:8000` during development.

## Research Runs

Every backtest attempt becomes a durable local Research Run. Searchable metadata lives in
`.vqd/vqd.sqlite`; immutable artifacts live under `.vqd/runs/<run-id>/`. A completed directory contains a
versioned `manifest.json`, the exact `strategy.py` source snapshot, and `trace.json`. Diagnostics and P&L
Autopsy JSON artifacts are added only after those analyses are requested. Failed attempts retain their
manifest, failure facts, source snapshot, and any partial trace that was produced.

Open **Runs** to filter the ledger by strategy, dataset, or status; inspect revisions, parameters, metrics,
and artifacts; edit plain-text annotations; or open a historical Replay, Diagnose, or P&L Autopsy without
executing the strategy again. Run facts are immutable. Display name, note, and tags are annotations and can
be edited. Run detail deep links use `/runs/<run-id>`.

The CLI exposes the same local ledger:

```bash
.venv/bin/vqd run list
.venv/bin/vqd run show run-...
.venv/bin/vqd run delete run-... --force
```

## Reproducibility

VQD records the strategy source, normalized dataset revision, parameters, research segment, execution model,
engine version, Python version, and platform required to reproduce the research logic locally. **Re-run exact
revision** executes the saved trusted-local source snapshot against the referenced immutable dataset revision
and creates a new Run; it never overwrites the original. Equivalent inputs share a deterministic run
fingerprint, but are never deduplicated. VQD does not yet freeze the complete Python dependency, OS, BLAS, or
hardware environment, so this is intentionally not presented as universal cross-machine reproducibility.

## Compare Runs

Select two to four ledger rows and choose **Compare**. VQD first classifies the context as strictly comparable,
contextually comparable, or descriptive only. The report then presents backend-saved parameter and metric
differences. Strictly comparable runs also expose aligned equity, signal and execution differences; two-run
comparisons identify the first feature, condition, signal, position, order, or execution divergence and link
both sides back to the corresponding historical Replay events. The comparison reports differences without
ranking runs or recommending parameters.

## Framework adapters

VQD can run trusted local strategies through the framework that owns their execution and accounting:

```bash
.venv/bin/vqd strategy add examples/adapters/backtesting_py_sma.py \
  --framework backtesting.py --class SmaCross
.venv/bin/vqd strategy add examples/adapters/vectorbt_sma.py \
  --framework vectorbt --entrypoint build_portfolio
.venv/bin/vqd strategy list
```

The VQD dataset is the only market-data input. The adapter sends its exact timestamps, symbols, and
canonical OHLCV values to a separate Python worker; it never lets a strategy silently replace that data.
The framework remains the source of truth for signals, orders/trades, equity, and accounting. VQD only
normalizes facts the adapter can prove. In particular, a missing feature dependency, fee, slippage value,
or point-in-time record is displayed as **not available**, never as zero, safe, or verified.

Every framework Run stores the exact `strategy.py`, an `adapter-manifest.json`, framework/adapter versions,
execution configuration, capability set, and normalized Trace. Replay, P&L Autopsy, comparison, and the
descriptive portion of Diagnose read those immutable artifacts after restart without importing the original
strategy. Exact rerun requires the same registered adapter identity, framework version, saved source, adapter
manifest, and dataset revision.

Trace fidelity communicates evidence depth:

- **FULL** — VQD Native only: market, features and lineage, decisions and conditions, point-in-time
  dependencies, execution, positions, equity, costs, and P&L are all recorded.
- **STANDARD** — a framework run supplies the market/equity/P&L path plus recorded feature values or explicit
  decision events, but does not prove the complete native lineage.
- **BASIC** — portfolio/trade/equity facts are available without auditable per-decision evidence.

Framework strategies are historical-research-only in Phase 11. Forward Validation and Live Paper require the
Native Strategy SDK. Strategy source is trusted local code: the subprocess boundary isolates imports and
normalizes failures, but it is not an OS security sandbox. Do not register untrusted Python files.

See [`examples/adapters/backtesting_py_sma.py`](examples/adapters/backtesting_py_sma.py),
[`examples/adapters/vectorbt_sma.py`](examples/adapters/vectorbt_sma.py), and the portfolio-only BASIC example
at [`examples/adapters/vectorbt_portfolio_only.py`](examples/adapters/vectorbt_portfolio_only.py). They run on
the checked single-symbol fixture [`sample_data/single_ohlcv_daily.csv`](sample_data/single_ohlcv_daily.csv).

## Open Factor SDK

Local factors import the stable public surface from `app.factor_sdk`. `FactorContext` exposes only
`current`, `history`, `fundamental`, declared numeric `parameters`, and recursive `factor` composition. It
never passes a full market or fundamental DataFrame to user code. Every point records `available_at` and
`used_at`; VQD rejects inputs where `available_at > used_at`.

```python
from app.factor_sdk import (
    FactorContext, FactorMetadata, FactorResult, VQDFactor, factor_parameter,
)


class QualityMomentum(VQDFactor):
    metadata = FactorMetadata(
        factor_id="quality-momentum",
        name="Quality Momentum",
        version="1.0.0",
        description="Combine explicit momentum and filed ROE inputs.",
        formula="momentum_60 + quality_weight * roe",
        required_fields=("close",),
        required_fundamental_fields=("net_income", "equity"),
        lookback=60,
        category="MIXED",
        data_source="MIXED",
    )
    quality_weight = factor_parameter(
        default=0.35, minimum=0.0, maximum=2.0, step=0.05,
        description="Explicit ROE contribution.",
    )

    def compute(self, context: FactorContext, symbol: str) -> FactorResult:
        momentum = context.factor("momentum", symbol, {"lookback": 60})
        roe = context.factor("roe", symbol, {"max_age_days": 550})
        value = None if momentum.value is None or roe.value is None else (
            momentum.value + float(self.quality_weight) * roe.value
        )
        return context.result(value, inputs=(momentum, roe), formula=self.metadata.formula)
```

Use **Factor Lab → Import Factor** to register the local `.py` path. VQD validates syntax, SDK identity,
metadata, parameters, declared fields, lookback, point-in-time context capability, and stores a SHA-256
source fingerprint. The registration survives backend restarts and then uses the same Factor Engine,
Research/Validation/Holdout ledger, mixed-factor evaluation, strategy bridge, Backtest, Replay, and Paper
runtime as built-in factors. Parameters are always explicit; VQD does not sweep, optimize, search formulas,
or use AI to select them.

Factor source is **trusted local Python** and runs with the backend process permissions. VQD does not claim
or provide a security sandbox. The import API accepts a local filesystem path only and rejects non-local
clients; it does not accept remote HTTP Python uploads. Do not register untrusted source files.

## Native Strategy SDK

A strategy declares metadata and numeric parameters once. The backend turns them into the Strategy
Definition and frontend parameter controls. The runtime calls `initialize` once and `on_bar` once per newly
available synchronized market frame. The strategy returns portfolio intent; it cannot mutate cash, fills,
or equity.

This is a minimal complete native strategy. The repository's fuller, directly registerable version is
[`examples/sma_cross.py`](examples/sma_cross.py).

```python
from app.sdk import (
    DataRequirements,
    StrategyContext,
    StrategyMetadata,
    TargetPortfolioIntent,
    VQDStrategy,
    parameter,
)


class MovingAverageCross(VQDStrategy):
    metadata = StrategyMetadata(
        strategy_id="user.sma-cross",
        name="SMA Cross",
        version="1.0.0",
        description="Trace a fast/slow moving-average position.",
        data_requirements=DataRequirements(
            required_fields=("close",),
            symbols=("AAPL",),
            symbol_count=1,
            minimum_history=5,
        ),
    )
    fast_window = parameter(
        default=3, minimum=1, maximum=100, step=1,
        unit="bars", description="Responsive moving-average window.",
    )
    slow_window = parameter(
        default=5, minimum=2, maximum=250, step=1,
        unit="bars", description="Slow moving-average window.",
    )

    def initialize(self, context: StrategyContext) -> None:
        self._target = 0.0

    def on_bar(self, context: StrategyContext) -> TargetPortfolioIntent:
        fast_values = context.history(symbol="AAPL", bars=self.fast_window)
        slow_values = context.history(symbol="AAPL", bars=self.slow_window)
        fast_value = sum(fast_values) / len(fast_values)
        slow_value = sum(slow_values) / len(slow_values)
        fast = context.feature(
            name="fast_ma", value=fast_value, inputs=(fast_values,),
            formula="SMA(AAPL.close, fast_window)",
        )
        slow = context.feature(
            name="slow_ma", value=slow_value, inputs=(slow_values,),
            formula="SMA(AAPL.close, slow_window)",
        )
        ready = len(slow_values) == self.slow_window
        target = 100.0 if ready and fast_value > slow_value else 0.0
        self._target = target
        return context.target_positions(
            {"AAPL": target},
            reason="fast_ma above slow_ma" if target else "flat or warming up",
            dependencies=(fast, slow),
        )
```

`context.current`, `context.history`, `context.feature`, `context.condition`,
`context.target_positions`, and `context.target_weights` are the public decision surface. A feature may
reference market series or earlier feature references, so the runtime generates stable feature and market
dependency IDs without asking strategy authors to construct Trace Pydantic models.

## Register a Strategy

Install the backend package and register a trusted local file from the workspace in which VQD will run:

```bash
.venv/bin/vqd strategy add "$HOME/my-strategies/sma_cross.py"
.venv/bin/vqd strategy list
.venv/bin/vqd strategy remove user.sma-cross
```

Use `--class ClassName` only when the module contains multiple concrete `VQDStrategy` subclasses. The
registry stores the resolved absolute path, class, registration timestamp, and SHA-256 source fingerprint in
`.vqd/strategies.json`; it does not copy the source. IDs are validated and unique. Import, syntax, metadata,
parameter, and duplicate-ID errors are reported with the source path and useful local traceback.

## Import Data

Open **Data** in the app, select a UTF-8 CSV, inspect the preview, map source columns to canonical fields,
then commit the import. At minimum, map `timestamp`, `symbol`, and `close`; `open`, `high`, `low`, and
`volume` are optional. For example:

```text
date    → timestamp
ticker  → symbol
price   → close
```

The preview call is multipart data upload; the commit is deliberately a separate JSON request so the user
can inspect mapping and timezone before persistence:

```bash
curl -F 'file=@my_market_data.csv;type=text/csv' \
  http://127.0.0.1:8000/api/datasets/import/preview

curl -H 'Content-Type: application/json' -d '{
  "preview_id": "preview-...",
  "name": "My market data",
  "mapping": {"timestamp": "date", "symbol": "ticker", "close": "price"},
  "timezone": "America/New_York"
}' http://127.0.0.1:8000/api/datasets/import
```

Timezone-aware timestamps are converted to UTC while preserving their source-timezone label. Naive
timestamps require an explicit IANA timezone. Duplicate `(symbol, timestamp)` bars and missing/non-positive
closes are rejected; prices are never forward-filled. Reordering and multi-symbol alignment gaps remain
visible in the quality report. Runs use the strict intersection of symbol timestamps.

Normalized data and metadata are stored in `.vqd/datasets/<dataset-id>/`. Dataset IDs and SHA-256
fingerprints come from normalized semantic content, so display-name changes do not change the revision and
reimporting identical mapped content does not create duplicates.

## Real Market Data and Paper Validation

Open **My → Market data connection** to save and verify your own Alpaca credentials. The browser submits
them once and never receives the saved secret. The backend stores them in the encrypted workspace vault at
`.vqd/secrets/`. Environment variables remain available as a deployment-managed fallback:

```bash
export ALPACA_API_KEY='...'
export ALPACA_SECRET_KEY='...'
export ALPACA_DATA_FEED='iex' # or sip when entitled
```

Open **Data**, search by US stock symbol or company, inspect the provider snapshot, choose a UTC period and
frequency, then save the returned OHLCV bars as a VQD Dataset. The dataset records provider, feed, requested
symbols/period, retrieval time, market timestamp range, quality facts, and a semantic content fingerprint.
It can be selected by the existing Backtest workflow without a separate import step.

Open **Paper Trading** with a registered Native Strategy and explicitly choose an execution mode:

- **VQD simulated execution** keeps every order local and deterministically fills at the next bar close.
- **Alpaca Paper broker** sends market/day orders only to `paper-api.alpaca.markets`. Alpaca owns the
  submitted, partial-fill, fill, reject, and cancel lifecycle. This is still virtual money, but it is an
  external Paper account action.

VQD assigns every broker order a stable `client_order_id`, journals each cumulative broker update before
applying its incremental fill, and reconciles ambiguous submissions by that client ID. Restart recovery
replays market and broker journals in recorded order, then resumes REST reconciliation. The UI keeps the
boundary visible as **REAL MARKET DATA / ALPACA PAPER BROKER / NO REAL MONEY**, shows open-order progress,
supports cancel, and compares the VQD decision-time reference price with Alpaca's average Paper fill.

Stopping either mode freezes the observed PAPER Run and a deterministic VQD REFERENCE Run rebuilt from the
append-only recorded feed. The broker mode never makes the live Trading API hostname configurable; Phase 16
cannot route an order to a real-money Alpaca account.

In **Runs**, select one BACKTEST and one PAPER record and choose **Validate**. A historical comparison with a
different period or market path is `DESCRIPTIVE_ONLY`. Strict validation compares the REFERENCE and PAPER
traces layer by layer: data, feature, decision, order, execution, portfolio, and P&L. The first difference
retains both event IDs and both Replay links. P&L attribution never forces an unexplained difference into a
named bucket; remaining value stays `residual_unattributed`. Reports persist under `.vqd/validations/`.

## Point-in-Time Guarantee

The native strategy never receives the full dataset. Each callback gets an immutable market snapshot and a
history view ending at the current availability watermark. Market lineage records `source_timestamp`,
`available_at`, and `used_at`; execution remains signal at `close(t)` and fill at `close(t+1)`. Batch and
Forward both call the same incremental `StrategyRuntime`. Future-mutation tests change every unrevealed bar
and verify that earlier features, decisions, intents, orders, executions, positions, portfolio, and Trace are
unchanged.

## Security

Strategy files execute as trusted local Python code with the permissions of the VQD backend process. There
is no sandbox or plugin isolation. Do not register untrusted files, expose a strategy-loading backend to
untrusted users, or build a public Python-upload endpoint around this runtime. The documented development
command binds Uvicorn to `127.0.0.1`; this is a local safety boundary, not authentication.

Alpaca credentials are read from the encrypted workspace vault first, with `ALPACA_API_KEY`,
`ALPACA_SECRET_KEY`, and optional `ALPACA_DATA_FEED` as a fallback. The generated vault key and encrypted
payload are restricted to the operating-system user. Managed deployments may supply a valid Fernet key in
`VQD_SECRETS_MASTER_KEY`. Secrets are never written to SQLite, manifests, journals, traces, Dataset metadata,
API responses, or browser storage. Do not commit `.vqd/secrets/` or expose a credentialed VQD backend to an
untrusted network.

## Quant definitions and assumptions

These choices are part of the model, not hidden implementation details:

1. **Input data** — `sample_data/pairs_daily.csv` is a fixed, synthetic test dataset. Rows must be
   timezone-aware, strictly increasing, complete, and positive. Missing data is rejected; it is not
   silently forward-filled.
2. **Rolling regression** — the hedge ratio is an ordinary least-squares regression through the
   origin: `beta = dot(B, A) / dot(B, B)`. The current close is included in the rolling window.
3. **Spread** — `spread(t) = A(t) - beta(t) * B(t)`.
4. **Z-score** — the rolling mean and population standard deviation (`ddof=0`) use the latest
   `lookback` valid spreads. This creates an explicit two-window warm-up period.
5. **Signals** — enter short when `z > entry_z`, enter long when `z < -entry_z`, and close when
   `abs(z) < exit_z`. Signals are stateful; a position is held until a transition condition occurs.
6. **Execution timing** — a signal calculated at `close(t)` executes at `close(t+1)`. This is a
   deliberate simplification because the sample has closes only; no order uses same-bar execution.
7. **Position sizing** — each entry targets a fixed gross notional, split according to the current
   hedge ratio. Quantities may be fractional. Holdings are unchanged until the next transition.
8. **Fees** — `traded_notional * fee_bps / 10_000`, deducted from cash.
9. **Slippage** — buys fill above and sells below the next close by `slippage_bps / 10_000`.
   Reported slippage cost is `traded_notional * slippage_bps / 10_000`.
10. **P&L** — equity is `cash + quantity_A * price_A + quantity_B * price_B`. Gross P&L is net P&L
    plus cumulative fees and slippage, so the reconciliation is exact.
11. **Sharpe** — `mean(daily_returns) / sample_std(daily_returns) * sqrt(252)`, with risk-free rate
    set to zero. A zero-volatility series returns a Sharpe of zero.
12. **Drawdown** — `equity / running_max(equity) - 1`, including initial cash as the first point.
13. **Turnover** — total expected-price traded notional divided by average marked daily equity.
14. **Diagnostic train/test split** — ordered bars are split once at `floor(70% * N)`. The test
    decisions run in a single chronological pipeline and may use earlier train bars as feature
    history. Test return starts from equity immediately before the first test bar, so train P&L is
    not counted in the test window.
15. **Diagnostic execution delay** — `additional_delay=0/1/2` means execution at `t+1/t+2/t+3`.
    Every scenario reruns signal scheduling, orders, fills, costs, holdings, and P&L. Signals whose
    scheduled bar falls after the dataset remain explicitly unfilled; they are never forced onto the
    last bar.

The engine is deterministic: identical ordered bars and parameters produce an identical immutable
result. `backend/tests/fixtures/golden_backtest.json` pins the end-to-end output of the sample run.

## Quant Trace Protocol

Every `BacktestResult` now includes a `BacktestTrace`. The current `trace_version` is **`1.0`**.
The top-level model contains deterministic metadata, a strategy descriptor, parameters, one
`TimelineEvent` per bar, trade lifecycle records, final metrics, and structured diagnostics. It has
no generated timestamp or random UUID, so the same ordered dataset and parameters produce the same
semantic JSON.

Each timeline event records:

```text
MarketSnapshot
→ FeatureSnapshot[]
→ SignalEvaluation
→ PositionSnapshot
→ OrderEvent[]
→ ExecutionEvent[]
→ CostSnapshot
→ PnLSnapshot
```

Feature snapshots are first-class nodes. `hedge_ratio`, `spread`, `rolling_mean`, `rolling_std`, and
`zscore` retain their real Phase 1 values, formula descriptions, source windows, stable IDs, feature
inputs, and market-data dependency IDs. Formula strings are explanatory only; the Trace Builder
does not evaluate them. Signal conditions and previous/next target states are produced by the
Strategy Engine as structured domain results, then copied into the trace.

### Time and availability semantics

- Market closes have `source_timestamp` and `available_at` equal to their bar timestamp.
- A feature dependency has a separate `used_at`, equal to the decision bar timestamp.
- Signals retain their `decision_time = close(t)`.
- Under the current Phase 1 engine, orders are submitted and filled at `close(t+1)`; trace order and
  execution timestamps preserve that actual behavior.
- All timestamps are timezone-aware and serialize as ISO 8601.

A `DataDependency` identifies its source, field, optional symbol/value, source timestamp,
availability time, use time, and deterministic ID. Validation is based only on observed trace data:

```text
available_at > used_at  →  LOOK_AHEAD_WARNING
```

No AST or source-code guessing is performed. The golden trace has zero look-ahead warnings, while
the test suite injects a future dependency and verifies the structured warning.

### Accounting and serialization

Portfolio Accounting remains the P&L source of truth. The Trace Builder records marked equity and
derives period/cumulative deltas from those immutable portfolio snapshots; fees and slippage are
copied from actual executions. Tests reconcile every trace bar and final gross/net P&L against the
Phase 1 result.

Use `trace_to_json(trace)` and `trace_from_json(payload)` from `app.trace` for stable Pydantic JSON
serialization. `BacktestTrace → JSON → BacktestTrace` is covered by a semantic round-trip test.
`backend/tests/fixtures/golden_trace.json` stores a compact, human-reviewable projection covering
warm-up, no-action, entry, holding, exit, orders, executions, costs, and P&L.

## Phase 3 — Replay MVP

Replay directly consumes `BacktestTrace 1.0`; it does not import strategy implementation details or
recalculate hedge ratio, spread, rolling statistics, z-score, signals, position, costs, P&L, or
look-ahead diagnostics in the browser. The data flow is:

```text
Quant Engine → BacktestTrace 1.0 → Replay API → strict TypeScript types → Replay UI
```

The current Replay experience provides:

- An SVG equity timeline sourced from `PnLSnapshot.equity`, with selected, signal, and execution markers
- Previous/next bar and previous/next real-signal navigation with disabled boundary states
- Market and `PositionSnapshot` inspectors for every bar, including WARMUP and HOLD evaluations
- Structured decision conditions and state transitions from `SignalEvaluation`
- Recursive, expandable feature lineage built from feature IDs and `inputs`, with cycle and missing-ID fallbacks
- A feature inspector for the recorded value, formula text, inputs, window, and UTC availability time
- A “What Did It Know?” panel using recorded `DataDependency` timestamps and backend diagnostics
- Bidirectional signal → order → execution navigation, plus recorded costs and P&L
- Explicit loading, API error, malformed-trace, empty-trace, no-signal, and no-execution states

The execution assumption remains unchanged: a decision at `close(t)` executes at `close(t+1)`.
Replay intentionally keeps those events on separate dates because that timing difference is part of
the trace evidence.

## Phase 4 — Strategy Anatomy

Strategy Anatomy is an explanatory inspector, not a visual execution engine or workflow builder.
Its calculation spine, concept descriptions, formulas, relationships, parameter metadata, presets,
validation rules, and execution assumptions come from the backend `StrategyDefinition`. Formulas are
display text only; the Quant Engine remains the calculation source of truth.

The Pairs Trading anatomy follows the implemented system:

```text
Market Data
→ Rolling Regression
→ Hedge Ratio
→ Spread
→ Rolling Mean / Rolling Std
→ Z-score
→ Signal Rules
→ Target Position
→ Execution
```

There are two explicitly different backend presets:

- **Strategy Default** — `lookback=60`, `entry_z=2.0`, `exit_z=0.5`, `fee_bps=5`,
  `slippage_bps=5`. These values are derived from the current Quant Engine defaults.
- **Demo: Active Signals** — `lookback=5`, `entry_z=1.0`, `exit_z=0.8`, `fee_bps=5`,
  `slippage_bps=5`. This preserves the short golden Replay walkthrough; it is not presented as the
  production default.

Preset selection and numeric edits update draft parameters only. The browser performs immediate
basic validation from the definition, while the backend repeats validation as the final authority.
Only **Run Backtest** performs computation. The returned `trace_id` is retained, and **Open Replay**
loads it directly; Replay displays parameters from `BacktestTrace.parameters`, not Strategy page state.

## Phase 5 — Diagnose

Diagnose is bound to the active run context and returns a typed, deterministic `DiagnosisReport`.
The backend remains the calculation authority. The frontend renders paired metrics and SVG
comparisons; it does not calculate return, Sharpe, drawdown, turnover, trades, or final equity.

The report contains:

- **Train/Test** — the exact chronological 70/30 split and its feature-history/P&L-isolation policy
- **Lookback sensitivity** — 5–9 deterministic candidates valid for the train length, with the
  current value included when valid; each point is a full backtest
- **Cost stress** — total friction at 0/5/10/15/20 bps, split into fee and slippage using the source
  run's ratio (50/50 when both source values are zero); each point is a full backtest
- **Execution delay** — full-engine t+1/t+2/t+3 comparisons plus end-of-data unfilled counts
- **Observations** — cautious, deterministic descriptions of the returned evidence; no model-based
  claims or generated explanation

Statuses such as `NO_TRADES`, `INSUFFICIENT_DATA`, and `UNDEFINED_SHARPE` are explicit. API payloads
never serialize NaN or infinity.

## Phase 6 — P&L Autopsy

P&L Autopsy uses `BacktestTrace.timeline`, `CostSnapshot`, and `TradeTrace` as its only accounting
inputs. It aggregates and ranks those immutable records; it does not run a second portfolio ledger.

- Summary reconciliation checks both `gross - fees - slippage = net` and
  `initial equity + net = final equity`.
- Monthly, quarterly, and yearly buckets use UTC event timestamps. Period return is
  `end_equity / start_equity - 1`.
- A trade receives event P&L and costs from its entry execution event through its exit execution
  event, inclusive. Open trades continue through the final event. Best/Worst rankings include only
  closed trades and sort on attributed net P&L.
- Any amount not covered by a strict trade interval is exposed as `unattributed_net_pnl`; it is not
  silently assigned.
- Drawdown episodes record the peak, first below-peak event, trough, optional recovery at or above
  the peak, depth, duration, and recovery bars. Each important event can be opened in Replay.

## Phase 6.5 — Professional Product UI

The product surface is now organized as one research workspace instead of four isolated demo pages.
Desktop uses a persistent sidebar and a compact run-context bar. Large teaching heroes, oversized cards,
large CTA-style controls, and presentation-style status decoration are de-emphasized. Quant explanations
remain available in inspectors and disclosures, but the default surface prioritizes trace selection,
configuration, comparison, tables, execution evidence, and attribution.

Diagnostic metric states are also treated semantically: `NO_TRADES`, `INSUFFICIENT_DATA`, and
`UNDEFINED_SHARPE` render Sharpe as `N/A` instead of presenting the finite backend placeholder `0` as a
meaningful Sharpe value.

## Phase 7 — Forward Validation

Forward Validation is not a live broker integration. `sample_data/forward_pairs_daily.csv` is a separate,
deterministic holdout segment. `HistoricalBarFeed` reveals exactly one new bar per `step`, maintains an
availability watermark, and explicitly rejects future-bar access. The same Pairs Trading feature and signal
semantics are reused by batch backtests and forward sessions.

Session lifecycle is in-memory and explicit: `CREATED → RUNNING ↔ PAUSED → COMPLETED`, with `STOPPED`
as a terminal user action. A signal produced at `close(t)` becomes a pending transition and is filled only
when `close(t+1)` arrives. End-of-data transitions expire rather than being forced onto the last bar.
Portfolio state, fees, slippage, equity, and trace events are updated only from revealed bars.

The Forward workspace separates two comparisons:

- **Historical research vs Forward holdout** uses different evaluation periods and is descriptive evidence
  about generalization, not an “expected vs actual” execution claim.
- **Batch vs Streaming Consistency** runs the currently revealed forward path through the ordinary batch
  engine and compares signal sequence, executions, fees, slippage, net P&L, and final equity. A mismatch is
  surfaced as `DIVERGENCE` with the first differing field.

Historical Forward sessions are ephemeral and intentionally driven by frontend `step` requests. Live Paper
uses the separate persistent Phase 10 runtime described below.

## Phase 8 — Open Strategy Runtime & Data Workspace

Phase 8 is complete. Registered `VQDStrategy` classes and imported CSV datasets travel through the same
incremental runtime, execution engine, portfolio accounting, Trace 1.0, Replay, Diagnose, P&L Autopsy, and
Forward Validation boundaries as built-in Pairs Trading. The research cutoff separates research bars
(`<= cutoff`) from an incrementally revealed Forward holdout (`> cutoff`); the holdout is not a parameter
selection input. Trace and session storage remains in memory, while source and data registries are durable.

## Live Paper Trading (Phase 10)

Open **Forward → LIVE PAPER** to run a registered Python strategy against real Alpaca stock market data with
virtual money. Alpaca is a market-data provider only. Orders, fills, cash, positions, fees, slippage, P&L,
equity, and Trace lineage remain inside VQD; the backend never calls an Alpaca order endpoint or Alpaca Paper
Broker.

Configure Alpaca from **My → Market data connection**. Server-managed deployments can instead configure the
backend environment before launch:

```bash
export ALPACA_API_KEY='...'
export ALPACA_SECRET_KEY='...'
export ALPACA_DATA_FEED='iex'  # or sip when the account is entitled
```

The first live resolution is fixed at finalized **1-minute bars** in the **US regular session**. The official
Alpaca clock and calendar define market-open boundaries and early closes. The execution contract remains:

```text
minute bar t becomes available
→ strategy decision at close(t)
→ local pending paper order
→ simulated fill at close(t+1)
```

No quote, same-bar, tick, or broker fill is substituted. The browser connects only to the local VQD SSE
endpoint; it never receives Alpaca credentials or connects to Alpaca directly.

### IEX and SIP

`ALPACA · IEX` is real market data from the IEX exchange, but it is not the complete consolidated US market.
`ALPACA · SIP` consolidates US exchange data and requires the corresponding Alpaca entitlement. Provider and
feed are persisted on every Paper Session, displayed in the workspace, and passed to both WebSocket and REST
gap-backfill requests. See Alpaca's [Market Data FAQ](https://docs.alpaca.markets/docs/market-data-faq).

### Persistence, corrections, and recovery

Mutable session metadata shares `.vqd/vqd.sqlite` with the Research Ledger but uses an independent
`paper_sessions` table. Artifacts live under `.vqd/paper-sessions/<session-id>/`:

```text
manifest.json
strategy.py
market-events.jsonl
trace.json
```

The exact strategy source is snapshotted at creation. Every normalized bar, duplicate, rejected out-of-order
event, skipped paused evaluation, and late revision is recorded in received order. Backend restart recovery
loads the snapshot and deterministically replays the journal rather than serializing arbitrary Python object
state. The reconstructed portfolio and semantic Trace hashes must match the persisted checkpoint; otherwise
the session enters `ERROR / RECOVERY_MISMATCH` and does not continue silently.

Alpaca `updatedBars` are new point-in-time market revisions. VQD never rewrites the already emitted decision,
order, execution, P&L, or TimelineEvent. The original decision retains the revision it actually saw, while
future strategy history may use the corrected bar. The UI reports both values and when the later revision
became available.

Pausing suppresses `strategy.on_bar` and new strategy orders, but real bars continue into the journal and
Trace as `EVALUATION_SKIPPED_PAUSED`. A pending order emitted before the pause may still execute on its due
completed minute. Resume does not invent or backfill decisions for paused minutes. Disconnects create no fake
prices or decisions; reconnect performs chronological historical 1Min backfill and exactly-once deduplication
before live subscription continues.

### Paper execution limitations

VQD paper execution is a deterministic local research model. It does **not** model real queue position,
market impact, real broker routing, partial-fill liquidity, exchange/broker rejection, or
network-to-exchange latency. Observed market-data delivery latency in the UI is not execution latency. Paper
results must not be described as identical to live trading.

## Roadmap

- **Phase 8 — Open Strategy Runtime & Data Workspace: COMPLETE**
- **Phase 9 — Research Ledger & Run Comparison: COMPLETE**
- **Phase 10 — Live Paper Trading Runtime: COMPLETE**
- **Phase 11 — Framework Adapter: READY FOR DESIGN, NOT IMPLEMENTED**

## Deliberate non-goals for Phase 10

Phase 10 has no real broker orders, Alpaca Paper Broker orders, IBKR, manual Buy/Sell controls, limit orders,
tick or quote strategies, order-book simulation, market-impact model, additional live providers, crypto,
options, framework adapters, remote Python upload, process sandbox, authentication, cloud deployment,
multi-user state, automatic optimization, or AI/LLM explanation.
