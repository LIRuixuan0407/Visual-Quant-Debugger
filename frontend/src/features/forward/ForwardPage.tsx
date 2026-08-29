import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  createForwardSession,
  getForwardComparison,
  getForwardSession,
  getForwardTrace,
  pauseForwardSession,
  resumeForwardSession,
  startForwardSession,
  stepForwardSession,
  stopForwardSession,
} from '../../api/forward'
import { useI18n } from '../../i18n/I18nProvider'
import type { ForwardComparisonReport, ForwardSessionSnapshot, ForwardTrace } from '../../types/forward'
import type { StrategyDefinition, StrategyParameters } from '../../types/strategy'
import ReplayTimeline from '../replay/ReplayTimeline'
import { DependenciesPanel, ExecutionOutcomePanel, MarketPositionPanel, StrategyDecisionPanel } from '../replay/ReplayInspectors'
import SignalLineage from '../replay/SignalLineage'
import { createReplayIndex, findSourceSignalEvent } from '../replay/utils/navigation'
import { formatCurrency, formatPercent, formatTimestamp } from '../replay/utils/format'
import { defaultsFromDefinition, validateParameters } from '../strategy/utils/parameters'

interface ForwardPageProps {
  definition: StrategyDefinition
  configuration?: {
    strategy_id: string
    dataset_id: string
    parameters: StrategyParameters
    research_cutoff: string | null
  } | null
  sessionId: string | null
  onSessionChange: (sessionId: string | null) => void
  initialEventId?: string | null
}

function metric(value: number, status?: string) {
  return status && status !== 'OK' ? 'N/A' : value.toFixed(2)
}

function ForwardSetup({ definition, configuration, onCreated }: { definition: StrategyDefinition; configuration?: ForwardPageProps['configuration']; onCreated: (snapshot: ForwardSessionSnapshot) => void }) {
  const { tr } = useI18n()
  const demo = definition.presets.find((preset) => preset.preset_id === 'demo-active-signals')
  const [presetId, setPresetId] = useState(demo?.preset_id ?? definition.presets[0]?.preset_id ?? '')
  const [draft, setDraft] = useState<StrategyParameters>(() => configuration?.parameters ?? demo?.parameters ?? defaultsFromDefinition(definition))
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const errors = validateParameters(definition, draft)
  const firstValidationError = Object.values(errors)[0]
  const valid = Object.keys(errors).length === 0

  function selectPreset(id: string) {
    setPresetId(id)
    const preset = definition.presets.find((item) => item.preset_id === id)
    if (preset) setDraft({ ...preset.parameters })
  }

  async function create() {
    if (!valid || creating) return
    setCreating(true); setError(null)
    try {
      onCreated(await createForwardSession(configuration
        ? { ...configuration, parameters: draft }
        : draft))
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : tr('Forward session creation failed.')) }
    finally { setCreating(false) }
  }

  return <main className="forward-shell">
    <header className="workspace-title"><h1>{tr('Forward Validation')}</h1><span>{tr('Historical bars are revealed one at a time. No live market feed is used.')}</span></header>
    <section className="workspace-panel forward-setup">
      <div className="section-heading"><h2>{tr('Session configuration')}</h2><code>forward-demo-v1</code></div>
      {configuration && <div className="strategy-requirements"><span>{tr('Strategy')} <code>{configuration.strategy_id}</code></span><span>{tr('Dataset')} <code>{configuration.dataset_id}</code></span><span>{tr('Research cutoff')} <code>{configuration.research_cutoff ?? tr('required')}</code></span></div>}
      {configuration && configuration.dataset_id !== 'forward-demo-v1' && !configuration.research_cutoff && <p className="inline-error">{tr('Set an explicit Research cutoff in Strategy before revealing the Forward holdout.')}</p>}
      <div className="dense-form-row"><label>{tr('Preset')}<select value={presetId} onChange={(event) => selectPreset(event.target.value)}>{definition.presets.map((preset) => <option key={preset.preset_id} value={preset.preset_id}>{tr(preset.name)}</option>)}</select></label></div>
      <div className="parameter-table forward-parameter-table" role="table">
        <div className="parameter-table-row header" role="row"><span>{tr('Parameter')}</span><span>{tr('Current value')}</span><span className="parameter-default-heading">{tr('Default')}</span><span>{tr('Description')}</span></div>
        {definition.parameters.map((parameter) => <label className="parameter-table-row" key={parameter.key}>
          <span className="parameter-name-cell"><strong>{tr(parameter.label)}</strong></span>
          <span className="parameter-value-cell"><input type="number" value={draft[parameter.key]} min={parameter.minimum} max={parameter.maximum ?? undefined} step={parameter.step} onChange={(event) => setDraft((current) => ({ ...current, [parameter.key]: Number(event.target.value) }))} /><small>{tr(parameter.unit)}</small></span>
          <span className="parameter-default-cell"><code>{String(parameter.default_value)}</code><small>{tr(parameter.unit)}</small></span>
          <span className="parameter-description-cell">{tr(parameter.description)}</span>
        </label>)}
      </div>
      {firstValidationError && <p className="inline-error">{tr(firstValidationError)}</p>}
      {error && <p className="inline-error">{error}</p>}
      <div className="toolbar end"><button className="primary-button" disabled={!valid || creating || Boolean(configuration && configuration.dataset_id !== 'forward-demo-v1' && !configuration.research_cutoff)} onClick={() => void create()}>{creating ? tr('Creating…') : tr('Create session')}</button></div>
    </section>
  </main>
}

function Comparison({ report }: { report: ForwardComparisonReport | null }) {
  const { tr } = useI18n()
  if (!report) return null
  return <section className="workspace-panel comparison-panel">
    <div className="section-heading"><h2>{tr('Research vs Forward')}</h2><span>{tr('Different evaluation periods')}</span></div>
    <div className="comparison-table" role="table">
      <div className="comparison-row header"><span>{tr('Metric')}</span><span>{tr('Historical research')}</span><span>{tr('Forward holdout')}</span></div>
      <div className="comparison-row"><span>{tr('Return')}</span><code>{formatPercent(report.research.total_return)}</code><code>{formatPercent(report.forward.total_return)}</code></div>
      <div className="comparison-row"><span>{tr('Sharpe')}</span><code>{metric(report.research.sharpe)}</code><code>{metric(report.forward.sharpe)}</code></div>
      <div className="comparison-row"><span>{tr('Max drawdown')}</span><code>{formatPercent(report.research.max_drawdown)}</code><code>{formatPercent(report.forward.max_drawdown)}</code></div>
      <div className="comparison-row"><span>{tr('Trades')}</span><code>{report.research.trades}</code><code>{report.forward.trades}</code></div>
      <div className="comparison-row"><span>{tr('Final equity')}</span><code>{formatCurrency(report.research.final_equity)}</code><code>{formatCurrency(report.forward.final_equity)}</code></div>
    </div>
    <div className="section-heading sub"><h2>{tr('Batch vs Streaming Consistency')}</h2><span className={`status-badge ${report.consistency_status.toLowerCase()}`}>{tr(report.consistency_status)}</span></div>
    <div className="consistency-grid">{report.consistency.map((check) => <div key={check.field}><span>{tr(check.field)}</span><strong>{tr(check.status)}</strong></div>)}</div>
    {report.first_divergence && <div className="inline-error"><strong>{tr('First divergence')} · {tr(report.first_divergence.field)}</strong><code>{String(report.first_divergence.batch_value)} → {String(report.first_divergence.forward_value)}</code></div>}
  </section>
}

function ForwardWorkspace({ strategyName, snapshot, trace, comparison, onSnapshot, initialEventId }: { strategyName: string; snapshot: ForwardSessionSnapshot; trace: ForwardTrace; comparison: ForwardComparisonReport | null; onSnapshot: (snapshot: ForwardSessionSnapshot) => void; initialEventId?: string | null }) {
  const { tr } = useI18n()
  const [selectedEventId, setSelectedEventId] = useState(initialEventId ?? trace.timeline.at(-1)?.event_id ?? '')
  const [selectedFeatureId, setSelectedFeatureId] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const stepping = useRef(false)
  const index = useMemo(() => createReplayIndex({ trace_version: '1.0', metadata: { dataset_id: snapshot.dataset_id, dataset_name: snapshot.dataset_id, bar_count: trace.timeline.length, data_start: trace.timeline[0]?.timestamp ?? new Date(0).toISOString(), data_end: trace.timeline.at(-1)?.timestamp ?? new Date(0).toISOString(), execution_model: 'signal at close(t); execute at close(t+1)' }, strategy: { strategy_id: snapshot.strategy_id, name: strategyName }, parameters: trace.parameters, timeline: trace.timeline, trades: [], metrics: {}, diagnostics: trace.diagnostics }), [snapshot, strategyName, trace])
  const effectiveSelectedEventId = index.eventById.has(selectedEventId)
    ? selectedEventId
    : (trace.timeline.at(-1)?.event_id ?? '')
  const event = index.eventById.get(effectiveSelectedEventId) ?? null
  const allFeatures = useMemo(() => Array.from(index.featureById.values()), [index])
  const rootFeatureId = event?.signal_evaluation.dependencies[0] ?? event?.feature_snapshots.find((feature) => feature.name === 'zscore')?.feature_id ?? null
  const selectedFeature = selectedFeatureId ? index.featureById.get(selectedFeatureId) ?? null : rootFeatureId ? index.featureById.get(rootFeatureId) ?? null : null

  const step = useCallback(async () => {
    if (stepping.current || snapshot.status !== 'RUNNING') return
    stepping.current = true
    try { onSnapshot(await stepForwardSession(snapshot.session_id)) }
    finally { stepping.current = false }
  }, [onSnapshot, snapshot.session_id, snapshot.status])

  const isPlaying = playing && snapshot.status === 'RUNNING'
  useEffect(() => {
    if (!isPlaying) return
    const timer = window.setInterval(() => void step(), 500)
    return () => window.clearInterval(timer)
  }, [isPlaying, step])

  async function action(kind: 'start' | 'pause' | 'resume' | 'stop') {
    const fn = kind === 'start' ? startForwardSession : kind === 'pause' ? pauseForwardSession : kind === 'resume' ? resumeForwardSession : stopForwardSession
    onSnapshot(await fn(snapshot.session_id))
  }

  const activePending = snapshot.pending_transitions.filter((item) => item.status === 'PENDING')
  return <main className="forward-shell">
    <header className="workspace-title forward-title"><div><h1>{tr('Forward Validation')}</h1><span><code>{snapshot.session_id}</code> · {snapshot.processed_bar_count}/{snapshot.total_bar_count} {tr('bars')}</span></div><span className={`status-badge ${snapshot.status.toLowerCase()}`}>{tr(snapshot.status)}</span></header>
    <div className="toolbar forward-controls">
      {snapshot.status === 'CREATED' && <button className="primary-button" onClick={() => void action('start')}>{tr('Start')}</button>}
      {snapshot.status === 'RUNNING' && <><button onClick={() => void step()}>{tr('Step')}</button><button aria-label={tr(isPlaying ? 'Pause playback' : 'Play forward session')} onClick={() => setPlaying((value) => !value)}>{tr(isPlaying ? 'Pause play' : 'Play')}</button><button onClick={() => void action('pause')}>{tr('Pause session')}</button></>}
      {snapshot.status === 'PAUSED' && <button className="primary-button" onClick={() => void action('resume')}>{tr('Resume')}</button>}
      {['CREATED', 'RUNNING', 'PAUSED'].includes(snapshot.status) && <button className="ghost-button" onClick={() => void action('stop')}>{tr('Stop')}</button>}
      <span className="toolbar-spacer" />
      <code>{snapshot.current_timestamp ? `${formatTimestamp(snapshot.current_timestamp).date} ${formatTimestamp(snapshot.current_timestamp).time}` : tr('No bars processed')}</code>
    </div>
    {trace.timeline.length > 0 && <ReplayTimeline events={trace.timeline} selectedEventId={effectiveSelectedEventId} onSelect={(id) => { setSelectedEventId(id); setSelectedFeatureId(null) }} />}
    <section className="metric-strip"><div><span>{tr('Cash')}</span><strong>{formatCurrency(snapshot.cash)}</strong></div><div><span>{tr('Equity')}</span><strong>{formatCurrency(snapshot.equity)}</strong></div><div><span>{tr('P&L')}</span><strong>{formatCurrency(snapshot.cumulative_pnl)}</strong></div><div><span>{tr('Fees')}</span><strong>{formatCurrency(snapshot.cumulative_fees)}</strong></div><div><span>{tr('Slippage')}</span><strong>{formatCurrency(snapshot.cumulative_slippage)}</strong></div></section>
    {event && <>
      <div className="inspector-grid"><MarketPositionPanel event={event} /><StrategyDecisionPanel event={event} /></div>
      <div className="evidence-grid"><SignalLineage evaluation={event.signal_evaluation} allFeatures={allFeatures} rootFeatureId={rootFeatureId} selectedFeature={selectedFeature} onSelectFeature={setSelectedFeatureId} /><DependenciesPanel event={event} diagnostics={trace.diagnostics} /></div>
      <ExecutionOutcomePanel event={event} index={index} sourceSignalEvent={findSourceSignalEvent(event, index)} executionEvent={null} onSelect={setSelectedEventId} />
    </>}
    <section className="workspace-panel pending-panel"><div className="section-heading"><h2>{tr('Pending transitions')}</h2><span>{activePending.length} {tr('active')}</span></div>{snapshot.pending_transitions.length === 0 ? <p className="empty-state">{tr('No strategy transitions have been scheduled.')}</p> : <div className="dense-table"><div className="dense-row header"><span>{tr('Status')}</span><span>{tr('Signal')}</span><span>{tr('Target')}</span><span>{tr('Scheduled bar')}</span></div>{snapshot.pending_transitions.slice().reverse().map((item) => <div className="dense-row" key={item.pending_id}><span>{tr(item.status)}</span><code>{item.source_signal_id}</code><code>{item.target_position}</code><code>{item.scheduled_bar_index + 1}</code></div>)}</div>}</section>
    <Comparison report={comparison} />
  </main>
}

function HistoricalForwardPage({ definition, configuration, sessionId, onSessionChange, initialEventId }: ForwardPageProps) {
  const { tr } = useI18n()
  const [snapshot, setSnapshot] = useState<ForwardSessionSnapshot | null>(null)
  const [trace, setTrace] = useState<ForwardTrace | null>(null)
  const [comparison, setComparison] = useState<ForwardComparisonReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async (id: string) => {
    try {
      const [nextSnapshot, nextTrace, nextComparison] = await Promise.all([getForwardSession(id), getForwardTrace(id), getForwardComparison(id)])
      setSnapshot(nextSnapshot); setTrace(nextTrace); setComparison(nextComparison); setError(null)
    } catch (reason) { setError(reason instanceof Error ? reason.message : tr('Forward session failed.')) }
  }, [tr])

  useEffect(() => {
    if (!sessionId) return
    const timer = window.setTimeout(() => void refresh(sessionId), 0)
    return () => window.clearTimeout(timer)
  }, [refresh, sessionId])

  const updateSnapshot = useCallback((next: ForwardSessionSnapshot) => {
    setSnapshot(next)
    void Promise.all([getForwardTrace(next.session_id), getForwardComparison(next.session_id)]).then(([nextTrace, nextComparison]) => { setTrace(nextTrace); setComparison(nextComparison) }).catch((reason) => setError(reason instanceof Error ? reason.message : tr('Forward refresh failed.')))
  }, [tr])

  if (!sessionId) return <ForwardSetup definition={definition} configuration={configuration} onCreated={(created) => { onSessionChange(created.session_id); setSnapshot(created); setTrace({ trace_version: '1.0', session_id: created.session_id, strategy_id: created.strategy_id, parameters: created.parameters, timeline: [], diagnostics: [] }); setComparison(null) }} />
  if (error) return <main className="forward-shell"><section className="compact-error" role="alert"><strong>{tr('Forward session unavailable')}</strong><span>{tr(error)}</span><button onClick={() => void refresh(sessionId)}>{tr('Retry')}</button></section></main>
  if (!snapshot || !trace) return <main className="forward-shell"><p className="loading-line">{tr('Loading forward session…')}</p></main>
  return <ForwardWorkspace strategyName={definition.name} snapshot={snapshot} trace={trace} comparison={comparison} onSnapshot={updateSnapshot} initialEventId={initialEventId} />
}

export default function ForwardPage(props: ForwardPageProps) {
  const { tr } = useI18n()
  if (props.definition.historical_research_only) return <main className="forward-shell"><section className="workspace-panel capability-blocked"><h1>{tr('Native runtime required')}</h1><p>{tr('Framework strategies are historical-research adapters and cannot run in Forward or Live Paper.')}</p></section></main>
  return <HistoricalForwardPage {...props} />
}
