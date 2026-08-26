import { useEffect, useMemo, useState } from 'react'

import { getFactorResearchList } from '../../api/factors'
import { createBacktest } from '../../api/replay'
import {
  attachHypothesisRun,
  createHypothesis,
  createHypothesisRevision,
  getDiscoverySuggestions,
  getHypotheses,
  hypothesisAction,
} from '../../api/research'
import { useI18n } from '../../i18n/I18nProvider'
import type { FactorResearchSummary } from '../../types/factor'
import type {
  DiscoverySuggestion,
  HypothesisEvidence,
  RebalanceRule,
  ResearchHypothesis,
} from '../../types/research'

const shortId = (value: string | null) => value == null ? '—' : value.length > 24 ? `${value.slice(0, 11)}…${value.slice(-8)}` : value

function metricValue(value: number | string | boolean | null) {
  if (value == null) return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4)
  return String(value)
}

function EvidenceColumn({
  title,
  stance,
  evidence,
}: {
  title: string
  stance: HypothesisEvidence['stance']
  evidence: HypothesisEvidence[]
}) {
  const { tr } = useI18n()
  const rows = evidence.filter((item) => item.stance === stance)
  return <section className={`discovery-evidence-column ${stance.toLowerCase()}`}>
    <header><strong>{title}</strong><span>{rows.length}</span></header>
    {rows.length === 0 && <p className="empty-copy">{tr('No evidence in this category.')}</p>}
    {rows.map((item) => <article key={item.evidence_id}>
      <div><span>{tr(item.source_type)}</span><code>{tr(item.stage)}</code></div>
      <strong>{tr(item.label)}</strong>
      <p>{tr(item.detail)}</p>
      <dl>{Object.entries(item.metrics).map(([key, value]) => <div key={key}><dt>{tr(key.replaceAll('_', ' '))}</dt><dd>{typeof value === 'boolean' ? tr(String(value).toUpperCase()) : metricValue(value)}</dd></div>)}</dl>
    </article>)}
  </section>
}

interface DiscoveryWorkspacePageProps {
  onOpenReplay: (traceId: string) => void
  onRunComplete: (traceId: string, runId: string) => void
}

export default function DiscoveryWorkspacePage({
  onOpenReplay,
  onRunComplete,
}: DiscoveryWorkspacePageProps) {
  const { tr } = useI18n()
  const [factors, setFactors] = useState<FactorResearchSummary[]>([])
  const [records, setRecords] = useState<ResearchHypothesis[]>([])
  const [suggestions, setSuggestions] = useState<DiscoverySuggestion[]>([])
  const [record, setRecord] = useState<ResearchHypothesis | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [expectedRelationship, setExpectedRelationship] = useState('')
  const [holdingHorizon, setHoldingHorizon] = useState(() => tr('20 trading days'))
  const [rebalance, setRebalance] = useState<RebalanceRule>('MONTHLY')
  const [riskAssumptions, setRiskAssumptions] = useState(() => `${tr('Long-only')}\n${tr('No automatic optimization')}`)
  const [revisionReason, setRevisionReason] = useState('')
  const [revisionHorizon, setRevisionHorizon] = useState('')
  const [revisionRebalance, setRevisionRebalance] = useState<RebalanceRule | ''>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    void Promise.all([getFactorResearchList(), getHypotheses(), getDiscoverySuggestions()]).then(
      ([factorRows, hypothesisRows, ideaRows]) => {
        if (!mounted) return
        setFactors(factorRows)
        setRecords(hypothesisRows)
        setSuggestions(ideaRows)
        setRecord(hypothesisRows[0] ?? null)
      },
      (reason) => {
        if (mounted) setError(reason instanceof Error ? reason.message : String(reason))
      },
    )
    return () => { mounted = false }
  }, [])

  const selectedFactors = useMemo(
    () => factors.filter((item) => selected.includes(item.research_id)),
    [factors, selected],
  )
  const compatible = new Set(selectedFactors.map((item) => item.dataset_id)).size <= 1

  function toggleFactor(researchId: string) {
    setSelected((current) => current.includes(researchId)
      ? current.filter((item) => item !== researchId)
      : [...current, researchId])
  }

  function selectRecord(next: ResearchHypothesis) {
    setRecord(next)
    setRevisionReason('')
    setRevisionHorizon('')
    setRevisionRebalance('')
  }

  function updateRecord(next: ResearchHypothesis) {
    setRecord(next)
    setRecords((current) => [next, ...current.filter((item) => item.hypothesis_id !== next.hypothesis_id)])
  }

  function applySuggestion(suggestion: DiscoverySuggestion) {
    setSelected([...suggestion.factor_research_ids])
    setTitle(tr('Investigate low-redundancy Factor combination'))
    setExpectedRelationship(tr(suggestion.rationale))
  }

  async function createNewHypothesis() {
    if (!title.trim() || !description.trim() || !expectedRelationship.trim() || selected.length === 0 || !compatible) return
    setBusy(true)
    setError(null)
    try {
      const next = await createHypothesis({
        title: title.trim(),
        description: description.trim(),
        universe: [],
        factor_research_ids: selected,
        expected_relationship: expectedRelationship.trim(),
        holding_horizon: holdingHorizon.trim(),
        rebalance_idea: rebalance,
        risk_assumptions: riskAssumptions.split('\n').map((item) => item.trim()).filter(Boolean),
      })
      updateRecord(next)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function act(action: 'candidate' | 'validate' | 'reveal-holdout' | 'strategy') {
    if (!record) return
    setBusy(true)
    setError(null)
    try {
      updateRecord(await hypothesisAction(record.hypothesis_id, action))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function createRevision() {
    if (!record || !revisionReason.trim()) return
    setBusy(true)
    setError(null)
    try {
      const next = await createHypothesisRevision(record.hypothesis_id, {
        ...(revisionHorizon.trim() ? { holding_horizon: revisionHorizon.trim() } : {}),
        ...(revisionRebalance ? { rebalance_idea: revisionRebalance } : {}),
        revision_reason: revisionReason.trim(),
      })
      updateRecord(next)
      setRevisionReason('')
      setRevisionHorizon('')
      setRevisionRebalance('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function runBacktest() {
    if (!record?.lineage.strategy_id) return
    setBusy(true)
    setError(null)
    try {
      const created = await createBacktest({
        strategy_id: record.lineage.strategy_id,
        dataset_id: record.dataset_id,
        parameters: {},
      })
      if (!created.trace_id) throw new Error('Discovery candidate did not produce a replayable Trace.')
      const next = await attachHypothesisRun(record.hypothesis_id, created.run_id, created.trace_id)
      updateRecord(next)
      onRunComplete(created.trace_id, created.run_id)
      onOpenReplay(created.trace_id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  return <main className="discover-shell research-workbench discovery-workspace">
    <section className="discover-title">
      <div><span className="section-kicker">{tr('Hypothesis workbench')}</span><h1>{tr('Strategy Discovery')}</h1><p>{tr('Turn existing research evidence into explicit, revisioned hypotheses without searching for a historical winner.')}</p></div>
      <span className="bias-tag">{tr('Hypothesis · Evidence · Lineage')}</span>
    </section>

    {error && <section className="workspace-panel research-error" role="alert">{tr(error)}</section>}

    <section className="discovery-workspace-grid">
      <aside className="workspace-panel discovery-ledger">
        <div className="section-heading"><h2>{tr('Hypotheses')}</h2><span>{records.length}</span></div>
        {records.length === 0 && <p className="empty-copy">{tr('No hypotheses yet.')}</p>}
        {records.map((item) => <button key={item.hypothesis_id} className={record?.hypothesis_id === item.hypothesis_id ? 'selected' : ''} onClick={() => selectRecord(item)}>
          <strong>{item.title}</strong><span>{tr('Revision')} {item.revision} · {tr(item.status)}</span><small>{tr(item.outcome).replaceAll('_', ' ')}</small>
        </button>)}
      </aside>

      <div className="discovery-stack">
        <section className="workspace-panel discovery-builder">
          <div className="section-heading"><div><span className="section-kicker">{tr('Research hypothesis')}</span><h2>{tr('Create from existing Factor evidence')}</h2></div><span>{tr('No mass search')}</span></div>
          {suggestions.length > 0 && <div className="discovery-suggestions"><strong>{tr('Research Ideas')}</strong>{suggestions.map((item) => <button key={`${item.source_relationship_id}:${item.factor_research_ids.join(':')}`} onClick={() => applySuggestion(item)}><span>{tr(item.label)}</span><p>{tr(item.rationale)}</p><code>{shortId(item.source_relationship_id)}</code></button>)}</div>}
          <div className="discovery-factor-picker">{factors.map((item) => <label key={item.research_id} className={selected.includes(item.research_id) ? 'selected' : ''}><input type="checkbox" checked={selected.includes(item.research_id)} onChange={() => toggleFactor(item.research_id)} /><span><strong>{tr(item.factor_id)}</strong><small>{item.name}</small></span><code>{tr(item.revealed_stage)}</code></label>)}</div>
          <div className="discovery-form-grid">
            <label><span>{tr('Title')}</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
            <label><span>{tr('Holding horizon')}</span><input value={holdingHorizon} onChange={(event) => setHoldingHorizon(event.target.value)} /></label>
            <label><span>{tr('Rebalance idea')}</span><select value={rebalance} onChange={(event) => setRebalance(event.target.value as RebalanceRule)}><option value="DAILY">{tr('DAILY')}</option><option value="WEEKLY">{tr('WEEKLY')}</option><option value="MONTHLY">{tr('MONTHLY')}</option></select></label>
            <label className="wide"><span>{tr('Description')}</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label>
            <label className="wide"><span>{tr('Expected relationship')}</span><textarea value={expectedRelationship} onChange={(event) => setExpectedRelationship(event.target.value)} /></label>
            <label className="wide"><span>{tr('Risk assumptions')}</span><textarea value={riskAssumptions} onChange={(event) => setRiskAssumptions(event.target.value)} /></label>
          </div>
          {!compatible && <p className="relationship-selection-error">{tr('Selected Factor studies must use the same market dataset.')}</p>}
          <div className="builder-footer"><p>{tr('Discovery stores the hypothesis first. Candidate construction is a separate deterministic backend action.')}</p><button className="primary-button" disabled={busy || selected.length === 0 || !compatible || !title.trim() || !description.trim() || !expectedRelationship.trim()} onClick={() => void createNewHypothesis()}>{tr(busy ? 'Working…' : 'Create Hypothesis')}</button></div>
        </section>

        {record && <>
          <section className="workspace-panel discovery-summary">
            <div className="section-heading"><div><span className="section-kicker">{tr(record.status)} · {tr('Revision')} {record.revision}</span><h2>{record.title}</h2></div><span className={`discovery-outcome ${record.outcome.toLowerCase()}`}>{tr(record.outcome).replaceAll('_', ' ')}</span></div>
            <p>{record.description}</p>
            <div className="discovery-identity"><code>{record.hypothesis_id}</code><span>{record.lineage.factor_ids.join(' · ')}</span><b>{tr(record.created_with_known_stage)} · {tr('known at creation')}</b></div>
            <blockquote>{record.expected_relationship}</blockquote>
          </section>

          <section className="workspace-panel discovery-experiment">
            <div className="section-heading"><div><span className="section-kicker">{tr('Fixed candidate template')}</span><h2>{tr('Experiment')}</h2></div><span>{tr('Long only')}</span></div>
            <div className="candidate-contract"><span>{tr('Combine')} <strong>{tr(record.candidate.combination)}</strong></span><span>{tr('Select')} <strong>{tr('Top')} {record.candidate.top_percent}%</strong></span><span>{tr('Weight')} <strong>{tr(record.candidate.weighting)}</strong></span><span>{tr('Rebalance')} <strong>{tr(record.candidate.rebalance)}</strong></span></div>
            <div className="discovery-actions">
              {record.status === 'DRAFT' && <button className="primary-button" disabled={busy} onClick={() => void act('candidate')}>{tr('Create Candidate')}</button>}
              {record.status === 'RESEARCHED' && <button className="primary-button" disabled={busy} onClick={() => void act('validate')}>{tr('Run Validation')}</button>}
              {record.status === 'VALIDATED' && <button className="holdout-button" disabled={busy} onClick={() => void act('reveal-holdout')}>{tr('Reveal Holdout')}</button>}
              {record.status === 'HOLDOUT_REVEALED' && <button className="primary-button" disabled={busy} onClick={() => void act('strategy')}>{tr('Create Native Strategy')}</button>}
              {record.status === 'STRATEGY_CREATED' && <button className="primary-button" disabled={busy} onClick={() => void runBacktest()}>{tr('Backtest & Replay')}</button>}
            </div>
            {record.status === 'VALIDATED' && <p className="holdout-warning">{tr('Holdout remains sealed until you explicitly reveal it. The system does not use it to modify this hypothesis.')}</p>}
          </section>

          <section className="workspace-panel discovery-evidence">
            <div className="section-heading"><div><span className="section-kicker">{tr('Support + contradiction')}</span><h2>{tr('Evidence')}</h2></div><span>{record.evidence.length} {tr('items')}</span></div>
            <div className="discovery-evidence-grid">
              <EvidenceColumn title={tr('Supporting')} stance="SUPPORTING" evidence={record.evidence} />
              <EvidenceColumn title={tr('Contradicting')} stance="CONTRADICTING" evidence={record.evidence} />
              <EvidenceColumn title={tr('Neutral / insufficient')} stance="NEUTRAL" evidence={record.evidence} />
            </div>
          </section>

          <section className="workspace-panel discovery-lineage">
            <div className="section-heading"><div><span className="section-kicker">{tr('Why this strategy exists')}</span><h2>{tr('Lineage')}</h2></div><span>{record.family_id}</span></div>
            <div className="lineage-chain"><article><span>{tr('Factors')}</span><code>{record.lineage.factor_ids.join(' · ')}</code><small>{record.lineage.factor_research_ids.map(shortId).join(' · ')}</small></article><i>→</i><article><span>{tr('Relationships')}</span><code>{record.lineage.relationship_ids.length || '—'}</code><small>{record.lineage.relationship_ids.map(shortId).join(' · ') || tr('None')}</small></article><i>→</i><article><span>{tr('Walk-Forward')}</span><code>{record.lineage.walk_forward_ids.length || '—'}</code><small>{record.lineage.walk_forward_ids.map(shortId).join(' · ') || tr('None')}</small></article><i>→</i><article><span>{tr('Portfolio')}</span><code>{shortId(record.lineage.portfolio_research_id)}</code></article><i>→</i><article><span>{tr('Strategy')}</span><code>{shortId(record.lineage.strategy_id)}</code></article><i>→</i><article><span>{tr('Runs / Traces')}</span><code>{record.lineage.run_ids.length} / {record.lineage.trace_ids.length}</code></article></div>
          </section>

          <section className="workspace-panel discovery-revision">
            <div className="section-heading"><div><span className="section-kicker">{tr('Immutable experiment')}</span><h2>{tr('Create a new revision')}</h2></div><span>{tr('Never mutate in place')}</span></div>
            <p>{tr('If you change the hypothesis after seeing Validation or Holdout, create a new revision. The current experiment remains unchanged in the Research Ledger.')}</p>
            <div className="revision-grid"><label><span>{tr('New holding horizon')}</span><input placeholder={tr(record.holding_horizon)} value={revisionHorizon} onChange={(event) => setRevisionHorizon(event.target.value)} /></label><label><span>{tr('New rebalance')}</span><select value={revisionRebalance} onChange={(event) => setRevisionRebalance(event.target.value as RebalanceRule | '')}><option value="">{tr('Unchanged')}</option><option value="DAILY">{tr('DAILY')}</option><option value="WEEKLY">{tr('WEEKLY')}</option><option value="MONTHLY">{tr('MONTHLY')}</option></select></label><label className="wide"><span>{tr('Revision reason')}</span><input value={revisionReason} onChange={(event) => setRevisionReason(event.target.value)} /></label></div>
            <button disabled={busy || !revisionReason.trim()} onClick={() => void createRevision()}>{tr('Create Revision')}</button>
          </section>

          <section className="workspace-panel discovery-ai-boundary"><strong>{tr('Optional AI boundary')}</strong><p>{tr(record.ai_boundary)}</p></section>
        </>}
      </div>
    </section>
  </main>
}
