import { useEffect, useMemo, useState, type KeyboardEvent, type MouseEvent } from 'react'

import { getHistoricalMarket } from '../../api/factors'
import {
  downloadSecFundamentals,
  getFundamentalDatasets,
  getFundamentalProviders,
  getUniverses,
} from '../../api/fundamentals'
import { saveHistoricalDataset } from '../../api/marketData'
import { useI18n } from '../../i18n/I18nProvider'
import type { DatasetDefinition } from '../../types/dataset'
import type { HistoricalMarketView } from '../../types/factor'
import type {
  FundamentalDatasetSummary,
  FundamentalFieldSnapshot,
  FundamentalProviderInfo,
  HistoricalUniverse,
} from '../../types/fundamental'
import { nearestChartIndex, pointerToViewBoxX } from './chartInteraction'

interface Props {
  datasets: DatasetDefinition[]
  onImported: (dataset: DatasetDefinition) => void
}

type MarketTab = 'UNIVERSE' | 'PRICE' | 'FUNDAMENTALS'

const dateOnly = (value: string) => value.slice(0, 10)
const percent = (value: number | null) => value == null ? '—' : `${(value * 100).toFixed(2)}%`
const compact = (value: number | null) => value == null ? '—' : Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value)

function TrendChart({ view }: { view: HistoricalMarketView }) {
  const { tr } = useI18n()
  const points = view.trend
  const [activeIndex, setActiveIndex] = useState(Math.max(points.length - 1, 0))
  if (points.length < 2) return <div className="research-empty">—</div>
  const width = 920; const height = 260; const top = 18; const priceBottom = 176; const volumeTop = 198; const bottom = 244
  const closes = points.map((item) => item.close); const volumes = points.map((item) => item.volume ?? 0)
  const min = Math.min(...closes); const max = Math.max(...closes); const maxVolume = Math.max(...volumes, 1)
  const x = (index: number) => 12 + index / Math.max(points.length - 1, 1) * (width - 24)
  const y = (value: number) => top + (max - value) / Math.max(max - min, 1e-9) * (priceBottom - top)
  const path = points.map((item, index) => `${index ? 'L' : 'M'} ${x(index)} ${y(item.close)}`).join(' ')
  const active = points[activeIndex]; const activeX = x(activeIndex); const activeY = y(active.close)
  const tooltipWidth = 154; const tooltipX = activeX > width / 2 ? activeX - tooltipWidth - 10 : activeX + 10
  const volumeHeight = (active.volume ?? 0) / maxVolume * (bottom - volumeTop)

  function handleHover(event: MouseEvent<SVGSVGElement>) {
    const pointerX = pointerToViewBoxX(event.clientX, event.currentTarget.getBoundingClientRect(), width, height)
    setActiveIndex(nearestChartIndex(pointerX, points.length, 12, width - 12))
  }

  function handleKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    if (event.key === 'Home') setActiveIndex(0)
    else if (event.key === 'End') setActiveIndex(points.length - 1)
    else setActiveIndex((current) => Math.min(points.length - 1, Math.max(0, current + (event.key === 'ArrowRight' ? 1 : -1))))
  }

  return <svg className="market-trend interactive-research-chart" viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0} aria-label={`${view.selected_symbol} · ${tr('Price and volume history')}`} onMouseMove={handleHover} onKeyDown={handleKeyDown}>
    <path className="trend-grid" d={`M12 ${priceBottom}H${width - 12} M12 ${top}H${width - 12}`} />
    {points.map((item, index) => <rect key={item.timestamp} x={x(index) - 1.5} y={bottom - (item.volume ?? 0) / maxVolume * (bottom - volumeTop)} width="3" height={(item.volume ?? 0) / maxVolume * (bottom - volumeTop)} className="trend-volume" />)}
    <path d={path} className="trend-price" />
    <line className="research-hover-guide" x1={activeX} x2={activeX} y1={top} y2={bottom} />
    <rect className="trend-volume active" x={activeX - 3} y={bottom - volumeHeight} width="6" height={volumeHeight} />
    <circle cx={activeX} cy={activeY} r="5" className="research-hover-point price" />
    <g className="research-chart-tooltip" transform={`translate(${tooltipX} 26)`}>
      <rect width={tooltipWidth} height="70" rx="5" />
      <text className="tooltip-date" x="10" y="17">{dateOnly(active.timestamp)}</text>
      <text x="10" y="39">{tr('Close price')}</text><text className="tooltip-value" x={tooltipWidth - 10} y="39" textAnchor="end">${active.close.toFixed(2)}</text>
      <text x="10" y="58">{tr('volume')}</text><text className="tooltip-value" x={tooltipWidth - 10} y="58" textAnchor="end">{compact(active.volume)}</text>
    </g>
  </svg>
}

function fieldValue(item: FundamentalFieldSnapshot) {
  if (item.value == null) return '—'
  return item.unit === 'USD' ? `$${compact(item.value)}` : compact(item.value)
}

export default function HistoricalMarketPage({ datasets, onImported }: Props) {
  const { tr } = useI18n()
  const providerDatasets = useMemo(() => datasets.filter((item) => item.source_type === 'PROVIDER' && item.frequency === '1Day'), [datasets])
  const [datasetId, setDatasetId] = useState(providerDatasets[0]?.dataset_id ?? '')
  const dataset = providerDatasets.find((item) => item.dataset_id === datasetId) ?? providerDatasets[0]
  const [asOf, setAsOf] = useState(dataset ? dateOnly(dataset.end_time) : '2024-12-31')
  const [symbol, setSymbol] = useState(dataset?.symbols[0] ?? '')
  const [tab, setTab] = useState<MarketTab>('PRICE')
  const [view, setView] = useState<HistoricalMarketView | null>(null)
  const [fundamentalDatasets, setFundamentalDatasets] = useState<FundamentalDatasetSummary[]>([])
  const [fundamentalId, setFundamentalId] = useState('')
  const [providers, setProviders] = useState<FundamentalProviderInfo[]>([])
  const [universes, setUniverses] = useState<HistoricalUniverse[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showBuilder, setShowBuilder] = useState(providerDatasets.length === 0)
  const [symbols, setSymbols] = useState('AAPL, MSFT, AMZN, NVDA, META, GOOGL, TSLA, JPM, XOM, JNJ')
  const [start, setStart] = useState('2022-01-01')
  const [end, setEnd] = useState('2024-12-31')

  useEffect(() => {
    let active = true
    void Promise.all([getFundamentalDatasets(), getFundamentalProviders(), getUniverses()])
      .then(([nextDatasets, nextProviders, nextUniverses]) => {
        if (!active) return
        setFundamentalDatasets(Array.isArray(nextDatasets) ? nextDatasets : [])
        setProviders(Array.isArray(nextProviders) ? nextProviders : [])
        setUniverses(Array.isArray(nextUniverses) ? nextUniverses : [])
      })
      .catch(() => undefined)
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!dataset || !asOf) return
    let active = true
    void getHistoricalMarket(dataset.dataset_id, `${asOf}T23:59:59Z`, symbol || undefined, fundamentalId || undefined)
      .then((next) => { if (active) { setView(next); setSymbol(next.selected_symbol) } })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : 'Historical market failed.') })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [dataset, asOf, symbol, fundamentalId])

  async function downloadUniverse() {
    const universe = symbols.split(/[\s,]+/).map((item) => item.trim().toUpperCase()).filter(Boolean)
    if (universe.length < 5) { setError('Choose at least five stocks for cross-sectional research.'); return }
    setBusy(true); setError(null)
    try {
      const saved = await saveHistoricalDataset({ name: `${universe.length} US stocks · ${start}—${end}`, symbols: universe, start: `${start}T00:00:00Z`, end: `${end}T23:59:59Z`, timeframe: '1Day', feed: 'iex' })
      onImported(saved); setDatasetId(saved.dataset_id); setAsOf(dateOnly(saved.end_time)); setSymbol(saved.symbols[0] ?? '')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Historical download failed.') }
    finally { setBusy(false) }
  }

  async function downloadFundamentals() {
    if (!dataset) return
    setBusy(true); setError(null)
    try {
      const saved = await downloadSecFundamentals({ name: `${dataset.name} · SEC filings`, symbols: dataset.symbols, start: dataset.start_time, end: dataset.end_time })
      setFundamentalDatasets((current) => [saved, ...current.filter((item) => item.fundamental_dataset_id !== saved.fundamental_dataset_id)])
      setFundamentalId(saved.fundamental_dataset_id); setTab('FUNDAMENTALS')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Fundamental download failed.') }
    finally { setBusy(false) }
  }

  const universe = universes.find((item) => item.universe_id === view?.universe_id)
  const provider = providers[0]
  const matchingFundamentals = fundamentalDatasets.filter((item) => dataset?.symbols.every((stock) => item.symbols.includes(stock)))

  return <main className="discover-shell">
    <header className="workspace-title discover-title"><div><span className="section-kicker">{tr('MARKET MEMORY')}</span><h1>{tr('Historical Market')}</h1><p>{tr('Reconstruct price, membership, and filings exactly as they were knowable on one date.')}</p></div><div className="title-actions">{dataset && <code>{dataset.content_fingerprint.slice(0, 22)}…</code>}<button className="secondary-button" onClick={() => setShowBuilder((value) => !value)}>{tr(showBuilder ? 'Close universe builder' : 'Create real universe')}</button></div></header>
    {error && <div className="compact-error" role="alert"><strong>{tr('Historical market unavailable')}</strong><span>{tr(error)}</span></div>}
    <section className="research-command-bar phase18-command">
      <label><span>{tr('Saved real universe')}</span><select value={dataset?.dataset_id ?? ''} onChange={(event) => { const next = providerDatasets.find((item) => item.dataset_id === event.target.value); setDatasetId(event.target.value); setFundamentalId(''); if (next) { setAsOf(dateOnly(next.end_time)); setSymbol(next.symbols[0] ?? '') } }}><option value="">{tr('Select a provider dataset')}</option>{providerDatasets.map((item) => <option key={item.dataset_id} value={item.dataset_id}>{item.name} · {item.symbols.length}</option>)}</select></label>
      <label><span>{tr('Market date')}</span><input type="date" min={dataset ? dateOnly(dataset.start_time) : undefined} max={dataset ? dateOnly(dataset.end_time) : undefined} value={asOf} onChange={(event) => setAsOf(event.target.value)} /></label>
      <label><span>{tr('Inspect stock')}</span><select value={symbol} onChange={(event) => setSymbol(event.target.value)}>{dataset?.symbols.map((item) => <option key={item}>{item}</option>)}</select></label>
      <label><span>{tr('Fundamental record')}</span><select value={fundamentalId} onChange={(event) => setFundamentalId(event.target.value)}><option value="">{tr('Price only')}</option>{matchingFundamentals.map((item) => <option key={item.fundamental_dataset_id} value={item.fundamental_dataset_id}>{item.name}</option>)}</select></label>
      <span className="command-status">{busy ? tr('Loading point-in-time record…') : view ? `${view.cross_section.length} ${tr('stocks at')} ${dateOnly(view.as_of)}` : tr('No market selected')}</span>
    </section>
    {showBuilder && <section className="workspace-panel universe-builder always"><div className="section-heading"><div><span className="section-kicker">{tr('CREATE A REAL UNIVERSE')}</span><h2>{tr('Download a research-ready stock pool')}</h2></div><span className="bias-tag">ALPACA · IEX</span></div><p>{tr('Choose at least five stocks. Membership is stored as a static universe and disclosed as survivorship-biased.')}</p><div className="universe-form"><label className="wide"><span>{tr('Symbols')}</span><input value={symbols} onChange={(event) => setSymbols(event.target.value)} /></label><label><span>{tr('Start date')}</span><input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label><label><span>{tr('End date')}</span><input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></label><button className="primary-button" disabled={busy || start >= end} onClick={() => void downloadUniverse()}>{tr(busy ? 'Downloading…' : 'Download universe')}</button></div></section>}
    {view && <>
      <nav className="market-view-tabs" aria-label={tr('Historical data views')}>{(['UNIVERSE', 'PRICE', 'FUNDAMENTALS'] as const).map((item) => <button key={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{tr(item)}</button>)}</nav>
      {!view.survivorship_bias_free && <section className="bias-notice compact historical-bias"><strong>{tr(view.universe_mode)}</strong><span>{tr('NOT SURVIVORSHIP-BIAS FREE')}</span><p>{tr(view.universe_disclosure ?? 'Current constituents are held fixed through history; this is not survivorship-bias free.')}</p></section>}
      {tab === 'UNIVERSE' && <section className="workspace-panel historical-section universe-evidence"><div className="section-heading"><div><span className="section-kicker">{tr('HISTORICAL MEMBERSHIP')}</span><h2>{tr('Universe evidence')} · {dateOnly(view.as_of)}</h2></div><span className={view.survivorship_bias_free ? 'status-badge ok' : 'status-badge warning'}>{tr(view.universe_mode)}</span></div><div className="universe-proof"><dl><div><dt>{tr('Universe ID')}</dt><dd><code>{view.universe_id}</code></dd></div><div><dt>{tr('Source')}</dt><dd>{view.universe_source}</dd></div><div><dt>{tr('Effective date')}</dt><dd>{dateOnly(universe?.snapshots.at(-1)?.effective_date ?? view.as_of)}</dd></div><div><dt>{tr('Members')}</dt><dd>{view.cross_section.length}</dd></div></dl><p>{tr(view.universe_disclosure ?? '')}</p></div><div className="membership-list">{view.cross_section.map((row) => <div key={row.symbol}><strong>{row.symbol}</strong><span>{row.company}</span><code>{universe?.snapshots.at(-1)?.membership_provenance.find((item) => item.symbol === row.symbol)?.source ?? view.universe_source}</code></div>)}</div></section>}
      {tab === 'PRICE' && <>
        <section className="workspace-panel historical-section"><div className="section-heading"><div><span className="section-kicker">{tr('CROSS-SECTION')}</span><h2>{tr('Market state')} · {dateOnly(view.as_of)}</h2></div><code>{view.source.toUpperCase()}</code></div><div className="historical-table" role="table"><div className="historical-row header"><span>{tr('Symbol / company')}</span><span>{tr('Close')}</span><span>{tr('1D return')}</span><span>{tr('Volume')}</span><span>{tr('20D volatility')}</span><span>{tr('Daily range')}</span><span>{tr('20D avg volume')}</span></div>{view.cross_section.map((row) => <button key={row.symbol} className={`historical-row ${row.symbol === view.selected_symbol ? 'selected' : ''}`} onClick={() => setSymbol(row.symbol)}><span><strong>{row.symbol}</strong><small>{row.company}</small></span><code>${row.close.toFixed(2)}</code><code className={(row.return_1d ?? 0) >= 0 ? 'positive' : 'negative'}>{percent(row.return_1d)}</code><code>{compact(row.volume)}</code><code>{percent(row.volatility_20d)}</code><code>{percent(row.high_low_range)}</code><code>{compact(row.average_volume_20d)}</code></button>)}</div></section>
        <section className="workspace-panel historical-section trend-section"><div className="section-heading"><div><span className="section-kicker">{tr('PRICE + VOLUME')}</span><h2>{view.selected_symbol} · {view.cross_section.find((item) => item.symbol === view.selected_symbol)?.company}</h2></div><span>{view.trend.length} {tr('sessions')}</span></div><TrendChart key={`${view.selected_symbol}:${view.as_of}:${view.trend.length}`} view={view} /><div className="trend-legend"><span><i className="price" />{tr('Close price')}</span><span><i className="volume" />{tr('Volume')}</span><span>{dateOnly(view.trend[0]?.timestamp ?? view.as_of)} — {dateOnly(view.as_of)}</span></div></section>
      </>}
      {tab === 'FUNDAMENTALS' && <section className="workspace-panel historical-section fundamental-record"><div className="section-heading"><div><span className="section-kicker">{tr('POINT-IN-TIME FUNDAMENTALS')}</span><h2>{view.selected_symbol} · {tr('What was knowable then')}</h2></div>{view.fundamentals && <span className={view.fundamentals.restatement_safe ? 'status-badge ok' : 'status-badge warning'}>{tr(view.fundamentals.restatement_safe ? 'RESTATEMENT SAFE' : 'NOT RESTATEMENT-SAFE')}</span>}</div>{!view.fundamentals ? <div className="research-empty actionable"><strong>{tr('No saved fundamental record for this universe')}</strong><p>{tr(provider?.detail ?? 'Configure SEC_USER_AGENT on the backend, then download filed company facts.')}</p><button className="primary-button" disabled={busy || provider?.status === 'BLOCKED'} onClick={() => void downloadFundamentals()}>{tr('Download SEC filings')}</button></div> : <><div className="filing-time-axis"><div><span>{tr('Fiscal period')}</span><strong>{view.fundamentals.fields.find((item) => item.fiscal_period)?.fiscal_period ?? '—'}</strong></div><i>→</i><div><span>{tr('Filed / available')}</span><strong>{dateOnly(view.fundamentals.fields.find((item) => item.available_at)?.available_at ?? view.as_of)}</strong></div><i>→</i><div><span>{tr('Used by research')}</span><strong>{dateOnly(view.fundamentals.used_at)}</strong></div></div><div className="fundamental-table"><div className="fundamental-row header"><span>{tr('Field')}</span><span>{tr('Value')}</span><span>{tr('Status')}</span><span>{tr('Fiscal period')}</span><span>{tr('Report date')}</span><span>{tr('Filed')}</span><span>{tr('Available')}</span><span>{tr('Used')}</span></div>{view.fundamentals.fields.map((item) => <div className="fundamental-row" key={item.field}><strong>{tr(item.field)}</strong><code>{fieldValue(item)}</code><span className={`fund-status ${item.status.toLowerCase()}`}>{tr(item.status)}</span><span>{item.fiscal_period ?? '—'}</span><span>{item.report_date ? dateOnly(item.report_date) : '—'}</span><span>{item.filed_at ? dateOnly(item.filed_at) : '—'}</span><span>{item.available_at ? dateOnly(item.available_at) : '—'}</span><span>{dateOnly(item.used_at)}</span></div>)}</div></>}</section>}
    </>}
  </main>
}
