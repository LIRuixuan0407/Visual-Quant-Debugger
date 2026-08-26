import { useMemo, useState } from 'react'

import type { BacktestTrace } from '../../types/trace'
import { useI18n } from '../../i18n/I18nProvider'
import ReplayControls from './ReplayControls'
import { DependenciesPanel, ExecutionOutcomePanel, MarketPositionPanel, StrategyDecisionPanel } from './ReplayInspectors'
import ReplayTimeline from './ReplayTimeline'
import SignalLineage from './SignalLineage'
import { capabilitiesFor, runtimeLabel } from './capabilities'
import { formatTimestamp } from './utils/format'
import { adjacentBarId, adjacentSignalId, createReplayIndex, findExecutionEventForSignal, findSourceSignalEvent } from './utils/navigation'

function ReplayPage({ trace, initialEventId, onDiagnose, onAutopsy }: { trace: BacktestTrace; initialEventId?: string | null; onDiagnose?: () => void; onAutopsy?: () => void }) {
  const { tr } = useI18n()
  const initialSelection = initialEventId && trace.timeline.some((event) => event.event_id === initialEventId) ? initialEventId : trace.timeline[0]?.event_id ?? ''
  const [selectedEventId, setSelectedEventId] = useState(initialSelection)
  const index = useMemo(() => createReplayIndex(trace), [trace])
  const allFeatures = useMemo(() => Array.from(index.featureById.values()), [index])
  const selectedEvent = index.eventById.get(selectedEventId) ?? trace.timeline[0]
  const rootFeatureId = selectedEvent
    ? (selectedEvent.signal_evaluation.dependencies[0]
      ?? selectedEvent.feature_snapshots.find((feature) => feature.name === 'zscore')?.feature_id
      ?? null)
    : null
  const [selectedFeatureId, setSelectedFeatureId] = useState<string | null>(null)

  if (!selectedEvent) {
    return <main className="startup-shell"><section className="startup-message"><h1>{tr('No timeline events were recorded.')}</h1></section></main>
  }

  const effectiveFeatureId = selectedFeatureId ?? rootFeatureId
  const selectedFeature = effectiveFeatureId ? (index.featureById.get(effectiveFeatureId) ?? null) : null
  const previousBarId = adjacentBarId(trace, selectedEventId, -1)
  const nextBarId = adjacentBarId(trace, selectedEventId, 1)
  const previousSignalId = adjacentSignalId(trace, selectedEventId, -1)
  const nextSignalId = adjacentSignalId(trace, selectedEventId, 1)
  const sourceSignalEvent = findSourceSignalEvent(selectedEvent, index)
  const signalId = selectedEvent.signal_evaluation.signal_id
  const executionEvent = signalId ? findExecutionEventForSignal(trace, signalId) : null
  const timestamp = formatTimestamp(selectedEvent.timestamp)
  const hasSignals = trace.timeline.some((event) => event.signal_evaluation.signal_id)
  const runtime = trace.metadata.runtime
  const capabilities = capabilitiesFor(runtime)

  function selectEvent(eventId: string) {
    setSelectedEventId(eventId)
    setSelectedFeatureId(null)
  }

  return (
    <main className="replay-shell">
      <header className="replay-header">
        <h1>{tr('Replay')}</h1>
        <div className="trace-meta"><span>{tr(trace.strategy.name)}</span><span className="runtime-label">{runtimeLabel(runtime)} · {tr(runtime?.trace_fidelity ?? 'FULL')}</span><strong>{timestamp.date} {timestamp.time}</strong>{onDiagnose && <button className="link-button" onClick={onDiagnose}>{tr('Diagnose')}</button>}{onAutopsy && <button className="link-button" onClick={onAutopsy}>{tr('P&L Autopsy')}</button>}</div>
      </header>
      <details className="trace-parameters" aria-label={tr('Trace parameters')}>
        <summary>{tr('Backtest parameters')}</summary>
        <div>{Object.entries(trace.parameters).map(([key, value]) => <span key={key}><small>{key}</small><strong>{String(value)}</strong></span>)}</div>
      </details>
      <ReplayTimeline events={trace.timeline} selectedEventId={selectedEventId} onSelect={selectEvent} />
      <ReplayControls previousBarId={previousBarId} nextBarId={nextBarId} previousSignalId={previousSignalId} nextSignalId={nextSignalId} onSelect={selectEvent} />
      {!hasSignals && <p className="no-signals">{tr('No trading signals were generated for this backtest.')}</p>}
      <div className="inspector-grid">
        <MarketPositionPanel event={selectedEvent} />
        <StrategyDecisionPanel event={selectedEvent} capabilities={capabilities} />
      </div>
      <div className="evidence-grid">
        <SignalLineage evaluation={selectedEvent.signal_evaluation} allFeatures={allFeatures} rootFeatureId={rootFeatureId} selectedFeature={selectedFeature} onSelectFeature={setSelectedFeatureId} capabilities={capabilities} fidelity={runtime?.trace_fidelity ?? 'FULL'} />
        <DependenciesPanel event={selectedEvent} diagnostics={trace.diagnostics} capabilities={capabilities} />
      </div>
      <ExecutionOutcomePanel event={selectedEvent} index={index} sourceSignalEvent={sourceSignalEvent} executionEvent={executionEvent} onSelect={selectEvent} capabilities={capabilities} />
    </main>
  )
}

export default ReplayPage
