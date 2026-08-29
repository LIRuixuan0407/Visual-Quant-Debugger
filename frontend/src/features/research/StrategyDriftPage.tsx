import { useEffect, useMemo, useState } from 'react'

import { createStrategyDriftReport, getStrategyDriftReport, getStrategyDriftReports } from '../../api/strategyDrift'
import { useI18n } from '../../i18n/I18nProvider'
import type {
  CreateStrategyDriftReport,
  DriftDimension,
  DriftMetricStatus,
  StrategyDriftReport,
  StrategyDriftSummary,
} from '../../types/strategyDrift'

interface StrategyDriftPageProps {
  initialReportId?: string | null
  onOpenReplay: (traceId: string, eventId: string) => void
  onOpenForward: (sessionId: string, eventId: string) => void
}

const dimensions: DriftDimension[] = ['FACTOR', 'SIGNAL', 'TURNOVER', 'EXPOSURE', 'PERFORMANCE']

function shortId(value: string): string {
  return value.length <= 22 ? value : `${value.slice(0, 18)}…`
}

function dateTime(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short',
  }).format(new Date(value))
}

function metricValue(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 100) return value.toFixed(1)
  if (Math.abs(value) >= 1) return value.toFixed(3)
  return value.toFixed(4)
}

function statusClass(status: DriftMetricStatus | StrategyDriftReport['overall_status']): string {
  return status.toLowerCase().replaceAll('_', '-')
}

export default function StrategyDriftPage({ initialReportId, onOpenReplay, onOpenForward }: StrategyDriftPageProps) {
  const { tr } = useI18n()
  const [summaries, setSummaries] = useState<StrategyDriftSummary[]>([])
  const [report, setReport] = useState<StrategyDriftReport | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(initialReportId ?? null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState<CreateStrategyDriftReport>({
    baseline_type: 'RUN', baseline_id: '', observed_type: 'FORWARD_SESSION', observed_id: '', window_bars: 20,
  })

  useEffect(() => {
    let active = true
    void getStrategyDriftReports().then(async (rows) => {
      if (!active) return
      setSummaries(rows)
      const requested = initialReportId && rows.some((item) => item.drift_report_id === initialReportId)
        ? initialReportId
        : rows[0]?.drift_report_id ?? null
      setSelectedId(requested)
      if (requested) {
        const detail = await getStrategyDriftReport(requested)
        if (active) setReport(detail)
      }
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : String(reason))
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [initialReportId])

  async function selectReport(reportId: string) {
    setBusy(true)
    setError(null)
    try {
      const detail = await getStrategyDriftReport(reportId)
      setSelectedId(reportId)
      setReport(detail)
      window.history.replaceState({}, '', `/strategy-drift?report_id=${encodeURIComponent(reportId)}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function createReport() {
    if (!form.baseline_id.trim() || !form.observed_id.trim()) return
    setBusy(true)
    setError(null)
    try {
      const created = await createStrategyDriftReport({
        ...form,
        baseline_id: form.baseline_id.trim(),
        observed_id: form.observed_id.trim(),
      })
      const rows = await getStrategyDriftReports()
      setSummaries(rows)
      setSelectedId(created.drift_report_id)
      setReport(created)
      window.history.replaceState({}, '', `/strategy-drift?report_id=${encodeURIComponent(created.drift_report_id)}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const firstTraceId = report?.observed.trace_id ?? null
  const firstDrift = useMemo(() => report?.first_drift_dimension
    ? report.dimensions.find((item) => item.dimension === report.first_drift_dimension) ?? null
    : null, [report])

  return <main className="discover-shell drift-shell">
    <section className="discover-title">
      <div><span className="section-kicker">{tr('BEHAVIORAL EVIDENCE')}</span><h1>{tr('Strategy Drift')}</h1><p>{tr('Compare a fixed historical baseline with recorded Forward or Paper evidence, then locate the first material behavioral change.')}</p></div>
      <span className="bias-tag">{tr('Detect + locate · no causal attribution')}</span>
    </section>

    {error && <section className="workspace-panel research-error" role="alert">{tr(error)}</section>}

    <section className="drift-layout">
      <aside className="workspace-panel drift-ledger">
        <div className="section-heading"><h2>{tr('Drift Reports')}</h2><span>{summaries.length}</span></div>
        {summaries.length === 0 && !loading && <p className="empty-copy">{tr('No Strategy Drift reports yet.')}</p>}
        {summaries.map((item) => <button key={item.drift_report_id} className={selectedId === item.drift_report_id ? 'selected' : ''} disabled={busy} onClick={() => void selectReport(item.drift_report_id)}>
          <span><strong>{tr(item.overall_status)}</strong><code>{shortId(item.drift_report_id)}</code></span>
          <small>{tr(item.baseline_type)} → {tr(item.observed_type)}</small>
          <small>{item.first_drift_dimension ? `${tr('First Drift')} · ${tr(item.first_drift_dimension)}` : tr('No material drift located')}</small>
        </button>)}
      </aside>

      <div className="drift-stack">
        <section className="workspace-panel drift-create">
          <div className="section-heading"><div><span className="section-kicker">{tr('Explicit evidence selection')}</span><h2>{tr('Create Drift Report')}</h2></div><span>{tr('Rule version')} 1.0</span></div>
          <div className="drift-create-grid">
            <label><span>{tr('Baseline type')}</span><select value={form.baseline_type} onChange={(event) => setForm((current) => ({ ...current, baseline_type: event.target.value as CreateStrategyDriftReport['baseline_type'] }))}><option value="RUN">RUN</option><option value="SNAPSHOT">SNAPSHOT</option></select></label>
            <label><span>{tr('Baseline ID')}</span><input value={form.baseline_id} onChange={(event) => setForm((current) => ({ ...current, baseline_id: event.target.value }))} placeholder="run-… / snapshot-…" /></label>
            <label><span>{tr('Observed type')}</span><select value={form.observed_type} onChange={(event) => setForm((current) => ({ ...current, observed_type: event.target.value as CreateStrategyDriftReport['observed_type'] }))}><option value="FORWARD_SESSION">FORWARD_SESSION</option><option value="PAPER_SESSION">PAPER_SESSION</option><option value="PAPER_RUN">PAPER_RUN</option></select></label>
            <label><span>{tr('Observed ID')}</span><input value={form.observed_id} onChange={(event) => setForm((current) => ({ ...current, observed_id: event.target.value }))} placeholder="forward-… / paper-… / run-…" /></label>
            <label><span>{tr('Window bars')}</span><input type="number" min={5} max={500} value={form.window_bars} onChange={(event) => setForm((current) => ({ ...current, window_bars: Number(event.target.value) }))} /></label>
            <button className="primary-button" disabled={busy || !form.baseline_id.trim() || !form.observed_id.trim()} onClick={() => void createReport()}>{tr(busy ? 'Working…' : 'Create Report')}</button>
          </div>
        </section>

        {loading && <section className="workspace-panel workspace-empty"><span>···</span><h2>{tr('Loading Strategy Drift…')}</h2></section>}
        {!loading && !report && <section className="workspace-panel workspace-empty"><span>Δ</span><h2>{tr('Select or create a Drift Report')}</h2><p>{tr('Reports are immutable snapshots of the evidence available at creation time.')}</p></section>}

        {report && <>
          <section className="workspace-panel drift-overview">
            <div className="section-heading"><div><span className="section-kicker">{tr('Immutable drift evidence')}</span><h2>{tr('Overall Drift')}</h2></div><span className={`drift-status ${statusClass(report.overall_status)}`}>{tr(report.overall_status)}</span></div>
            <div className="drift-source-grid">
              <article><span>{tr('Baseline')}</span><strong>{tr(report.baseline.source_type)}</strong><code>{report.baseline.source_id}</code><small>{report.baseline.sample_size} {tr('bars')} · {shortId(report.baseline.dataset_revision ?? report.baseline.dataset_id)}</small></article>
              <article><span>{tr('Observed')}</span><strong>{tr(report.observed.source_type)}</strong><code>{report.observed.source_id}</code><small>{report.observed.sample_size} {tr('bars')} · {tr(report.observed.status)} · {dateTime(report.observed.observed_until)}</small></article>
              <article><span>{tr('Comparability')}</span><strong>{tr(report.comparability)}</strong><small>{report.comparability_checks.filter((item) => !item.same).map((item) => tr(item.field)).join(' · ') || tr('Exact strategy configuration')}</small></article>
            </div>
          </section>

          <section className="drift-dimensions" aria-label={tr('Five drift dimensions')}>
            {dimensions.map((dimension) => {
              const row = report.dimensions.find((item) => item.dimension === dimension)
              if (!row) return null
              return <article key={dimension} className="workspace-panel drift-dimension-card">
                <header><h3>{tr(`${dimension} Drift`)}</h3><span className={`drift-status ${statusClass(row.status)}`}>{tr(row.status)}</span></header>
                {row.status === 'INSUFFICIENT_EVIDENCE' && <p className="drift-insufficient">{tr('Insufficient evidence')}</p>}
                <div className="drift-metrics">{row.metrics.map((metric) => <div key={metric.metric}><span>{tr(metric.metric)}</span><strong>{metricValue(metric.observed_value)}</strong><small>{tr('Baseline')} {metricValue(metric.baseline_value)} · Δz {metricValue(metric.normalized_distance)}</small></div>)}</div>
                <p>{row.evidence.map((item) => tr(item)).join(' · ')}</p>
              </article>
            })}
          </section>

          <section className="workspace-panel drift-first">
            <div><span className="section-kicker">{tr('First Drift')}</span><h2>{firstDrift ? `${tr(firstDrift.dimension)} · ${dateTime(report.first_drift_at)}` : tr('No material drift located')}</h2><p>{firstDrift ? tr('The first complete observed window where a dimension crossed the deterministic DRIFT threshold.') : tr('No complete observed window crossed the deterministic DRIFT threshold.')}</p></div>
            {report.first_drift_event_id && firstTraceId
              ? <button className="primary-button" onClick={() => onOpenReplay(firstTraceId, report.first_drift_event_id!)}>{tr('Open Replay')}</button>
              : report.first_drift_event_id && report.observed.source_type === 'FORWARD_SESSION'
                ? <button className="primary-button" onClick={() => onOpenForward(report.observed.source_id, report.first_drift_event_id!)}>{tr('Open Replay')}</button>
                : report.first_drift_event_id && <span className="evidence-label">{tr('Replay is unavailable until this live source is persisted as a Trace.')}</span>}
          </section>

          <section className="workspace-panel drift-timeline">
            <div className="section-heading"><div><span className="section-kicker">{tr('Observed windows')}</span><h2>{tr('Drift Timeline')}</h2></div><span>{report.timeline.length} {tr('windows')}</span></div>
            <div className="drift-timeline-table" role="table">
              <div className="drift-timeline-row header" role="row"><span>{tr('Window')}</span><span>{tr('Time')}</span>{dimensions.map((dimension) => <span key={dimension}>{tr(dimension)}</span>)}</div>
              {report.timeline.map((window) => <div className={`drift-timeline-row ${window.complete ? '' : 'partial'}`} role="row" key={window.window_index}><strong>#{window.window_index}</strong><span>{dateTime(window.end_at)}<small>{window.sample_size} {tr('bars')}{!window.complete ? ` · ${tr('Incomplete')}` : ''}</small></span>{dimensions.map((dimension) => { const item = window.dimensions.find((entry) => entry.dimension === dimension); return <span key={dimension} className={`drift-status ${item ? statusClass(item.status) : ''}`}>{item ? tr(item.status) : '—'}</span> })}</div>)}
            </div>
          </section>
          <p className="workspace-disclosure">{tr(report.disclosure)}</p>
        </>}
      </div>
    </section>
  </main>
}
