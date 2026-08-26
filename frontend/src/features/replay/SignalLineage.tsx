import { useMemo } from 'react'

import type { FeatureSnapshot, SignalEvaluation, TraceCapabilitySet, TraceFidelity } from '../../types/trace'
import { nativeTraceCapabilities } from './capabilities'
import { useI18n } from '../../i18n/I18nProvider'
import { buildFeatureLineage, type FeatureLineageNode } from './utils/lineage'
import { formatNumber, formatTimestamp, humanize } from './utils/format'

interface SignalLineageProps {
  evaluation: SignalEvaluation
  allFeatures: Iterable<FeatureSnapshot>
  rootFeatureId: string | null
  selectedFeature: FeatureSnapshot | null
  onSelectFeature: (featureId: string) => void
  capabilities?: TraceCapabilitySet
  fidelity?: TraceFidelity
}

function LineageBranch({ node, depth, onSelect }: { node: FeatureLineageNode; depth: number; onSelect: (featureId: string) => void }) {
  const { tr } = useI18n()
  if (node.status !== 'ok' || !node.feature) {
    return <p className="lineage-error" role="alert">{node.label}</p>
  }
  if (node.children.length === 0) {
    return (
      <div className="lineage-leaf">
        <button className="feature-node" type="button" onClick={() => onSelect(node.featureId)} aria-label={`${tr('Inspect')} ${tr(humanize(node.feature.name))}`}>
          <span>{tr(humanize(node.feature.name))}</span>
          <strong>{formatNumber(node.feature.value, 6)}</strong>
        </button>
      </div>
    )
  }
  return (
    <details className="lineage-branch" open={depth === 0}>
      <summary onClick={() => onSelect(node.featureId)} aria-label={`${tr('Inspect')} ${tr(humanize(node.feature.name))}`}>
        <span className="feature-node">
          <span>{tr(humanize(node.feature.name))}</span>
          <strong>{formatNumber(node.feature.value, 6)}</strong>
        </span>
      </summary>
      <div className="lineage-children">
        {node.children.map((child, index) => (
          <LineageBranch key={`${child.featureId}-${index}`} node={child} depth={depth + 1} onSelect={onSelect} />
        ))}
      </div>
    </details>
  )
}

function SignalLineage({ evaluation, allFeatures, rootFeatureId, selectedFeature, onSelectFeature, capabilities = nativeTraceCapabilities, fidelity = 'FULL' }: SignalLineageProps) {
  const { tr } = useI18n()
  const featureLookup = useMemo(
    () => new Map(Array.from(allFeatures, (feature) => [feature.feature_id, feature])),
    [allFeatures],
  )
  const lineage = useMemo(
    () => (rootFeatureId ? buildFeatureLineage(allFeatures, rootFeatureId) : null),
    [allFeatures, rootFeatureId],
  )
  const availableTime = selectedFeature ? formatTimestamp(selectedFeature.available_at) : null
  return (
    <section className="inspector lineage-panel" aria-labelledby="lineage-heading">
      <div className="section-heading">
        <h2 id="lineage-heading">{tr('Decision basis')}</h2>
      </div>
      <div className="decision-origin">
        <strong>{tr(humanize(evaluation.signal))}</strong>
        <span>{evaluation.conditions.map((condition) => tr(condition.description)).join(' / ')}</span>
      </div>
      <details className="advanced-disclosure lineage-disclosure" open>
        <summary>{tr('Calculation details')}</summary>
        {capabilities.feature_lineage === 'UNAVAILABLE'
          ? <p className="capability-unavailable"><strong>{tr('Feature lineage not available')}</strong><span>{tr('This Framework Run does not provide verifiable feature dependencies.')} · {tr(fidelity)}</span></p>
          : lineage ? <div className="lineage-tree"><LineageBranch node={lineage} depth={0} onSelect={onSelectFeature} /></div> : <p className="empty-state">{tr('No feature dependency was recorded for this evaluation.')}</p>}
        <div className="feature-inspector" aria-live="polite">
          {selectedFeature && availableTime ? (
            <>
              <div className="feature-title"><h3>{tr(humanize(selectedFeature.name))}</h3><strong>{formatNumber(selectedFeature.value, 10)}</strong></div>
              <dl className="detail-list">
                <div><dt>{tr('Formula')}</dt><dd><code>{selectedFeature.formula}</code></dd></div>
                <div><dt>{tr('Inputs')}</dt><dd>{selectedFeature.inputs.length ? selectedFeature.inputs.map((inputId) => tr(humanize(featureLookup.get(inputId)?.name ?? inputId))).join(', ') : tr('None')}</dd></div>
                <div><dt>{tr('Window')}</dt><dd>{selectedFeature.window_start ? `${formatTimestamp(selectedFeature.window_start).date} → ${formatTimestamp(selectedFeature.window_end!).date}` : tr('No complete window')}</dd></div>
                <div><dt>{tr('Available at')}</dt><dd>{availableTime.date} {availableTime.time}</dd></div>
              </dl>
            </>
          ) : <p className="empty-state">{tr('Select a feature node to inspect its recorded value.')}</p>}
        </div>
      </details>
    </section>
  )
}

export default SignalLineage
