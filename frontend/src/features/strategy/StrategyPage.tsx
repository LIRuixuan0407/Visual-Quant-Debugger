import { useEffect, useMemo, useState } from 'react'

import { checkCompatibility } from '../../api/datasets'
import { createBacktest } from '../../api/replay'
import { importStrategy } from '../../api/strategies'
import { useI18n } from '../../i18n/I18nProvider'
import type { BacktestCreated } from '../../types/trace'
import type { CompatibilityCheck, DatasetDefinition } from '../../types/dataset'
import type { StrategyDefinition, StrategyParameterKey, StrategyParameters } from '../../types/strategy'
import { formatCurrency, formatPercent } from '../replay/utils/format'
import { runtimeLabel } from '../replay/capabilities'
import StrategyAnatomy, { ConceptInspector } from './StrategyAnatomy'
import { defaultsFromDefinition, parametersEqual, validateParameters } from './utils/parameters'

interface LastRun {
  parameters: StrategyParameters
  result: BacktestCreated
}

interface StrategyPageProps {
  definition: StrategyDefinition
  strategies?: StrategyDefinition[]
  datasets?: DatasetDefinition[]
  selectedDatasetId?: string
  loadedConfiguration?: {
    strategy_id: string
    dataset_id: string
    parameters: StrategyParameters
    research_cutoff: string | null
  } | null
  onStrategyChange?: (strategyId: string) => void
  onDatasetChange?: (datasetId: string) => void
  onConfigurationChange?: (configuration: {
    strategy_id: string
    dataset_id: string
    parameters: StrategyParameters
    research_cutoff: string | null
  }) => void
  onOpenReplay: (traceId: string) => void
  onRunComplete?: (traceId: string, runId?: string) => void
  onStrategyImported?: (definition: StrategyDefinition) => void
  runBacktest?: (parameters: StrategyParameters) => Promise<BacktestCreated>
}

function StrategyPage({
  definition,
  strategies = [definition],
  datasets = [],
  selectedDatasetId = datasets[0]?.dataset_id ?? 'pairs-sample-v1',
  loadedConfiguration = null,
  onStrategyChange,
  onDatasetChange,
  onConfigurationChange,
  onOpenReplay,
  onRunComplete,
  onStrategyImported,
  runBacktest,
}: StrategyPageProps) {
  const { tr } = useI18n()
  const defaults = useMemo(() => defaultsFromDefinition(definition), [definition])
  const recommendedSamplePreset = useMemo(() => (
    !loadedConfiguration
    && definition.strategy_id === 'pairs-trading'
    && selectedDatasetId === 'pairs-sample-v1'
    && datasets.some((item) => item.dataset_id === 'pairs-sample-v1')
      ? definition.presets.find((item) => item.preset_id === 'demo-active-signals') ?? null
      : null
  ), [datasets, definition, loadedConfiguration, selectedDatasetId])
  const [selectedNodeId, setSelectedNodeId] = useState('zscore')
  const loadedDraft = loadedConfiguration?.strategy_id === definition.strategy_id
    ? loadedConfiguration.parameters
    : recommendedSamplePreset?.parameters ?? defaults
  const [selectedPresetId, setSelectedPresetId] = useState(loadedConfiguration ? 'custom' : recommendedSamplePreset?.preset_id ?? 'strategy-default')
  const [draft, setDraft] = useState<StrategyParameters>(loadedDraft)
  const [lastRun, setLastRun] = useState<LastRun | null>(null)
  const [runState, setRunState] = useState<'idle' | 'running' | 'error'>('idle')
  const [runError, setRunError] = useState<string | null>(null)
  const [researchCutoff, setResearchCutoff] = useState(loadedConfiguration?.research_cutoff
    ? loadedConfiguration.research_cutoff.slice(0, 16)
    : '')
  const [compatibility, setCompatibility] = useState<CompatibilityCheck | null>(null)
  const [compatibilityError, setCompatibilityError] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [strategyPath, setStrategyPath] = useState('')
  const [strategyClass, setStrategyClass] = useState('')
  const [importState, setImportState] = useState<'idle' | 'checking'>('idle')
  const selectedNode = definition.pipeline.find((node) => node.node_id === selectedNodeId) ?? definition.pipeline[0]
  const errors = validateParameters(definition, draft)
  const isValid = Object.keys(errors).length === 0
  const baseline = lastRun?.parameters ?? recommendedSamplePreset?.parameters ?? defaults
  const isDirty = !parametersEqual(draft, baseline)

  useEffect(() => {
    if (!datasets.some((item) => item.dataset_id === selectedDatasetId)) return
    let active = true
    const timer = window.setTimeout(() => {
      void checkCompatibility({
        strategy_id: definition.strategy_id,
        dataset_id: selectedDatasetId,
        parameters: draft,
      }).then((result) => { if (active) { setCompatibility(result); setCompatibilityError(null) } })
        .catch((reason) => { if (active) setCompatibilityError(reason instanceof Error ? reason.message : 'Compatibility check failed.') })
    }, 0)
    return () => { active = false; window.clearTimeout(timer) }
  }, [datasets, definition.strategy_id, draft, selectedDatasetId])

  function publishConfiguration(nextDraft = draft, nextDatasetId = selectedDatasetId, nextCutoff = researchCutoff) {
    onConfigurationChange?.({
      strategy_id: definition.strategy_id,
      dataset_id: nextDatasetId,
      parameters: nextDraft,
      research_cutoff: nextCutoff ? new Date(nextCutoff).toISOString() : null,
    })
  }

  function updateParameter(key: StrategyParameterKey, value: number) {
    const next = { ...draft, [key]: value }
    setDraft(next)
    publishConfiguration(next)
    setSelectedPresetId('custom')
    setRunError(null)
  }

  function selectPreset(presetId: string) {
    const preset = definition.presets.find((item) => item.preset_id === presetId)
    if (!preset) return
    setDraft({ ...preset.parameters })
    publishConfiguration({ ...preset.parameters })
    setSelectedPresetId(presetId)
    setRunError(null)
  }

  async function submitBacktest() {
    if (!isValid || runState === 'running' || definition.available === false) return
    setRunState('running')
    setRunError(null)
    try {
      const result = runBacktest
        ? await runBacktest(draft)
        : await createBacktest({
          strategy_id: definition.strategy_id,
          dataset_id: selectedDatasetId,
          parameters: draft,
          research_cutoff: researchCutoff ? new Date(researchCutoff).toISOString() : null,
        })
      if (!result.trace_id || !result.summary || result.status === 'FAILED') {
        const failure = result.failure
        throw new Error(failure
          ? `${failure.exception_type} at ${failure.timestamp}: ${failure.message}`
          : 'The run did not produce a complete trace.')
      }
      setLastRun({ parameters: { ...draft }, result })
      onRunComplete?.(result.trace_id, result.run_id)
      setRunState('idle')
    } catch (reason) {
      setRunError(reason instanceof Error ? reason.message : 'Backtest failed with an unknown error.')
      setRunState('error')
    }
  }

  async function submitStrategyImport() {
    if (!strategyPath.trim() || importState === 'checking') return
    setImportState('checking'); setRunError(null)
    try {
      const imported = await importStrategy({ path: strategyPath.trim(), class_name: strategyClass.trim() || null })
      onStrategyImported?.(imported); setImportOpen(false)
    } catch (reason) {
      setRunError(reason instanceof Error ? reason.message : 'Strategy import failed.')
    } finally { setImportState('idle') }
  }

  return (
    <main className="strategy-shell">
      <header className="strategy-header">
        <div><h1>{tr(definition.name)}</h1><p>{tr(definition.description)}</p></div><button className="secondary-button" onClick={() => setImportOpen((value) => !value)}>＋ {tr('Import Strategy')}</button>
      </header>
      {importOpen && <section className="local-import-panel strategy-import-panel" aria-label={tr('Import Strategy')}><div className="import-copy"><span className="section-kicker">{tr('NATIVE STRATEGY REGISTRY')}</span><h2>{tr('Register an existing VQDStrategy')}</h2><p>{tr('This adds a local Python source to the existing Native Strategy Registry; it does not create another runtime.')}</p></div><div className="import-fields"><label><span>{tr('Strategy source path')}</span><input autoFocus value={strategyPath} onChange={(event) => setStrategyPath(event.target.value)} placeholder="/home/me/strategies/my_strategy.py" /></label><label><span>{tr('Class name (optional)')}</span><input value={strategyClass} onChange={(event) => setStrategyClass(event.target.value)} placeholder="MyStrategy" /></label><button className="primary-button" disabled={!strategyPath.trim() || importState === 'checking'} onClick={() => void submitStrategyImport()}>{tr(importState === 'checking' ? 'Checking…' : 'Validate and import')}</button></div></section>}

      <section className="research-configuration" aria-label={tr('Current research configuration')}>
        <label>{tr('Strategy')}<select aria-label={tr('Strategy selector')} value={definition.strategy_id} onChange={(event) => onStrategyChange?.(event.target.value)}>{strategies.map((item) => <option key={item.strategy_id} value={item.strategy_id}>{tr(item.name)} · {runtimeLabel(item.runtime)} · {tr(item.trace_fidelity ?? 'FULL')}</option>)}</select></label>
        <label>{tr('Dataset')}<select aria-label={tr('Dataset selector')} value={selectedDatasetId} onChange={(event) => { onDatasetChange?.(event.target.value); publishConfiguration(draft, event.target.value) }}>{datasets.length === 0 && <option value="pairs-sample-v1">{tr('Pairs Daily Sample')}</option>}{datasets.map((item) => <option key={item.dataset_id} value={item.dataset_id}>{tr(item.name)}</option>)}</select></label>
        <label>{tr('Research cutoff')}<input aria-label={tr('Research cutoff')} type="datetime-local" value={researchCutoff} onChange={(event) => { setResearchCutoff(event.target.value); publishConfiguration(draft, selectedDatasetId, event.target.value) }} /></label>
        <div><span>{tr('Runtime')}</span><strong>{runtimeLabel(definition.runtime)}</strong></div>
        <div><span>{tr('Trace fidelity')}</span><strong className="status-badge ok">{tr(definition.trace_fidelity ?? 'FULL')}</strong></div>
      </section>
      {definition.historical_research_only && <p className="capability-notice">{tr('Framework strategies are available for historical research only.')}</p>}
      {definition.available === false && <p className="inline-error">{tr('Adapter unavailable')}: {tr(definition.unavailable_reason ?? 'The required framework is not installed.')}</p>}
      <section className="strategy-requirements">
        <span>{tr('Required fields')} <code>{definition.data_requirements?.required_fields.join(', ') ?? 'close'}</code></span>
        <span>{tr('Symbols')} <code>{definition.data_requirements?.symbols.join(', ') || definition.data_requirements?.symbol_count || 2}</code></span>
        <span>{tr('Minimum history')} <code>{compatibility?.minimum_history ?? definition.data_requirements?.minimum_history ?? 3} {tr('bars')}</code></span>
        {compatibility && <strong className={`status-badge ${compatibility.compatible ? 'ok' : 'error'}`}>{tr(compatibility.compatible ? 'COMPATIBLE' : 'INCOMPATIBLE')}</strong>}
      </section>
      {compatibility?.reasons.map((reason) => <p className="inline-error" key={reason}>{tr(reason)}</p>)}
      {compatibilityError && <p className="inline-error">{tr(compatibilityError)}</p>}

      <div className="anatomy-layout">
        <StrategyAnatomy definition={definition} selectedNodeId={selectedNode.node_id} onSelect={setSelectedNodeId} />
        <ConceptInspector definition={definition} node={selectedNode} />
      </div>

      <section className="parameter-lab" aria-labelledby="parameters-heading">
        <div className="parameter-lab-header">
          <h2 id="parameters-heading">{tr('Backtest settings')}</h2>
          <label className="preset-select">{tr('Preset')}<select aria-label={tr('Strategy preset')} value={selectedPresetId} onChange={(event) => selectPreset(event.target.value)}>
            {selectedPresetId === 'custom' && <option value="custom" disabled>{tr('Custom draft')}</option>}
            {definition.presets.map((preset) => <option key={preset.preset_id} value={preset.preset_id}>{tr(preset.name)}</option>)}
          </select></label>
        </div>
        <div className="parameter-grid">
          {definition.parameters.map((parameter) => {
            const error = errors[parameter.key]
            return (
              <label className={error ? 'parameter-control invalid' : 'parameter-control'} key={parameter.key}>
                <span className="parameter-label"><strong>{tr(parameter.label)}</strong></span>
                <span className="numeric-control"><input
                  aria-label={tr(parameter.label)}
                  aria-invalid={Boolean(error)}
                  aria-describedby={`${parameter.key}-description${error ? ` ${parameter.key}-error` : ''}`}
                  type="number"
                  min={parameter.minimum}
                  max={parameter.maximum ?? undefined}
                  step={parameter.step}
                  value={draft[parameter.key]}
                  onChange={(event) => updateParameter(parameter.key, Number(event.target.value))}
                /><b>{tr(parameter.unit)}</b></span>
                <small id={`${parameter.key}-description`}>{tr(parameter.description)}</small>
                {error && <span className="parameter-error" id={`${parameter.key}-error`} role="alert">{tr(error)}</span>}
              </label>
            )
          })}
        </div>
        <div className="run-console">
          <div className={isDirty ? 'draft-status dirty' : 'draft-status'}>
            <span>{tr(isDirty ? 'Parameters changed' : lastRun ? 'Trace matches this draft' : 'Strategy defaults loaded')}</span>
          </div>
          <div className="run-actions">
            <button className="secondary-button" type="button" disabled={!isDirty || runState === 'running'} onClick={() => setDraft({ ...baseline })}>{tr('Reset draft')}</button>
            <button className="primary-button" type="button" disabled={!isValid || runState === 'running' || compatibility?.compatible === false || definition.available === false} onClick={() => void submitBacktest()}>{tr(runState === 'running' ? 'Running…' : 'Run Backtest')}</button>
          </div>
        </div>
        {runError && <div className="run-error" role="alert"><strong>{tr('Backtest failed')}</strong><p>{tr(runError)}</p></div>}
        {lastRun && lastRun.result.summary && lastRun.result.trace_id && (
          <section className="run-summary" aria-labelledby="summary-heading">
            <h3 id="summary-heading">{tr('Backtest summary')}</h3>
            <dl>
              <div><dt>{tr('Total return')}</dt><dd>{formatPercent(lastRun.result.summary.total_return)}</dd></div>
              <div><dt>{tr('Net P&L')}</dt><dd>{formatCurrency(lastRun.result.summary.net_pnl)}</dd></div>
              <div><dt>{tr('Max drawdown')}</dt><dd>{formatPercent(lastRun.result.summary.max_drawdown)}</dd></div>
              <div><dt>{tr('Signals')}</dt><dd>{lastRun.result.summary.signals}</dd></div>
            </dl>
            <button className="open-replay-button" type="button" onClick={() => onOpenReplay(lastRun.result.trace_id!)}>{tr('Open Replay')} <span aria-hidden="true">→</span></button>
          </section>
        )}
      </section>
    </main>
  )
}

export default StrategyPage
