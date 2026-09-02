import { useCallback, useEffect, useState, type FormEvent } from 'react'

import { createDiagnosis, createWhatIfScenario } from '../../api/diagnostics'
import { useI18n } from '../../i18n/I18nProvider'
import type { DiagnosisReport, DiagnosticMetrics, ReturnDiagnostics, VolatilityDiagnostics, WhatIfMetrics, WhatIfScenario } from '../../types/diagnostics'
import { formatCurrency } from '../replay/utils/format'

function percent(value: number) { return `${(value * 100).toFixed(2)}%` }
function decimal(value: number) { return value.toFixed(2) }
function sharpeDisplay(metrics: DiagnosticMetrics) { return metrics.status === 'OK' ? decimal(metrics.sharpe) : 'N/A' }
function diagnosticValue(value: number | null, digits = 3) { return value === null ? 'N/A' : value.toFixed(digits) }

function ReturnAcfChart({ diagnostics }: { diagnostics: ReturnDiagnostics }) {
  const { tr } = useI18n()
  const values = [...diagnostics.return_acf, ...diagnostics.squared_return_acf]
    .flatMap((point) => point.value === null ? [] : [Math.abs(point.value)])
  const extent = Math.max(0.1, ...values)
  const zeroY = 108
  const scaledY = (value: number) => zeroY - value / extent * 72
  const bar = (value: number | null) => value === null
    ? { y: zeroY, height: 0 }
    : { y: Math.min(zeroY, scaledY(value)), height: Math.abs(scaledY(value) - zeroY) }

  return <svg className="acf-chart" viewBox="0 0 920 220" role="img" aria-label={tr('Return and squared return autocorrelation by lag')}>
    <line className="acf-gridline" x1="48" x2="884" y1="36" y2="36" />
    <line className="acf-zero-line" x1="48" x2="884" y1={zeroY} y2={zeroY} />
    <line className="acf-gridline" x1="48" x2="884" y1="180" y2="180" />
    <text x="42" y="40" textAnchor="end">{extent.toFixed(2)}</text>
    <text x="42" y={zeroY + 4} textAnchor="end">0</text>
    <text x="42" y="184" textAnchor="end">-{extent.toFixed(2)}</text>
    {diagnostics.return_acf.map((point, index) => {
      const first = bar(point.value); const squared = bar(diagnostics.squared_return_acf[index]?.value ?? null)
      const x = 70 + index * 81
      return <g key={point.lag}>
        <rect className="acf-bar return" x={x - 15} y={first.y} width="14" height={first.height}><title>{tr('Return ACF')} · {tr('Lag')} {point.lag}: {diagnosticValue(point.value)}</title></rect>
        <rect className="acf-bar squared" x={x + 2} y={squared.y} width="14" height={squared.height}><title>{tr('Squared Return ACF')} · {tr('Lag')} {point.lag}: {diagnosticValue(diagnostics.squared_return_acf[index]?.value ?? null)}</title></rect>
        <text x={x} y="205" textAnchor="middle">{point.lag}</text>
      </g>
    })}
    <text className="acf-axis-label" x="466" y="218" textAnchor="middle">{tr('Lag')}</text>
  </svg>
}

function StatisticalDiagnosticsSection({ report }: { report: DiagnosisReport }) {
  const { tr } = useI18n()
  const diagnostics = report.statistical_diagnostics
  const returns = diagnostics?.returns
  const pair = diagnostics?.pair_mean_reversion
  return <section className="diagnose-section statistical-diagnostics-section">
    <div className="section-heading"><h2>{tr('Statistical diagnostics')}</h2><span>{tr('Trace equity evidence')}</span></div>
    {!returns ? <p className="capability-unavailable"><strong>{tr('Not available in this cached report')}</strong><span>{tr('Run diagnostics again on a new Trace to calculate statistical evidence.')}</span></p> : <>
      <div className="statistical-diagnostics-grid">
        <div className="acf-panel">
          <div className="chart-legend"><span><i className="legend-return-acf" /> {tr('Return ACF')}</span><span><i className="legend-squared-acf" /> {tr('Squared Return ACF')}</span></div>
          <ReturnAcfChart diagnostics={returns} />
        </div>
        <dl className="statistical-summary">
          <div><dt>{tr('Status')}</dt><dd><span className={`metric-status ${returns.status === 'OK' ? 'ok' : 'warning'}`}>{tr(returns.status)}</span></dd></div>
          <div><dt>{tr('Return observations')}</dt><dd>{returns.observation_count}</dd></div>
          <div><dt>{tr('Lag-1 return autocorrelation')}</dt><dd>{diagnosticValue(returns.lag_1_return_autocorrelation)}</dd></div>
          <div><dt>{tr('Lag-1 squared-return autocorrelation')}</dt><dd>{diagnosticValue(returns.lag_1_squared_return_autocorrelation)}</dd></div>
        </dl>
      </div>
      {returns.status === 'INSUFFICIENT_DATA' && <p className="statistical-note">{tr('Some ACF values are unavailable because the return series is too short or constant.')}</p>}
    </>}
    {report.source_run.strategy_id === 'pairs-trading' && pair && <article className="pair-mean-reversion-card">
      <div className="section-heading"><div><span className="section-kicker">AR(1)</span><h3>{tr('Pair mean-reversion evidence')}</h3></div><span className={`metric-status ${pair.status === 'OK' ? 'ok' : 'warning'}`}>{tr(pair.status)}</span></div>
      <dl>
        <div><dt>{tr('Valid spread observations')}</dt><dd>{pair.observation_count}</dd></div>
        <div><dt>phi (φ)</dt><dd>{diagnosticValue(pair.phi)}</dd></div>
        <div><dt>{tr('Spread lag-1 ACF')}</dt><dd>{diagnosticValue(pair.spread_lag_1_autocorrelation)}</dd></div>
        <div><dt>{tr('Half-life')}</dt><dd>{pair.half_life_bars === null ? tr('Unavailable') : `${pair.half_life_bars.toFixed(2)} ${tr('bars')}`}</dd></div>
        <div><dt>{tr('Hedge ratio mean / std')}</dt><dd>{diagnosticValue(pair.hedge_ratio_mean)} / {diagnosticValue(pair.hedge_ratio_std)}</dd></div>
      </dl>
      <p>{tr('This is diagnostic evidence, not proof of stationarity or cointegration.')}</p>
    </article>}
  </section>
}

function VolatilityChart({ diagnostics }: { diagnostics: VolatilityDiagnostics }) {
  const { tr } = useI18n()
  const values = diagnostics.points.flatMap((point) => [point.rolling_historical_vol, point.ewma_vol]).filter((value): value is number => value !== null)
  const maximum = Math.max(0.01, ...values)
  const x = (index: number) => 54 + index * (820 / Math.max(diagnostics.points.length - 1, 1))
  const y = (value: number) => 178 - value / maximum * 140
  const line = (key: 'rolling_historical_vol' | 'ewma_vol') => diagnostics.points
    .map((point, index) => ({ value: point[key], index }))
    .filter((item): item is { value: number; index: number } => item.value !== null)
    .map((item) => `${x(item.index)},${y(item.value)}`).join(' ')
  const indexes = new Map(diagnostics.points.map((point, index) => [point.timestamp, index]))

  return <svg className="volatility-chart" viewBox="0 0 920 220" role="img" aria-label={tr('Historical and EWMA volatility with strategy drawdown overlays')}>
    {diagnostics.drawdown_overlap.map((drawdown) => {
      const start = indexes.get(drawdown.start_time); const end = indexes.get(drawdown.end_time)
      if (start === undefined || end === undefined) return null
      return <rect key={drawdown.episode_id} className="volatility-drawdown-overlay" x={x(start)} y="28" width={Math.max(x(end) - x(start), 3)} height="150"><title>#{drawdown.rank_by_depth} · {percent(drawdown.max_drawdown)}</title></rect>
    })}
    <line className="volatility-gridline" x1="54" x2="874" y1="38" y2="38" />
    <line className="volatility-gridline" x1="54" x2="874" y1="108" y2="108" />
    <line className="volatility-axis" x1="54" x2="874" y1="178" y2="178" />
    <text x="48" y="42" textAnchor="end">{percent(maximum)}</text>
    <text x="48" y="112" textAnchor="end">{percent(maximum / 2)}</text>
    <text x="48" y="182" textAnchor="end">0%</text>
    <polyline className="historical-vol-line" points={line('rolling_historical_vol')} />
    <polyline className="ewma-vol-line" points={line('ewma_vol')} />
    {diagnostics.points.map((point, index) => index % Math.max(Math.floor(diagnostics.points.length / 5), 1) === 0
      ? <text key={point.timestamp} x={x(index)} y="205" textAnchor="middle">{point.timestamp.slice(5, 10)}</text>
      : null)}
  </svg>
}

function VolatilityDiagnosticsSection({ report }: { report: DiagnosisReport }) {
  const { tr } = useI18n()
  const diagnostics = report.volatility_diagnostics
  return <section className="diagnose-section volatility-diagnostics-section">
    <div className="section-heading"><h2>{tr('Volatility diagnostics')}</h2><span>{tr('Recorded market and strategy evidence')}</span></div>
    {!diagnostics ? <p className="capability-unavailable"><strong>{tr('Not available in this cached report')}</strong><span>{tr('Run diagnostics again to calculate volatility evidence.')}</span></p> : <>
      <article className={`diagnostic-verdict ${diagnostics.status === 'OK' ? '' : 'unavailable'}`}>
        <span className="section-kicker">{tr('Verdict')}</span>
        <strong>{tr(diagnostics.verdict)}</strong>
        <p>{tr(diagnostics.summary)}</p>
      </article>
      <div className="volatility-evidence-grid">
        <div className="volatility-chart-panel">
          <div className="chart-legend"><span><i className="legend-historical-vol" /> {diagnostics.rolling_window}{tr('d historical vol')}</span><span><i className="legend-ewma-vol" /> EWMA λ={diagnostics.ewma_decay.toFixed(2)}</span><span><i className="legend-drawdown-overlay" /> {tr('Major drawdowns')}</span></div>
          <VolatilityChart diagnostics={diagnostics} />
        </div>
        <dl className="volatility-summary">
          <div><dt>{tr('Volatility regime')}</dt><dd><span className={`volatility-regime ${(diagnostics.current_regime ?? 'unavailable').toLowerCase()}`}>{diagnostics.current_regime ? tr(diagnostics.current_regime) : tr('Unavailable')}</span></dd></div>
          <div><dt>{diagnostics.rolling_window}{tr('d historical vol')}</dt><dd>{diagnostics.current_historical_vol === null ? 'N/A' : percent(diagnostics.current_historical_vol)}</dd></div>
          <div><dt>{tr('EWMA volatility')}</dt><dd>{diagnostics.current_ewma_vol === null ? 'N/A' : percent(diagnostics.current_ewma_vol)}</dd></div>
          <div><dt>{tr('Rising-vol drawdown starts')}</dt><dd>{diagnostics.rising_volatility_start_count} / {diagnostics.evaluable_drawdown_count}</dd></div>
          <div><dt>{tr('Regime-change drawdown starts')}</dt><dd>{diagnostics.regime_change_start_count} / {diagnostics.evaluable_drawdown_count}</dd></div>
        </dl>
      </div>
      <details className="advanced-disclosure volatility-methodology"><summary>{tr('Calculation details')}</summary><div className="method-notes"><p>{tr(diagnostics.market_return_method)}</p>{diagnostics.calculation_details.map((detail) => <p key={detail}>{tr(detail)}</p>)}</div></details>
    </>}
  </section>
}

type WhatIfMetricKey = keyof WhatIfMetrics

function whatIfMetric(value: number, key: WhatIfMetricKey) {
  if (key === 'total_return' || key === 'max_drawdown') return percent(value)
  if (key === 'net_pnl') return formatCurrency(value)
  if (key === 'trade_count') return value.toFixed(0)
  return value.toFixed(2)
}

function whatIfDelta(value: number, key: WhatIfMetricKey) {
  if (key === 'total_return' || key === 'max_drawdown') return `${(value * 100).toFixed(2)} pp`
  return whatIfMetric(value, key)
}

function WhatIfLab({ report }: { report: DiagnosisReport }) {
  const { tr } = useI18n()
  const support = report.what_if
  const baselineInputs = support?.baseline_inputs
  const baseline = support?.baseline_metrics
  const parameter = support?.parameter
  const [feeBps, setFeeBps] = useState(baselineInputs?.fee_bps ?? 0)
  const [slippageBps, setSlippageBps] = useState(baselineInputs?.slippage_bps ?? 0)
  const [delay, setDelay] = useState<0 | 1 | 2>(baselineInputs?.additional_execution_delay_bars ?? 0)
  const [parameterValue, setParameterValue] = useState(parameter?.current_value ?? 0)
  const [scenario, setScenario] = useState<WhatIfScenario | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!support || support.status === 'NOT_SUPPORTED' || !baselineInputs || !baseline) return <section className="diagnose-section what-if-section"><div className="section-heading"><h2>{tr('What-if Lab')}</h2></div><p className="capability-unavailable"><strong>{tr('Not supported for this run')}</strong><span>{tr('What-if requires a native VQD rerun contract.')}</span></p></section>

  async function runScenario(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null)
    try {
      setScenario(await createWhatIfScenario(report.source_run.trace_id, {
        fee_bps: feeBps,
        slippage_bps: slippageBps,
        additional_execution_delay_bars: delay,
        strategy_parameters: parameter ? { [parameter.key]: parameterValue } : {},
      }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'What-if scenario failed.')
    } finally { setBusy(false) }
  }

  const stressed = scenario?.stressed_metrics ?? baseline
  const deltas = scenario?.deltas ?? { total_return: 0, sharpe: 0, max_drawdown: 0, turnover: 0, trade_count: 0, net_pnl: 0 }
  const rows: Array<{ key: WhatIfMetricKey; label: string }> = [
    { key: 'total_return', label: 'Total return' }, { key: 'sharpe', label: 'Sharpe' },
    { key: 'max_drawdown', label: 'Max drawdown' }, { key: 'turnover', label: 'Turnover' },
    { key: 'trade_count', label: 'Trade count' }, { key: 'net_pnl', label: 'Net P&L' },
  ]
  return <section className="diagnose-section what-if-section">
    <div className="section-heading"><h2>{tr('What-if Lab')}</h2><span>{tr('Full deterministic rerun')}</span></div>
    <form className="what-if-controls" onSubmit={(event) => void runScenario(event)}>
      <label><span>{tr('Transaction cost')}</span><input aria-label={tr('Transaction cost')} type="number" min="0" max="10000" step="1" value={feeBps} onChange={(event) => { setFeeBps(Number(event.target.value)); setScenario(null) }} /><small>{tr('Baseline')}: {baselineInputs.fee_bps} bps</small></label>
      <label><span>{tr('Slippage')}</span><input aria-label={tr('Slippage')} type="number" min="0" max="10000" step="1" value={slippageBps} onChange={(event) => { setSlippageBps(Number(event.target.value)); setScenario(null) }} /><small>{tr('Baseline')}: {baselineInputs.slippage_bps} bps</small></label>
      <label><span>{tr('Execution delay')}</span><select aria-label={tr('Execution delay')} value={delay} onChange={(event) => { setDelay(Number(event.target.value) as 0 | 1 | 2); setScenario(null) }}><option value={0}>+0 {tr('bars')}</option><option value={1}>+1 {tr('bars')}</option><option value={2}>+2 {tr('bars')}</option></select><small>{tr('Baseline')}: +{baselineInputs.additional_execution_delay_bars}</small></label>
      {parameter && <label><span>{tr(parameter.label)}</span><input aria-label={tr(parameter.label)} type="number" min={parameter.minimum} max={parameter.maximum ?? undefined} step={parameter.step} value={parameterValue} onChange={(event) => { setParameterValue(Number(event.target.value)); setScenario(null) }} /><small>{tr('Baseline')}: {parameter.current_value} {tr(parameter.unit)}</small></label>}
      <button className="primary-button" type="submit" disabled={busy}>{busy ? tr('Running scenario…') : tr('Run scenario')}</button>
    </form>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <article className="diagnostic-verdict what-if-verdict">
      <span className="section-kicker">{tr('Verdict')}</span>
      <strong>{tr(scenario?.verdict ?? 'BASELINE_READY')}</strong>
      <p>{scenario ? tr('The comparison reflects a full backend rerun under the selected assumptions.') : tr('Baseline metrics remain visible until a stressed scenario is run.')}</p>
    </article>
    <div className="what-if-comparison" role="table">
      <div className="what-if-row header"><span>{tr('Metric')}</span><span>{tr('Baseline')}</span><span>{tr('Stress')}</span><span>{tr('Change')}</span></div>
      {rows.map((row) => <div className="what-if-row" key={row.key}><span>{tr(row.label)}</span><code>{whatIfMetric(baseline[row.key], row.key)}</code><code>{whatIfMetric(stressed[row.key], row.key)}</code><code className={deltas[row.key] < 0 ? 'negative' : deltas[row.key] > 0 ? 'positive' : ''}>{whatIfDelta(deltas[row.key], row.key)}</code></div>)}
    </div>
    <div className="what-if-evidence"><h3>{tr('Evidence')}</h3><ul>{(scenario?.evidence ?? [tr('Change assumptions, then run a scenario to produce stressed evidence.')]).map((item) => <li key={item}>{tr(item)}</li>)}</ul></div>
    <details className="advanced-disclosure what-if-methodology"><summary>{tr('Calculation details')}</summary><div className="method-notes">{(scenario?.calculation_details ?? support.calculation_details).map((detail) => <p key={detail}>{tr(detail)}</p>)}</div></details>
  </section>
}

function DiagnoseContent({ report, onOpenReplay }: { report: DiagnosisReport; onOpenReplay: () => void }) {
  const { tr } = useI18n(); const split = report.train_test
  return <main className="diagnose-shell">
    <header className="diagnose-header"><h1>{tr('Diagnose')}</h1><button className="link-button" onClick={onOpenReplay}>{tr('Open in Replay')} →</button></header>

    <section className="diagnose-section">
      <div className="section-heading"><h2>{tr('Train / Test · 70 / 30')}</h2><span>{split.train_bar_count} + {split.test_bar_count} {tr('bars')}</span></div>
      <div className="comparison-table train-test-table" role="table">
        <div className="comparison-row header"><span>{tr('Metric')}</span><span>{tr('Train')}</span><span>{tr('Test')}</span></div>
        <div className="comparison-row"><span>{tr('Window')}</span><code>{split.train_start.slice(0,10)} → {split.train_end.slice(0,10)}</code><code>{split.test_start.slice(0,10)} → {split.test_end.slice(0,10)}</code></div>
        <div className="comparison-row"><span>{tr('Return')}</span><code>{percent(split.train.return)}</code><code>{percent(split.test.return)}</code></div>
        <div className="comparison-row"><span>{tr('Sharpe')}</span><code>{sharpeDisplay(split.train)}</code><code>{sharpeDisplay(split.test)}</code></div>
        <div className="comparison-row"><span>{tr('Drawdown')}</span><code>{percent(split.train.max_drawdown)}</code><code>{percent(split.test.max_drawdown)}</code></div>
        <div className="comparison-row"><span>{tr('Turnover')}</span><code>{decimal(split.train.turnover)}×</code><code>{decimal(split.test.turnover)}×</code></div>
        <div className="comparison-row"><span>{tr('Trades')}</span><code>{split.train.trade_count}</code><code>{split.test.trade_count}</code></div>
        <div className="comparison-row"><span>{tr('End equity')}</span><code>{formatCurrency(split.train.final_equity)}</code><code>{formatCurrency(split.test.final_equity)}</code></div>
        <div className="comparison-row"><span>{tr('Status')}</span><code>{tr(split.train.status)}</code><code>{tr(split.test.status)}</code></div>
      </div>
      <details className="advanced-disclosure methodology-disclosure"><summary>{tr('Methodology')}</summary><div className="method-notes"><p>{tr(split.feature_context_policy)}</p><p>{tr(split.pnl_isolation_policy)}</p></div></details>
    </section>

    <StatisticalDiagnosticsSection report={report} />

    <VolatilityDiagnosticsSection report={report} />

    <WhatIfLab report={report} />

    <section className="diagnose-section observations"><div className="section-heading"><h2>{tr('Key findings')}</h2></div><ul className="findings-list">{report.observations.map((item) => <li key={item.observation_id}><strong>{tr(item.title)}</strong><span>{tr(item.evidence)}</span></li>)}</ul></section>
  </main>
}

export default function DiagnosePage({ traceId, onOpenReplay }: { traceId: string | null; onOpenReplay: () => void }) {
  const { tr } = useI18n(); const [report, setReport] = useState<DiagnosisReport | null>(null); const [error, setError] = useState<string | null>(null); const [loading, setLoading] = useState(false)
  const load = useCallback(async () => { if (!traceId) return; setLoading(true); setError(null); setReport(null); try { setReport(await createDiagnosis(traceId)) } catch (reason) { setError(reason instanceof Error ? reason.message : 'Diagnosis failed with an unknown error.') } finally { setLoading(false) } }, [traceId])
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer) }, [load])
  if (!traceId) return <main className="diagnose-empty"><section><h1>{tr('Diagnose')}</h1><p>{tr('Run a backtest from Strategy before diagnosing it.')}</p></section></main>
  if (loading || (!report && !error)) return <main className="diagnose-empty"><section><h1>{tr('Running diagnostics…')}</h1></section></main>
  if (error) return <main className="diagnose-empty"><section role="alert"><h1>{tr('Could not diagnose this trace.')}</h1><p>{error}</p><button className="primary-button" onClick={() => void load()}>{tr('Retry')}</button></section></main>
  return <DiagnoseContent report={report!} onOpenReplay={onOpenReplay} />
}
