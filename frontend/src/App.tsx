import { useCallback, useEffect, useMemo, useState } from 'react'

import { getDatasets } from './api/datasets'
import { createBacktest, getRunContext, getTrace } from './api/replay'
import { getStrategyDefinitions } from './api/strategies'
import ProductNav from './components/ProductNav'
import type { ProductPage } from './components/ProductNav'
import GlobalSearch from './components/GlobalSearch'
import AutopsyPage from './features/autopsy/AutopsyPage'
import DiagnosePage from './features/diagnose/DiagnosePage'
import DataPage from './features/data/DataPage'
import ForwardPage from './features/forward/ForwardPage'
import LivePaperPage from './features/forward/LivePaperPage'
import ProfilePage from './features/profile/ProfilePage'
import HistoricalMarketPage from './features/discover/HistoricalMarketPage'
import FactorLabPage from './features/discover/FactorLabPage'
import FactorRelationshipPage from './features/discover/FactorRelationshipPage'
import DiscoveryWorkspacePage from './features/discover/DiscoveryWorkspacePage'
import PortfolioLabPage from './features/discover/PortfolioLabPage'
import WalkForwardPage from './features/discover/WalkForwardPage'
import IntegrityPage from './features/research/IntegrityPage'
import DataAuditPage from './features/research/DataAuditPage'
import ResearchSnapshotsPage from './features/research/ResearchSnapshotsPage'
import ResearchWorkspacePage from './features/research/ResearchWorkspacePage'
import StrategyDriftPage from './features/research/StrategyDriftPage'
import ResearchLineagePage from './features/research/ResearchLineagePage'
import ReplayPage from './features/replay/ReplayPage'
import RunsPage from './features/runs/RunsPage'
import type { LoadedRunConfiguration } from './features/runs/RunsPage'
import StrategyPage from './features/strategy/StrategyPage'
import WorkspacePage from './features/workspaces/WorkspacePage'
import { useWorkspace } from './features/workspaces/WorkspaceContext'
import { useI18n } from './i18n/I18nProvider'
import type { BacktestTrace, RunContext } from './types/trace'
import type { DatasetDefinition } from './types/dataset'
import type { AuditRootType } from './types/dataAudit'
import type { LineageNode } from './types/researchLineage'
import type { StrategyDefinition, StrategyParameters } from './types/strategy'
import type { SearchOpenTarget } from './types/search'
import { resolveSearchTarget } from './utils/searchRoutes'

type ReplayStage = 'idle' | 'running' | 'loading-trace' | 'ready' | 'error'

function initialLocation(): { page: ProductPage; runId: string | null } {
  const match = window.location.pathname.match(/^\/runs\/(run-[0-9a-f]{24})(?:\/.*)?$/)
  if (match) return { page: 'runs', runId: match[1] }
  if (window.location.pathname === '/runs') return { page: 'runs', runId: null }
  if (window.location.pathname === '/paper') return { page: 'paper', runId: null }
  if (window.location.pathname === '/forward') return { page: 'forward', runId: null }
  if (window.location.pathname === '/me') return { page: 'profile', runId: null }
  if (window.location.pathname === '/workspaces') return { page: 'workspaces', runId: null }
  if (window.location.pathname === '/historical-market') return { page: 'historical', runId: null }
  if (window.location.pathname === '/factor-lab') return { page: 'factors', runId: null }
  if (window.location.pathname === '/portfolio-lab') return { page: 'portfolio', runId: null }
  if (window.location.pathname === '/walk-forward') return { page: 'walk-forward', runId: null }
  if (window.location.pathname === '/factor-relationships') return { page: 'relationships', runId: null }
  if (window.location.pathname === '/discovery') return { page: 'discovery', runId: null }
  if (window.location.pathname.startsWith('/research-workspace')) return { page: 'workspace', runId: null }
  if (window.location.pathname === '/research-lineage') return { page: 'lineage', runId: null }
  if (window.location.pathname === '/research-snapshots') return { page: 'snapshots', runId: null }
  if (window.location.pathname === '/research-integrity') return { page: 'integrity', runId: null }
  if (window.location.pathname === '/data-audits') return { page: 'audit', runId: null }
  if (window.location.pathname === '/strategy-drift') return { page: 'drift', runId: null }
  if (window.location.pathname === '/data') return { page: 'data', runId: null }
  if (window.location.pathname === '/strategy') return { page: 'strategy', runId: null }
  return { page: 'strategy', runId: null }
}

function StartupState({ title, detail, error, onRetry }: { title: string; detail?: string; error?: boolean; onRetry?: () => void }) {
  const { tr } = useI18n()
  return <main className="workspace-loading"><section role={error ? 'alert' : undefined}><h1>{title}</h1>{detail && <span>{tr(detail)}</span>}{onRetry && <button type="button" onClick={onRetry}>{tr('Retry')}</button>}</section></main>
}

function RunContextBar({ runId, traceId, trace, context, forwardSessionId }: { runId: string | null; traceId: string | null; trace: BacktestTrace | null; context: RunContext | null; forwardSessionId: string | null }) {
  const { tr } = useI18n()
  return <header className="run-context-bar">
    <div className="context-primary">
      <span className="context-kicker">{tr('Current context')}</span>
      <strong>{trace ? tr(trace.strategy.name) : tr('Visual Quant Debugger')}</strong>
    </div>
    <div className="context-secondary">
      {runId && <span className="context-chip"><small>{tr('Run')}</small><code>{runId}</code></span>}
      {traceId && <span className="context-chip"><small>{tr('Trace')}</small><code>{traceId}</code></span>}
      {trace && <span className="context-chip"><small>{tr('Dataset')}</small><b>{tr(trace.metadata.dataset_name)}</b></span>}
      {context && <span className="context-chip context-status"><small>{tr('Status')}</small><strong>{tr(context.status)}</strong></span>}
      {!trace && traceId && !runId && <span className="context-chip"><small>{tr('Status')}</small><b>{tr('Active run')}</b></span>}
    </div>
    {forwardSessionId && <span className="context-forward">{tr('Forward')} <code>{forwardSessionId}</code></span>}
  </header>
}

function App() {
  const { tr } = useI18n()
  const { currentWorkspace, memberships } = useWorkspace()
  const initial = initialLocation()
  const [page, setPage] = useState<ProductPage>(initial.page)
  const [definitions, setDefinitions] = useState<StrategyDefinition[]>([])
  const [datasets, setDatasets] = useState<DatasetDefinition[]>([])
  const [selectedStrategyId, setSelectedStrategyId] = useState('pairs-trading')
  const [selectedDatasetId, setSelectedDatasetId] = useState('pairs-sample-v1')
  const [researchConfiguration, setResearchConfiguration] = useState<{
    strategy_id: string
    dataset_id: string
    parameters: StrategyParameters
    research_cutoff: string | null
  } | null>(null)
  const [loadedStrategyConfiguration, setLoadedStrategyConfiguration] = useState<LoadedRunConfiguration | null>(null)
  const [definitionError, setDefinitionError] = useState<string | null>(null)
  const [replayStage, setReplayStage] = useState<ReplayStage>('idle')
  const [trace, setTrace] = useState<BacktestTrace | null>(null)
  const [runContext, setRunContext] = useState<RunContext | null>(null)
  const [replayError, setReplayError] = useState<string | null>(null)
  const [activeTraceId, setActiveTraceId] = useState<string | null>(null)
  const [activeRunId, setActiveRunId] = useState<string | null>(initial.runId)
  const [searchOpen, setSearchOpen] = useState(false)
  const [replayTargetEventId, setReplayTargetEventId] = useState<string | null>(null)
  const [forwardSessionId, setForwardSessionId] = useState<string | null>(() => new URLSearchParams(window.location.search).get('session_id'))
  const [activeIdeaId, setActiveIdeaId] = useState<string | null>(() => {
    const match = window.location.pathname.match(/^\/research-workspace\/(hypothesis-[0-9a-f]+)$/)
    return match?.[1] ?? null
  })
  const workspaceDatasets = useMemo(() => {
    if (!currentWorkspace) return datasets
    const ids = new Set(memberships.filter((item) => item.object_type === 'DATASET').map((item) => item.object_id))
    return datasets.filter((item) => ids.has(item.dataset_id))
  }, [currentWorkspace, datasets, memberships])

  const loadDefinition = useCallback(async () => {
    setDefinitionError(null)
    try {
      const [nextDefinitions, nextDatasets] = await Promise.all([getStrategyDefinitions(), getDatasets()])
      if (nextDefinitions.length === 0) throw new Error('The Strategy Library is empty.')
      if (nextDatasets.length === 0) throw new Error('The Dataset Library is empty.')
      setDefinitions(nextDefinitions); setDatasets(nextDatasets)
      const strategy = nextDefinitions.find((item) => item.strategy_id === 'pairs-trading') ?? nextDefinitions[0]
      const dataset = nextDatasets.find((item) => item.dataset_id === 'pairs-sample-v1') ?? nextDatasets[0]
      const samplePreset = strategy.strategy_id === 'pairs-trading' && dataset.dataset_id === 'pairs-sample-v1'
        ? strategy.presets.find((item) => item.preset_id === 'demo-active-signals')
        : null
      setSelectedStrategyId(strategy.strategy_id); setSelectedDatasetId(dataset.dataset_id)
      setResearchConfiguration({
        strategy_id: strategy.strategy_id,
        dataset_id: dataset.dataset_id,
        parameters: samplePreset?.parameters ?? Object.fromEntries(strategy.parameters.map((item) => [item.key, item.default_value])),
        research_cutoff: null,
      })
    }
    catch (reason) { setDefinitionError(reason instanceof Error ? reason.message : 'Strategy Definition failed with an unknown error.') }
  }, [])

  useEffect(() => { const timer = window.setTimeout(() => void loadDefinition(), 0); return () => window.clearTimeout(timer) }, [loadDefinition])

  useEffect(() => {
    function restoreLocation() {
      const location = initialLocation()
      const ideaMatch = window.location.pathname.match(/^\/research-workspace\/(hypothesis-[0-9a-f]+)$/)
      setPage(location.page)
      setActiveRunId(location.runId)
      setActiveIdeaId(ideaMatch?.[1] ?? null)
      if (location.page === 'forward') setForwardSessionId(new URLSearchParams(window.location.search).get('session_id'))
    }
    window.addEventListener('popstate', restoreLocation)
    return () => window.removeEventListener('popstate', restoreLocation)
  }, [])

  const loadTraceById = useCallback(async (traceId: string) => {
    setReplayStage('loading-trace'); setReplayError(null); setActiveTraceId(traceId)
    try { const [nextTrace, nextContext] = await Promise.all([getTrace(traceId), getRunContext(traceId)]); setTrace(nextTrace); setRunContext(nextContext); setActiveRunId(nextContext.run_id); setReplayStage('ready') }
    catch (reason) { setReplayError(reason instanceof Error ? reason.message : 'Replay failed with an unknown error.'); setReplayStage('error') }
  }, [])

  const runDemoReplay = useCallback(async () => {
    const definition = definitions.find((item) => item.strategy_id === 'pairs-trading')
    if (!definition) return
    const demo = definition.presets.find((preset) => preset.preset_id === 'demo-active-signals')
    if (!demo) { setReplayError("Strategy Definition is missing the 'Demo: Active Signals' preset."); setReplayStage('error'); return }
    setReplayStage('running'); setReplayError(null); setReplayTargetEventId(null)
    try { const created = await createBacktest({ strategy_id: definition.strategy_id, dataset_id: 'pairs-sample-v1', parameters: demo.parameters }); if (!created.trace_id) throw new Error('Demo run did not produce a trace.'); setActiveRunId(created.run_id); await loadTraceById(created.trace_id) }
    catch (reason) { setReplayError(reason instanceof Error ? reason.message : 'Demo backtest failed with an unknown error.'); setReplayStage('error') }
  }, [definitions, loadTraceById])

  function openReplay(traceId: string) { setActiveTraceId(traceId); setReplayTargetEventId(null); setPage('replay'); void loadTraceById(traceId) }
  function activateTrace(traceId: string, runId?: string) { setActiveTraceId(traceId); setActiveRunId(runId ?? null); setTrace(null); setRunContext(null); setReplayStage('idle'); setReplayTargetEventId(null) }
  function selectStrategy(strategyId: string) {
    const next = definitions.find((item) => item.strategy_id === strategyId)
    if (!next) return
    setSelectedStrategyId(strategyId)
    setLoadedStrategyConfiguration(null)
    setResearchConfiguration({
      strategy_id: strategyId,
      dataset_id: selectedDatasetId,
      parameters: Object.fromEntries(next.parameters.map((item) => [item.key, item.default_value])),
      research_cutoff: null,
    })
  }
  function navigateReplay() { setPage('replay'); if (trace) setReplayStage('ready'); else if (activeTraceId) void loadTraceById(activeTraceId); else if (replayStage === 'idle' || replayStage === 'error') void runDemoReplay() }
  function openReplayEvent(eventId: string) { setReplayTargetEventId(eventId || null); setPage('replay'); if (trace) setReplayStage('ready'); else if (activeTraceId) void loadTraceById(activeTraceId) }

  const selectHistoricalRun = useCallback((runId: string) => {
    setActiveRunId(runId)
    window.history.replaceState({}, '', `/runs/${runId}`)
  }, [])

  function navigate(pageId: ProductPage, path = '/') {
    setPage(pageId)
    window.history.pushState({}, '', path)
  }

  function openDataAudit(rootType: AuditRootType, rootId: string) {
    navigate('audit', `/data-audits?root_type=${encodeURIComponent(rootType)}&root_id=${encodeURIComponent(rootId)}&run=1`)
  }

  function openHistoricalArtifact(runId: string, traceId: string, destination: 'replay' | 'diagnose' | 'autopsy', eventId?: string | null) {
    setActiveRunId(runId); setActiveTraceId(traceId); setReplayTargetEventId(eventId ?? null); setPage(destination)
    window.history.pushState({}, '', `/runs/${runId}`)
    if (destination === 'replay') void loadTraceById(traceId)
  }

  function loadHistoricalConfiguration(configuration: LoadedRunConfiguration) {
    if (!definitions.some((item) => item.strategy_id === configuration.strategy_id)) {
      setDefinitionError(`Strategy '${configuration.strategy_id}' is not currently registered.`)
      return
    }
    setSelectedStrategyId(configuration.strategy_id)
    setSelectedDatasetId(configuration.dataset_id)
    setResearchConfiguration(configuration)
    setLoadedStrategyConfiguration(configuration)
    navigate('strategy')
  }

  async function openWorkspaceStrategy(strategyId: string, datasetId: string) {
    try {
      const nextDefinitions = definitions.some((item) => item.strategy_id === strategyId)
        ? definitions
        : await getStrategyDefinitions()
      const next = nextDefinitions.find((item) => item.strategy_id === strategyId)
      if (!next) throw new Error(`Strategy '${strategyId}' is not currently registered.`)
      setDefinitions(nextDefinitions)
      setSelectedStrategyId(strategyId)
      setSelectedDatasetId(datasetId)
      setLoadedStrategyConfiguration(null)
      setResearchConfiguration({
        strategy_id: strategyId,
        dataset_id: datasetId,
        parameters: Object.fromEntries(next.parameters.map((item) => [item.key, item.default_value])),
        research_cutoff: null,
      })
      navigate('strategy', '/strategy')
    } catch (reason) {
      setDefinitionError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  function openLineageNode(node: LineageNode) {
    if (node.node_type === 'HYPOTHESIS') {
      setActiveIdeaId(node.artifact_id)
      navigate('workspace', `/research-workspace/${node.artifact_id}`)
    } else if (node.node_type === 'RUN') {
      setActiveRunId(node.artifact_id)
      navigate('runs', `/runs/${node.artifact_id}`)
    } else if (node.node_type === 'TRACE') {
      window.history.pushState({}, '', `/replay?trace_id=${encodeURIComponent(node.artifact_id)}`)
      openReplay(node.artifact_id)
    } else if (node.node_type === 'SNAPSHOT') {
      navigate('snapshots', `/research-snapshots?snapshot_id=${encodeURIComponent(node.artifact_id)}`)
    } else if (node.node_type === 'DRIFT_REPORT') {
      navigate('drift', node.route ?? `/strategy-drift?report_id=${encodeURIComponent(node.artifact_id)}`)
    } else if (node.node_type === 'FORWARD_SESSION') {
      navigate('forward', node.route ?? '/forward')
    } else if (node.node_type === 'PAPER_SESSION') {
      navigate('paper', node.route ?? '/paper')
    } else if (node.node_type === 'STRATEGY') {
      const datasetId = typeof node.metadata.dataset_id === 'string' ? node.metadata.dataset_id : selectedDatasetId
      void openWorkspaceStrategy(node.artifact_id, datasetId)
    } else if (node.node_type === 'DATASET') navigate('data', node.route ?? '/data')
    else if (node.node_type === 'FACTOR' || node.node_type === 'FACTOR_RESEARCH') navigate('factors', node.route ?? '/factor-lab')
    else if (node.node_type === 'FACTOR_RELATIONSHIP') navigate('relationships', node.route ?? '/factor-relationships')
    else if (node.node_type === 'WALK_FORWARD') navigate('walk-forward', node.route ?? '/walk-forward')
    else if (node.node_type === 'PORTFOLIO_RESEARCH') navigate('portfolio', node.route ?? '/portfolio-lab')
  }

  function openSearchTarget(item: SearchOpenTarget) {
    const target = resolveSearchTarget(item)
    if (item.entity_type === 'HYPOTHESIS') {
      setActiveIdeaId(item.entity_id)
      navigate('workspace', target.route)
    } else if (item.entity_type === 'RUN') {
      setActiveRunId(item.entity_id)
      navigate('runs', target.route)
    } else if (item.entity_type === 'TRACE') {
      window.history.pushState({}, '', target.route)
      openReplay(item.entity_id)
    } else if (item.entity_type === 'STRATEGY') {
      const datasetId = typeof item.metadata?.dataset_id === 'string' ? item.metadata.dataset_id : selectedDatasetId
      void openWorkspaceStrategy(item.entity_id, datasetId)
    } else {
      navigate(target.page, target.route)
    }
  }

  const definition = definitions.find((item) => item.strategy_id === selectedStrategyId) ?? null
  if (!definition) {
    if (definitionError) return <StartupState title={tr('Could not load strategy definition.')} detail={definitionError} error onRetry={() => void loadDefinition()} />
    return <StartupState title={tr('Loading strategy anatomy…')} />
  }

  let content
  const addDataset = (dataset: DatasetDefinition) => { setDatasets((current) => current.some((item) => item.dataset_id === dataset.dataset_id) ? current : [...current, dataset]); setSelectedDatasetId(dataset.dataset_id) }
  if (page === 'historical') content = <HistoricalMarketPage datasets={datasets} onImported={addDataset} onRunDataAudit={(datasetId) => openDataAudit('DATASET', datasetId)} />
  else if (page === 'factors') content = <FactorLabPage key={`factors:${window.location.search}`} datasets={datasets} onOpenHistorical={() => navigate('historical', '/historical-market')} onOpenReplay={openReplay} onRunComplete={activateTrace} onRunDataAudit={(researchId) => openDataAudit('FACTOR_RESEARCH', researchId)} />
  else if (page === 'portfolio') content = <PortfolioLabPage key={`portfolio:${window.location.search}`} onOpenReplay={openReplay} onRunComplete={activateTrace} />
  else if (page === 'walk-forward') content = <WalkForwardPage key={`walk-forward:${window.location.search}`} strategies={definitions} onOpenHistorical={(path) => navigate('historical', path)} onOpenFactor={(path) => navigate('factors', path)} onOpenReplay={(traceId, path) => { window.history.pushState({}, '', path); openReplay(traceId) }} onRunComplete={activateTrace} />
  else if (page === 'relationships') content = <FactorRelationshipPage key={`relationships:${window.location.search}`} />
  else if (page === 'discovery') content = <DiscoveryWorkspacePage initialHypothesisId={activeIdeaId} onOpenReplay={openReplay} onRunComplete={(traceId, runId) => activateTrace(traceId, runId)} />
  else if (page === 'workspace') content = <ResearchWorkspacePage initialIdeaId={activeIdeaId} onIdeaChange={setActiveIdeaId} onOpenData={() => navigate('data', '/data')} onOpenFactors={() => navigate('factors', '/factor-lab')} onOpenRelationships={() => navigate('relationships', '/factor-relationships')} onOpenWalkForward={() => navigate('walk-forward', '/walk-forward')} onOpenLineage={(ideaId) => navigate('lineage', `/research-lineage?root_type=HYPOTHESIS&root_id=${encodeURIComponent(ideaId)}&direction=BOTH&max_depth=8`)} onOpenPortfolio={() => navigate('portfolio', '/portfolio-lab')} onOpenHypothesis={(ideaId) => { setActiveIdeaId(ideaId || null); navigate('discovery', '/discovery') }} onOpenStrategy={(strategyId, datasetId) => void openWorkspaceStrategy(strategyId, datasetId)} onOpenRun={(runId) => { setActiveRunId(runId); navigate('runs', `/runs/${runId}`) }} onOpenReplay={openReplay} onOpenIntegrity={(ideaId) => { setActiveIdeaId(ideaId); navigate('integrity', '/research-integrity') }} onOpenSnapshots={() => navigate('snapshots', '/research-snapshots')} onOpenDrift={(reportId) => navigate('drift', `/strategy-drift?report_id=${encodeURIComponent(reportId)}`)} onRunComplete={(traceId, runId) => activateTrace(traceId, runId)} onRunDataAudit={openDataAudit} />
  else if (page === 'workspaces') content = <WorkspacePage />
  else if (page === 'lineage') content = <ResearchLineagePage onOpenNode={openLineageNode} />
  else if (page === 'snapshots') content = <ResearchSnapshotsPage initialSnapshotId={new URLSearchParams(window.location.search).get('snapshot_id')} onOpenRuns={(runId) => { setActiveRunId(runId); navigate('runs', `/runs/${runId}`) }} onOpenReplay={openReplay} />
  else if (page === 'integrity') content = <IntegrityPage initialHypothesisId={activeIdeaId} />
  else if (page === 'audit') content = <DataAuditPage key={`audit:${window.location.search}`} />
  else if (page === 'drift') content = <StrategyDriftPage key={`drift:${window.location.search}`} initialReportId={new URLSearchParams(window.location.search).get('report_id')} onOpenReplay={(traceId, eventId) => { setActiveTraceId(traceId); setReplayTargetEventId(eventId); setPage('replay'); void loadTraceById(traceId) }} onOpenForward={(sessionId, eventId) => { setForwardSessionId(sessionId); navigate('forward', `/forward?session_id=${encodeURIComponent(sessionId)}&event_id=${encodeURIComponent(eventId)}`) }} />
  else if (page === 'strategy') content = <StrategyPage key={definition.strategy_id} definition={definition} strategies={definitions} datasets={datasets} selectedDatasetId={selectedDatasetId} loadedConfiguration={loadedStrategyConfiguration} onStrategyChange={selectStrategy} onDatasetChange={setSelectedDatasetId} onConfigurationChange={(configuration) => { setResearchConfiguration(configuration); setLoadedStrategyConfiguration(null) }} onOpenReplay={openReplay} onRunComplete={activateTrace} onStrategyImported={(imported) => { setDefinitions((current) => [...current.filter((item) => item.strategy_id !== imported.strategy_id), imported]); setSelectedStrategyId(imported.strategy_id); setResearchConfiguration({ strategy_id: imported.strategy_id, dataset_id: selectedDatasetId, parameters: Object.fromEntries(imported.parameters.map((item) => [item.key, item.default_value])), research_cutoff: null }) }} />
  else if (page === 'data') content = <DataPage key={`data:${window.location.search}:${currentWorkspace?.workspace_id ?? 'all'}`} datasets={workspaceDatasets} onImported={addDataset} />
  else if (page === 'runs') content = <RunsPage key={activeRunId ?? 'ledger'} strategies={definitions} datasets={datasets} initialRunId={activeRunId} onRunSelection={selectHistoricalRun} onOpenReplay={(runId, traceId, eventId) => openHistoricalArtifact(runId, traceId, 'replay', eventId)} onOpenDiagnose={(runId, traceId) => openHistoricalArtifact(runId, traceId, 'diagnose')} onOpenAutopsy={(runId, traceId) => openHistoricalArtifact(runId, traceId, 'autopsy')} onLoadConfiguration={loadHistoricalConfiguration} onRunDataAudit={(runId) => openDataAudit('RUN', runId)} />
  else if (page === 'diagnose') content = <DiagnosePage traceId={activeTraceId} onOpenReplay={navigateReplay} />
  else if (page === 'autopsy') content = <AutopsyPage traceId={activeTraceId} onReplay={openReplayEvent} />
  else if (page === 'forward') content = <ForwardPage definition={definition} configuration={researchConfiguration} sessionId={forwardSessionId} onSessionChange={setForwardSessionId} initialEventId={new URLSearchParams(window.location.search).get('event_id')} />
  else if (page === 'paper') {
    const paperDefinition = definition.historical_research_only ? definitions.find((item) => !item.historical_research_only) : definition
    content = paperDefinition
      ? <LivePaperPage definition={paperDefinition} definitions={definitions} onDefinitionChange={selectStrategy} onOpenProfile={() => navigate('profile', '/me')} />
      : <main className="forward-shell"><section className="workspace-panel capability-blocked"><h1>{tr('Native runtime required')}</h1><p>{tr('Framework strategies are historical-research adapters and cannot run in Forward or Live Paper.')}</p></section></main>
  }
  else if (page === 'profile') content = <ProfilePage />
  else if (replayStage === 'ready' && trace) content = <>{runContext?.status === 'PARTIAL' && <div className="partial-trace-banner global"><strong>{tr('PARTIAL TRACE')}</strong><span>{tr('Replay contains events captured before the strategy failure.')}</span></div>}<ReplayPage key={activeTraceId} trace={trace} initialEventId={replayTargetEventId} onDiagnose={() => setPage('diagnose')} onAutopsy={() => setPage('autopsy')} /></>
  else if (replayStage === 'error') content = <StartupState title={tr('Could not load trace.')} detail={tr(replayError ?? 'Unknown Replay error.')} error onRetry={() => activeTraceId ? void loadTraceById(activeTraceId) : void runDemoReplay()} />
  else content = <StartupState title={tr(replayStage === 'running' ? 'Running demo backtest…' : 'Loading trace…')} />

  return <div className="app-frame"><GlobalSearch open={searchOpen} onOpenChange={setSearchOpen} onNavigate={openSearchTarget} /><ProductNav activePage={page} onSearch={() => setSearchOpen(true)} onManageWorkspaces={() => navigate('workspaces', '/workspaces')} onHistorical={() => navigate('historical', '/historical-market')} onFactors={() => navigate('factors', '/factor-lab')} onPortfolio={() => navigate('portfolio', '/portfolio-lab')} onWalkForward={() => navigate('walk-forward', '/walk-forward')} onRelationships={() => navigate('relationships', '/factor-relationships')} onDiscovery={() => navigate('discovery', '/discovery')} onWorkspace={() => navigate('workspace', activeIdeaId ? `/research-workspace/${activeIdeaId}` : '/research-workspace')} onLineage={() => navigate('lineage', '/research-lineage')} onSnapshots={() => navigate('snapshots', '/research-snapshots')} onIntegrity={() => navigate('integrity', '/research-integrity')} onDataAudit={() => navigate('audit', '/data-audits')} onStrategy={() => navigate('strategy')} onData={() => navigate('data')} onRuns={() => navigate('runs', activeRunId ? `/runs/${activeRunId}` : '/runs')} onReplay={navigateReplay} onDiagnose={() => setPage('diagnose')} onAutopsy={() => setPage('autopsy')} onForward={() => setPage('forward')} onPaper={() => navigate('paper', '/paper')} onProfile={() => navigate('profile', '/me')} /><div className="app-workspace">{!['profile', 'paper', 'historical', 'factors', 'portfolio', 'walk-forward', 'relationships', 'discovery', 'workspace', 'workspaces', 'lineage', 'snapshots', 'integrity', 'audit', 'drift'].includes(page) && <RunContextBar runId={activeRunId} traceId={activeTraceId} trace={trace} context={runContext} forwardSessionId={forwardSessionId} />}{content}</div></div>
}

export default App
