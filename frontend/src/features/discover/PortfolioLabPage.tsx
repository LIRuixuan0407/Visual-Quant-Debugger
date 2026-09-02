import { useEffect, useMemo, useState } from 'react'

import { createBacktest } from '../../api/replay'
import { getFactorResearchList } from '../../api/factors'
import {
  createPortfolioResearch,
  createPortfolioStrategy,
  getPortfolioResearch,
  getPortfolioResearchList,
  getRelationshipList,
  revealPortfolio,
} from '../../api/research'
import { useI18n } from '../../i18n/I18nProvider'
import type { FactorResearchSummary } from '../../types/factor'
import type {
  CombinationMethod,
  FactorRelationshipRecord,
  PortfolioResearchRecord,
  PortfolioResearchSummary,
  RebalanceRule,
  RiskMatrix,
} from '../../types/research'

const pct = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(2)}%`
const num = (value: number | null | undefined) => value == null ? '—' : value.toFixed(4)
const humanize = (value: string) => value.replaceAll('_', ' ')
const signedPp = (value: number) => `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)} pp`
const splitSymbols = (value: string) => [...new Set(value.split(/[\s,]+/).map((item) => item.trim().toUpperCase()).filter(Boolean))]

type SelectionMethod = 'TOP_N' | 'TOP_PERCENT'
type PositionWeighting = 'EQUAL_WEIGHT' | 'SCORE_WEIGHTED'
type DirectionChoice = 'DEFAULT' | 'HIGH' | 'LOW'

function RiskMatrixTable({ matrix, kind }: { matrix: RiskMatrix; kind: 'covariance' | 'correlation' }) {
  const { tr } = useI18n()
  const format = (value: number) => kind === 'correlation' ? value.toFixed(3) : value.toExponential(3)
  return <section className="risk-matrix-card">
    <header><strong>{tr(kind === 'correlation' ? 'Correlation' : 'Covariance')}</strong><span>{matrix.symbols.length} × {matrix.symbols.length}</span></header>
    <div>
      <table>
        <thead><tr><th />{matrix.symbols.map((symbol) => <th key={symbol}>{symbol}</th>)}</tr></thead>
        <tbody>{matrix.symbols.map((symbol, rowIndex) => <tr key={symbol}>
          <th>{symbol}</th>
          {matrix.values[rowIndex]?.map((value, columnIndex) => <td key={`${symbol}:${matrix.symbols[columnIndex]}`}><code>{format(value)}</code></td>)}
        </tr>)}</tbody>
      </table>
    </div>
  </section>
}

export default function PortfolioLabPage({
  onOpenReplay,
  onRunComplete,
}: {
  onOpenReplay: (traceId: string) => void
  onRunComplete: (traceId: string, runId?: string) => void
}) {
  const { tr } = useI18n()
  const [factors, setFactors] = useState<FactorResearchSummary[]>([])
  const [ledger, setLedger] = useState<PortfolioResearchSummary[]>([])
  const [relationships, setRelationships] = useState<FactorRelationshipRecord[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [directions, setDirections] = useState<Record<string, DirectionChoice>>({})
  const [combination, setCombination] = useState<CombinationMethod>('RANK_AVERAGE')
  const [rebalance, setRebalance] = useState<RebalanceRule>('MONTHLY')
  const [selection, setSelection] = useState<SelectionMethod>('TOP_N')
  const [topN, setTopN] = useState(5)
  const [topPercent, setTopPercent] = useState(20)
  const [weighting, setWeighting] = useState<PositionWeighting>('EQUAL_WEIGHT')
  const [maxWeight, setMaxWeight] = useState(.2)
  const [minLiquidity, setMinLiquidity] = useState('')
  const [maxVolatility, setMaxVolatility] = useState('')
  const [requireAvailability, setRequireAvailability] = useState(true)
  const [includeSymbols, setIncludeSymbols] = useState('')
  const [excludeSymbols, setExcludeSymbols] = useState('')
  const [record, setRecord] = useState<PortfolioResearchRecord | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const initialPortfolioId = new URLSearchParams(window.location.search).get('portfolio_research_id')

  const latest = record?.stages.at(-1) ?? null
  const latestSnapshot = latest?.snapshots.at(-1) ?? null
  const risk = latest?.risk_decomposition ?? null
  const selectedFactors = useMemo(
    () => factors.filter((item) => selected.includes(item.research_id)),
    [factors, selected],
  )
  const selectedDatasetIds = useMemo(
    () => new Set(selectedFactors.map((item) => item.dataset_id)),
    [selectedFactors],
  )
  const userWeightTotal = selected.reduce((sum, id) => sum + (weights[id] ?? 0), 0)
  const userWeightsValid = combination !== 'USER_DEFINED_WEIGHT' || Math.abs(userWeightTotal - 1) <= 1e-6
  const compatibleSelection = selectedDatasetIds.size <= 1
  const redundancyWarnings = useMemo(
    () => relationships.flatMap((relationship) => relationship.redundancy
      .filter((item) => item.status === 'HIGH_REDUNDANCY'
        && selected.includes(item.left_research_id)
        && selected.includes(item.right_research_id))
      .map((item) => ({ relationship, item }))),
    [relationships, selected],
  )

  async function refresh() {
    const [factorRows, portfolioRows, relationshipRows] = await Promise.all([
      getFactorResearchList(),
      getPortfolioResearchList(),
      getRelationshipList(),
    ])
    setFactors(factorRows)
    setLedger(portfolioRows)
    setRelationships(relationshipRows)
  }

  useEffect(() => {
    let mounted = true
    void Promise.all([
      getFactorResearchList(),
      getPortfolioResearchList(),
      getRelationshipList(),
    ]).then(
      ([factorRows, portfolioRows, relationshipRows]) => {
        if (!mounted) return
        setFactors(factorRows)
        setLedger(portfolioRows)
        setRelationships(relationshipRows)
        const requested = portfolioRows.find((item) => item.portfolio_research_id === initialPortfolioId)
        if (requested) void getPortfolioResearch(requested.portfolio_research_id).then((next) => { if (mounted) setRecord(next) })
      },
      (reason) => {
        if (!mounted) return
        setError(String(reason))
      },
    )
    return () => {
      mounted = false
    }
  }, [initialPortfolioId])

  function directionOverride(id: string): 'HIGH' | 'LOW' | null {
    const value = directions[id]
    return value === undefined || value === 'DEFAULT' ? null : value
  }

  function toggle(id: string) {
    setSelected((current) => current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id])
    setWeights((current) => ({ ...current, [id]: current[id] ?? 0 }))
    setDirections((current) => ({ ...current, [id]: current[id] ?? 'DEFAULT' }))
  }

  async function create() {
    if (selected.length < 2) {
      setError(tr('Select at least two existing Factor studies.'))
      return
    }
    if (!compatibleSelection) {
      setError(tr('Selected Factor studies must use the same market dataset.'))
      return
    }
    if (!userWeightsValid) {
      setError(tr('User-defined factor weights must sum to 1.0'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      const equal = 1 / selected.length
      const next = await createPortfolioResearch({
        name: `Portfolio · ${selectedFactors.map((item) => item.factor_id).join(' + ')}`,
        factors: selected.map((researchId) => ({
          research_id: researchId,
          weight: combination === 'USER_DEFINED_WEIGHT' ? (weights[researchId] ?? 0) : equal,
          direction_override: directionOverride(researchId),
        })),
        combination,
        filters: {
          minimum_liquidity: minLiquidity ? Number(minLiquidity) : null,
          maximum_volatility: maxVolatility ? Number(maxVolatility) : null,
          require_factor_availability: requireAvailability,
          include_symbols: splitSymbols(includeSymbols),
          exclude_symbols: splitSymbols(excludeSymbols),
        },
        construction: {
          selection,
          top_n: topN,
          top_percent: topPercent,
          weighting,
          max_single_position_weight: maxWeight,
        },
        rebalance,
        gross_notional: 20_000,
        initial_cash: 100_000,
        fee_bps: 5,
        slippage_bps: 5,
      })
      setRecord(next)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function stage(action: 'validate' | 'reveal-holdout') {
    if (!record) return
    setBusy(true)
    setError(null)
    try {
      const next = await revealPortfolio(record.portfolio_research_id, action)
      setRecord(next)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function makeStrategy() {
    if (!record) return
    setBusy(true)
    setError(null)
    try {
      await createPortfolioStrategy(record.portfolio_research_id)
      setRecord(await getPortfolioResearch(record.portfolio_research_id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function run() {
    if (!record?.strategy) return
    setBusy(true)
    setError(null)
    try {
      const created = await createBacktest({
        strategy_id: record.strategy.strategy_id,
        dataset_id: record.dataset_id,
        parameters: {
          fee_bps: record.fee_bps,
          slippage_bps: record.slippage_bps,
          initial_cash: record.initial_cash,
        },
      })
      if (!created.trace_id) throw new Error('Portfolio strategy run did not produce a trace.')
      onRunComplete(created.trace_id, created.run_id)
      onOpenReplay(created.trace_id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  return <main className="discover-shell research-workbench portfolio-lab">
    <section className="discover-title">
      <div>
        <span className="section-kicker">{tr('Multi-factor portfolio research')}</span>
        <h1>{tr('Portfolio Lab')}</h1>
        <p>{tr('Combine existing Factor evidence into a transparent long-only Native VQD Strategy.')}</p>
      </div>
      <span className="bias-tag">{tr('BACKEND CALCULATION')}</span>
    </section>

    {error && <section className="workspace-panel research-error" role="alert">{tr(error)}</section>}

    <section className="portfolio-workspace-grid">
      <aside className="workspace-panel research-ledger">
        <div className="section-heading"><h2>{tr('Portfolio research')}</h2><span>{ledger.length}</span></div>
        {ledger.length === 0 && <p className="empty-copy">{tr('No portfolio research yet.')}</p>}
        {ledger.map((item) => <button
          key={item.portfolio_research_id}
          className={record?.portfolio_research_id === item.portfolio_research_id ? 'selected' : ''}
          onClick={() => {
            setBusy(true)
            void getPortfolioResearch(item.portfolio_research_id)
              .then(setRecord)
              .catch((reason) => setError(String(reason)))
              .finally(() => setBusy(false))
          }}
        >
          <strong>{tr(item.name)}</strong>
          <span>{item.factor_count} Factors · {item.combination}</span>
          <small>{tr(item.revealed_stage)} · {pct(item.net_return)} · {item.turnover.toFixed(2)}×</small>
        </button>)}
      </aside>

      <div className="research-stack">
        <section className="workspace-panel research-builder">
          <div className="section-heading">
            <div><span className="section-kicker">FACTOR SELECTION → CONSTRUCTION</span><h2>{tr('Build a research portfolio')}</h2></div>
            <span className="long-only-badge">LONG ONLY</span>
          </div>

          <div className="factor-picker portfolio-factor-picker">
            {factors.map((item) => {
              const active = selected.includes(item.research_id)
              const direction = directions[item.research_id] ?? 'DEFAULT'
              return <div key={item.research_id} className={`portfolio-factor-card ${active ? 'selected' : ''}`}>
                <label className="factor-card-main">
                  <input type="checkbox" checked={active} onChange={() => toggle(item.research_id)} />
                  <span>
                    <strong>{tr(item.factor_id)}</strong>
                    <small>{tr(item.name)}</small>
                  </span>
                </label>
                <div className="factor-badges">
                  <span>{tr(item.factor_origin)}</span>
                  <span>{tr(item.factor_category)}</span>
                  <span>{tr(item.data_source)}</span>
                  <span>{tr(item.direction)}</span>
                </div>
                {active && <div className="factor-card-controls">
                  {combination === 'USER_DEFINED_WEIGHT' && <label>
                    <span>{tr('Factor weight')}</span>
                    <input aria-label={`${item.factor_id} weight`} type="number" min="0" max="1" step="0.05" value={weights[item.research_id] ?? 0} onChange={(event) => setWeights((current) => ({ ...current, [item.research_id]: Number(event.target.value) }))} />
                  </label>}
                  <label>
                    <span>{tr('Direction')}</span>
                    <select value={direction} onChange={(event) => setDirections((current) => ({ ...current, [item.research_id]: event.target.value as DirectionChoice }))}>
                      <option value="DEFAULT">{tr(`Factor default · ${item.direction}`)}</option>
                      <option value="HIGH">HIGH</option>
                      <option value="LOW">LOW</option>
                    </select>
                  </label>
                </div>}
              </div>
            })}
          </div>

          {redundancyWarnings.length > 0 && <div className="portfolio-redundancy-warning" role="status">
            <strong>{tr('High-redundancy warning')}</strong>
            <span>{tr('Portfolio Lab does not remove, reweight, or optimize these factors.')}</span>
            {redundancyWarnings.map(({ relationship, item }) => <small key={`${relationship.relationship_id}:${item.left_research_id}:${item.right_research_id}`}>{tr(relationship.name)} · {tr(item.reason)}</small>)}
          </div>}

          <div className="portfolio-config-section">
            <div className="config-section-title"><strong>{tr('Combination & ranking')}</strong><small>{tr('All composite scores are calculated by the backend.')}</small></div>
            <div className="research-form-grid">
              <label><span>{tr('Factor combination')}</span><select value={combination} onChange={(event) => setCombination(event.target.value as CombinationMethod)}><option>EQUAL_WEIGHT</option><option>USER_DEFINED_WEIGHT</option><option>RANK_AVERAGE</option><option>Z_SCORE_COMPOSITE</option></select></label>
              <label><span>{tr('Selection')}</span><select value={selection} onChange={(event) => setSelection(event.target.value as SelectionMethod)}><option value="TOP_N">TOP_N</option><option value="TOP_PERCENT">TOP_PERCENT</option></select></label>
              {selection === 'TOP_N'
                ? <label><span>{tr('Top N')}</span><input type="number" min="1" value={topN} onChange={(event) => setTopN(Number(event.target.value))} /></label>
                : <label><span>{tr('Top %')}</span><input type="number" min="0.1" max="100" step="1" value={topPercent} onChange={(event) => setTopPercent(Number(event.target.value))} /></label>}
              <label><span>{tr('Position weighting')}</span><select value={weighting} onChange={(event) => setWeighting(event.target.value as PositionWeighting)}><option>EQUAL_WEIGHT</option><option>SCORE_WEIGHTED</option></select></label>
              <label><span>{tr('Maximum single-position weight')}</span><input type="number" min="0.01" max="1" step="0.01" value={maxWeight} onChange={(event) => setMaxWeight(Number(event.target.value))} /></label>
              <label><span>{tr('Rebalance')}</span><select value={rebalance} onChange={(event) => setRebalance(event.target.value as RebalanceRule)}><option>DAILY</option><option>WEEKLY</option><option>MONTHLY</option></select></label>
            </div>
            {combination === 'USER_DEFINED_WEIGHT' && <div className={`weight-check ${userWeightsValid ? 'valid' : 'invalid'}`}><span>{tr('User-defined weight total')}</span><strong>{userWeightTotal.toFixed(4)}</strong><small>{userWeightsValid ? tr('VALID') : tr('Must equal 1.0')}</small></div>}
          </div>

          <div className="portfolio-config-section">
            <div className="config-section-title"><strong>{tr('Universe & filters')}</strong><small>{tr('Filters are applied before portfolio ranking.')}</small></div>
            <div className="universe-preview">
              <span>{tr('Base universe')}</span>
              <strong>{selectedFactors.length ? `${selectedFactors[0].symbols} ${tr('stocks')}` : '—'}</strong>
              {selectedDatasetIds.size === 1 && <code>{selectedFactors[0]?.dataset_id}</code>}
              {selectedDatasetIds.size > 1 && <b>{tr('Selected factors use different datasets')}</b>}
            </div>
            <div className="research-form-grid filter-grid">
              <label><span>{tr('Include symbols')}</span><input value={includeSymbols} placeholder="AAPL, MSFT (optional)" onChange={(event) => setIncludeSymbols(event.target.value)} /><small>{tr('Leave blank to use the full Factor universe.')}</small></label>
              <label><span>{tr('Exclude symbols')}</span><input value={excludeSymbols} placeholder="TSLA (optional)" onChange={(event) => setExcludeSymbols(event.target.value)} /></label>
              <label><span>{tr('Minimum liquidity')}</span><input type="number" placeholder="optional USD/day" value={minLiquidity} onChange={(event) => setMinLiquidity(event.target.value)} /></label>
              <label><span>{tr('Maximum volatility')}</span><input type="number" step="0.001" placeholder="optional" value={maxVolatility} onChange={(event) => setMaxVolatility(event.target.value)} /></label>
              <label className="availability-toggle"><span>{tr('Factor availability')}</span><span className="checkbox-row"><input type="checkbox" checked={requireAvailability} onChange={(event) => setRequireAvailability(event.target.checked)} />{tr('Require every selected Factor')}</span><small>{tr('If disabled, the backend renormalizes over factors available at that timestamp.')}</small></label>
            </div>
          </div>

          <div className="builder-footer">
            <p>{tr('No optimizer is used. User-defined weights, direction, coverage, missing values, filters and ranking are checked by the backend.')}</p>
            <button className="primary-button" disabled={busy || selected.length < 2 || !compatibleSelection || !userWeightsValid} onClick={() => void create()}>{tr(busy ? 'Calculating…' : 'Run Portfolio Research')}</button>
          </div>
        </section>

        {record && latest && <>
          <section className="workspace-panel evidence-summary">
            <div className="section-heading">
              <div><span className="section-kicker">TRANSACTION COST PREVIEW · {latest.stage}</span><h2>{tr(record.name)}</h2></div>
              <code>{record.portfolio_research_id}</code>
            </div>
            <div className="portfolio-definition-strip">
              <span>{record.combination}</span><span>{record.construction.selection === 'TOP_N' ? `Top ${record.construction.top_n}` : `Top ${record.construction.top_percent}%`}</span><span>{record.construction.weighting}</span><span>{record.rebalance}</span><span>Max {pct(record.construction.max_single_position_weight)}</span>
            </div>
            <div className="metric-strip portfolio-metrics">
              <div><span>{tr('Gross return')}</span><strong>{pct(latest.cost_preview.gross_return)}</strong></div>
              <div><span>{tr('Net return')}</span><strong>{pct(latest.cost_preview.net_return)}</strong></div>
              <div><span>{tr('Turnover')}</span><strong>{latest.cost_preview.turnover.toFixed(2)}×</strong></div>
              <div><span>{tr('Fees')}</span><strong>${latest.cost_preview.fees.toFixed(2)}</strong></div>
              <div><span>{tr('Slippage')}</span><strong>${latest.cost_preview.slippage.toFixed(2)}</strong></div>
              <div><span>{tr('Max drawdown')}</span><strong>{pct(latest.cost_preview.max_drawdown)}</strong></div>
              <div><span>{tr('Position count')}</span><strong>{latest.cost_preview.positions}</strong></div>
              <div><span>{tr('Rebalances')}</span><strong>{latest.cost_preview.rebalance_count}</strong></div>
            </div>
            <div className="stage-actions">
              {record.revealed_stage === 'RESEARCH' && <button className="secondary-button" disabled={busy} onClick={() => void stage('validate')}>{tr('Reveal Validation')}</button>}
              {record.revealed_stage === 'VALIDATION' && <button className="secondary-button" disabled={busy} onClick={() => void stage('reveal-holdout')}>{tr('Reveal Holdout')}</button>}
              {!record.strategy && record.revealed_stage !== 'RESEARCH' && <button className="primary-button" disabled={busy} onClick={() => void makeStrategy()}>{tr('Create Native Strategy')}</button>}
              {record.strategy && <><code>{record.strategy.strategy_id}</code><button className="primary-button" disabled={busy} onClick={() => void run()}>{tr('Backtest')}</button></>}
            </div>
          </section>

          {risk && <section className="workspace-panel portfolio-risk-decomposition">
            <div className="section-heading">
              <div><span className="section-kicker">WEIGHT ≠ RISK CONTRIBUTION</span><h2>{tr('Risk Decomposition')}</h2></div>
              <span>{tr(humanize(risk.verdict))}</span>
            </div>
            {risk.status === 'AVAILABLE'
              ? <>
                <div className="risk-metric-strip">
                  <div><span>{risk.volatility_basis === 'ANNUALIZED' ? tr('Annualized portfolio volatility') : tr('Per-observation portfolio volatility')}</span><strong>{pct(risk.portfolio_volatility)}</strong><small>{risk.observations} {tr('observations')}</small></div>
                  <div><span>{tr('Historical VaR 95%')}</span><strong>{pct(risk.historical_var_95)}</strong><small>{tr('One-observation loss')}</small></div>
                  <div><span>{tr('Expected Shortfall 95%')}</span><strong>{pct(risk.expected_shortfall_95)}</strong><small>{tr('Mean loss beyond VaR')}</small></div>
                  <div><span>{tr('Risk basis')}</span><strong>{risk.volatility_basis}</strong><small>{risk.dataset_frequency}{risk.annualization_factor ? ` · √${risk.annualization_factor}` : ''}</small></div>
                </div>
                <div className="risk-contribution-table">
                  <div className="header"><span>{tr('Asset')}</span><span>{tr('Invested weight')}</span><span>{tr('Risk share')}</span><span>{tr('Weight → risk gap')}</span><span>{tr('Component volatility')}</span></div>
                  {risk.contributions.map((item) => <div key={item.symbol} className={item.low_weight_high_risk ? 'risk-flagged' : ''}>
                    <span><strong>{item.symbol}</strong>{item.low_weight_high_risk && <small>{tr('Low weight · high risk')}</small>}</span>
                    <code>{pct(item.invested_weight)}</code>
                    <code>{pct(item.component_risk_share)}</code>
                    <code>{signedPp(item.risk_weight_gap)}</code>
                    <code>{pct(item.component_contribution_to_volatility)}</code>
                  </div>)}
                </div>
                {(risk.covariance || risk.correlation) && <div className="risk-matrix-grid">
                  {risk.correlation && <RiskMatrixTable matrix={risk.correlation} kind="correlation" />}
                  {risk.covariance && <RiskMatrixTable matrix={risk.covariance} kind="covariance" />}
                </div>}
              </>
              : <p className="risk-unavailable">{tr('Risk decomposition needs at least 20 aligned return observations for the latest portfolio snapshot.')}</p>}
            <details className="evidence-calculation-details">
              <summary>{tr('Calculation details')}</summary>
              {risk.calculation_details.map((detail) => <p key={detail}>{detail}</p>)}
            </details>
            <p className="relationship-disclosure">{risk.boundary_disclosure}</p>
          </section>}

          <section className="workspace-panel">
            <div className="section-heading"><div><span className="section-kicker">FACTOR CHECKS</span><h2>{tr('Direction, weight, coverage & missing data')}</h2></div><span>{tr(latest.stage)}</span></div>
            <div className="factor-check-table">
              <div className="header"><span>{tr('Factor')}</span><span>{tr('Type')}</span><span>{tr('Direction')}</span><span>{tr('Weight')}</span><span>{tr('Coverage')}</span><span>{tr('Missing')}</span></div>
              {latest.factor_checks.map((check) => <div key={check.research_id}>
                <span><strong>{tr(check.factor_name)}</strong><small>{tr(check.origin)}</small></span>
                <span>{tr(check.category)} · {tr(check.data_source)}</span>
                <code>{check.direction}</code>
                <code>{pct(check.effective_weight)}</code>
                <code>{pct(check.coverage)}</code>
                <code>{check.missing_observations}/{check.expected_observations}</code>
              </div>)}
            </div>
          </section>

          {latestSnapshot && <section className="workspace-panel">
            <div className="section-heading"><div><span className="section-kicker">PORTFOLIO LINEAGE</span><h2>{tr('Position → Factor evidence')}</h2></div><span>{new Date(latestSnapshot.timestamp).toLocaleDateString()}</span></div>
            <div className="portfolio-lineage-list">
              {latestSnapshot.positions.filter((item) => item.selected).map((item) => <article key={item.symbol}>
                <div className="lineage-position-head"><strong>{item.symbol}</strong><span>{tr('Portfolio rank')} #{item.portfolio_rank}</span><code>{tr('Composite')} {num(item.composite_score)}</code><b>{pct(item.target_weight)}</b></div>
                <div className="lineage-filter-row">{item.filter_status.map((status) => <span key={status}>{tr(status)}</span>)}</div>
                <div className="lineage-factor-grid">
                  {item.factors.map((factor) => <div key={factor.research_id} className={!factor.available ? 'missing' : ''}>
                    <span><strong>{tr(factor.factor_name)}</strong><small>{factor.direction}</small></span>
                    {factor.available
                      ? <><code>#{factor.rank}/{factor.universe_count}</code><code>{tr('Score')} {num(factor.normalized_score)}</code><code>{tr('Contribution')} {num(factor.contribution)}</code></>
                      : <strong>{tr('MISSING')}</strong>}
                  </div>)}
                </div>
              </article>)}
            </div>
          </section>}
        </>}
      </div>
    </section>
  </main>
}
