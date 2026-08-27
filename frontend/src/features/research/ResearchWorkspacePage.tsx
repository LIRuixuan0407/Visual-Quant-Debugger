import { useEffect, useRef, useState } from 'react'

import { createBacktest } from '../../api/replay'
import {
  attachHypothesisRun,
  getResearchWorkspace,
  getResearchWorkspaces,
  hypothesisAction,
} from '../../api/research'
import { useI18n } from '../../i18n/I18nProvider'
import type {
  ResearchWorkspace,
  ResearchWorkspaceSummary,
  WorkspaceStageKey,
} from '../../types/research'

const shortId = (value: string) => value.length > 30 ? `${value.slice(0, 14)}…${value.slice(-10)}` : value
const dateTime = (value: string) => new Date(value).toLocaleString()
const number = (value: number | null) => value == null ? '—' : new Intl.NumberFormat(undefined, { maximumSignificantDigits: 6 }).format(value)
const stageLabel: Record<WorkspaceStageKey, string> = {
  DATA: 'Data',
  FACTOR: 'Factor',
  PORTFOLIO: 'Portfolio',
  VALIDATION: 'Validation',
  HYPOTHESIS: 'Hypothesis',
  STRATEGY: 'Strategy',
  RUN: 'Run',
}

interface ResearchWorkspacePageProps {
  initialIdeaId?: string | null
  onIdeaChange: (ideaId: string) => void
  onOpenData: () => void
  onOpenFactors: () => void
  onOpenPortfolio: () => void
  onOpenHypothesis: (ideaId: string) => void
  onOpenStrategy: (strategyId: string, datasetId: string) => void
  onOpenRun: (runId: string) => void
  onOpenReplay: (traceId: string) => void
  onOpenIntegrity: (ideaId: string) => void
  onOpenSnapshots: () => void
  onRunComplete: (traceId: string, runId: string) => void
}

export default function ResearchWorkspacePage({
  initialIdeaId,
  onIdeaChange,
  onOpenData,
  onOpenFactors,
  onOpenPortfolio,
  onOpenHypothesis,
  onOpenStrategy,
  onOpenRun,
  onOpenReplay,
  onOpenIntegrity,
  onOpenSnapshots,
  onRunComplete,
}: ResearchWorkspacePageProps) {
  const { tr } = useI18n()
  const selectedIdeaRef = useRef<string | null>(null)
  const [summaries, setSummaries] = useState<ResearchWorkspaceSummary[]>([])
  const [workspace, setWorkspace] = useState<ResearchWorkspace | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [confirmHoldout, setConfirmHoldout] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (initialIdeaId && selectedIdeaRef.current === initialIdeaId) {
      setLoading(false)
      return
    }
    let mounted = true
    async function load() {
      if (mounted) { setLoading(true); setError(null) }
      try {
        const rows = await getResearchWorkspaces()
        if (!mounted) return
        setSummaries(rows)
        const selected = rows.find((item) => item.idea_id === initialIdeaId) ?? rows[0]
        if (selected) {
          const detail = await getResearchWorkspace(selected.idea_id)
          if (mounted) {
            selectedIdeaRef.current = detail.idea_id
            setWorkspace(detail)
            onIdeaChange(detail.idea_id)
          }
        } else {
          selectedIdeaRef.current = null
          setWorkspace(null)
        }
      } catch (reason) {
        if (mounted) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (mounted) setLoading(false)
      }
    }
    void load()
    return () => { mounted = false }
  }, [initialIdeaId, onIdeaChange])

  async function selectIdea(ideaId: string) {
    setBusy(true)
    setConfirmHoldout(false)
    setError(null)
    try {
      const detail = await getResearchWorkspace(ideaId)
      selectedIdeaRef.current = detail.idea_id
      setWorkspace(detail)
      onIdeaChange(detail.idea_id)
      window.history.replaceState({}, '', `/research-workspace/${ideaId}`)
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function refresh(ideaId: string) {
    const [rows, detail] = await Promise.all([getResearchWorkspaces(), getResearchWorkspace(ideaId)])
    setSummaries(rows)
    setWorkspace(detail)
  }

  async function advance() {
    if (!workspace) return
    if (workspace.next_action.action === 'OPEN_RUN') {
      const latest = workspace.runs.at(-1)
      if (latest) onOpenRun(latest.run_id)
      return
    }
    if (workspace.next_action.action === 'REVEAL_HOLDOUT' && !confirmHoldout) {
      setConfirmHoldout(true)
      return
    }
    setBusy(true)
    setError(null)
    try {
      if (workspace.next_action.action === 'BUILD_CANDIDATE') await hypothesisAction(workspace.idea_id, 'candidate')
      else if (workspace.next_action.action === 'RUN_VALIDATION') await hypothesisAction(workspace.idea_id, 'validate')
      else if (workspace.next_action.action === 'REVEAL_HOLDOUT') await hypothesisAction(workspace.idea_id, 'reveal-holdout')
      else if (workspace.next_action.action === 'CREATE_STRATEGY') await hypothesisAction(workspace.idea_id, 'strategy')
      else if (workspace.next_action.action === 'RUN_BACKTEST') {
        if (!workspace.strategy) throw new Error('Native Strategy is missing from this Research Workspace.')
        const created = await createBacktest({ strategy_id: workspace.strategy.strategy_id, dataset_id: workspace.dataset_id, parameters: {} })
        if (!created.trace_id) throw new Error('Workspace Backtest did not produce a replayable Trace.')
        await attachHypothesisRun(workspace.idea_id, created.run_id, created.trace_id)
        onRunComplete(created.trace_id, created.run_id)
      }
      setConfirmHoldout(false)
      await refresh(workspace.idea_id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  function openStage(stage: WorkspaceStageKey) {
    if (!workspace) return
    if (stage === 'DATA') onOpenData()
    else if (stage === 'FACTOR') onOpenFactors()
    else if (stage === 'PORTFOLIO') onOpenPortfolio()
    else if (stage === 'VALIDATION' || stage === 'HYPOTHESIS') onOpenHypothesis(workspace.idea_id)
    else if (stage === 'STRATEGY' && workspace.strategy) onOpenStrategy(workspace.strategy.strategy_id, workspace.dataset_id)
    else if (stage === 'RUN' && workspace.runs.length > 0) onOpenRun(workspace.runs.at(-1)!.run_id)
  }

  const latestRun = workspace?.runs.at(-1) ?? null

  return <main className="discover-shell research-workbench unified-workspace">
    <section className="discover-title">
      <div><span className="section-kicker">{tr('Idea-centered research')}</span><h1>{tr('Research Workspace')}</h1><p>{tr('Continue one research Idea across its existing Data, Factor, Portfolio, Validation, Hypothesis, Native Strategy, and Run records without losing context.')}</p></div>
      <span className="bias-tag">{tr('One Idea · one continuous chain')}</span>
    </section>

    {error && <section className="workspace-panel research-error" role="alert">{tr(error)}</section>}

    <section className="workspace-unified-grid">
      <aside className="workspace-panel workspace-idea-list">
        <div className="section-heading"><h2>{tr('Research Ideas')}</h2><span>{summaries.length}</span></div>
        {!loading && summaries.length === 0 && <><p className="empty-copy">{tr('No Research Ideas yet.')}</p><button className="primary-button" onClick={() => onOpenHypothesis('')}>{tr('Create Research Idea')}</button></>}
        {summaries.map((item) => <button key={item.idea_id} className={workspace?.idea_id === item.idea_id ? 'selected' : ''} disabled={busy} onClick={() => void selectIdea(item.idea_id)}>
          <span className="workspace-idea-title"><strong>{item.title}</strong><b className={`integrity-severity ${item.integrity_status.toLowerCase()}`}>{tr(item.integrity_status)}</b></span>
          <small>{tr('Revision')} {item.revision} · {tr(item.lifecycle_status)}</small>
          <span className="workspace-progress"><i style={{ width: `${(item.completed_stage_count / item.total_stage_count) * 100}%` }} /><em>{item.completed_stage_count}/{item.total_stage_count}</em></span>
          <code>{tr(item.next_action.label)}</code>
        </button>)}
      </aside>

      <div className="workspace-unified-stack">
        {loading && <section className="workspace-panel workspace-empty"><span>···</span><h2>{tr('Loading Research Workspace…')}</h2></section>}
        {!loading && workspace == null && summaries.length === 0 && <section className="workspace-panel workspace-empty"><span>01</span><h2>{tr('Start in Strategy Discovery')}</h2><p>{tr('A Research Workspace appears automatically when you record a versioned Hypothesis from existing Factor evidence.')}</p><button onClick={() => onOpenHypothesis('')}>{tr('Open Strategy Discovery')}</button></section>}

        {!loading && workspace && <>
          <section className="workspace-panel workspace-idea-header">
            <div className="section-heading"><div><span className="section-kicker">{tr(workspace.lifecycle_status)} · {tr('Revision')} {workspace.revision}</span><h2>{workspace.title}</h2></div><span className={`integrity-severity ${workspace.integrity_status.toLowerCase()}`}>{tr('Integrity')} · {tr(workspace.integrity_status)}</span></div>
            <p>{workspace.description}</p>
            <div className="workspace-idea-meta"><code>{workspace.idea_id}</code><span>{tr(workspace.outcome)}</span><span>{tr('Updated')} · {dateTime(workspace.updated_at)}</span></div>
          </section>

          <section className="workspace-panel workspace-flow">
            <div className="section-heading"><div><span className="section-kicker">{tr('Continuous research path')}</span><h2>{tr('Idea progress')}</h2></div><span>{workspace.stages.filter((item) => item.status === 'COMPLETE').length}/7 {tr('complete')}</span></div>
            <div className="workspace-stage-rail">{workspace.stages.map((stage, index) => <button key={stage.key} className={stage.status.toLowerCase()} onClick={() => openStage(stage.key)}>
              <span>{String(index + 1).padStart(2, '0')}</span><b>{tr(stageLabel[stage.key])}</b><small>{tr(stage.status)}</small><p>{tr(stage.summary)}</p>{stage.artifact_ids[0] && <code title={stage.artifact_ids.join(' · ')}>{shortId(stage.artifact_ids[0])}</code>}
            </button>)}</div>
          </section>

          <section className={`workspace-panel workspace-next-action ${confirmHoldout ? 'confirming' : ''}`}>
            <div><span className="section-kicker">{tr('Next explicit action')}</span><h2>{tr(workspace.next_action.label)}</h2><p>{confirmHoldout ? tr('Holdout is sealed. Confirming will reveal it for this immutable Hypothesis revision; no parameter or Idea is changed automatically.') : tr('Continue this Idea through the existing research and execution services. Every completed action returns here with the same context.')}</p></div>
            <div>{confirmHoldout && <button onClick={() => setConfirmHoldout(false)}>{tr('Keep Holdout sealed')}</button>}<button className={confirmHoldout ? 'holdout-button' : 'primary-button'} disabled={busy} onClick={() => void advance()}>{tr(busy ? 'Working…' : confirmHoldout ? 'Confirm Reveal Holdout' : workspace.next_action.label)}</button></div>
          </section>

          <section className="workspace-overview-grid">
            <article className="workspace-panel workspace-context-card">
              <div className="section-heading"><div><span className="section-kicker">{tr('Research context')}</span><h2>{tr('Data & Factors')}</h2></div><button onClick={onOpenData}>{tr('Open Data')}</button></div>
              <dl><div><dt>{tr('Dataset')}</dt><dd>{workspace.dataset_name ?? tr('Missing')}</dd></div><div><dt>{tr('Dataset revision')}</dt><dd><code>{shortId(workspace.dataset_revision)}</code></dd></div><div><dt>{tr('Coverage')}</dt><dd>{workspace.dataset_period ? `${dateTime(workspace.dataset_period[0])} → ${dateTime(workspace.dataset_period[1])}` : '—'}</dd></div></dl>
              <div className="workspace-factor-list">{workspace.factors.map((factor) => <button key={factor.research_id} onClick={onOpenFactors}><span><strong>{tr(factor.factor_id)}</strong><small>{factor.name}</small></span><code>{tr(factor.revealed_stage)}</code></button>)}</div>
            </article>

            <article className="workspace-panel workspace-context-card">
              <div className="section-heading"><div><span className="section-kicker">{tr('Idea contract')}</span><h2>{tr('Hypothesis')}</h2></div><button onClick={() => onOpenHypothesis(workspace.idea_id)}>{tr('Open Hypothesis')}</button></div>
              <blockquote>{workspace.expected_relationship}</blockquote>
              <dl><div><dt>{tr('Holding horizon')}</dt><dd>{workspace.holding_horizon}</dd></div><div><dt>{tr('Rebalance idea')}</dt><dd>{tr(workspace.rebalance_idea)}</dd></div><div><dt>{tr('Risk assumptions')}</dt><dd>{workspace.risk_assumptions.join(' · ')}</dd></div></dl>
            </article>

            <article className="workspace-panel workspace-context-card">
              <div className="section-heading"><div><span className="section-kicker">{tr('Constructed research')}</span><h2>{tr('Portfolio & Strategy')}</h2></div>{workspace.portfolio && <button onClick={onOpenPortfolio}>{tr('Open Portfolio')}</button>}</div>
              {workspace.portfolio ? <dl><div><dt>{tr('Portfolio')}</dt><dd>{workspace.portfolio.name}</dd></div><div><dt>{tr('Combination')}</dt><dd>{tr(workspace.portfolio.combination)}</dd></div><div><dt>{tr('Rebalance')}</dt><dd>{tr(workspace.portfolio.rebalance)}</dd></div><div><dt>{tr('Net return')}</dt><dd>{number(workspace.portfolio.net_return)}</dd></div><div><dt>{tr('Turnover')}</dt><dd>{number(workspace.portfolio.turnover)}</dd></div></dl> : <p className="empty-copy">{tr('Candidate Portfolio has not been created.')}</p>}
              {workspace.strategy && <button className="workspace-strategy-link" onClick={() => onOpenStrategy(workspace.strategy!.strategy_id, workspace.dataset_id)}><span>{tr('Native Strategy')}</span><code>{shortId(workspace.strategy.strategy_id)}</code></button>}
            </article>

            <article className="workspace-panel workspace-context-card">
              <div className="section-heading"><div><span className="section-kicker">{tr('Recorded execution')}</span><h2>{tr('Runs & Evidence')}</h2></div>{latestRun && <button onClick={() => onOpenRun(latestRun.run_id)}>{tr('Open Run')}</button>}</div>
              {latestRun ? <div className="workspace-run-summary"><header><span>{tr(latestRun.status)}</span><code>{shortId(latestRun.run_id)}</code></header><dl><div><dt>{tr('Total return')}</dt><dd>{number(latestRun.total_return)}</dd></div><div><dt>{tr('Max drawdown')}</dt><dd>{number(latestRun.max_drawdown)}</dd></div></dl>{latestRun.trace_id && <button onClick={() => onOpenReplay(latestRun.trace_id!)}>{tr('Open Replay')}</button>}</div> : <p className="empty-copy">{tr('No immutable Run / Trace is attached yet.')}</p>}
              <div className="workspace-evidence-links"><button onClick={() => onOpenIntegrity(workspace.idea_id)}>{tr('Integrity')} <b>{tr(workspace.integrity_status)}</b><small>{workspace.integrity_violations} / {workspace.integrity_warnings}</small></button><button onClick={onOpenSnapshots}>{tr('Research Snapshots')} <b>{workspace.snapshot_ids.length}</b><small>{tr('frozen records')}</small></button></div>
            </article>
          </section>

          <p className="workspace-disclosure">{tr(workspace.disclosure)}</p>
        </>}
      </div>
    </section>
  </main>
}
