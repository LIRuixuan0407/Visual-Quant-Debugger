import { useCallback, useEffect, useRef, useState } from 'react'

import {
  compareRuns,
  deleteRun,
  getRun,
  getRuns,
  getRunStrategySource,
  rerunExactRevision,
  saveRunAnnotations,
  validateRuns,
} from '../../api/runs'
import type { RunFilters } from '../../api/runs'
import type { DatasetDefinition } from '../../types/dataset'
import type {
  RunAnnotations,
  RunComparisonReport,
  RunDetail,
  RunListItem,
  RunListResponse,
  RunStatus,
  RunValidationReport,
  StrategySourceArtifact,
} from '../../types/run'
import type { StrategyDefinition, StrategyParameters } from '../../types/strategy'
import { useI18n } from '../../i18n/I18nProvider'
import { runtimeLabel } from '../replay/capabilities'

export interface LoadedRunConfiguration {
  strategy_id: string
  dataset_id: string
  parameters: StrategyParameters
  research_cutoff: string | null
}

interface RunsPageServices {
  getRuns: typeof getRuns
  getRun: typeof getRun
  saveAnnotations: typeof saveRunAnnotations
  deleteRun: typeof deleteRun
  rerun: typeof rerunExactRevision
  compare: typeof compareRuns
  validate: typeof validateRuns
  getSource: typeof getRunStrategySource
}

interface RunsPageProps {
  strategies: StrategyDefinition[]
  datasets: DatasetDefinition[]
  initialRunId?: string | null
  onRunSelection?: (runId: string) => void
  onOpenReplay: (runId: string, traceId: string, eventId?: string | null) => void
  onOpenDiagnose: (runId: string, traceId: string) => void
  onOpenAutopsy: (runId: string, traceId: string) => void
  onLoadConfiguration: (configuration: LoadedRunConfiguration) => void
  onRunDataAudit?: (runId: string) => void
  services?: Partial<RunsPageServices>
}

const formatNumber = (value: number | null | undefined, digits = 2) => value == null
  ? '—'
  : new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value)

const formatPercent = (value: number | null | undefined) => value == null
  ? '—'
  : `${(value * 100).toFixed(2)}%`

const shortHash = (value: string) => value.startsWith('sha256:') ? value.slice(7, 19) : value.slice(0, 12)

function utcTimeParts(value: string | null) {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return { date: value, time: '' }
  const iso = parsed.toISOString()
  return { date: iso.slice(0, 10), time: `${iso.slice(11, 16)} UTC` }
}

const formatTime = (value: string | null) => {
  const parts = utcTimeParts(value)
  return parts ? `${parts.date}${parts.time ? ` ${parts.time}` : ''}` : '—'
}

function RunTime({ value }: { value: string | null }) {
  const parts = utcTimeParts(value)
  return parts
    ? <time className="run-time" dateTime={value ?? undefined}><span>{parts.date}</span>{parts.time && <small>{parts.time}</small>}</time>
    : <span>—</span>
}

const parameterSummary = (parameters: Record<string, number>) => Object.entries(parameters)
  .filter(([key]) => !['initial_cash', 'gross_target'].includes(key))
  .slice(0, 3)
  .map(([key, value]) => `${key}=${value}`)
  .join(' · ')

function StatusBadge({ status }: { status: RunStatus }) {
  const { tr } = useI18n()
  const severity = status === 'COMPLETED' ? 'match' : status === 'RUNNING' ? 'running' : status === 'PARTIAL' ? 'paused' : 'error'
  return <strong className={`status-badge ${severity}`}>{tr(status)}</strong>
}

function RunActions({ run, onReplay, onDiagnose, onAutopsy }: {
  run: RunListItem
  onReplay: () => void
  onDiagnose: () => void
  onAutopsy: () => void
}) {
  const { tr } = useI18n()
  const available = Boolean(run.trace_id)
  return <div className="run-row-actions">
    <button type="button" disabled={!available} onClick={(event) => { event.stopPropagation(); onReplay() }}>{tr('Replay')}</button>
    {run.run_type === 'BACKTEST' && <button type="button" disabled={!available} onClick={(event) => { event.stopPropagation(); onDiagnose() }}>{tr('Diagnose')}</button>}
    <button type="button" disabled={!available} onClick={(event) => { event.stopPropagation(); onAutopsy() }}>{tr('P&L Autopsy')}</button>
  </div>
}

function EquityOverlay({ report }: { report: RunComparisonReport }) {
  const { tr } = useI18n()
  if (report.equity_comparison.length < 2) return <p className="run-empty-inline">{tr('Equity overlay requires identical timestamps.')}</p>
  const width = 920
  const height = 190
  const values = report.equity_comparison.flatMap((point) => point.values)
  const minimum = Math.min(...values)
  const maximum = Math.max(...values)
  const spread = maximum - minimum || 1
  const paths = report.run_ids.map((_, runIndex) => report.equity_comparison.map((point, index) => {
    const x = (index / (report.equity_comparison.length - 1)) * width
    const y = height - ((point.values[runIndex] - minimum) / spread) * (height - 16) - 8
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' '))
  return <div className="equity-overlay">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={tr('Run equity comparison')}>
      {paths.map((points, index) => <polyline key={report.run_ids[index]} className={`compare-equity-line run-${index}`} points={points} />)}
    </svg>
    <div>{report.run_ids.map((runId, index) => <span key={runId}><i className={`run-${index}`} />{runId}</span>)}</div>
  </div>
}

function ComparisonWorkspace({ report, runs, onBack, onOpenReplay }: {
  report: RunComparisonReport
  runs: RunListItem[]
  onBack: () => void
  onOpenReplay: (runId: string, traceId: string, eventId?: string | null) => void
}) {
  const { tr } = useI18n()
  const runById = new Map(runs.map((run) => [run.run_id, run]))
  const labels = report.run_ids.map((runId, index) => `${tr('Run')} ${String.fromCharCode(65 + index)}`)
  const divergence = report.first_behavioral_divergence
  return <main className="runs-shell compare-shell">
    <header className="workspace-title">
      <div><h1>{tr('Compare Runs')}</h1><span>{report.run_ids.join(' · ')}</span></div>
      <button className="secondary-button" type="button" onClick={onBack}>{tr('Back to Runs')}</button>
    </header>

    <section className="workspace-panel compare-context" aria-labelledby="context-compatibility">
      <div className="run-section-title"><h2 id="context-compatibility">{tr('Context Compatibility')}</h2><strong className="status-badge">{tr(report.comparability)}</strong></div>
      <div className="compare-run-labels">{report.run_ids.map((runId, index) => <div key={runId}><small>{labels[index]}</small><strong>{tr(runById.get(runId)?.strategy_name ?? runId)}</strong><code>{runId}</code></div>)}</div>
      <div className="comparison-table">
        <div className="run-compare-row header"><span>{tr('Context')}</span><span>{tr('Compatibility')}</span><span>{tr('Values')}</span></div>
        {report.context_diff.map((item) => <div className="run-compare-row" key={item.field}><strong>{tr(item.field.replaceAll('_', ' '))}</strong><span>{tr(item.same ? 'SAME' : 'DIFFERENT')}</span><code>{item.values.map(shortHash).join(' · ')}</code></div>)}
      </div>
    </section>

    <section className="workspace-panel compare-section" aria-labelledby="parameter-diff"><div className="run-section-title"><h2 id="parameter-diff">{tr('Parameter Diff')}</h2><span>{tr('Changed values only')}</span></div>
      {report.parameter_diff.length === 0 ? <p className="run-empty-inline">{tr('No parameter differences.')}</p> : <div className="comparison-table">
        <div className="dynamic-compare-row header" style={{ gridTemplateColumns: `minmax(180px, 1fr) repeat(${report.run_ids.length}, minmax(130px, .8fr))` }}><span>{tr('Parameter')}</span>{labels.map((label) => <span key={label}>{label}</span>)}</div>
        {report.parameter_diff.map((item) => <div className="dynamic-compare-row" style={{ gridTemplateColumns: `minmax(180px, 1fr) repeat(${report.run_ids.length}, minmax(130px, .8fr))` }} key={item.parameter}><strong>{item.parameter}</strong>{item.values.map((value, index) => <code key={`${item.parameter}-${report.run_ids[index]}`}>{String(value ?? '—')}</code>)}</div>)}
      </div>}
    </section>

    <section className="workspace-panel compare-section" aria-labelledby="metric-diff"><div className="run-section-title"><h2 id="metric-diff">{tr('Metric Diff')}</h2><span>{tr('Backend-persisted research facts')}</span></div>
      <div className="comparison-table">
        <div className="dynamic-compare-row header" style={{ gridTemplateColumns: `minmax(180px, 1fr) repeat(${report.run_ids.length}, minmax(130px, .8fr))` }}><span>{tr('Metric')}</span>{labels.map((label) => <span key={label}>{label}</span>)}</div>
        {report.metric_diff.map((item) => <div className="dynamic-compare-row" style={{ gridTemplateColumns: `minmax(180px, 1fr) repeat(${report.run_ids.length}, minmax(130px, .8fr))` }} key={item.metric}><strong>{tr(item.metric.replaceAll('_', ' '))}</strong>{item.values.map((value, index) => <code key={`${item.metric}-${report.run_ids[index]}`}>{formatNumber(value, 5)}</code>)}</div>)}
      </div>
    </section>

    <section className="workspace-panel compare-section" aria-labelledby="equity-comparison"><div className="run-section-title"><h2 id="equity-comparison">{tr('Equity Comparison')}</h2><span>{tr('Overlay only for aligned timelines')}</span></div><EquityOverlay report={report} /></section>

    <section className="workspace-panel compare-section" aria-labelledby="behavioral-divergence"><div className="run-section-title"><h2 id="behavioral-divergence">{tr('First Behavioral Divergence')}</h2><span>{tr('Two-run strict path')}</span></div>
      {!divergence && <p className="run-empty-inline">{tr('Behavioral comparison is limited to two strictly comparable runs.')}</p>}
      {divergence && <div className={`divergence-result ${divergence.status === 'DIVERGENCE' ? 'found' : ''}`}>
        <div><strong>{tr(divergence.status)}</strong>{divergence.kind && <span>{tr(divergence.kind)}</span>}<time>{formatTime(divergence.timestamp)}</time></div>
        <p>{tr(divergence.summary)}</p>
        {divergence.associated_parameter_differences.length > 0 && <small>{tr('Associated configuration differences')}: {divergence.associated_parameter_differences.join(', ')}</small>}
        <div className="toolbar">{report.run_ids.map((runId, index) => {
          const run = runById.get(runId)
          return <button key={runId} type="button" disabled={!run?.trace_id} onClick={() => run?.trace_id && onOpenReplay(runId, run.trace_id, divergence.event_ids[index])}>{tr('Open')} {labels[index]} {tr('in Replay')}</button>
        })}</div>
      </div>}
    </section>

    <section className="workspace-panel compare-section" aria-labelledby="signal-diff"><div className="run-section-title"><h2 id="signal-diff">{tr('Signal Diff')}</h2><span>{report.signal_comparison.length} {tr('changed timestamps')}</span></div>
      {report.signal_comparison.length === 0 ? <p className="run-empty-inline">{tr('No signal differences available.')}</p> : <div className="behavior-table"><div className="behavior-row header"><span>{tr('Time')}</span>{labels.map((label) => <span key={label}>{label}</span>)}</div>{report.signal_comparison.slice(0, 80).map((row) => <div className="behavior-row" key={row.timestamp}><time>{formatTime(row.timestamp)}</time>{row.values.map((value, index) => <code key={`${row.timestamp}-${index}`}>{value}</code>)}</div>)}</div>}
    </section>

    <section className="workspace-panel compare-section" aria-labelledby="execution-diff"><div className="run-section-title"><h2 id="execution-diff">{tr('Execution Diff')}</h2><span>{report.execution_comparison.length} {tr('changed timestamps')}</span></div>
      {report.execution_comparison.length === 0 ? <p className="run-empty-inline">{tr('No execution differences available.')}</p> : <div className="behavior-table"><div className="behavior-row header"><span>{tr('Time')}</span>{labels.map((label) => <span key={label}>{label}</span>)}</div>{report.execution_comparison.slice(0, 80).map((row) => <div className="behavior-row" key={row.timestamp}><time>{formatTime(row.timestamp)}</time>{row.values.map((value, index) => <code key={`${row.timestamp}-${index}`}>{value}</code>)}</div>)}</div>}
    </section>
  </main>
}

function ValidationWorkspace({ report, onBack, onOpenReplay }: {
  report: RunValidationReport
  onBack: () => void
  onOpenReplay: (runId: string, traceId: string, eventId?: string | null) => void
}) {
  const { tr } = useI18n()
  const attribution = report.pnl_attribution
  const amounts = attribution.components.flatMap((component) => component.amount == null ? [] : [Math.abs(component.amount)])
  const largestAmount = Math.max(Math.abs(attribution.total_difference), ...amounts, 1)
  return <main className="runs-shell validation-shell">
    <header className="workspace-title"><div><h1>{tr('Backtest vs Paper Attribution')}</h1><span>{report.report_id}</span></div><button className="secondary-button" onClick={onBack}>{tr('Back to Runs')}</button></header>
    <section className="validation-status-grid attribution-status-grid">
      <div className="workspace-panel"><span>{tr('Historical Baseline')}</span><strong>{tr('Historical Backtest')}</strong><code>{report.backtest_run_id}</code><small>{tr('Selected immutable historical evidence')}</small></div>
      <div className="workspace-panel"><span>{tr('Paper Evidence')}</span><strong>{tr('Frozen Recorded Feed')}</strong><code>{report.paper_run_id}</code><small>{tr('Reference Run')} · {report.reference_run_id}</small></div>
      <div className="workspace-panel"><span>{tr('Comparability')}</span><strong>{tr(report.historical_comparability)}</strong><small>{tr('Cross-period claims stay descriptive unless evidence is equivalent.')}</small></div>
    </section>

    <section className="workspace-panel attribution-overview">
      <div className="section-heading"><div><h2>{tr('Total P&L Gap')}</h2><span>{tr('Backend-calculated attribution')}</span></div><strong className="attribution-total">{formatNumber(attribution.total_difference)}</strong></div>
      <div className="attribution-reconciliation"><span>{tr(attribution.status)}</span><span>{tr('Attributed total')}: {formatNumber(attribution.attributed_total)}</span><span>{tr('Residual')}: {formatNumber(attribution.residual_unattributed)}</span><span>{tr('Reconciliation error')}: {formatNumber(attribution.reconciliation_error)}</span></div>
    </section>

    <section className="workspace-panel attribution-waterfall">
      <div className="section-heading"><div><h2>{tr('Attribution Waterfall')}</h2><span>{tr('Unknown effects remain in Residual')}</span></div><span>{attribution.components.length} {tr('layers')}</span></div>
      <div className="attribution-waterfall-list">
        {attribution.components.map((component) => {
          const width = component.amount == null ? 0 : Math.max(3, Math.min(100, Math.abs(component.amount) / largestAmount * 100))
          return <article key={component.layer} className="attribution-waterfall-row">
            <div><strong>{tr(component.layer.replaceAll('_', ' '))}</strong><small>{tr(component.status)}</small></div>
            <div className="attribution-waterfall-track" aria-hidden="true"><i style={{ width: `${width}%` }} /></div>
            <strong>{component.amount == null ? tr('Not isolated') : formatNumber(component.amount)}</strong>
          </article>
        })}
      </div>
    </section>

    <section className="attribution-layer-grid" aria-label={tr('Attribution layers')}>
      {attribution.components.map((component) => <article className="workspace-panel attribution-layer-card" key={`detail-${component.layer}`}>
        <header><div><span className="section-kicker">{tr(component.layer.replaceAll('_', ' '))}</span><h3>{component.amount == null ? tr('Not isolated') : formatNumber(component.amount)}</h3></div><span className={`status-badge ${component.status === 'MATCH' ? 'match' : component.status === 'ATTRIBUTED' ? 'completed' : 'paused'}`}>{tr(component.status)}</span></header>
        <p>{tr(component.summary)}</p>
        {component.average_delay_ms != null && <dl className="attribution-delay-facts"><div><dt>{tr('Average delay')}</dt><dd>{formatNumber(component.average_delay_ms)} ms</dd></div><div><dt>{tr('Maximum delay')}</dt><dd>{formatNumber(component.max_delay_ms)} ms</dd></div></dl>}
        {component.evidence.length > 0 && <ul>{component.evidence.map((item) => <li key={item}>{tr(item)}</li>)}</ul>}
      </article>)}
    </section>

    <section className="workspace-panel validation-divergence"><div className="section-heading"><h2>{tr('First Divergence')}</h2><span>{report.first_divergence.layer ? tr(report.first_divergence.layer) : tr('All recorded-feed layers match')}</span></div><div className={`validation-result ${report.first_divergence.status.toLowerCase()}`}><strong>{tr(report.first_divergence.status)}</strong><time>{formatTime(report.first_divergence.timestamp)}</time><p>{tr(report.first_divergence.difference)}</p>{report.first_divergence.status === 'DIVERGENCE' && <div className="validation-values"><code>{report.first_divergence.reference_value}</code><code>{report.first_divergence.paper_value}</code></div>}</div><div className="toolbar"><button disabled={!report.reference_trace_id} onClick={() => report.reference_trace_id && onOpenReplay(report.reference_run_id, report.reference_trace_id, report.first_divergence.reference_event_id)}>{tr('Open Reference Replay')}</button><button disabled={!report.paper_trace_id} onClick={() => report.paper_trace_id && onOpenReplay(report.paper_run_id, report.paper_trace_id, report.first_divergence.paper_event_id)}>{tr('Open Paper Replay')}</button></div></section>

    <section className="workspace-panel validation-checks"><div className="section-heading"><h2>{tr('Comparability checks')}</h2><span>{tr('No cross-period equivalence is inferred')}</span></div><div className="validation-check-table"><div className="validation-check-row header"><span>{tr('Check')}</span><span>{tr('Result')}</span><span>{tr('Backtest')}</span><span>{tr('Paper')}</span></div>{report.checks.map((check) => <div className="validation-check-row" key={check.field}><strong>{tr(check.field.replaceAll('_', ' '))}</strong><span className={`status-badge ${check.same ? 'match' : 'paused'}`}>{tr(check.same ? 'SAME' : 'DIFFERENT')}</span><code>{shortHash(check.reference_value)}</code><code>{shortHash(check.paper_value)}</code></div>)}</div></section>
    <p className="provenance-note attribution-disclosure">{tr(report.note)}</p>
  </main>
}

function RunInspector({ detail, source, annotationDraft, busy, onAnnotationChange, onSave, onLoadSource, onReplay, onDiagnose, onAutopsy, onAudit, onLoadConfiguration, onRerun, onDelete }: {
  detail: RunDetail
  source: StrategySourceArtifact | null
  annotationDraft: RunAnnotations
  busy: string | null
  onAnnotationChange: (value: RunAnnotations) => void
  onSave: () => void
  onLoadSource: () => void
  onReplay: () => void
  onDiagnose: () => void
  onAutopsy: () => void
  onAudit?: () => void
  onLoadConfiguration: () => void
  onRerun: () => void
  onDelete: () => void
}) {
  const { tr } = useI18n()
  const manifest = detail.manifest
  return <aside className="run-inspector workspace-panel" aria-label={tr('Run Inspector')}>
    <div className="run-inspector-head"><div><small><span>{tr('RUN INSPECTOR')}</span> · {tr(manifest.run_type)}</small><h2>{tr(detail.annotations.display_name || manifest.strategy.name)}</h2><code>{manifest.run_id}</code></div><StatusBadge status={manifest.status} /></div>
    {manifest.status === 'PARTIAL' && <div className="partial-trace-banner"><strong>{tr('PARTIAL TRACE')}</strong><span>{tr('The strategy failed after')} {manifest.failure?.event_index ?? tr('an unknown number of')} {tr('bars. Historical events remain inspectable.')}</span></div>}
    {detail.current_source_matches === false && <div className="source-mismatch"><strong>{tr('Current registered source differs from run revision.')}</strong><span>{tr('Replay still uses the immutable saved artifacts.')}</span></div>}
    <div className="inspector-actions"><button type="button" disabled={!manifest.trace_id} onClick={onReplay}>{tr('Open Replay')}</button>{manifest.run_type === 'BACKTEST' && <button type="button" disabled={!manifest.trace_id} onClick={onDiagnose}>{tr('Diagnose')}</button>}<button type="button" disabled={!manifest.trace_id} onClick={onAutopsy}>{tr('P&L Autopsy')}</button>{onAudit && <button type="button" onClick={onAudit}>{tr('Run Data Audit')}</button>}</div>

    <dl className="run-facts">
      <div><dt>{tr('Created')}</dt><dd><RunTime value={manifest.created_at} /></dd></div><div><dt>{tr('Integrity')}</dt><dd>{tr(detail.integrity)}</dd></div>
      <div><dt>{tr('Strategy')}</dt><dd>{tr(manifest.strategy.name)} · v{manifest.strategy.version}</dd></div><div><dt>{tr('Revision')}</dt><dd><code title={manifest.strategy.source_fingerprint}>{shortHash(manifest.strategy.source_fingerprint)}</code></dd></div>
      <div><dt>{tr('Dataset')}</dt><dd>{tr(manifest.dataset.name)}</dd></div><div><dt>{tr('Dataset revision')}</dt><dd><code title={manifest.dataset.content_fingerprint}>{shortHash(manifest.dataset.content_fingerprint)}</code></dd></div>
      <div className="run-fact-period"><dt>{tr('Period')}</dt><dd><RunTime value={manifest.period.start} /><span className="fact-arrow" aria-hidden="true">→</span><RunTime value={manifest.period.end} /></dd></div><div><dt>{tr('Cutoff')}</dt><dd><RunTime value={manifest.period.cutoff} /></dd></div>
      <div><dt>{tr('Execution model')}</dt><dd className="fact-nowrap">{manifest.execution_model.execution_model_id}@{manifest.execution_model.version}</dd></div><div><dt>{tr('Runtime')}</dt><dd>{runtimeLabel(manifest.runtime)} · {tr(manifest.runtime?.trace_fidelity ?? 'FULL')}</dd></div><div><dt>{tr('Engine')}</dt><dd className="fact-engine"><span>VQD {manifest.engine.vqd_version}</span><span>Python {manifest.engine.python_version}</span></dd></div>
    </dl>

    <section className="inspector-section"><div className="run-section-title"><h3>{tr('Parameters')}</h3>{manifest.run_type === 'BACKTEST' && <button type="button" className="link-button" onClick={onLoadConfiguration}>{tr('Load config into Strategy')}</button>}</div><div className="inspector-key-values">{Object.entries(manifest.parameters).map(([key, value]) => <div key={key}><span>{key}</span><code>{value}</code></div>)}</div></section>
    {manifest.metrics && <section className="inspector-section"><h3>{tr('Metrics')}</h3><div className="inspector-key-values"><div><span>{tr('Return')}</span><code>{formatPercent(manifest.metrics.total_return)}</code></div><div><span>{tr('Sharpe')}</span><code>{formatNumber(manifest.metrics.sharpe, 4)}</code></div><div><span>{tr('Max DD')}</span><code>{formatPercent(manifest.metrics.max_drawdown)}</code></div><div><span>{tr('Trades')}</span><code>{manifest.metrics.trades}</code></div><div><span>{tr('Final equity')}</span><code>{formatNumber(manifest.metrics.final_equity)}</code></div><div><span>{tr('Fees / slippage')}</span><code>{formatNumber(manifest.metrics.fees)} / {formatNumber(manifest.metrics.slippage)}</code></div></div></section>}
    <section className="inspector-section"><h3>{tr('Artifacts')}</h3><div className="artifact-list">{Object.entries(detail.artifacts).map(([name, available]) => <span key={name}><i className={available ? 'available' : ''} />{tr(name.replaceAll('_', ' '))}</span>)}</div><button className="link-button" type="button" onClick={onLoadSource}>{tr(source ? 'Refresh strategy snapshot' : 'View strategy snapshot')}</button>{source && <pre className="strategy-source"><code>{source.source}</code></pre>}</section>
    <section className="inspector-section annotation-editor"><h3>{tr('Notes & Tags')}</h3><label>{tr('Display name')}<input aria-label={tr('Run display name')} value={annotationDraft.display_name} onChange={(event) => onAnnotationChange({ ...annotationDraft, display_name: event.target.value })} /></label><label>{tr('Note')}<textarea aria-label={tr('Run note')} value={annotationDraft.note} onChange={(event) => onAnnotationChange({ ...annotationDraft, note: event.target.value })} /></label><label>{tr('Tags')}<input aria-label={tr('Run tags')} value={annotationDraft.tags.join(', ')} onChange={(event) => onAnnotationChange({ ...annotationDraft, tags: event.target.value.split(',').map((tag) => tag.trim()).filter(Boolean) })} /></label><button className="secondary-button" type="button" disabled={busy === 'annotations'} onClick={onSave}>{tr(busy === 'annotations' ? 'Saving…' : 'Save annotations')}</button></section>
    <div className="inspector-footer">{manifest.run_type === 'BACKTEST' && <button className="primary-button" type="button" disabled={busy === 'rerun'} onClick={onRerun}>{tr(busy === 'rerun' ? 'Re-running…' : 'Re-run exact revision')}</button>}<button className="danger-button" type="button" disabled={Boolean(busy)} onClick={onDelete}>{tr('Delete Run')}</button></div>
  </aside>
}

function RunsPage({ strategies, datasets, initialRunId = null, onRunSelection, onOpenReplay, onOpenDiagnose, onOpenAutopsy, onLoadConfiguration, onRunDataAudit, services }: RunsPageProps) {
  const { tr } = useI18n()
  const loadRuns = services?.getRuns ?? getRuns
  const loadRun = services?.getRun ?? getRun
  const persistAnnotations = services?.saveAnnotations ?? saveRunAnnotations
  const removeRun = services?.deleteRun ?? deleteRun
  const reproduceRun = services?.rerun ?? rerunExactRevision
  const loadComparison = services?.compare ?? compareRuns
  const loadValidation = services?.validate ?? validateRuns
  const loadSource = services?.getSource ?? getRunStrategySource
  const [filters, setFilters] = useState<RunFilters>({ limit: 100 })
  const [ledger, setLedger] = useState<RunListResponse | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(initialRunId)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [annotationDraft, setAnnotationDraft] = useState<RunAnnotations>({ display_name: '', note: '', tags: [] })
  const [source, setSource] = useState<StrategySourceArtifact | null>(null)
  const [comparisonSelection, setComparisonSelection] = useState<string[]>([])
  const [comparison, setComparison] = useState<RunComparisonReport | null>(null)
  const [validation, setValidation] = useState<RunValidationReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const hasAutoSelected = useRef(Boolean(initialRunId))

  const refresh = useCallback(async () => {
    setError(null)
    try {
      const next = await loadRuns(filters)
      setLedger(next)
      if (!hasAutoSelected.current && next.items[0]) {
        hasAutoSelected.current = true
        setSelectedRunId(next.items[0].run_id)
        onRunSelection?.(next.items[0].run_id)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : tr('Could not load research runs.'))
    }
  }, [filters, loadRuns, onRunSelection, tr])

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(timer)
  }, [refresh])
  useEffect(() => {
    if (!selectedRunId) return
    let active = true
    void loadRun(selectedRunId).then((next) => {
      if (!active) return
      setDetail(next)
      setAnnotationDraft(next.annotations)
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : tr('Could not load the run.')) })
    return () => { active = false }
  }, [loadRun, selectedRunId, tr])

  function selectRun(runId: string) {
    setDetail(null)
    setSource(null)
    setSelectedRunId(runId)
    onRunSelection?.(runId)
  }

  function toggleComparison(runId: string) {
    setComparisonSelection((current) => current.includes(runId)
      ? current.filter((item) => item !== runId)
      : current.length < 4 ? [...current, runId] : current)
  }

  async function startComparison() {
    if (comparisonSelection.length < 2 || comparisonSelection.length > 4) return
    setBusy('compare'); setError(null)
    try { setComparison(await loadComparison(comparisonSelection)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : tr('Run comparison failed.')) }
    finally { setBusy(null) }
  }

  async function startValidation() {
    if (!ledger || comparisonSelection.length !== 2) return
    const selected = comparisonSelection.map((id) => ledger.items.find((run) => run.run_id === id)).filter((run): run is RunListItem => Boolean(run))
    const backtest = selected.find((run) => run.run_type === 'BACKTEST')
    const paper = selected.find((run) => run.run_type === 'PAPER')
    if (!backtest || !paper) return
    setBusy('validate'); setError(null)
    try { setValidation(await loadValidation(backtest.run_id, paper.run_id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : tr('Run validation failed.')) }
    finally { setBusy(null) }
  }

  async function saveAnnotations() {
    if (!selectedRunId) return
    setBusy('annotations')
    try {
      const saved = await persistAnnotations(selectedRunId, annotationDraft)
      setAnnotationDraft(saved)
      setDetail((current) => current ? { ...current, annotations: saved } : current)
      setLedger((current) => current ? { ...current, items: current.items.map((run) => run.run_id === selectedRunId ? { ...run, annotations: saved } : run) } : current)
    } catch (reason) { setError(reason instanceof Error ? reason.message : tr('Could not save annotations.')) }
    finally { setBusy(null) }
  }

  async function exactRerun() {
    if (!selectedRunId) return
    setBusy('rerun'); setError(null)
    try {
      const created = await reproduceRun(selectedRunId)
      await refresh()
      selectRun(created.run_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : tr('Exact re-run failed.')) }
    finally { setBusy(null) }
  }

  async function confirmDelete() {
    if (!selectedRunId || !window.confirm(`Delete ${selectedRunId} and its run artifacts?`)) return
    setBusy('delete')
    try {
      await removeRun(selectedRunId)
      setSelectedRunId(null); setDetail(null); setComparisonSelection((current) => current.filter((item) => item !== selectedRunId))
      await refresh()
    } catch (reason) { setError(reason instanceof Error ? reason.message : tr('Could not delete the run.')) }
    finally { setBusy(null) }
  }

  if (comparison && ledger) return <ComparisonWorkspace report={comparison} runs={ledger.items} onBack={() => setComparison(null)} onOpenReplay={onOpenReplay} />
  if (validation) return <ValidationWorkspace report={validation} onBack={() => setValidation(null)} onOpenReplay={onOpenReplay} />

  const selectedRunTypes = ledger?.items.filter((run) => comparisonSelection.includes(run.run_id)).map((run) => run.run_type) ?? []
  const canValidate = comparisonSelection.length === 2 && selectedRunTypes.includes('BACKTEST') && selectedRunTypes.includes('PAPER')

  return <main className="runs-shell">
    <header className="workspace-title"><div><h1>{tr('Research Runs')}</h1><span>{tr('Durable, immutable research records')}</span></div><div className="toolbar"><span>{comparisonSelection.length} {tr('selected')}</span>{canValidate && <button className="primary-button" type="button" disabled={busy === 'validate'} onClick={() => void startValidation()}>{tr(busy === 'validate' ? 'Attributing…' : 'Attribute')}</button>}<button type="button" disabled={comparisonSelection.length < 2 || comparisonSelection.length > 4 || busy === 'compare'} onClick={() => void startComparison()}>{tr(busy === 'compare' ? 'Comparing…' : 'Compare')}</button></div></header>
    <section className="run-filterbar" aria-label={tr('Run filters')}>
      <label>{tr('Search')}<input aria-label={tr('Search runs')} placeholder={tr('Run ID, name, tag')} value={filters.search ?? ''} onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))} /></label>
      <label>{tr('Strategy')}<select aria-label={tr('Strategy filter')} value={filters.strategy_id ?? ''} onChange={(event) => setFilters((current) => ({ ...current, strategy_id: event.target.value }))}><option value="">{tr('All strategies')}</option>{strategies.map((strategy) => <option key={strategy.strategy_id} value={strategy.strategy_id}>{tr(strategy.name)}</option>)}</select></label>
      <label>{tr('Dataset')}<select aria-label={tr('Dataset filter')} value={filters.dataset_id ?? ''} onChange={(event) => setFilters((current) => ({ ...current, dataset_id: event.target.value }))}><option value="">{tr('All datasets')}</option>{datasets.map((dataset) => <option key={dataset.dataset_id} value={dataset.dataset_id}>{tr(dataset.name)}</option>)}</select></label>
      <label>{tr('Status')}<select aria-label={tr('Status filter')} value={filters.status ?? ''} onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value as RunStatus | '' }))}><option value="">{tr('All statuses')}</option><option value="COMPLETED">{tr('COMPLETED')}</option><option value="PARTIAL">{tr('PARTIAL')}</option><option value="FAILED">{tr('FAILED')}</option><option value="RUNNING">{tr('RUNNING')}</option></select></label>
    </section>
    {error && <div className="compact-error" role="alert"><span>{error}</span><button type="button" onClick={() => void refresh()}>{tr('Retry')}</button></div>}
    {!ledger && !error && <p className="loading-line">{tr('Loading research ledger…')}</p>}
    {ledger?.items.length === 0 && <section className="workspace-panel run-empty"><h2>{tr('No research runs yet.')}</h2><p>{tr('Run a strategy to create the first record.')}</p></section>}
    {ledger && ledger.items.length > 0 && <div className="run-ledger-layout">
      <section className="workspace-panel run-table-panel" aria-label={tr('Research Ledger')}>
        <div className="run-table-scroll"><table className="run-table"><thead><tr><th aria-label={tr('Compare selection')} /><th>{tr('Time')}</th><th>{tr('Run')}</th><th>{tr('Strategy')}</th><th>{tr('Dataset')}</th><th>{tr('Parameters')}</th><th>{tr('Return')}</th><th>{tr('Sharpe')}</th><th>{tr('Max DD')}</th><th>{tr('Trades')}</th><th>{tr('Status')}</th><th>{tr('Actions')}</th></tr></thead><tbody>{ledger.items.map((run) => <tr key={run.run_id} className={selectedRunId === run.run_id ? 'selected' : ''} onClick={() => selectRun(run.run_id)}><td><input aria-label={`${tr('Select')} ${run.run_id} ${tr('for comparison')}`} type="checkbox" checked={comparisonSelection.includes(run.run_id)} disabled={!comparisonSelection.includes(run.run_id) && comparisonSelection.length >= 4} onClick={(event) => event.stopPropagation()} onChange={() => toggleComparison(run.run_id)} /></td><td><RunTime value={run.created_at} /></td><td><button className="run-id-button" type="button" onClick={() => selectRun(run.run_id)}>{tr(run.annotations.display_name || run.run_id)}</button><code>{run.run_id}</code><small>{tr(run.run_type)}</small></td><td>{tr(run.strategy_name)}<small>{runtimeLabel(run.runtime)} · {tr(run.runtime?.trace_fidelity ?? 'FULL')}</small><small>{shortHash(run.strategy_fingerprint)}</small></td><td>{tr(run.dataset_name)}<small>{shortHash(run.dataset_fingerprint)}</small></td><td><code>{parameterSummary(run.parameters)}</code></td><td><code>{formatPercent(run.metrics?.total_return)}</code></td><td><code>{formatNumber(run.metrics?.sharpe)}</code></td><td><code>{formatPercent(run.metrics?.max_drawdown)}</code></td><td><code>{run.metrics?.trades ?? '—'}</code></td><td><StatusBadge status={run.status} /></td><td><RunActions run={run} onReplay={() => run.trace_id && onOpenReplay(run.run_id, run.trace_id)} onDiagnose={() => run.trace_id && onOpenDiagnose(run.run_id, run.trace_id)} onAutopsy={() => run.trace_id && onOpenAutopsy(run.run_id, run.trace_id)} /></td></tr>)}</tbody></table></div>
        <footer className="run-table-footer"><span>{ledger.total} {tr('research records · newest first')}</span><span>{tr('Select 2–4 runs to compare')}</span></footer>
      </section>
      {selectedRunId && !detail && <aside className="run-inspector workspace-panel"><p className="loading-line">{tr('Loading Run Inspector…')}</p></aside>}
      {detail && <RunInspector detail={detail} source={source} annotationDraft={annotationDraft} busy={busy} onAnnotationChange={setAnnotationDraft} onSave={() => void saveAnnotations()} onLoadSource={() => void loadSource(detail.manifest.run_id).then(setSource).catch((reason) => setError(reason instanceof Error ? reason.message : tr('Could not load source snapshot.')))} onReplay={() => detail.manifest.trace_id && onOpenReplay(detail.manifest.run_id, detail.manifest.trace_id)} onDiagnose={() => detail.manifest.trace_id && onOpenDiagnose(detail.manifest.run_id, detail.manifest.trace_id)} onAutopsy={() => detail.manifest.trace_id && onOpenAutopsy(detail.manifest.run_id, detail.manifest.trace_id)} onAudit={onRunDataAudit ? () => onRunDataAudit(detail.manifest.run_id) : undefined} onLoadConfiguration={() => onLoadConfiguration({ strategy_id: detail.manifest.strategy.strategy_id, dataset_id: detail.manifest.dataset.dataset_id, parameters: detail.manifest.parameters, research_cutoff: detail.manifest.period.cutoff })} onRerun={() => void exactRerun()} onDelete={() => void confirmDelete()} />}
    </div>}
  </main>
}

export default RunsPage
