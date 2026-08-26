import { useCallback, useEffect, useMemo, useState, type KeyboardEvent, type MouseEvent } from 'react'

import { createDiagnosis } from '../../api/diagnostics'
import { useI18n } from '../../i18n/I18nProvider'
import type { DiagnosisReport, DiagnosticMetrics } from '../../types/diagnostics'
import { nearestChartIndex, pointerToViewBoxX } from '../discover/chartInteraction'
import { formatCurrency } from '../replay/utils/format'

function percent(value: number) { return `${(value * 100).toFixed(2)}%` }
function decimal(value: number) { return value.toFixed(2) }
function sharpeDisplay(metrics: DiagnosticMetrics) { return metrics.status === 'OK' ? decimal(metrics.sharpe) : 'N/A' }

function SensitivityChart({ report }: { report: DiagnosisReport }) {
  const { tr } = useI18n()
  const points = report.lookback_sensitivity
  const currentIndex = Math.max(points.findIndex((point) => point.is_current), 0)
  const [activeIndex, setActiveIndex] = useState(currentIndex)
  const chart = useMemo(() => {
    const validValues = points.flatMap((point) => [point.train, point.test]).filter((item) => item.status === 'OK').map((item) => item.sharpe)
    const values = validValues.length ? validValues : [0]
    const minimum = Math.min(...values); const maximum = Math.max(...values); const range = maximum - minimum || 1
    const x = (index: number) => 48 + index * (824 / Math.max(points.length - 1, 1))
    const y = (value: number) => 174 - ((value - minimum) / range) * 128
    const line = (key: 'train' | 'test') => points.map((point, index) => ({ point, index })).filter(({ point }) => point[key].status === 'OK').map(({ point, index }) => `${x(index)},${y(point[key].sharpe)}`).join(' ')
    return { train: line('train'), test: line('test'), x, y }
  }, [points])
  const safeIndex = Math.min(Math.max(activeIndex, 0), Math.max(points.length - 1, 0))
  const active = points[safeIndex]
  const activeX = chart.x(safeIndex)
  const tooltipWidth = 190
  const tooltipX = activeX > 460 ? activeX - tooltipWidth - 12 : activeX + 12

  function handleHover(event: MouseEvent<SVGSVGElement>) {
    const pointerX = pointerToViewBoxX(event.clientX, event.currentTarget.getBoundingClientRect(), 920, 220)
    setActiveIndex(nearestChartIndex(pointerX, points.length, 48, 872))
  }

  function handleKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    if (event.key === 'Home') setActiveIndex(0)
    else if (event.key === 'End') setActiveIndex(points.length - 1)
    else setActiveIndex((current) => Math.min(points.length - 1, Math.max(0, current + (event.key === 'ArrowRight' ? 1 : -1))))
  }

  return <>
    <svg className="sensitivity-chart interactive-research-chart" viewBox="0 0 920 220" role="img" tabIndex={0} aria-label={tr('Train and test Sharpe across lookback candidates')} onMouseMove={handleHover} onKeyDown={handleKeyDown}>
      <line className="diagnostic-axis" x1="48" x2="872" y1="174" y2="174" />
      <polyline className="train-line" points={chart.train} /><polyline className="test-line" points={chart.test} />
      {points.map((point, index) => <g key={point.lookback}>
        {point.is_current && <line className="current-candidate" x1={chart.x(index)} x2={chart.x(index)} y1="28" y2="184" />}
        {point.train.status === 'OK' && <circle className="train-point" cx={chart.x(index)} cy={chart.y(point.train.sharpe)} r={safeIndex === index ? 6 : 4} />}
        {point.test.status === 'OK' && <circle className="test-point" cx={chart.x(index)} cy={chart.y(point.test.sharpe)} r={safeIndex === index ? 6 : 4} />}
        <text x={chart.x(index)} y="204" textAnchor="middle">{point.lookback}</text>
      </g>)}
      {active && <>
        <line className="research-hover-guide" x1={activeX} x2={activeX} y1="28" y2="184" />
        <g className="research-chart-tooltip" transform={`translate(${tooltipX} 28)`}>
          <rect width={tooltipWidth} height="72" rx="5" />
          <text className="tooltip-date" x="10" y="18">{tr('Lookback')} {active.lookback}{active.is_current ? ` · ${tr('current')}` : ''}</text>
          <text x="10" y="41">{tr('Train Sharpe')}</text><text className="tooltip-value" x={tooltipWidth - 10} y="41" textAnchor="end">{sharpeDisplay(active.train)}</text>
          <text x="10" y="61">{tr('Test Sharpe')}</text><text className="tooltip-value" x={tooltipWidth - 10} y="61" textAnchor="end">{sharpeDisplay(active.test)}</text>
        </g>
      </>}
    </svg>
    <p className="empty-copy">{tr('Hover the chart or use left and right arrow keys to inspect each lookback.')}</p>
  </>
}

function DiagnoseContent({ report, onOpenReplay }: { report: DiagnosisReport; onOpenReplay: () => void }) {
  const { tr } = useI18n(); const split = report.train_test
  const supported = report.support ?? { train_test: 'AVAILABLE', parameter_sensitivity: 'AVAILABLE', cost_stress: 'AVAILABLE', execution_delay: 'AVAILABLE' }
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

    <section className="diagnose-section">
      <div className="section-heading"><h2>{tr(report.source_run.sensitivity_parameter && report.source_run.sensitivity_parameter !== 'lookback' ? `${report.source_run.sensitivity_parameter} sensitivity` : 'Lookback sensitivity')}</h2>{report.sensitivity_available !== false && <div className="chart-legend"><span><i className="legend-train" /> {tr('Train Sharpe')}</span><span><i className="legend-test" /> {tr('Test Sharpe')}</span></div>}</div>
      {supported.parameter_sensitivity === 'NOT_SUPPORTED' || report.sensitivity_available === false || report.lookback_sensitivity.length === 0
        ? <p className="capability-unavailable"><strong>{tr('Not supported for this run')}</strong><span>{tr('This adapter cannot reproduce parameter sensitivity without changing framework semantics.')}</span></p>
        : <><SensitivityChart report={report} /><div className="dense-table sensitivity-table"><div className="dense-row header"><span>{tr(report.source_run.sensitivity_parameter ?? 'Lookback')}</span><span>{tr('Train Sharpe')}</span><span>{tr('Test Sharpe')}</span><span>{tr('Status')}</span></div>{report.lookback_sensitivity.map((point) => <div className="dense-row" key={point.lookback}><code>{point.lookback}{point.is_current ? ` · ${tr('current')}` : ''}</code><code>{sharpeDisplay(point.train)}</code><code>{sharpeDisplay(point.test)}</code><span>{tr(point.train.status)} / {tr(point.test.status)}</span></div>)}</div></>}
    </section>

    <section className="diagnose-section">
      <div className="section-heading"><h2>{tr('Cost stress')}</h2><span>{tr('Full-pipeline reruns')}</span></div>
      {supported.cost_stress === 'NOT_SUPPORTED' ? <p className="capability-unavailable"><strong>{tr('Not supported for this run')}</strong><span>{tr('Cost stress requires a framework-native rerun contract that this adapter does not provide.')}</span></p> : <div className="dense-table cost-stress-table"><div className="dense-row header"><span>{tr('Total friction')}</span><span>{tr('Fee / Slippage')}</span><span>{tr('Return')}</span><span>{tr('Sharpe')}</span></div>{report.cost_stress.map((point) => <div className="dense-row" key={point.total_friction_bps}><code>{point.total_friction_bps} bps</code><code>{point.fee_bps} / {point.slippage_bps}</code><code>{percent(point.metrics.return)}</code><code>{sharpeDisplay(point.metrics)}</code></div>)}</div>}
    </section>

    <section className="diagnose-section">
      <div className="section-heading"><h2>{tr('Execution delay')}</h2><span>{tr('0 = baseline close(t+1)')}</span></div>
      {supported.execution_delay === 'NOT_SUPPORTED' ? <p className="capability-unavailable"><strong>{tr('Not supported for this run')}</strong><span>{tr('Execution delay cannot be inferred from persisted results.')}</span></p> : <div className="dense-table execution-delay-table"><div className="dense-row header"><span>{tr('Additional delay')}</span><span>{tr('Effective execution')}</span><span>{tr('Return')}</span><span>{tr('Unfilled')}</span></div>{report.execution_delay.map((point) => <div className="dense-row" key={point.additional_delay_bars}><code>+{point.additional_delay_bars} {tr('bars')}</code><code>close(t+{point.execution_offset_bars})</code><code>{percent(point.metrics.return)}</code><code>{point.unfilled_signal_count}</code></div>)}</div>}
    </section>

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
