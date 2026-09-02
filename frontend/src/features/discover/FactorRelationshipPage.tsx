import { useEffect, useMemo, useState } from 'react'

import { getFactorResearchList } from '../../api/factors'
import { createRelationship, getRelationshipList } from '../../api/research'
import { useI18n } from '../../i18n/I18nProvider'
import type { FactorResearchSummary, ResearchStage } from '../../types/factor'
import type {
  CorrelationCell,
  CorrelationSemantic,
  FactorRelationshipRecord,
  RollingCorrelationPoint,
} from '../../types/research'

const num = (value: number | null | undefined) => value == null ? '—' : value.toFixed(4)
const pct = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(2)}%`
const day = (value: string) => value.slice(0, 10)
const humanize = (value: string) => value.replaceAll('_', ' ')

function factorName(record: FactorRelationshipRecord, researchId: string) {
  const index = record.factor_research_ids.indexOf(researchId)
  return record.factor_names[index] ?? record.factor_ids[index] ?? researchId
}

function CorrelationMatrix({
  record,
  cells,
  title,
}: {
  record: FactorRelationshipRecord
  cells: CorrelationCell[]
  title: string
}) {
  return <section className="relationship-matrix-card">
    <header><strong>{title}</strong><span>Pearson r / Spearman ρ</span></header>
    <div className="relationship-matrix-scroll">
      <table>
        <thead><tr><th>{title}</th>{record.factor_research_ids.map((id) => <th key={id}>{factorName(record, id)}</th>)}</tr></thead>
        <tbody>{record.factor_research_ids.map((leftId) => <tr key={leftId}>
          <th>{factorName(record, leftId)}</th>
          {record.factor_research_ids.map((rightId) => {
            const cell = cells.find((item) => item.left_research_id === leftId && item.right_research_id === rightId)
            return <td key={rightId} title={`${cell?.observations ?? 0} aligned observations`}><code>{num(cell?.pearson)}</code><small>{num(cell?.spearman)}</small></td>
          })}
        </tr>)}</tbody>
      </table>
    </div>
  </section>
}

function correlationPath(points: RollingCorrelationPoint[], metric: 'pearson' | 'spearman') {
  const denominator = Math.max(points.length - 1, 1)
  return points.reduce((path, point, index) => {
    const value = point[metric]
    if (value == null) return path
    const command = path ? 'L' : 'M'
    return `${path}${command}${(index / denominator) * 100},${50 - value * 44} `
  }, '')
}

const semanticLabel: Record<CorrelationSemantic, string> = {
  FACTOR_VALUES: 'Factor Values',
  FACTOR_RANKS: 'Factor Ranks',
  FACTOR_RETURNS: 'Factor Returns',
}

export default function FactorRelationshipPage() {
  const { tr } = useI18n()
  const [factors, setFactors] = useState<FactorResearchSummary[]>([])
  const [records, setRecords] = useState<FactorRelationshipRecord[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [stage, setStage] = useState<ResearchStage>('RESEARCH')
  const [horizon, setHorizon] = useState<1 | 5 | 20>(20)
  const [rollingWindow, setRollingWindow] = useState(60)
  const [topPercent, setTopPercent] = useState(20)
  const [correlationThreshold, setCorrelationThreshold] = useState(0.75)
  const [overlapThreshold, setOverlapThreshold] = useState(0.60)
  const [record, setRecord] = useState<FactorRelationshipRecord | null>(null)
  const [rollingKey, setRollingKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const initialRelationshipId = new URLSearchParams(window.location.search).get('relationship_id')

  useEffect(() => {
    let mounted = true
    void Promise.all([getFactorResearchList(), getRelationshipList()]).then(
      ([factorRows, relationshipRows]) => {
        if (!mounted) return
        setFactors(factorRows)
        setRecords(relationshipRows)
        setRecord(relationshipRows.find((item) => item.relationship_id === initialRelationshipId) ?? relationshipRows[0] ?? null)
      },
      (reason) => mounted && setError(reason instanceof Error ? reason.message : String(reason)),
    )
    return () => { mounted = false }
  }, [initialRelationshipId])

  const selectedFactors = useMemo(
    () => factors.filter((item) => selected.includes(item.research_id)),
    [factors, selected],
  )
  const compatible = new Set(selectedFactors.map((item) => item.dataset_id)).size <= 1
  const selectedRolling = record?.rolling_correlations.find((item) => (
    `${item.left_research_id}|${item.right_research_id}|${item.semantic}` === rollingKey
  )) ?? record?.rolling_correlations[0] ?? null

  function toggle(researchId: string) {
    setSelected((current) => current.includes(researchId)
      ? current.filter((item) => item !== researchId)
      : [...current, researchId])
  }

  async function run() {
    if (selected.length < 2 || !compatible) return
    setBusy(true)
    setError(null)
    try {
      const next = await createRelationship({
        name: `Factor Relationship · ${selectedFactors.map((item) => item.factor_id).join(' × ')}`,
        factor_research_ids: selected,
        stage,
        horizon,
        rolling_window: rollingWindow,
        top_percent: topPercent,
        redundancy_threshold: correlationThreshold,
        overlap_threshold: overlapThreshold,
      })
      setRecord(next)
      setRollingKey('')
      setRecords((current) => [next, ...current.filter((item) => item.relationship_id !== next.relationship_id)])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  return <main className="discover-shell research-workbench factor-relationship-lab">
    <section className="discover-title">
      <div><span className="section-kicker">{tr('Factor relationship research')}</span><h1>{tr('Factor Relationships')}</h1><p>{tr('Compare what existing factors express, where they overlap, and what association remains when they are combined.')}</p></div>
      <span className="bias-tag">BACKEND CALCULATION · NO OPTIMIZER</span>
    </section>

    {error && <section className="workspace-panel research-error" role="alert">{tr(error)}</section>}

    <section className="relationship-workspace-grid">
      <aside className="workspace-panel relationship-ledger">
        <div className="section-heading"><h2>{tr('Relationship research')}</h2><span>{records.length}</span></div>
        {records.length === 0 && <p className="empty-copy">{tr('No relationship research yet.')}</p>}
        {records.map((item) => <button key={item.relationship_id} className={record?.relationship_id === item.relationship_id ? 'selected' : ''} onClick={() => { setRecord(item); setRollingKey('') }}>
          <strong>{tr(item.name)}</strong><span>{item.factor_ids.length} Factors · {item.stage}</span><small>{item.horizon}D · {item.rolling_window}D rolling</small>
        </button>)}
      </aside>

      <div className="relationship-stack">
        <section className="workspace-panel relationship-builder">
          <div className="section-heading"><div><span className="section-kicker">EXISTING FACTORS → ASSOCIATION EVIDENCE</span><h2>{tr('Configure relationship study')}</h2></div><span className="long-only-badge">DETERMINISTIC</span></div>
          <div className="relationship-factor-picker">
            {factors.map((item) => <label key={item.research_id} className={selected.includes(item.research_id) ? 'selected' : ''}>
              <input type="checkbox" checked={selected.includes(item.research_id)} onChange={() => toggle(item.research_id)} />
              <span><strong>{tr(item.factor_id)}</strong><small>{tr(item.name)}</small></span>
              <code>{item.revealed_stage}</code>
            </label>)}
          </div>
          <div className="relationship-form-grid">
            <label><span>{tr('Evidence stage')}</span><select value={stage} onChange={(event) => setStage(event.target.value as ResearchStage)}><option value="RESEARCH">RESEARCH</option><option value="VALIDATION">VALIDATION</option><option value="HOLDOUT">HOLDOUT</option></select></label>
            <label><span>{tr('Forward horizon')}</span><select value={horizon} onChange={(event) => setHorizon(Number(event.target.value) as 1 | 5 | 20)}><option value={1}>1D</option><option value={5}>5D</option><option value={20}>20D</option></select></label>
            <label><span>{tr('Rolling window')}</span><input type="number" min="2" max="504" value={rollingWindow} onChange={(event) => setRollingWindow(Number(event.target.value))} /></label>
            <label><span>{tr('Top quantile')}</span><input type="number" min="1" max="50" value={topPercent} onChange={(event) => setTopPercent(Number(event.target.value))} /></label>
            <label><span>{tr('Rank-correlation threshold')}</span><input type="number" min="0" max="1" step="0.05" value={correlationThreshold} onChange={(event) => setCorrelationThreshold(Number(event.target.value))} /></label>
            <label><span>{tr('Overlap threshold')}</span><input type="number" min="0" max="1" step="0.05" value={overlapThreshold} onChange={(event) => setOverlapThreshold(Number(event.target.value))} /></label>
          </div>
          <div className="builder-footer"><p>{tr('Correlation, ranks, factor returns, overlap, deltas and clusters are returned by the backend. The frontend only displays them.')}</p><button className="primary-button" disabled={busy || selected.length < 2 || !compatible} onClick={() => void run()}>{tr(busy ? 'Calculating…' : 'Run Relationship Research')}</button></div>
          {!compatible && <p className="relationship-selection-error">{tr('Selected Factor studies must use the same market dataset.')}</p>}
        </section>

        {record && <>
          <section id="relationship-overview" className="workspace-panel relationship-summary">
            <div className="section-heading"><div><span className="section-kicker">{record.stage} · {day(record.period.start)} → {day(record.period.end)}</span><h2>{tr(record.name)}</h2></div><code>{record.relationship_id}</code></div>
            <div className="relationship-identity"><span>{record.factor_ids.join(' · ')}</span><code>{record.dataset_fingerprint}</code><b>{record.universe.length} securities</b></div>
            <p>{record.correlation_methodology}</p>
          </section>

          <nav className="evidence-jump-nav relationship-evidence-nav" aria-label={tr('Evidence map')}>
            <a href="#relationship-overview">{tr('Summary')}</a>
            <a href="#relationship-correlation">{tr('Correlation Matrix')}</a>
            {record.pca && <a href="#relationship-pca">{tr('PCA Factor Structure')}</a>}
            <a href="#relationship-rolling">{tr('Rolling Correlation')}</a>
            <a href="#relationship-overlap">{tr('Redundancy')} / {tr('Overlap')}</a>
            <a href="#relationship-incremental">{tr('Incremental Information')}</a>
            <a href="#relationship-clusters">{tr('Factor Cluster')}</a>
          </nav>

          <section id="relationship-correlation" className="workspace-panel relationship-section">
            <div className="section-heading"><div><span className="section-kicker">THREE DISTINCT SEMANTICS</span><h2>{tr('Correlation Matrix')}</h2></div><span>Pearson + Spearman</span></div>
            <div className="relationship-matrices">
              <CorrelationMatrix record={record} cells={record.value_correlations} title={tr('Factor Values')} />
              <CorrelationMatrix record={record} cells={record.rank_correlations} title={tr('Rank Correlation')} />
              <CorrelationMatrix record={record} cells={record.return_correlations} title={tr('Factor Return Correlation')} />
            </div>
          </section>

          {record.pca && <section id="relationship-pca" className="workspace-panel relationship-section pca-factor-structure">
            <div className="section-heading">
              <div><span className="section-kicker">LATENT FACTOR STRUCTURE · NO AUTO ACTION</span><h2>{tr('PCA Factor Structure')}</h2></div>
              <span>{tr(humanize(record.pca.verdict))}</span>
            </div>
            {record.pca.status === 'AVAILABLE'
              ? <>
                <div className="pca-summary-strip">
                  <div><span>{tr('Aligned observations')}</span><strong>{record.pca.observations}</strong></div>
                  {record.pca.components.map((component) => <div key={component.component}>
                    <span>{component.component} · {tr('Explained variance')}</span>
                    <strong>{pct(component.explained_variance)}</strong>
                    <small>{tr('Cumulative')} {pct(component.cumulative_explained_variance)}</small>
                  </div>)}
                </div>
                <div className="pca-component-grid">
                  {record.pca.components.map((component) => <article key={component.component}>
                    <header><strong>{component.component}</strong><span>λ {num(component.eigenvalue)}</span></header>
                    <div className="pca-loading-list">
                      {component.loadings.map((loading) => <div key={loading.factor_research_id}>
                        <span>{tr(loading.factor_name)}</span>
                        <div className="pca-loading-track"><i className={loading.loading < 0 ? 'negative' : ''} style={{ width: `${Math.min(100, Math.abs(loading.loading) * 100)}%` }} /></div>
                        <code>{loading.loading >= 0 ? '+' : ''}{loading.loading.toFixed(3)}</code>
                      </div>)}
                    </div>
                  </article>)}
                </div>
                <div className="pca-evidence-block">
                  <strong>{tr('Latent factor evidence')}</strong>
                  {record.pca.latent_factor_evidence.length
                    ? record.pca.latent_factor_evidence.map((evidence) => <article key={`${evidence.component}:${evidence.factor_research_ids.join(':')}`}>
                      <span>{evidence.component}</span>
                      <b>{evidence.factor_research_ids.map((id) => factorName(record, id)).join(' · ')}</b>
                      <code>|loading| ≥ {evidence.minimum_absolute_loading.toFixed(2)} · max |r| {evidence.maximum_absolute_pairwise_return_correlation.toFixed(2)}</code>
                      <p>{evidence.reason}</p>
                    </article>)
                    : <p>{tr('No latent redundancy signal crossed the deterministic rule.')}</p>}
                </div>
              </>
              : <p className="relationship-empty-evidence">{tr('PCA is unavailable because the selected factors do not have enough aligned, varying history.')}</p>}
            <details className="evidence-calculation-details">
              <summary>{tr('Calculation details')}</summary>
              {record.pca.calculation_details.map((detail) => <p key={detail}>{detail}</p>)}
            </details>
            <p className="relationship-disclosure">{record.pca.boundary_disclosure}</p>
          </section>}

          <section id="relationship-rolling" className="workspace-panel relationship-section">
            <div className="section-heading"><div><span className="section-kicker">TRAILING BACKEND SERIES</span><h2>{tr('Rolling Correlation')}</h2></div>{selectedRolling && <span>{selectedRolling.window} timestamps</span>}</div>
            <label className="rolling-selector"><span>{tr('Pair and semantic')}</span><select value={selectedRolling ? `${selectedRolling.left_research_id}|${selectedRolling.right_research_id}|${selectedRolling.semantic}` : ''} onChange={(event) => setRollingKey(event.target.value)}>{record.rolling_correlations.map((item) => {
              const key = `${item.left_research_id}|${item.right_research_id}|${item.semantic}`
              return <option key={key} value={key}>{factorName(record, item.left_research_id)} × {factorName(record, item.right_research_id)} · {semanticLabel[item.semantic]}</option>
            })}</select></label>
            {selectedRolling && <>
              <svg className="rolling-correlation-chart" viewBox="0 0 100 100" preserveAspectRatio="none" aria-label={tr('Rolling Correlation')}>
                <line x1="0" x2="100" y1="50" y2="50" /><path className="pearson-line" d={correlationPath(selectedRolling.points, 'pearson')} /><path className="spearman-line" d={correlationPath(selectedRolling.points, 'spearman')} />
              </svg>
              <div className="rolling-legend"><span><i />Pearson</span><span><i className="spearman" />Spearman</span><small>+1</small><small>0</small><small>−1</small></div>
              <div className="relationship-data-table rolling-points"><div className="header"><span>{tr('Date')}</span><span>Pearson</span><span>Spearman</span><span>{tr('Observations')}</span></div>{selectedRolling.points.slice(-10).map((point) => <div key={point.timestamp}><code>{day(point.timestamp)}</code><code>{num(point.pearson)}</code><code>{num(point.spearman)}</code><code>{point.observations}</code></div>)}</div>
            </>}
          </section>

          <section id="relationship-overlap" className="relationship-two-column">
            <section className="workspace-panel relationship-section">
              <div className="section-heading"><div><span className="section-kicker">ABS RANK CORR + OVERLAP RULE</span><h2>{tr('Redundancy')}</h2></div><span>NO AUTO ACTION</span></div>
              <div className="redundancy-list">{record.redundancy.map((item) => <article key={`${item.left_research_id}:${item.right_research_id}`} className={item.status.toLowerCase()}><header><strong>{factorName(record, item.left_research_id)} × {factorName(record, item.right_research_id)}</strong><span>{item.status.replaceAll('_', ' ')}</span></header><div><code>r {num(item.rank_correlation)}</code><code>{tr('Overlap')} {pct(item.top_quantile_overlap)}</code></div><p>{item.reason}</p></article>)}</div>
            </section>

            <section className="workspace-panel relationship-section">
              <div className="section-heading"><div><span className="section-kicker">INTERNAL RESEARCH PORTFOLIOS</span><h2>{tr('Top Quantile Overlap & Jaccard')}</h2></div><span>TOP {record.top_percent}%</span></div>
              <div className="relationship-data-table overlap-table"><div className="header"><span>{tr('Pair')}</span><span>{tr('Intersection')}</span><span>{tr('Union')}</span><span>{tr('Overlap')}</span><span>Jaccard</span></div>{record.exposure_overlap.map((item) => {
                const point = item.points.at(-1)
                return <div key={`${item.left_research_id}:${item.right_research_id}`}><span>{factorName(record, item.left_research_id)} × {factorName(record, item.right_research_id)}<small>{point ? day(point.timestamp) : `${item.timestamps} dates`}</small></span><code>{point?.intersection_count ?? '—'}</code><code>{point?.union_count ?? '—'}</code><code>{pct(point?.overlap_percent ?? item.mean_overlap)}</code><code>{num(point?.jaccard ?? item.mean_jaccard)}</code></div>
              })}</div>
              <p className="relationship-disclosure">{record.crowding_disclosure}</p>
            </section>
          </section>

          <section id="relationship-incremental" className="workspace-panel relationship-section">
            <div className="section-heading"><div><span className="section-kicker">DIRECTION-ADJUSTED PERCENTILE RANK AVERAGE</span><h2>{tr('Incremental Information')}</h2></div><span>ASSOCIATION · NOT CAUSAL</span></div>
            <div className="relationship-data-table incremental-table"><div className="header"><span>{tr('Base + Added')}</span><span>Rank IC A / A+B / Δ</span><span>Q5−Q1 A / A+B / Δ</span><span>{tr('Coverage')} A / A+B / Δ</span><span>{tr('Turnover')} A / A+B / Δ</span><span>{tr('Portfolio effect')} A / A+B / Δ</span></div>{record.incremental_information.map((item) => <div key={`${item.base_research_id}:${item.added_research_id}`}><span><strong>{factorName(record, item.base_research_id)}</strong><small>+ {factorName(record, item.added_research_id)}</small></span><code>{num(item.base_rank_ic)} / {num(item.composite_rank_ic)} / {num(item.rank_ic_delta)}</code><code>{pct(item.base_spread)} / {pct(item.composite_spread)} / {pct(item.spread_delta)}</code><code>{pct(item.base_coverage)} / {pct(item.composite_coverage)} / {pct(item.coverage_delta)}</code><code>{pct(item.base_turnover)} / {pct(item.composite_turnover)} / {pct(item.turnover_delta)}</code><code>{pct(item.base_portfolio_return)} / {pct(item.composite_portfolio_return)} / {pct(item.portfolio_effect)}</code></div>)}</div>
            <p className="relationship-disclosure">{record.incremental_disclosure}</p>
          </section>

          <section id="relationship-clusters" className="workspace-panel relationship-section">
            <div className="section-heading"><div><span className="section-kicker">THRESHOLD GRAPH · CONNECTED COMPONENTS</span><h2>{tr('Factor Cluster')}</h2></div><span>{record.clusters.length}</span></div>
            <div className="factor-clusters">{record.clusters.map((cluster) => <article key={cluster.cluster_id}><span>{cluster.cluster_id}</span><strong>{cluster.factor_research_ids.map((id) => factorName(record, id)).join(' · ')}</strong><small>{cluster.rule}</small></article>)}</div>
          </section>
        </>}
      </div>
    </section>
  </main>
}
