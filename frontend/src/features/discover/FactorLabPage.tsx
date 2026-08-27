import { useEffect, useMemo, useState, type KeyboardEvent, type MouseEvent } from 'react'

import {
  createFactorResearch,
  createFactorStrategy,
  getFactorResearch,
  getFactorResearchList,
  getFactors,
  importFactor,
  revealFactorHoldout,
  validateFactorResearch,
} from '../../api/factors'
import { getFundamentalDatasets } from '../../api/fundamentals'
import { createBacktest } from '../../api/replay'
import { useI18n } from '../../i18n/I18nProvider'
import type { DatasetDefinition } from '../../types/dataset'
import type { FactorDefinition, FactorResearchRecord, FactorResearchSummary, HorizonEvaluation, ResearchStage } from '../../types/factor'
import type { FundamentalDatasetSummary } from '../../types/fundamental'
import { nearestChartIndex, pointerToViewBoxX } from './chartInteraction'

interface Props {
  datasets: DatasetDefinition[]
  onOpenHistorical: () => void
  onOpenReplay: (traceId: string) => void
  onRunComplete: (traceId: string, runId: string) => void
}

interface DraftComponent { factor_id: string; weight: number; parameters: Record<string, number> }

const dateOnly = (value: string) => value.slice(0, 10)
const isoStart = (value: string) => `${value}T00:00:00Z`
const isoEnd = (value: string) => `${value}T23:59:59Z`
const metric = (value: number | null, percent = false) => value == null ? '—' : percent ? `${(value * 100).toFixed(2)}%` : value.toFixed(4)

function splitDates(dataset: DatasetDefinition | undefined) {
  if (!dataset) return { researchStart: '2022-01-01', researchEnd: '2022-12-31', validationStart: '2023-01-01', validationEnd: '2023-12-31', holdoutStart: '2024-01-01', holdoutEnd: '2024-12-31' }
  const start = new Date(dataset.start_time).getTime(); const end = new Date(dataset.end_time).getTime(); const span = end - start
  const at = (ratio: number) => new Date(start + span * ratio).toISOString().slice(0, 10)
  return { researchStart: dateOnly(dataset.start_time), researchEnd: at(.6), validationStart: at(.61), validationEnd: at(.8), holdoutStart: at(.81), holdoutEnd: dateOnly(dataset.end_time) }
}

function StageRail({ stage, onValidate, onReveal, busy }: { stage: ResearchStage; onValidate: () => void; onReveal: () => void; busy: boolean }) {
  const { tr } = useI18n(); const order: ResearchStage[] = ['RESEARCH', 'VALIDATION', 'HOLDOUT']; const current = order.indexOf(stage)
  return <div className="stage-rail">{order.map((item, index) => <div key={item} className={index <= current ? 'revealed' : 'sealed'}><span>{String(index + 1).padStart(2, '0')}</span><strong>{tr(item)}</strong><small>{index <= current ? tr('Results available') : tr('Results sealed')}</small>{item === 'VALIDATION' && current === 0 && <button onClick={onValidate} disabled={busy}>{tr('Validate')}</button>}{item === 'HOLDOUT' && current === 1 && <button onClick={onReveal} disabled={busy}>{tr('Reveal Holdout')}</button>}</div>)}</div>
}

function IcTimeline({ evaluation }: { evaluation: HorizonEvaluation }) {
  const { tr } = useI18n(); const values = evaluation.timeline; const width = 800; const height = 180; const mid = height / 2
  const [activeIndex, setActiveIndex] = useState(Math.max(values.length - 1, 0))
  if (values.length < 2) return <div className="research-empty">—</div>
  const x = (index: number) => 8 + index / Math.max(values.length - 1, 1) * (width - 16)
  const y = (value: number) => mid - Math.max(-1, Math.min(1, value)) * (mid - 12)
  const pathFor = (key: 'ic' | 'rank_ic') => { let open = false; return values.map((item, index) => { const value = item[key]; if (value == null) { open = false; return '' } const command = open ? 'L' : 'M'; open = true; return `${command} ${x(index)} ${y(value)}` }).filter(Boolean).join(' ') }
  const active = values[activeIndex]; const activeX = x(activeIndex); const tooltipWidth = 178
  const tooltipX = activeX > width / 2 ? activeX - tooltipWidth - 10 : activeX + 10

  function handleHover(event: MouseEvent<SVGSVGElement>) {
    const pointerX = pointerToViewBoxX(event.clientX, event.currentTarget.getBoundingClientRect(), width, height)
    setActiveIndex(nearestChartIndex(pointerX, values.length, 8, width - 8))
  }

  function handleKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'Home' && event.key !== 'End') return
    event.preventDefault()
    if (event.key === 'Home') setActiveIndex(0)
    else if (event.key === 'End') setActiveIndex(values.length - 1)
    else setActiveIndex((current) => Math.min(values.length - 1, Math.max(0, current + (event.key === 'ArrowRight' ? 1 : -1))))
  }

  return <svg className="ic-timeline interactive-research-chart" viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0} aria-label={tr('Interactive IC and Rank IC timeline')} onMouseMove={handleHover} onKeyDown={handleKeyDown}>
    <path className="ic-zero" d={`M8 ${mid}H${width - 8}`} /><path className="ic-line" d={pathFor('ic')} /><path className="rank-line" d={pathFor('rank_ic')} />
    <line className="research-hover-guide" x1={activeX} x2={activeX} y1="8" y2={height - 8} />
    {active.ic != null && <circle className="research-hover-point ic" cx={activeX} cy={y(active.ic)} r="4.5" />}
    {active.rank_ic != null && <circle className="research-hover-point rank" cx={activeX} cy={y(active.rank_ic)} r="4.5" />}
    <g className="research-chart-tooltip" transform={`translate(${tooltipX} 11)`}>
      <rect width={tooltipWidth} height="67" rx="5" />
      <text className="tooltip-date" x="10" y="17">{dateOnly(active.timestamp)}</text>
      <text x="10" y="38">IC</text><text className="tooltip-value ic-value" x={tooltipWidth - 10} y="38" textAnchor="end">{metric(active.ic)}</text>
      <text x="10" y="57">{tr('Rank IC')}</text><text className="tooltip-value rank-value" x={tooltipWidth - 10} y="57" textAnchor="end">{metric(active.rank_ic)}</text>
    </g>
  </svg>
}

function QuantileChart({ values }: { values: Array<number | null> }) {
  const [activeIndex, setActiveIndex] = useState(0)
  return <div className="quantile-chart" aria-label="Q1–Q5">
    <div className="quantile-hover-summary" aria-live="polite"><span>Q{activeIndex + 1}</span><strong>{metric(values[activeIndex] ?? null, true)}</strong></div>
    {values.map((value, index) => {
      const size = Math.min(100, Math.abs((value ?? 0) * 1200))
      return <div key={index} className={activeIndex === index ? 'active' : ''} tabIndex={0} onMouseEnter={() => setActiveIndex(index)} onFocus={() => setActiveIndex(index)}>
        <span>Q{index + 1}</span><div className="quantile-track"><i className={(value ?? 0) >= 0 ? 'positive' : 'negative'} style={{ width: `${size}%` }} /></div><code>{metric(value, true)}</code>
      </div>
    })}
  </div>
}

function EvidencePanel({ record, evaluation }: { record: FactorResearchRecord; evaluation: HorizonEvaluation }) {
  const { tr } = useI18n(); const sample = record.sample_observations[1] ?? record.sample_observations[0]
  return <div className="factor-evidence-grid">
    <section className="workspace-panel factor-metrics"><div className="section-heading"><div><span className="section-kicker">{tr('PREDICTIVE EVIDENCE')}</span><h2>{evaluation.horizon}{tr('D forward return')}</h2></div><span className={evaluation.monotonic ? 'status-badge ok' : 'status-badge warning'}>{tr(evaluation.monotonic ? 'MONOTONIC' : 'NON-MONOTONIC')}</span></div><div className="metric-strip"><div><span>{tr('IC mean')}</span><strong>{metric(evaluation.ic)}</strong></div><div><span>{tr('Rank IC mean')}</span><strong>{metric(evaluation.rank_ic)}</strong></div><div><span>{tr('IC stability')}</span><strong>{metric(evaluation.ic_stability)}</strong></div><div><span>{tr('Turnover')}</span><strong>{metric(evaluation.turnover, true)}</strong></div><div><span>{tr('Coverage')}</span><strong>{metric(evaluation.coverage, true)}</strong></div><div><span>{tr('Observations')}</span><strong>{evaluation.observation_count}</strong></div></div><div className="ic-heading"><strong>{tr('IC / Rank IC through time')}</strong><span><i className="ic" />IC <i className="rank" />{tr('Rank IC')}</span></div><IcTimeline key={`${evaluation.horizon}:${evaluation.timeline[0]?.timestamp}:${evaluation.timeline.length}`} evaluation={evaluation} /></section>
    <section className="workspace-panel quantile-panel"><div className="section-heading"><div><span className="section-kicker">{tr('CROSS-SECTIONAL SORT')}</span><h2>{tr('Quantile returns')}</h2></div><code>Q5 − Q1 {metric(evaluation.long_short_spread, true)}</code></div><QuantileChart values={evaluation.quantile_returns} /><p>{tr('Each date ranks the full universe first; returns are then averaged within Q1–Q5. No top-stock shortcut is used.')}</p></section>
    {sample && <section className="workspace-panel factor-inspector"><div className="section-heading"><div><span className="section-kicker">{tr('POINT-IN-TIME INSPECTOR')}</span><h2>{sample.symbol} · {dateOnly(sample.timestamp)}</h2></div><span className="status-badge ok">{tr('SAFE')}</span></div>{!record.restatement_safe && <div className="restatement-disclosure"><strong>{tr('NOT RESTATEMENT-SAFE')}</strong><span>{tr(record.restatement_warning ?? '')}</span></div>}<div className="inspector-value"><span>{record.factor.factor_id}</span><strong>{sample.value.toFixed(6)}</strong></div><dl><div><dt>{tr('Formula')}</dt><dd><code>{record.factor.formula}</code></dd></div><div><dt>{tr('Window')}</dt><dd>{dateOnly(sample.window_start)} — {dateOnly(sample.window_end)}</dd></div><div><dt>{tr('Inputs')}</dt><dd>{sample.dependencies.length} {tr('recorded dependencies')}</dd></div><div><dt>{tr('Future data used')}</dt><dd className="safe-no">{tr('NO')}</dd></div><div><dt>{tr('Available / used')}</dt><dd><code>{dateOnly(sample.available_at)}</code></dd></div></dl>{sample.fundamental_inputs?.length > 0 && <div className="factor-filing-lineage"><div className="header"><span>{tr('Field')}</span><span>{tr('Fiscal period')}</span><span>{tr('Report date')}</span><span>{tr('Filed')}</span><span>{tr('Available')}</span><span>{tr('Used')}</span></div>{sample.fundamental_inputs.map((item, index) => <div key={`${item.field}:${item.accession}:${index}`}><strong>{tr(item.field)}</strong><span>{item.fiscal_period ?? '—'}</span><span>{item.report_date ? dateOnly(item.report_date) : '—'}</span><span>{item.filed_at ? dateOnly(item.filed_at) : '—'}</span><span>{item.available_at ? dateOnly(item.available_at) : '—'}</span><span>{dateOnly(item.used_at)}</span></div>)}</div>}<details><summary>{tr('Inspect data lineage')}</summary><div className="dependency-list">{sample.dependencies.map((item) => <div key={item.dependency_id}><code>{item.symbol}.{item.field}</code><span>{dateOnly(item.source_timestamp)}</span><strong>{item.value}</strong></div>)}</div></details></section>}
  </div>
}

export default function FactorLabPage({ datasets, onOpenHistorical, onOpenReplay, onRunComplete }: Props) {
  const { tr } = useI18n(); const eligible = useMemo(() => datasets.filter((item) => item.source_type === 'PROVIDER' && item.frequency === '1Day' && item.symbols.length >= 5), [datasets])
  const initialParameters = new URLSearchParams(window.location.search)
  const initialResearchId = initialParameters.get('research_id')
  const initialFactorId = initialParameters.get('factor_id')
  const [factors, setFactors] = useState<FactorDefinition[]>([]); const [ledger, setLedger] = useState<FactorResearchSummary[]>([]); const [record, setRecord] = useState<FactorResearchRecord | null>(null)
  const [fundamentalDatasets, setFundamentalDatasets] = useState<FundamentalDatasetSummary[]>([]); const [fundamentalId, setFundamentalId] = useState('')
  const [datasetId, setDatasetId] = useState(eligible[0]?.dataset_id ?? ''); const dataset = eligible.find((item) => item.dataset_id === datasetId) ?? eligible[0]
  const [factorId, setFactorId] = useState('momentum'); const factor = factors.find((item) => item.factor_id === factorId) ?? factors[0]
  const [factorParameters, setFactorParameters] = useState<Record<string, number>>({}); const [dates, setDates] = useState(() => splitDates(dataset)); const [horizon, setHorizon] = useState<1 | 5 | 20>(5); const [stage, setStage] = useState<ResearchStage>('RESEARCH')
  const [components, setComponents] = useState<DraftComponent[]>([
    { factor_id: 'momentum', weight: .6, parameters: { lookback: 20 } },
    { factor_id: 'roe', weight: .4, parameters: { max_age_days: 550 } },
  ])
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [run, setRun] = useState<{ runId: string; traceId: string } | null>(null)
  const [importOpen, setImportOpen] = useState(false); const [factorPath, setFactorPath] = useState(''); const [factorClass, setFactorClass] = useState(''); const [importProof, setImportProof] = useState<{ checks: string[]; fingerprint: string } | null>(null)

  useEffect(() => { void Promise.all([getFactors(), getFactorResearchList(), getFundamentalDatasets()]).then(([nextFactors, nextLedger, nextFundamentals]) => { setFactors(nextFactors); setLedger(nextLedger); setFundamentalDatasets(Array.isArray(nextFundamentals) ? nextFundamentals : []); const first = nextFactors.find((item) => item.factor_id === initialFactorId) ?? nextFactors.find((item) => item.factor_id === 'momentum') ?? nextFactors[0]; if (first) { setFactorId(first.factor_id); setFactorParameters(Object.fromEntries(first.parameters.map((item) => [item.key, item.default_value]))) } if (nextFundamentals[0]) setFundamentalId(nextFundamentals[0].fundamental_dataset_id); const requested = nextLedger.find((item) => item.research_id === initialResearchId); if (requested) void getFactorResearch(requested.research_id).then((next) => { setRecord(next); setStage(next.revealed_stage) }); else if (!initialFactorId && nextLedger[0]) void getFactorResearch(nextLedger[0].research_id).then(setRecord); else setRecord(null) }).catch((reason) => setError(reason instanceof Error ? reason.message : 'Factor Lab failed.')) }, [initialFactorId, initialResearchId])

  const visibleStage = record && !record.evaluations.some((item) => item.stage === stage) ? record.revealed_stage : stage
  const evaluation = record?.evaluations.find((item) => item.stage === visibleStage)?.horizons.find((item) => item.horizon === horizon)
  async function createResearch() {
    if (!dataset || !factor) return; setBusy(true); setError(null)
    if (factor.data_source !== 'MARKET' && !fundamentalId) { setError('Choose a saved point-in-time fundamental record.'); setBusy(false); return }
    const researchComponents = factor.factor_id === 'mixed' ? components : []
    try { const next = await createFactorResearch({ name: `${factor.name} · ${dataset.name}`, dataset_id: dataset.dataset_id, factor_id: factor.factor_id, parameters: factor.factor_id === 'mixed' ? {} : factorParameters, universe: dataset.symbols, fundamental_dataset_id: factor.data_source === 'MARKET' ? null : fundamentalId, components: researchComponents, periods: { research: { start: isoStart(dates.researchStart), end: isoEnd(dates.researchEnd) }, validation: { start: isoStart(dates.validationStart), end: isoEnd(dates.validationEnd) }, holdout: { start: isoStart(dates.holdoutStart), end: isoEnd(dates.holdoutEnd) } } }); setRecord(next); setStage('RESEARCH'); setLedger(await getFactorResearchList()) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Factor research failed.') } finally { setBusy(false) }
  }
  async function reveal(nextStage: 'VALIDATION' | 'HOLDOUT') { if (!record) return; setBusy(true); setError(null); try { const next = nextStage === 'VALIDATION' ? await validateFactorResearch(record.research_id) : await revealFactorHoldout(record.research_id); setRecord(next); setStage(nextStage); setLedger(await getFactorResearchList()) } catch (reason) { setError(reason instanceof Error ? reason.message : 'Stage evaluation failed.') } finally { setBusy(false) } }
  async function makeStrategy() { if (!record) return; setBusy(true); setError(null); try { const strategy = await createFactorStrategy(record.research_id, { long_percent: 10, rebalance_bars: 5, gross_notional: 20_000, max_volatility: null }); setRecord({ ...record, strategy }) } catch (reason) { setError(reason instanceof Error ? reason.message : 'Strategy creation failed.') } finally { setBusy(false) } }
  async function runStrategy() { if (!record?.strategy) return; setBusy(true); setError(null); try { const created = await createBacktest({ strategy_id: record.strategy.strategy_id, dataset_id: record.dataset_id, parameters: {} }); if (!created.trace_id) throw new Error('Factor strategy did not produce a Trace.'); setRun({ runId: created.run_id, traceId: created.trace_id }); onRunComplete(created.trace_id, created.run_id) } catch (reason) { setError(reason instanceof Error ? reason.message : 'Factor backtest failed.') } finally { setBusy(false) } }
  async function submitFactorImport() { if (!factorPath.trim()) return; setBusy(true); setError(null); try { const result = await importFactor({ path: factorPath.trim(), class_name: factorClass.trim() || null }); const nextFactors = await getFactors(); setFactors(nextFactors); setFactorId(result.factor.factor_id); setFactorParameters(Object.fromEntries(result.factor.parameters.map((item) => [item.key, item.default_value]))); setImportProof({ checks: result.checks, fingerprint: result.factor.source_fingerprint }); setImportOpen(false); setRecord(null) } catch (reason) { setError(reason instanceof Error ? reason.message : 'Factor import failed.') } finally { setBusy(false) } }

  return <main className="discover-shell factor-lab-shell">
    <header className="workspace-title discover-title"><div><span className="section-kicker">{tr('IDEA → EVIDENCE')}</span><h1>{tr('Factor Lab')}</h1><p>{tr('Test transparent market and filed-fundamental ideas without leaking future information.')}</p></div><div className="title-actions"><button className="secondary-button" onClick={() => setImportOpen((value) => !value)}>＋ {tr('Import Factor')}</button><button className="secondary-button" onClick={onOpenHistorical}>{tr('Open Historical Market')}</button></div></header>
    {importOpen && <section className="local-import-panel" aria-label={tr('Import Factor')}><div className="import-copy"><span className="section-kicker">{tr('TRUSTED LOCAL PYTHON')}</span><h2>{tr('Register a VQDFactor source')}</h2><p>{tr('Enter a local .py path on this computer. The code runs with backend permissions; VQD does not provide a sandbox.')}</p></div><div className="import-fields"><label><span>{tr('Factor source path')}</span><input autoFocus value={factorPath} onChange={(event) => setFactorPath(event.target.value)} placeholder="/home/me/factors/quality_momentum.py" /></label><label><span>{tr('Class name (optional)')}</span><input value={factorClass} onChange={(event) => setFactorClass(event.target.value)} placeholder="QualityMomentum" /></label><button className="primary-button" disabled={busy || !factorPath.trim()} onClick={() => void submitFactorImport()}>{tr(busy ? 'Checking…' : 'Validate and import')}</button></div><div className="import-evidence"><span>{tr('Source file')}</span><i>→</i><span>{tr('SDK validation')}</span><i>→</i><span>{tr('Registry')}</span><i>→</i><span>{tr('Factor research')}</span></div></section>}
    {importProof && <div className="import-proof" role="status"><strong>{tr('Custom factor registered')}</strong><span>{importProof.checks.map(tr).join(' · ')}</span><code>{importProof.fingerprint}</code></div>}
    {error && <div className="compact-error" role="alert"><strong>{tr('Factor operation failed')}</strong><span>{tr(error)}</span></div>}
    <section className="factor-layout"><aside className="workspace-panel factor-ledger"><div className="section-heading"><h2>{tr('Research Ledger')}</h2><span>{ledger.length}</span></div><button className={!record ? 'selected new-research' : 'new-research'} onClick={() => setRecord(null)}>＋ {tr('New factor research')}</button>{ledger.map((item) => <button key={item.research_id} className={record?.research_id === item.research_id ? 'selected' : ''} onClick={() => { setBusy(true); void getFactorResearch(item.research_id).then((next) => { setRecord(next); setStage(next.revealed_stage) }).catch((reason) => setError(String(reason))).finally(() => setBusy(false)) }}><strong>{item.name}</strong><span>{tr(item.factor_id)} · {item.symbols} {tr('stocks')}</span><small>{dateOnly(item.created_at)} · {tr(item.revealed_stage)}</small></button>)}</aside>
      <div className="factor-main">{!record ? <section className="workspace-panel factor-builder"><div className="section-heading"><div><span className="section-kicker">{tr('DEFINE ONE HYPOTHESIS')}</span><h2>{tr('Start a factor study')}</h2></div><span className="bias-tag">{tr('POINT-IN-TIME')}</span></div>{eligible.length === 0 ? <div className="research-empty actionable"><strong>{tr('A multi-stock real dataset is required')}</strong><p>{tr('Download at least five real stocks in Historical Market before starting cross-sectional research.')}</p><button className="primary-button" onClick={onOpenHistorical}>{tr('Build real universe')}</button></div> : <>
        <div className="factor-definition-grid factor-lab-definition-grid">
          <label><span>{tr('Real-stock universe')}</span><select value={dataset?.dataset_id ?? ''} onChange={(event) => { const next = eligible.find((item) => item.dataset_id === event.target.value); setDatasetId(event.target.value); setDates(splitDates(next)) }}>{eligible.map((item) => <option value={item.dataset_id} key={item.dataset_id}>{item.name} · {item.symbols.length}</option>)}</select><small>{dataset?.symbols.join(' · ')}</small></label>
          <label><span>{tr('Factor')}</span><select value={factor?.factor_id ?? ''} onChange={(event) => { const next = factors.find((item) => item.factor_id === event.target.value); setFactorId(event.target.value); if (next) setFactorParameters(Object.fromEntries(next.parameters.map((item) => [item.key, item.default_value]))) }}>{factors.map((item) => <option key={item.factor_id} value={item.factor_id}>{tr(item.origin ?? 'BUILT_IN')} · {tr(item.category)} · {tr(item.name)}</option>)}</select><small>{factor && tr(factor.description)}</small></label>
          {factor?.data_source !== 'MARKET' && <label><span>{tr('Point-in-time fundamentals')}</span><select value={fundamentalId} onChange={(event) => setFundamentalId(event.target.value)}><option value="">{tr('Select a saved filing record')}</option>{fundamentalDatasets.filter((item) => dataset?.symbols.every((symbol) => item.symbols.includes(symbol))).map((item) => <option key={item.fundamental_dataset_id} value={item.fundamental_dataset_id}>{item.name}</option>)}</select><small>{factor?.required_fundamental_fields.map(tr).join(' · ')}</small></label>}
          {factor?.factor_id !== 'mixed' && factor?.parameters.map((parameter) => <label key={parameter.key}><span>{tr(parameter.label)}</span><input type="number" min={parameter.minimum} max={parameter.maximum ?? undefined} step={parameter.step} value={factorParameters[parameter.key] ?? parameter.default_value} onChange={(event) => setFactorParameters((current) => ({ ...current, [parameter.key]: Number(event.target.value) }))} /><small>{tr(parameter.description)} · <code>{parameter.unit}</code></small></label>)}
        </div>
        {factor?.factor_id === 'mixed' && <div className="mixed-factor-builder"><div className="mixed-heading"><div><strong>{tr('Explicit factor mix')}</strong><span>{tr('VQD will not invent factors, weights, or formulas.')}</span></div><button className="secondary-button" onClick={() => setComponents((current) => [...current, { factor_id: 'momentum', weight: 0, parameters: { lookback: 20 } }])}>{tr('Add component')}</button></div><div className="mixed-row header"><span>{tr('Component')}</span><span>{tr('Weight')}</span><span>{tr('Parameters')}</span><span /></div>{components.map((component, index) => { const definition = factors.find((item) => item.factor_id === component.factor_id); return <div className="mixed-row" key={`${index}:${component.factor_id}`}><select value={component.factor_id} onChange={(event) => { const next = factors.find((item) => item.factor_id === event.target.value); setComponents((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, factor_id: event.target.value, parameters: Object.fromEntries((next?.parameters ?? []).map((parameter) => [parameter.key, parameter.default_value])) } : item)) }}>{factors.filter((item) => item.factor_id !== 'mixed').map((item) => <option key={item.factor_id} value={item.factor_id}>{tr(item.origin ?? 'BUILT_IN')} · {tr(item.name)}</option>)}</select><input aria-label={`${tr('Weight')} ${index + 1}`} type="number" step="0.1" min="-10" max="10" value={component.weight} onChange={(event) => setComponents((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, weight: Number(event.target.value) } : item))} /><div className="component-parameters">{definition?.parameters.map((parameter) => <label key={parameter.key}><span>{tr(parameter.label)}</span><input aria-label={`${tr(parameter.label)} ${index + 1}`} type="number" min={parameter.minimum} max={parameter.maximum ?? undefined} step={parameter.step} value={component.parameters[parameter.key] ?? parameter.default_value} onChange={(event) => setComponents((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, parameters: { ...item.parameters, [parameter.key]: Number(event.target.value) } } : item))} /></label>)}</div><button className="icon-button" aria-label={tr('Remove component')} disabled={components.length <= 2} onClick={() => setComponents((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></div> })}</div>}
        <div className="period-editor"><div><strong>{tr('RESEARCH')}</strong><input type="date" value={dates.researchStart} onChange={(event) => setDates({ ...dates, researchStart: event.target.value })} /><span>→</span><input type="date" value={dates.researchEnd} onChange={(event) => setDates({ ...dates, researchEnd: event.target.value })} /></div><div><strong>{tr('VALIDATION')}</strong><input type="date" value={dates.validationStart} onChange={(event) => setDates({ ...dates, validationStart: event.target.value })} /><span>→</span><input type="date" value={dates.validationEnd} onChange={(event) => setDates({ ...dates, validationEnd: event.target.value })} /></div><div><strong>{tr('HOLDOUT')}</strong><input type="date" value={dates.holdoutStart} onChange={(event) => setDates({ ...dates, holdoutStart: event.target.value })} /><span>→</span><input type="date" value={dates.holdoutEnd} onChange={(event) => setDates({ ...dates, holdoutEnd: event.target.value })} /></div></div><div className="builder-footer"><p>{tr('Only Research results are visible first. Validation and Holdout stay sealed until you explicitly reveal them.')}</p><button className="primary-button" disabled={busy || factor?.factor_id === 'mixed' && components.length < 2} onClick={() => void createResearch()}>{tr(busy ? 'Calculating…' : 'Run Research')}</button></div></>}</section> : <>
        <section className="workspace-panel factor-context"><div><span className="section-kicker">{tr('FACTOR RESEARCH')}</span><h2>{record.name}</h2><p>{record.universe.join(' · ')}</p></div><div className="factor-context-meta"><span className="status-badge">{tr(record.factor.origin ?? 'BUILT_IN')}</span><span className="status-badge">{tr(record.factor.category)}</span><span className="status-badge">{tr(record.factor.data_source)}</span><code>{record.research_id}</code><span>{record.factor_observation_count} {tr('factor observations')}</span></div></section>
        <section className="bias-notice compact"><strong>{tr(record.universe_mode)}</strong><span>{tr(record.survivorship_bias_free ? 'POINT-IN-TIME MEMBERSHIP' : 'NOT SURVIVORSHIP-BIAS FREE')}</span><p>{tr(record.survivorship_warning)}</p></section>
        <StageRail stage={record.revealed_stage} busy={busy} onValidate={() => void reveal('VALIDATION')} onReveal={() => void reveal('HOLDOUT')} />
        <div className="evidence-tabs"><div>{record.evaluations.map((item) => <button key={item.stage} className={visibleStage === item.stage ? 'active' : ''} onClick={() => setStage(item.stage)}>{tr(item.stage)}</button>)}</div><div>{([1, 5, 20] as const).map((item) => <button key={item} className={horizon === item ? 'active' : ''} onClick={() => setHorizon(item)}>{item}{tr('D')}</button>)}</div></div>
        {evaluation && <EvidencePanel record={record} evaluation={evaluation} />}
        <section className="workspace-panel strategy-bridge"><div><span className="section-kicker">{tr('FACTOR → NATIVE STRATEGY')}</span><h2>{tr('Turn this evidence into an inspectable strategy')}</h2><p>{tr('Long the top 10%, rebalance every five bars, and execute through the existing VQD runtime.')}</p></div>{!record.strategy ? <button className="primary-button" disabled={busy || record.revealed_stage === 'RESEARCH'} onClick={() => void makeStrategy()}>{tr('Create Research Strategy')}</button> : <div className="strategy-actions"><code>{record.strategy.strategy_id}</code><button className="primary-button" disabled={busy} onClick={() => void runStrategy()}>{tr('Run VQD Backtest')}</button></div>}{record.revealed_stage === 'RESEARCH' && <small>{tr('Validate once before creating a strategy.')}</small>}{run && <div className="run-proof"><span>{tr('Backtest completed')}</span><code>{run.runId}</code><button className="link-button" onClick={() => onOpenReplay(run.traceId)}>{tr('Open Replay')}</button></div>}</section>
      </>}</div>
    </section>
  </main>
}
