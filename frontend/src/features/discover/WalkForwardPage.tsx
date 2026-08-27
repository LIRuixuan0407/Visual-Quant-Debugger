import { useEffect, useMemo, useState } from 'react'

import { getFactorResearchList } from '../../api/factors'
import { createWalkForward, getWalkForwardList } from '../../api/research'
import { useI18n } from '../../i18n/I18nProvider'
import type { FactorResearchSummary } from '../../types/factor'
import type { FactorWindowMetrics, WalkForwardResearchRecord } from '../../types/research'
import type { StrategyDefinition } from '../../types/strategy'

const pct = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(2)}%`
const num = (value: number | null | undefined) => value == null ? '—' : value.toFixed(4)
const date = (value: string) => value.slice(0, 10)

function FactorMetrics({ label, metrics }: { label: string; metrics: FactorWindowMetrics }) {
  const { tr } = useI18n()
  return <div className="wf-phase-card">
    <div className="wf-phase-title"><strong>{tr(label)}</strong><span className={metrics.monotonic ? 'safe' : ''}>{tr(metrics.monotonic ? 'MONOTONIC' : 'NON-MONOTONIC')}</span></div>
    <dl>
      <div><dt>IC</dt><dd>{num(metrics.ic)}</dd></div>
      <div><dt>{tr('Rank IC')}</dt><dd>{num(metrics.rank_ic)}</dd></div>
      <div><dt>{tr('Coverage')}</dt><dd>{pct(metrics.coverage)}</dd></div>
      <div><dt>{tr('Turnover')}</dt><dd>{pct(metrics.turnover)}</dd></div>
      <div><dt>Q5−Q1</dt><dd>{pct(metrics.spread)}</dd></div>
      <div><dt>{tr('Observations')}</dt><dd>{metrics.observation_count}</dd></div>
    </dl>
    <div className="wf-quantiles">{metrics.quantile_returns.map((value, index) => <span key={index}><small>Q{index + 1}</small><code>{pct(value)}</code></span>)}</div>
  </div>
}

export default function WalkForwardPage({
  strategies,
  onOpenHistorical,
  onOpenFactor,
  onOpenReplay,
  onRunComplete,
}: {
  strategies: StrategyDefinition[]
  onOpenHistorical: (path: string) => void
  onOpenFactor: (path: string) => void
  onOpenReplay: (traceId: string, path: string) => void
  onRunComplete: (traceId: string, runId?: string) => void
}) {
  const { tr } = useI18n()
  const [factors, setFactors] = useState<FactorResearchSummary[]>([])
  const [records, setRecords] = useState<WalkForwardResearchRecord[]>([])
  const [factorId, setFactorId] = useState('')
  const [strategyId, setStrategyId] = useState('')
  const [researchMonths, setResearchMonths] = useState(12)
  const [validationMonths, setValidationMonths] = useState(3)
  const [forwardMonths, setForwardMonths] = useState(3)
  const [stepMonths, setStepMonths] = useState(3)
  const [horizon, setHorizon] = useState<1 | 5 | 20>(20)
  const [feeBps, setFeeBps] = useState(5)
  const [slippageBps, setSlippageBps] = useState(5)
  const [record, setRecord] = useState<WalkForwardResearchRecord | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const initialWalkForwardId = new URLSearchParams(window.location.search).get('walk_forward_id')

  const nativeStrategies = useMemo(
    () => strategies.filter((item) => !item.historical_research_only && item.available !== false),
    [strategies],
  )

  useEffect(() => {
    let mounted = true
    void Promise.all([getFactorResearchList(), getWalkForwardList()]).then(
      ([factorRows, walkForwardRows]) => {
        if (!mounted) return
        setFactors(factorRows)
        setRecords(walkForwardRows)
        setFactorId((current) => current || factorRows[0]?.research_id || '')
        setRecord(walkForwardRows.find((item) => item.walk_forward_id === initialWalkForwardId) ?? walkForwardRows[0] ?? null)
      },
      (reason) => mounted && setError(reason instanceof Error ? reason.message : String(reason)),
    )
    return () => { mounted = false }
  }, [initialWalkForwardId])

  async function run() {
    if (!factorId) return
    setBusy(true)
    setError(null)
    try {
      const selectedFactor = factors.find((item) => item.research_id === factorId)
      const next = await createWalkForward({
        name: `Walk-Forward · ${selectedFactor?.name ?? factorId}`,
        factor_research_id: factorId,
        strategy_id: strategyId || null,
        config: {
          research_months: researchMonths,
          validation_months: validationMonths,
          forward_months: forwardMonths,
          step_months: stepMonths,
          start: null,
          end: null,
        },
        horizon,
        initial_cash: 100_000,
        fee_bps: feeBps,
        slippage_bps: slippageBps,
        strategy_parameters: {},
      })
      setRecord(next)
      setRecords((current) => [next, ...current.filter((item) => item.walk_forward_id !== next.walk_forward_id)])
      if (next.trace_id) onRunComplete(next.trace_id, next.run_id ?? undefined)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  return <main className="discover-shell research-workbench walk-forward-lab">
    <section className="discover-title">
      <div><span className="section-kicker">{tr('Stability research')}</span><h1>{tr('Walk-Forward')}</h1><p>{tr('Measure factor and strategy stability across rolling Research, Validation, and Forward windows.')}</p></div>
      <span className="bias-tag">PIT SAFE · NO OPTIMIZER</span>
    </section>

    {error && <section className="workspace-panel research-error" role="alert">{tr(error)}</section>}

    <section className="wf-workspace-grid">
      <aside className="workspace-panel wf-ledger">
        <div className="section-heading"><h2>{tr('Walk-Forward research')}</h2><span>{records.length}</span></div>
        {records.length === 0 && <p className="empty-copy">{tr('No Walk-Forward research yet.')}</p>}
        {records.map((item) => <button key={item.walk_forward_id} className={record?.walk_forward_id === item.walk_forward_id ? 'selected' : ''} onClick={() => setRecord(item)}>
          <strong>{item.name}</strong><span>{item.windows.length} {tr('forward windows')} · {item.horizon}D</span><small>{item.factor_id} · {item.strategy_id ?? tr('Factor only')}</small>
        </button>)}
      </aside>

      <div className="wf-stack">
        <section className="workspace-panel wf-builder">
          <div className="section-heading"><div><span className="section-kicker">HISTORICAL DATA → REPEAT → STABILITY REPORT</span><h2>{tr('Configure rolling windows')}</h2></div><span className="long-only-badge">BACKEND GENERATED</span></div>
          <div className="wf-chain" aria-label={tr('Walk-Forward evaluation chain')}><span>{tr('Historical Data')}</span><i>→</i><span>{tr('Research Window')}</span><i>→</i><span>{tr('Validation Window')}</span><i>→</i><span>{tr('Forward Window')}</span><i>→</i><span>{tr('Advance & Repeat')}</span></div>
          <div className="wf-form-grid">
            <label><span>{tr('Factor research')}</span><select aria-label={tr('Factor research')} value={factorId} onChange={(event) => setFactorId(event.target.value)}><option value="">—</option>{factors.map((item) => <option key={item.research_id} value={item.research_id}>{item.name} · {item.factor_id}</option>)}</select></label>
            <label><span>{tr('Native strategy (optional)')}</span><select aria-label={tr('Native strategy (optional)')} value={strategyId} onChange={(event) => setStrategyId(event.target.value)}><option value="">{tr('Factor only')}</option>{nativeStrategies.map((item) => <option key={item.strategy_id} value={item.strategy_id}>{item.name}</option>)}</select></label>
            <label><span>{tr('Forward horizon')}</span><select value={horizon} onChange={(event) => setHorizon(Number(event.target.value) as 1 | 5 | 20)}><option value={1}>1D</option><option value={5}>5D</option><option value={20}>20D</option></select></label>
            <label><span>{tr('Research months')}</span><input aria-label={tr('Research months')} type="number" min="1" value={researchMonths} onChange={(event) => setResearchMonths(Number(event.target.value))} /></label>
            <label><span>{tr('Validation months')}</span><input aria-label={tr('Validation months')} type="number" min="1" value={validationMonths} onChange={(event) => setValidationMonths(Number(event.target.value))} /></label>
            <label><span>{tr('Forward months')}</span><input aria-label={tr('Forward months')} type="number" min="1" value={forwardMonths} onChange={(event) => setForwardMonths(Number(event.target.value))} /></label>
            <label><span>{tr('Step months')}</span><input aria-label={tr('Step months')} type="number" min="1" value={stepMonths} onChange={(event) => setStepMonths(Number(event.target.value))} /></label>
            <label><span>{tr('Fee bps')}</span><input type="number" min="0" value={feeBps} onChange={(event) => setFeeBps(Number(event.target.value))} /></label>
            <label><span>{tr('Slippage bps')}</span><input type="number" min="0" value={slippageBps} onChange={(event) => setSlippageBps(Number(event.target.value))} /></label>
          </div>
          <div className="builder-footer"><p>{tr('Each horizon endpoint must remain inside its own evaluation window. No optimization, parameter search, Monte Carlo, bootstrap, or AI is used.')}</p><button className="primary-button" disabled={busy || !factorId} onClick={() => void run()}>{tr(busy ? 'Calculating…' : 'Run Walk-Forward')}</button></div>
        </section>

        {record && <>
          <section className="workspace-panel wf-stability">
            <div className="section-heading"><div><span className="section-kicker">STABILITY REPORT · {record.windows.length} WINDOWS</span><h2>{record.name}</h2></div><code>{record.walk_forward_id}</code></div>
            <div className="wf-identity"><span>{record.factor_id}</span><code>{record.factor_revision}</code>{record.strategy_id && <><span>{record.strategy_id}</span><code>{record.strategy_revision}</code></>}</div>
            <div className="metric-strip wf-stability-metrics">
              <div><span>{tr('Positive IC windows')}</span><strong>{pct(record.stability.positive_ic_window_ratio)}</strong></div>
              <div><span>{tr('Rank IC mean')}</span><strong>{num(record.stability.rank_ic_distribution.mean)}</strong></div>
              <div><span>{tr('Rank IC std')}</span><strong>{num(record.stability.rank_ic_distribution.std)}</strong></div>
              <div><span>{tr('Factor sign consistency')}</span><strong>{pct(record.stability.factor_sign_consistency)}</strong></div>
              <div><span>{tr('Quantile monotonicity stability')}</span><strong>{pct(record.stability.quantile_monotonicity_stability)}</strong></div>
              <div><span>{tr('Turnover stability')}</span><strong>{pct(record.stability.turnover_stability)}</strong></div>
              <div><span>{tr('Strategy return mean')}</span><strong>{pct(record.stability.strategy_return_distribution?.mean)}</strong></div>
              <div><span>{tr('Strategy return range')}</span><strong>{record.stability.strategy_return_distribution ? `${pct(record.stability.strategy_return_distribution.minimum)} → ${pct(record.stability.strategy_return_distribution.maximum)}` : '—'}</strong></div>
            </div>
          </section>

          <section className={`workspace-panel wf-degradation ${record.first_degradation ? 'found' : 'stable'}`}>
            <div><span className="section-kicker">FIRST DEGRADATION</span><h2>{record.first_degradation ? `${tr('Window')} ${record.first_degradation.window_index} · ${date(record.first_degradation.timestamp)}` : tr('No deterministic degradation found')}</h2></div>
            {record.first_degradation ? <><div className="wf-reasons">{record.first_degradation.reasons.map((reason) => <code key={reason}>{tr(reason)}</code>)}</div><div className="stage-actions"><button className="secondary-button" onClick={() => onOpenHistorical(record.first_degradation!.historical_market_path)}>{tr('Open Historical Market')}</button><button className="secondary-button" onClick={() => onOpenFactor(record.first_degradation!.factor_lab_path)}>{tr('Open Factor Lab')}</button>{record.trace_id && record.first_degradation.replay_path && <button className="primary-button" onClick={() => onOpenReplay(record.trace_id!, record.first_degradation!.replay_path!)}>{tr('Open Replay')}</button>}</div></> : <p>{tr('All forward windows remained inside the configured deterministic degradation rules.')}</p>}
          </section>

          <section className="wf-window-list">
            {record.windows.map((window) => <article className="workspace-panel wf-window" key={window.definition.index}>
              <header><div><span className="section-kicker">WINDOW {window.definition.index}</span><h2>{date(window.definition.research.start)} → {date(window.definition.forward.end)}</h2></div><div className="wf-window-periods"><span>R {date(window.definition.research.start)}–{date(window.definition.research.end)}</span><span>V {date(window.definition.validation.start)}–{date(window.definition.validation.end)}</span><span>F {date(window.definition.forward.start)}–{date(window.definition.forward.end)}</span></div></header>
              <div className="wf-factor-grid"><FactorMetrics label="Research" metrics={window.research} /><FactorMetrics label="Validation" metrics={window.validation} /><FactorMetrics label="Forward" metrics={window.forward} /></div>
              {window.forward_strategy && <div className="wf-strategy-row"><span><small>{tr('Strategy return')}</small><strong>{pct(window.forward_strategy.total_return)}</strong></span><span><small>Sharpe</small><strong>{window.forward_strategy.sharpe.toFixed(2)}</strong></span><span><small>{tr('Max drawdown')}</small><strong>{pct(window.forward_strategy.max_drawdown)}</strong></span><span><small>{tr('Trades')}</small><strong>{window.forward_strategy.trades}</strong></span><span><small>{tr('Fees')}</small><strong>${window.forward_strategy.fees.toFixed(2)}</strong></span><span><small>{tr('Slippage')}</small><strong>${window.forward_strategy.slippage.toFixed(2)}</strong></span><span><small>{tr('Net costs')}</small><strong>${window.forward_strategy.net_costs.toFixed(2)}</strong></span></div>}
            </article>)}
          </section>
        </>}
      </div>
    </section>
  </main>
}
