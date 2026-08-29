import { useEffect, useMemo, useState } from 'react'

import {
  compareResearchSnapshots,
  createResearchSnapshot,
  getHypotheses,
  getResearchSnapshot,
  getResearchSnapshots,
} from '../../api/research'
import { getDatasetFamilies } from '../../api/datasets'
import { useI18n } from '../../i18n/I18nProvider'
import type { DatasetFamily } from '../../types/dataset'
import type {
  ExperimentComparisonReport,
  FrozenArtifact,
  ResearchHypothesis,
  ResearchSnapshot,
  ResearchSnapshotSummary,
  SnapshotPeriod,
} from '../../types/research'

const shortId = (value: string) => value.length > 26 ? `${value.slice(0, 12)}…${value.slice(-9)}` : value
const shortHash = (value: string) => value.length > 30 ? `${value.slice(0, 17)}…${value.slice(-10)}` : value

function dateTime(value: string | null) {
  return value == null ? '—' : new Date(value).toLocaleString()
}

function PeriodCard({ period }: { period: SnapshotPeriod }) {
  const { tr } = useI18n()
  return <article>
    <span>{tr(period.label)}</span>
    <strong>{dateTime(period.start)}</strong>
    <small>→ {dateTime(period.end)}</small>
    {period.cutoff && <code>{tr('Cutoff')} · {dateTime(period.cutoff)}</code>}
  </article>
}

function ArtifactRow({ artifact }: { artifact: FrozenArtifact }) {
  const { tr } = useI18n()
  return <div className="snapshot-artifact-row">
    <span>{tr(artifact.kind.replaceAll('_', ' '))}</span>
    <code title={artifact.artifact_id}>{shortId(artifact.artifact_id)}</code>
    <code title={artifact.source_revision}>{shortHash(artifact.source_revision)}</code>
    <code title={artifact.payload_sha256}>{shortHash(artifact.payload_sha256)}</code>
    <b>{tr('VERIFIED')}</b>
  </div>
}

function displayValue(value: string | number | boolean | null) {
  if (value == null) return '—'
  return typeof value === 'number'
    ? new Intl.NumberFormat(undefined, { maximumSignificantDigits: 7 }).format(value)
    : String(value)
}

function ExperimentComparison({
  report,
  onOpenRuns,
  onOpenReplay,
}: {
  report: ExperimentComparisonReport
  onOpenRuns: (runId: string) => void
  onOpenReplay: (traceId: string) => void
}) {
  const { tr } = useI18n()
  const changedArtifacts = report.artifact_diff.filter((item) => !item.same_revision)
  const divergence = report.primary_run_comparison.first_behavioral_divergence

  return <section className="workspace-panel experiment-comparison" aria-label={tr('Experiment Compare')}>
    <div className="section-heading">
      <div><span className="section-kicker">{tr('Frozen evidence comparison')}</span><h2>{tr('Experiment Compare')}</h2></div>
      <b className={`experiment-comparability ${report.comparability.toLowerCase()}`}>{tr(report.comparability)}</b>
    </div>

    <div className="experiment-identities">
      {report.snapshots.map((item, index) => <article key={item.snapshot_id}>
        <span>{index === 0 ? tr('Baseline') : `${tr('Experiment')} ${String.fromCharCode(65 + index)}`}</span>
        <strong>{tr(item.name)}</strong>
        <code title={item.content_fingerprint}>{shortHash(item.content_fingerprint)}</code>
        <small>{tr('Hypothesis')} r{item.hypothesis_revision}</small>
      </article>)}
    </div>

    <div className="experiment-section">
      <div className="section-heading"><div><span className="section-kicker">{tr('Controlled context')}</span><h3>{tr('Context controls')}</h3></div></div>
      <div className="experiment-table-scroll"><table className="experiment-table"><thead><tr><th>{tr('Context')}</th><th>{tr('Control role')}</th><th>{tr('Compatibility')}</th>{report.snapshots.map((item, index) => <th key={item.snapshot_id}>{index === 0 ? tr('Baseline') : `${tr('Experiment')} ${String.fromCharCode(65 + index)}`}</th>)}</tr></thead><tbody>
        {report.context_diff.map((item) => <tr key={item.field}><th>{tr(item.field.replaceAll('_', ' '))}</th><td>{tr(item.significance.replaceAll('_', ' '))}</td><td><b className={item.same ? 'same' : 'different'}>{tr(item.same ? 'SAME' : 'DIFFERENT')}</b></td>{item.values.map((value, index) => <td key={`${item.field}:${index}`}><code title={value}>{shortHash(value)}</code></td>)}</tr>)}
      </tbody></table></div>
    </div>

    <div className="experiment-section experiment-treatment-grid">
      <div>
        <div className="section-heading"><div><span className="section-kicker">{tr('Treatment')}</span><h3>{tr('Changed artifact revisions')}</h3></div><span>{changedArtifacts.length}</span></div>
        {changedArtifacts.length === 0 ? <p className="empty-copy">{tr('No artifact revision changes.')}</p> : <div className="experiment-change-list">{changedArtifacts.map((item) => <article key={`${item.kind}:${item.semantic_key}`}><span>{tr(item.kind.replaceAll('_', ' '))}</span><strong>{item.semantic_key}</strong><div>{item.source_revisions.map((value, index) => <code key={`${item.semantic_key}:${index}`} title={value ?? ''}>{value == null ? '—' : shortHash(value)}</code>)}</div></article>)}</div>}
      </div>
      <div>
        <div className="section-heading"><div><span className="section-kicker">{tr('Treatment')}</span><h3>{tr('Parameter changes')}</h3></div><span>{report.parameter_diff.length}</span></div>
        {report.parameter_diff.length === 0 ? <p className="empty-copy">{tr('No parameter differences.')}</p> : <div className="experiment-table-scroll"><table className="experiment-table compact"><thead><tr><th>{tr('Owner')}</th><th>{tr('Parameter')}</th>{report.snapshots.map((item, index) => <th key={item.snapshot_id}>{index === 0 ? tr('Baseline') : `${tr('Experiment')} ${String.fromCharCode(65 + index)}`}</th>)}</tr></thead><tbody>{report.parameter_diff.map((item) => <tr key={`${item.owner_type}:${item.owner_key}:${item.parameter}`}><th><span>{tr(item.owner_type)}</span><code>{item.owner_key}</code></th><td>{tr(item.parameter.replaceAll('_', ' '))}</td>{item.values.map((value, index) => <td key={`${item.parameter}:${index}`}><code>{displayValue(value)}</code></td>)}</tr>)}</tbody></table></div>}
      </div>
    </div>

    <div className="experiment-section">
      <div className="section-heading"><div><span className="section-kicker">{tr('Frozen outcomes')}</span><h3>{tr('Result differences')}</h3></div><span>{tr('Backend calculated')}</span></div>
      <div className="experiment-hypothesis-states">{report.hypothesis_states.map((item, index) => <article key={item.snapshot_id}><span>{index === 0 ? tr('Baseline') : `${tr('Experiment')} ${String.fromCharCode(65 + index)}`}</span><strong>{tr(item.outcome)}</strong><small>{tr(item.status)}</small><dl><div><dt>{tr('Supporting')}</dt><dd>{item.supporting_evidence}</dd></div><div><dt>{tr('Contradicting')}</dt><dd>{item.contradicting_evidence}</dd></div><div><dt>{tr('Neutral')}</dt><dd>{item.neutral_evidence}</dd></div></dl></article>)}</div>
      <div className="experiment-table-scroll"><table className="experiment-table metrics"><thead><tr><th>{tr('Scope')}</th><th>{tr('Metric')}</th>{report.snapshots.map((item, index) => <th key={item.snapshot_id}>{index === 0 ? tr('Baseline') : `${tr('Experiment')} ${String.fromCharCode(65 + index)}`}</th>)}</tr></thead><tbody>{report.metric_diff.map((item) => <tr key={`${item.scope}:${item.metric}`}><th>{tr(item.scope.replaceAll('_', ' '))}</th><td>{tr(item.metric.replaceAll('_', ' '))}</td>{item.values.map((value, index) => <td key={`${item.metric}:${index}`}><code>{displayValue(value)}</code>{index > 0 && <small>{tr('Delta vs baseline')} · {displayValue(item.differences_from_first[index])}</small>}</td>)}</tr>)}</tbody></table></div>
    </div>

    <div className="experiment-section experiment-behavior">
      <div className="section-heading"><div><span className="section-kicker">{tr('Recorded behavior')}</span><h3>{tr('Primary Run / Trace comparison')}</h3></div><b>{tr(report.primary_run_comparison.comparability)}</b></div>
      <div className="experiment-behavior-grid"><article><span>{tr('First behavioral divergence')}</span><strong>{divergence == null ? tr('Not available') : tr(divergence.status)}</strong><p>{divergence == null ? tr('Behavioral comparison is limited to two strictly comparable runs.') : tr(divergence.summary)}</p>{divergence?.timestamp && <code>{dateTime(divergence.timestamp)}</code>}</article><article><span>{tr('Run parameter differences')}</span><strong>{report.primary_run_comparison.parameter_diff.length}</strong><p>{tr('This section reuses the frozen Run and Trace comparison contract; it does not read mutable live records.')}</p></article></div>
      <div className="experiment-run-links">{report.snapshots.map((item, index) => <div key={item.snapshot_id}><span>{index === 0 ? tr('Baseline') : `${tr('Experiment')} ${String.fromCharCode(65 + index)}`}</span><button onClick={() => onOpenRuns(item.run_id)}>{tr('Open Run')}</button><button onClick={() => onOpenReplay(item.trace_id)}>{tr('Open Replay')}</button></div>)}</div>
    </div>

    <p className="experiment-disclosure">{tr(report.comparison_disclosure)}</p>
  </section>
}

interface ResearchSnapshotsPageProps {
  initialSnapshotId?: string | null
  onOpenRuns: (runId: string) => void
  onOpenReplay: (traceId: string) => void
}

export default function ResearchSnapshotsPage({
  initialSnapshotId = null,
  onOpenRuns,
  onOpenReplay,
}: ResearchSnapshotsPageProps) {
  const { tr } = useI18n()
  const [summaries, setSummaries] = useState<ResearchSnapshotSummary[]>([])
  const [hypotheses, setHypotheses] = useState<ResearchHypothesis[]>([])
  const [snapshot, setSnapshot] = useState<ResearchSnapshot | null>(null)
  const [selectedHypothesisId, setSelectedHypothesisId] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [compareBusy, setCompareBusy] = useState(false)
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [comparison, setComparison] = useState<ExperimentComparisonReport | null>(null)
  const [datasetFamilies, setDatasetFamilies] = useState<DatasetFamily[]>([])
  const [error, setError] = useState<string | null>(null)

  const eligible = useMemo(() => hypotheses.filter((item) => (
    item.lineage.portfolio_research_id != null
    && item.lineage.strategy_id != null
    && item.lineage.run_ids.length > 0
    && item.lineage.run_ids.length === item.lineage.trace_ids.length
  )), [hypotheses])

  useEffect(() => {
    let mounted = true
    void getDatasetFamilies()
      .then((rows) => { if (mounted) setDatasetFamilies(rows) })
      .catch(() => undefined)
    void Promise.all([getResearchSnapshots(), getHypotheses()]).then(
      async ([snapshotRows, hypothesisRows]) => {
        if (!mounted) return
        setSummaries(snapshotRows)
        setHypotheses(hypothesisRows)
        const ready = hypothesisRows.find((item) => item.lineage.portfolio_research_id != null && item.lineage.strategy_id != null && item.lineage.run_ids.length > 0 && item.lineage.run_ids.length === item.lineage.trace_ids.length)
        if (ready) {
          setSelectedHypothesisId(ready.hypothesis_id)
          setName(`${tr(ready.title)} · ${tr('frozen research')}`)
        }
        const detailId = initialSnapshotId ?? snapshotRows[0]?.snapshot_id
        if (detailId) {
          try {
            const detail = await getResearchSnapshot(detailId)
            if (mounted) setSnapshot(detail)
          } catch (reason) {
            if (mounted) setError(reason instanceof Error ? reason.message : String(reason))
          }
        }
      },
      (reason) => { if (mounted) setError(reason instanceof Error ? reason.message : String(reason)) },
    )
    return () => { mounted = false }
  }, [initialSnapshotId, tr])

  function selectHypothesis(id: string) {
    setSelectedHypothesisId(id)
    const record = eligible.find((item) => item.hypothesis_id === id)
    if (record) setName(`${tr(record.title)} · ${tr('frozen research')}`)
  }

  async function selectSnapshot(id: string) {
    setBusy(true)
    setError(null)
    try { setSnapshot(await getResearchSnapshot(id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function freezeResearch() {
    if (!selectedHypothesisId || !name.trim()) return
    setBusy(true)
    setError(null)
    try {
      const created = await createResearchSnapshot({ name: name.trim(), hypothesis_id: selectedHypothesisId })
      const summary: ResearchSnapshotSummary = {
        snapshot_id: created.snapshot_id,
        name: created.name,
        created_at: created.created_at,
        content_fingerprint: created.content_fingerprint,
        hypothesis_id: created.lineage.hypothesis_id,
        hypothesis_revision: created.lineage.hypothesis_revision,
        dataset_id: created.lineage.dataset_id,
        dataset_family_id: created.lineage.dataset_family_id,
        dataset_revision: created.lineage.dataset_revision,
        factor_count: created.lineage.factor_research_ids.length,
        strategy_id: created.lineage.strategy_id,
        run_count: created.lineage.run_ids.length,
        trace_count: created.lineage.trace_ids.length,
      }
      setSummaries((current) => [summary, ...current])
      setSnapshot(created)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  function toggleCompare(id: string) {
    setCompareIds((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length < 4 ? [...current, id] : current)
    setComparison(null)
  }

  async function compareExperiments() {
    if (compareIds.length < 2) return
    setCompareBusy(true)
    setError(null)
    try { setComparison(await compareResearchSnapshots(compareIds)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setCompareBusy(false) }
  }

  const artifacts = snapshot == null ? [] : [
    snapshot.dataset,
    ...snapshot.universes,
    ...snapshot.corporate_actions,
    ...snapshot.factors,
    ...snapshot.relationships,
    ...snapshot.walk_forward,
    snapshot.hypothesis,
    snapshot.portfolio,
    snapshot.strategy,
    ...snapshot.runs,
    ...snapshot.traces,
  ]
  const snapshotDatasetRevision = snapshot?.lineage.dataset_revision ?? 1
  const snapshotDatasetFamily = snapshot?.lineage.dataset_family_id
    ? datasetFamilies.find((family) => family.dataset_family_id === snapshot.lineage.dataset_family_id)
    : undefined

  return <main className="discover-shell research-workbench snapshot-workspace">
    <section className="discover-title">
      <div><span className="section-kicker">{tr('Immutable research record')}</span><h1>{tr('Research Snapshots')}</h1><p>{tr('Freeze one complete research lineage so its exact evidence, source revisions, parameters, periods, runtime records, and environment remain inspectable later.')}</p></div>
      <span className="bias-tag">{tr('Content verified · append only')}</span>
    </section>

    {error && <section className="workspace-panel research-error" role="alert">{tr(error)}</section>}

    <section className="snapshot-workspace-grid">
      <aside className="workspace-panel snapshot-ledger">
        <div className="section-heading"><h2>{tr('Saved Snapshots')}</h2><span>{summaries.length}</span></div>
        {summaries.length === 0 && <p className="empty-copy">{tr('No Research Snapshots yet.')}</p>}
        {summaries.map((item) => <article key={item.snapshot_id} className="snapshot-ledger-item">
          <button className={`snapshot-detail-button ${snapshot?.snapshot_id === item.snapshot_id ? 'selected' : ''}`} onClick={() => void selectSnapshot(item.snapshot_id)}>
            <strong>{tr(item.name)}</strong><span>{tr('Hypothesis')} r{item.hypothesis_revision} · {tr('Dataset')} r{item.dataset_revision ?? 1} · {item.factor_count} {tr('Factors')}</span><code>{shortHash(item.content_fingerprint)}</code>
          </button>
          <label className="snapshot-compare-toggle"><input type="checkbox" aria-label={`${tr('Select')} ${tr(item.name)} ${tr('for comparison')}`} checked={compareIds.includes(item.snapshot_id)} disabled={!compareIds.includes(item.snapshot_id) && compareIds.length >= 4} onChange={() => toggleCompare(item.snapshot_id)} /><span>{tr('Select for comparison')}</span></label>
        </article>)}
        {summaries.length > 1 && <div className="snapshot-compare-actions"><span>{compareIds.length}/4 {tr('selected')}</span><button disabled={compareBusy || compareIds.length < 2} onClick={() => void compareExperiments()}>{tr(compareBusy ? 'Comparing…' : 'Compare Experiments')}</button>{compareIds.length > 0 && <button onClick={() => { setCompareIds([]); setComparison(null) }}>{tr('Clear')}</button>}</div>}
      </aside>

      <div className="snapshot-stack">
        <section className="workspace-panel snapshot-builder">
          <div className="section-heading"><div><span className="section-kicker">{tr('Freeze completed research')}</span><h2>{tr('Create immutable Snapshot')}</h2></div><span>{tr('Backend verified')}</span></div>
          <p>{tr('Only hypotheses with a Portfolio, Native Strategy, and matched Run / Trace can be frozen.')}</p>
          <div className="snapshot-builder-fields">
            <label><span>{tr('Completed hypothesis')}</span><select value={selectedHypothesisId} onChange={(event) => selectHypothesis(event.target.value)}><option value="">{tr('Choose a completed hypothesis')}</option>{eligible.map((item) => <option key={item.hypothesis_id} value={item.hypothesis_id}>{tr(item.title)} · r{item.revision}</option>)}</select></label>
            <label><span>{tr('Snapshot name')}</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
            <button className="primary-button" disabled={busy || !selectedHypothesisId || !name.trim()} onClick={() => void freezeResearch()}>{tr(busy ? 'Working…' : 'Freeze Research')}</button>
          </div>
          {eligible.length === 0 && <p className="snapshot-readiness">{tr('Complete a Strategy Discovery hypothesis and attach its Run / Trace before creating a Snapshot.')}</p>}
        </section>

        {comparison && <ExperimentComparison report={comparison} onOpenRuns={onOpenRuns} onOpenReplay={onOpenReplay} />}

        {snapshot && <>
          <section className="workspace-panel snapshot-identity">
            <div className="section-heading"><div><span className="section-kicker">{tr('Immutable identity')}</span><h2>{tr(snapshot.name)}</h2></div><b>{tr('VERIFIED')}</b></div>
            <div className="snapshot-hashes"><span><small>{tr('Snapshot ID')}</small><code>{snapshot.snapshot_id}</code></span><span><small>{tr('Content fingerprint')}</small><code>{snapshot.content_fingerprint}</code></span><span><small>{tr('Created')}</small><strong>{dateTime(snapshot.created_at)}</strong></span></div>
            <p>{tr(snapshot.immutability_disclosure)}</p>
          </section>

          <section className="workspace-panel snapshot-lineage">
            <div className="section-heading"><div><span className="section-kicker">{tr('Frozen dependency chain')}</span><h2>{tr('Research lineage')}</h2></div><span>{artifacts.length} {tr('artifacts')}</span></div>
            <div className="lineage-chain"><article><span>{tr('Dataset')}</span><code>{shortId(snapshot.lineage.dataset_id)}</code><small>r{snapshotDatasetRevision}{snapshot.lineage.dataset_family_id ? ` · ${shortId(snapshot.lineage.dataset_family_id)}` : ''}</small>{snapshotDatasetFamily && <small>{tr('Uses revision')} r{snapshotDatasetRevision} · {tr('Latest revision is')} r{snapshotDatasetFamily.revision_count}</small>}</article><i>→</i><article><span>{tr('Factors')}</span><code>{snapshot.lineage.factor_ids.join(' · ')}</code><small>{snapshot.lineage.factor_research_ids.length} {tr('revisions')}</small></article><i>→</i><article><span>{tr('Hypothesis')}</span><code>r{snapshot.lineage.hypothesis_revision}</code><small>{shortId(snapshot.lineage.hypothesis_id)}</small></article><i>→</i><article><span>{tr('Portfolio')}</span><code>{shortId(snapshot.lineage.portfolio_research_id)}</code></article><i>→</i><article><span>{tr('Strategy')}</span><code>{shortId(snapshot.lineage.strategy_id)}</code></article><i>→</i><article><span>{tr('Runs / Traces')}</span><code>{snapshot.lineage.run_ids.length} / {snapshot.lineage.trace_ids.length}</code></article></div>
            <div className="snapshot-links"><button onClick={() => onOpenRuns(snapshot.lineage.run_ids[0])}>{tr('Open frozen Run')}</button><button onClick={() => onOpenReplay(snapshot.lineage.trace_ids[0])}>{tr('Open frozen Replay')}</button></div>
          </section>

          <section className="workspace-panel snapshot-periods">
            <div className="section-heading"><div><span className="section-kicker">{tr('No boundary drift')}</span><h2>{tr('Time boundaries')}</h2></div></div>
            <div className="snapshot-period-grid"><PeriodCard period={snapshot.time_boundaries.research} /><PeriodCard period={snapshot.time_boundaries.validation} /><PeriodCard period={snapshot.time_boundaries.holdout} />{snapshot.time_boundaries.runs.map((item) => <PeriodCard key={item.source_id} period={item} />)}</div>
          </section>

          <section className="workspace-panel snapshot-parameters">
            <div className="section-heading"><div><span className="section-kicker">{tr('Exact inputs')}</span><h2>{tr('Frozen parameters')}</h2></div><span>{snapshot.parameters.length} {tr('owners')}</span></div>
            <div className="snapshot-parameter-grid">{snapshot.parameters.map((set) => <article key={`${set.owner_type}:${set.owner_id}`}><header><span>{tr(set.owner_type)}</span><code>{shortId(set.owner_id)}</code></header><dl>{set.values.map((item) => <div key={item.key}><dt>{tr(item.key.replaceAll('_', ' '))}</dt><dd>{item.value == null ? '—' : String(item.value)}</dd></div>)}</dl></article>)}</div>
          </section>

          <section className="workspace-panel snapshot-artifacts">
            <div className="section-heading"><div><span className="section-kicker">{tr('Per-artifact integrity')}</span><h2>{tr('Frozen artifacts')}</h2></div><span>{artifacts.length}</span></div>
            <div className="snapshot-artifact-table"><div className="snapshot-artifact-row header"><span>{tr('Type')}</span><span>{tr('Artifact')}</span><span>{tr('Source revision')}</span><span>{tr('Frozen payload')}</span><span>{tr('Integrity')}</span></div>{artifacts.map((item) => <ArtifactRow key={`${item.kind}:${item.artifact_id}`} artifact={item} />)}</div>
          </section>

          <section className="workspace-panel snapshot-environment">
            <div className="section-heading"><div><span className="section-kicker">{tr('Creation host')}</span><h2>{tr('Environment summary')}</h2></div><span>VQD {snapshot.environment.vqd_version}</span></div>
            <div className="snapshot-environment-grid"><article><span>Python</span><strong>{snapshot.environment.python_implementation} {snapshot.environment.python_version}</strong></article><article><span>{tr('Platform')}</span><strong>{snapshot.environment.platform}</strong></article><article><span>{tr('Machine')}</span><strong>{snapshot.environment.machine}</strong></article>{snapshot.environment.dependencies.map((item) => <article key={item.name}><span>{item.name}</span><strong>{item.version}</strong></article>)}</div>
          </section>
        </>}
      </div>
    </section>
  </main>
}
