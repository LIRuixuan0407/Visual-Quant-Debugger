<h1 align="center">Visual Quant Debugger</h1>

<p align="center">
  <strong>Build quant ideas. Validate the evidence. Replay every decision.</strong>
</p>

<p align="center">
  An evidence-first workspace for building, validating, replaying, and debugging quantitative strategies.
</p>

<p align="center">
  <a href="https://visual-quant-debugger-production.up.railway.app"><strong>Live Demo</strong></a>
  ·
  <a href="README.zh.md">中文</a>
  ·
  <a href="https://github.com/LIRuixuan0407/Visual-Quant-Debugger/actions/workflows/ci.yml">CI</a>
</p>

---

## Why VQD exists

A backtest can tell you that a strategy made money.

It usually does not tell you enough about **why** it made money, **what information was available at each decision**, **where the result first became unstable**, or **whether the idea survived outside the window where it was created**.

Visual Quant Debugger (VQD) is built around those questions.

Instead of treating a backtest as the final answer, VQD treats every strategy idea as a research object that should leave behind its data version, factor evidence, validation boundary, execution path, trace, and result.

The goal is not to automatically find the highest historical Sharpe ratio.

The goal is to make quantitative research **inspectable, reproducible, and harder to fool yourself with**.

## What you can do

VQD connects the full research workflow in one place:

```text
Market Data
    ↓
Factor Research
    ↓
Validation / Holdout
    ↓
Multi-Factor Portfolio
    ↓
Walk-Forward Stability
    ↓
Factor Relationships
    ↓
Research Hypothesis
    ↓
Native Strategy
    ↓
Backtest
    ↓
Trace / Replay
    ↓
Diagnose / P&L Autopsy
    ↓
Forward Validation
    ↓
Paper Trading
```

Every step keeps the evidence and lineage needed to understand how the next step was produced.

## 5-minute guided demo

The built-in Pairs Trading sample requires no API key. Start on **Strategy**, run **Run guided demo**, then follow the same recorded Trace through **Replay → Diagnose → P&L Autopsy**.

The point of the demo is not to showcase a profitable backtest. It is to show how VQD exposes the assumptions behind one: exact decisions, execution timing, train/test degradation, parameter and friction sensitivity, statistical and volatility evidence, market-regime dependence, and the resulting **Strategy Failure Fingerprint**.

This is the shortest path to understanding the product:

```text
Run bundled sample
→ Replay the exact decisions
→ Inspect Diagnose / Failure Fingerprint
→ Trace losses in P&L Autopsy
```

## Product areas

### Historical Market

Work with real US-equity history or imported datasets and inspect the market as it was known at a selected date.

VQD keeps the dataset revision, timestamps, universe information, and available fundamental records attached to the research instead of treating data as an invisible input.

### Factor Lab

Test price/volume, fundamental, mixed, or custom factors with explicit point-in-time boundaries.

Research includes IC, Rank IC, quantile returns, spread, coverage, turnover, and staged Research / Validation / Holdout evidence.

Holdout data is not silently used to improve the idea.

### Portfolio Lab

Combine existing factor studies into a transparent multi-factor portfolio.

Choose the factors, directions, weights, ranking method, filters, selection rule, position weighting, rebalance schedule, and position cap. The backend records how every portfolio position was produced from factor evidence.

No hidden optimizer is required. Portfolio Risk Decomposition keeps portfolio volatility, covariance/correlation, historical VaR / Expected Shortfall, and component risk contribution visible alongside the chosen weights.

### Walk-Forward

Measure whether an idea stays stable as time moves forward.

VQD evaluates fixed research definitions across rolling Research, Validation, and Forward windows and reports where factor or strategy behavior first degrades.

### Factor Relationships

See whether two apparently different factors are actually expressing the same thing.

Compare factor values, ranks, factor returns, rolling correlations, top-quantile overlap, Jaccard similarity, redundancy, incremental information, and factor clusters.

VQD reports association and overlap; it does not automatically delete factors or re-optimize the portfolio. A PCA view also exposes latent factor structure through explained variance and factor loadings when enough aligned history is available.

### Strategy Discovery

Turn existing research evidence into an explicit hypothesis.

A hypothesis keeps:

- the factors it came from;
- supporting and contradicting evidence;
- expected relationship and holding horizon;
- validation and holdout state;
- revision history;
- portfolio and strategy lineage;
- resulting Runs and Traces.

Changing an idea after seeing new evidence creates a new revision instead of rewriting history.

### Research Snapshots

Freeze one completed research chain as an immutable, content-verified record.

A Research Snapshot preserves the exact Dataset revision, Factor and Strategy revisions, parameters, Research / Validation / Holdout boundaries, Hypothesis, Portfolio, Run and Trace artifacts, and creation environment. Every embedded artifact has its own source revision and frozen-payload hash, so later source changes cannot silently rewrite the saved experiment.

Snapshots are append-only. Creating a different experiment creates a new identity instead of updating an existing Snapshot.

Select two to four Snapshots in **Experiment Compare** to inspect controlled context, artifact revisions, parameter changes, frozen Portfolio and primary Run results, Hypothesis evidence states, and recorded Run / Trace behavior side by side. The comparison reads only content-verified frozen payloads and reuses the Run Comparison contract; it describes differences without selecting a winner, optimizing parameters, or making a recommendation.

### Replay

Replay a completed strategy run bar by bar.

Inspect the market snapshot, feature values, decision conditions, target position, execution, costs, P&L, and the data dependencies that were available at that moment.

This is the part of VQD that makes a quantitative strategy feel debuggable rather than opaque.

### Diagnose

Stress one recorded run without losing its original context.

Inspect chronological train/test behavior, interactive parameter sensitivity, transaction-cost stress, execution-delay scenarios, statistical diagnostics, historical/EWMA volatility, market-regime behavior, What-if scenarios, and a deterministic Strategy Failure Fingerprint.

The fingerprint summarizes *where a strategy is fragile* without using opaque AI scores: each severity links back to explicit evidence and calculation details.

### P&L Autopsy

Break a result down instead of stopping at one performance number.

Trace gross P&L, fees, slippage, trade attribution, open positions, and drawdown episodes back to the underlying strategy events.

### Forward & Paper

Move a strategy beyond historical batch backtesting.

VQD supports incremental forward validation, persistent simulated portfolios, real Alpaca market data, and optional Alpaca Paper broker execution.

Real-money trading is intentionally outside the product scope.

## What makes VQD different

### Evidence before conclusions

VQD stores both supporting and contradicting evidence. A strategy is not promoted just because one historical metric looks good.

### Point-in-time research

The system tracks when data became available and when it was used. Validation and forward-return endpoints are kept inside their own evaluation windows.

### Explicit holdout

Holdout evidence stays sealed until the user reveals it. The system does not automatically use Holdout to search for a winner.

### Research lineage

A result can be traced back through:

```text
Dataset
→ Factor
→ Factor Research
→ Portfolio
→ Hypothesis
→ Strategy
→ Run
→ Trace
```

### Debuggable execution

A run is more than an equity curve. VQD records features, decisions, positions, orders, executions, costs, and P&L so the path can be replayed later.

### No automatic winner machine

VQD is not an "AI finds alpha" product.

It does not automatically mass-search parameters, optimize every window, reveal Holdout, select a winner, or deploy real-money strategies.

Quantitative results come from deterministic backend calculations; AI, when used, is limited to explaining already available research evidence.

## Research records, not disposable runs

Backtests, factor studies, portfolios, hypotheses, traces, diagnostics, and paper sessions are persisted as research records.

That means you can return later and answer questions such as:

- Which dataset revision produced this result?
- Which factor revision was used?
- What changed between two runs?
- Where did behavior first diverge?
- Did the idea survive Validation and Holdout?
- What did the strategy know when it made this trade?
- How much of the result came from costs or execution timing?

VQD is designed so those questions do not depend on memory or screenshots.

## Live demo

The hosted application is available here:

**https://visual-quant-debugger-production.up.railway.app**

The public deployment is intended for product exploration. External market-data features may require your own provider credentials.

## Run locally

The simplest local path is Docker:

```bash
git clone https://github.com/LIRuixuan0407/Visual-Quant-Debugger.git
cd Visual-Quant-Debugger
docker compose up --build
```

Then open:

```text
http://localhost:8000
```

VQD stores persistent workspace data under `.vqd` locally. In the provided container setup, the persistent workspace is mounted at `/data`.

For real Alpaca market data or SEC filing downloads, configure the optional provider settings in `.env.example`.

## Strategy and Factor extensibility

VQD includes native strategy and factor SDKs for trusted local Python research.

Custom factors can participate in the same Factor Research, Validation, Portfolio, Walk-Forward, Relationship, Discovery, and Research Snapshot workflow as built-in factors.

Custom native strategies can use the same Backtest, Trace, Replay, Diagnose, Autopsy, Forward, and Paper workflow as built-in strategies.

Optional historical-research adapters are also available for `backtesting.py` and `vectorbt`, with explicit capability and trace-fidelity labels.

## Product principles

VQD follows a few deliberately strict rules:

- the frontend displays quantitative results; it does not recompute them;
- research and execution should use the same strategy semantics;
- unavailable evidence is shown as unavailable, not guessed;
- Holdout is explicit;
- revisions preserve history;
- losses and failed hypotheses are valid research outcomes;
- real-money trading is out of scope.

## Current scope

VQD is an active open-source project focused on a personal, evidence-first quantitative research workspace.

The current product covers historical market research, factors, multi-factor portfolios with risk decomposition, walk-forward validation, factor relationships with PCA structure, hypothesis-driven strategy discovery, immutable Research Snapshots, backtesting, replay, statistical / volatility / regime diagnostics, Strategy Failure Fingerprints, P&L attribution, forward validation, and paper trading.

It should be treated as research software, not investment advice or a promise of strategy profitability.

---

<p align="center">
  <strong>Don't just backtest your strategy. Debug it.</strong>
</p>
