import { useEffect, useState } from 'react'

import { createCorporateActionDataset, createHistoricalUniverse, getCorporateActionDatasets, getHistoricalUniverses } from '../../api/corporateActions'
import { getDatasetRows, importDataset, previewDataset } from '../../api/datasets'
import { getStockSnapshot, saveHistoricalDataset, searchStocks } from '../../api/marketData'
import { useI18n } from '../../i18n/I18nProvider'
import type { DatasetDefinition, DatasetPreview, StockSecurity, StockSnapshot } from '../../types/dataset'
import type { CorporateActionDataset, CreateCorporateActionDataset, CreateHistoricalUniverse, HistoricalUniverse } from '../../types/corporateAction'
import { formatTimestamp } from '../replay/utils/format'

const requiredFields = ['timestamp', 'symbol', 'close'] as const

function readableTimestamp(value: string | number): string {
  const formatted = formatTimestamp(String(value))
  return `${formatted.date} · ${formatted.time}`
}

interface DataPageProps {
  datasets: DatasetDefinition[]
  onImported: (dataset: DatasetDefinition) => void
}

export default function DataPage({ datasets, onImported }: DataPageProps) {
  const { tr } = useI18n()
  const requestedDatasetId = new URLSearchParams(window.location.search).get('dataset_id')
  const requestedActionId = new URLSearchParams(window.location.search).get('corporate_action_dataset_id')
  const requestedUniverseId = new URLSearchParams(window.location.search).get('universe_id')
  const [selectedId, setSelectedId] = useState(
    datasets.some((item) => item.dataset_id === requestedDatasetId)
      ? requestedDatasetId ?? ''
      : datasets[0]?.dataset_id ?? '',
  )
  const [preview, setPreview] = useState<DatasetPreview | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [name, setName] = useState('')
  const [timezone, setTimezone] = useState('UTC')
  const [rows, setRows] = useState<Array<Record<string, string | number>>>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [stockQuery, setStockQuery] = useState('AAPL')
  const [stockResults, setStockResults] = useState<StockSecurity[]>([])
  const [stock, setStock] = useState<StockSecurity | null>(null)
  const [snapshot, setSnapshot] = useState<StockSnapshot | null>(null)
  const [feed, setFeed] = useState<'iex' | 'sip'>('iex')
  const [timeframe, setTimeframe] = useState<'1Min' | '5Min' | '15Min' | '1Hour' | '1Day'>('1Day')
  const [startDate, setStartDate] = useState('2024-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [marketBusy, setMarketBusy] = useState(false)
  const [corporateActions, setCorporateActions] = useState<CorporateActionDataset[]>([])
  const [universes, setUniverses] = useState<HistoricalUniverse[]>([])
  const [selectedActionId, setSelectedActionId] = useState(requestedActionId ?? '')
  const [selectedUniverseId, setSelectedUniverseId] = useState(requestedUniverseId ?? '')
  const selected = datasets.find((item) => item.dataset_id === selectedId) ?? datasets[0]
  const selectedActions = corporateActions.find((item) => item.corporate_action_dataset_id === selectedActionId) ?? corporateActions[0]
  const selectedUniverse = universes.find((item) => item.universe_id === selectedUniverseId) ?? universes[0]

  useEffect(() => {
    let active = true
    void Promise.allSettled([getCorporateActionDatasets(), getHistoricalUniverses()]).then(([actions, historical]) => {
      if (!active) return
      if (actions.status === 'fulfilled') {
        setCorporateActions(actions.value)
        if (actions.value[0]) setSelectedActionId((current) => current || actions.value[0].corporate_action_dataset_id)
      }
      if (historical.status === 'fulfilled') {
        setUniverses(historical.value)
        if (historical.value[0]) setSelectedUniverseId((current) => current || historical.value[0].universe_id)
      }
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!selected) return
    let active = true
    void getDatasetRows(selected.dataset_id)
      .then((next) => { if (active) setRows(next) })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : 'Dataset preview failed.') })
    return () => { active = false }
  }, [selected])

  async function chooseFile(file: File | undefined) {
    if (!file) return
    setBusy(true); setError(null)
    try {
      const next = await previewDataset(file)
      setPreview(next); setMapping(next.candidate_mapping); setName(file.name.replace(/\.csv$/i, ''))
      setTimezone(next.detected_timezone ?? '')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'CSV preview failed.') }
    finally { setBusy(false) }
  }

  async function commit() {
    if (!preview) return
    setBusy(true); setError(null)
    try {
      const imported = await importDataset({
        preview_id: preview.preview_id,
        name,
        mapping,
        timezone: timezone || null,
      })
      onImported(imported); setSelectedId(imported.dataset_id); setPreview(null)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Dataset import failed.') }
    finally { setBusy(false) }
  }

  async function findStocks() {
    if (!stockQuery.trim()) return
    setMarketBusy(true); setError(null)
    try { setStockResults(await searchStocks(stockQuery.trim())) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Stock search failed.') }
    finally { setMarketBusy(false) }
  }

  async function selectStock(next: StockSecurity) {
    setStock(next); setMarketBusy(true); setError(null)
    try { setSnapshot(await getStockSnapshot(next.symbol, feed)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Market snapshot failed.') }
    finally { setMarketBusy(false) }
  }

  async function saveMarketData() {
    if (!stock) return
    setMarketBusy(true); setError(null)
    try {
      const saved = await saveHistoricalDataset({
        name: `${stock.symbol} · ${timeframe} · Alpaca ${feed.toUpperCase()}`,
        symbols: [stock.symbol],
        start: `${startDate}T00:00:00Z`,
        end: `${endDate}T23:59:59Z`,
        timeframe,
        feed,
      })
      onImported(saved); setSelectedId(saved.dataset_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Historical download failed.') }
    finally { setMarketBusy(false) }
  }

  async function importCorporateActions(file: File | undefined) {
    if (!file) return
    setBusy(true); setError(null)
    try {
      const request = JSON.parse(await file.text()) as CreateCorporateActionDataset
      const saved = await createCorporateActionDataset(request)
      setCorporateActions((current) => [saved, ...current.filter((item) => item.corporate_action_dataset_id !== saved.corporate_action_dataset_id)])
      setSelectedActionId(saved.corporate_action_dataset_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Corporate Action import failed.') }
    finally { setBusy(false) }
  }

  async function importUniverse(file: File | undefined) {
    if (!file) return
    setBusy(true); setError(null)
    try {
      const request = JSON.parse(await file.text()) as CreateHistoricalUniverse
      const saved = await createHistoricalUniverse(request)
      setUniverses((current) => [saved, ...current.filter((item) => item.universe_id !== saved.universe_id)])
      setSelectedUniverseId(saved.universe_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Historical Universe import failed.') }
    finally { setBusy(false) }
  }

  return <main className="data-shell">
    <header className="workspace-title"><div><h1>{tr('Data')}</h1><span>{tr('Real US equities and local datasets in one research workspace.')}</span></div><label className="secondary-button file-button">{tr('Import CSV')}<input aria-label={tr('Choose CSV')} type="file" accept=".csv,text/csv" onChange={(event) => void chooseFile(event.target.files?.[0])} /></label></header>
    <nav className="data-subnav" aria-label={tr('Data evidence sections')}><a href="#market-datasets">{tr('Market Datasets')}</a><a href="#corporate-actions">{tr('Corporate Actions')}</a><a href="#historical-universes">{tr('Historical Universes')}</a></nav>
    {error && <div className="compact-error" role="alert"><strong>{tr('Data operation failed')}</strong><span>{tr(error)}</span></div>}
    <section className="workspace-panel market-workspace" id="market-datasets">
      <div className="section-heading"><div><span className="section-kicker">{tr('US EQUITY UNIVERSE')}</span><h2>{tr('Find real market data')}</h2></div><span className="evidence-label">ALPACA · {feed.toUpperCase()}</span></div>
      <div className="market-search-bar"><label><span>{tr('Symbol or company')}</span><div className="input-action"><input value={stockQuery} placeholder="AAPL / Apple" onChange={(event) => setStockQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void findStocks() }} /><button disabled={marketBusy || !stockQuery.trim()} onClick={() => void findStocks()}>{tr('Search')}</button></div></label><label><span>{tr('Market feed')}</span><select value={feed} onChange={(event) => setFeed(event.target.value as 'iex' | 'sip')}><option value="iex">IEX</option><option value="sip">SIP</option></select></label></div>
      {stockResults.length > 0 && <div className="stock-results" role="listbox" aria-label={tr('Stock search results')}>{stockResults.map((item) => <button className={stock?.symbol === item.symbol ? 'selected' : ''} key={item.symbol} onClick={() => void selectStock(item)}><strong>{item.symbol}</strong><span>{item.name}</span><code>{item.exchange}</code></button>)}</div>}
      {stock && <div className="market-download-workspace">
        <div className="instrument-summary"><div><span>{tr('Selected stock')}</span><strong>{stock.symbol}</strong><small>{stock.name} · {stock.exchange}</small></div><div><span>{tr('Latest trade')}</span><strong>{snapshot?.latest_trade_price == null ? '—' : `$${snapshot.latest_trade_price.toFixed(2)}`}</strong><small>{snapshot ? readableTimestamp(snapshot.market_timestamp) : tr('Loading market snapshot…')}</small></div><div><span>{tr('Status')}</span><strong>{tr(stock.status.toUpperCase())}</strong><small>{stock.tradable ? tr('Tradable') : tr('Not tradable')}</small></div></div>
        <div className="historical-request-grid"><label>{tr('Start date')}<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label><label>{tr('End date')}<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label><label>{tr('Frequency')}<select value={timeframe} onChange={(event) => setTimeframe(event.target.value as typeof timeframe)}><option value="1Day">1D</option><option value="1Hour">1H</option><option value="15Min">15m</option><option value="5Min">5m</option><option value="1Min">1m</option></select></label><button className="primary-button" disabled={marketBusy || startDate >= endDate} onClick={() => void saveMarketData()}>{tr(marketBusy ? 'Working…' : 'Save as Dataset')}</button></div>
        <p className="provenance-note">{tr('Provider timestamps, feed, requested period, retrieval time, and content fingerprint are saved with the dataset.')}</p>
      </div>}
    </section>
    {preview && <section className="workspace-panel import-inspector">
      <div className="section-heading"><h2>{tr('Import preview')} · {preview.filename}</h2><span>{preview.rows.length} {tr('preview rows')}</span></div>
      <div className="import-grid"><label>{tr('Dataset name')}<input aria-label={tr('Dataset name')} value={name} onChange={(event) => setName(event.target.value)} /></label><label>{tr('Source timezone')}<input aria-label={tr('Source timezone')} placeholder="Asia/Hong_Kong" value={timezone} onChange={(event) => setTimezone(event.target.value)} /></label>{requiredFields.map((field) => <label key={field}>{tr(field)}<select aria-label={`${tr('Map')} ${tr(field)}`} value={mapping[field] ?? ''} onChange={(event) => setMapping((current) => ({ ...current, [field]: event.target.value }))}><option value="">{tr('Select column')}</option>{preview.columns.map((column) => <option key={column} value={column}>{column} · {tr(preview.detected_types[column])}</option>)}</select></label>)}</div>
      <div className="dataset-preview-table"><div className="dense-row header">{preview.columns.map((column) => <span key={column}>{column}</span>)}</div>{preview.rows.slice(0, 5).map((row, index) => <div className="dense-row" key={index}>{preview.columns.map((column) => <code key={column}>{row[column]}</code>)}</div>)}</div>
      <div className="toolbar end"><button className="primary-button" disabled={busy || requiredFields.some((field) => !mapping[field]) || !timezone && !preview.detected_timezone} onClick={() => void commit()}>{tr(busy ? 'Validating…' : 'Validate & Import')}</button></div>
    </section>}
    <section className="workspace-panel dataset-library">
      <div className="section-heading"><h2>{tr('Datasets')}</h2><span>{datasets.length} {tr('saved definitions')}</span></div>
      <div className="dataset-table" role="table"><div className="dataset-row header"><span>{tr('Name')}</span><span>{tr('Symbols')}</span><span>{tr('Dataset period')}</span><span>{tr('Rows')}</span><span>{tr('Quality')}</span></div>{datasets.map((dataset) => <button className={`dataset-row ${selected?.dataset_id === dataset.dataset_id ? 'selected' : ''}`} key={dataset.dataset_id} onClick={() => setSelectedId(dataset.dataset_id)}><strong>{tr(dataset.name)}</strong><code>{dataset.symbols.length}</code><span>{formatTimestamp(dataset.start_time).date} — {formatTimestamp(dataset.end_time).date}</span><code>{dataset.row_count}</code><span className={`status-badge ${dataset.quality.status === 'VALID' ? 'ok' : 'warning'}`}>{tr(dataset.quality.status)}</span></button>)}</div>
    </section>
    {selected && <section className="workspace-panel dataset-inspector">
      <div className="section-heading"><h2>{tr('Selected Dataset')} · {tr(selected.name)}</h2><code>{selected.dataset_id}</code></div>
      <div className="dataset-facts"><div><span>{tr('Schema')}</span><strong>{selected.fields.map(tr).join(' · ')}</strong><small>{selected.symbols.join(', ')}</small></div><div><span>{tr('Quality')}</span><strong>{tr(selected.quality.status)}</strong><small>{selected.quality.duplicates} {tr('duplicates')} · {selected.quality.missing_required_values} {tr('missing')} · {selected.quality.rows_reordered} {tr('reordered')} · {selected.quality.alignment_gaps} {tr('alignment gaps')}</small></div><div><span>{tr('Source')}</span><strong>{selected.provenance ? `${selected.provenance.provider.toUpperCase()} · ${selected.provenance.feed.toUpperCase()}` : tr(selected.source_type)}</strong><small>{selected.frequency} · {selected.timezone}</small></div><div><span>{tr('Revision')}</span><strong>{selected.content_fingerprint.slice(0, 20)}…</strong><small>{selected.synchronized_bar_count} {tr('synchronized bars')}</small></div></div>
      {selected.provenance && <div className="dataset-provenance"><span>{tr('Market period')} <strong>{readableTimestamp(selected.provenance.market_timestamp_start)} — {readableTimestamp(selected.provenance.market_timestamp_end)}</strong></span><span>{tr('Retrieved')} <strong>{readableTimestamp(selected.provenance.retrieved_at)}</strong></span></div>}
      {selected.quality.issues.map((issue) => <p className="inline-warning" key={issue}>{tr(issue)}</p>)}
      <div className="dataset-preview-table dataset-market-preview"><div className="dense-row header dataset-market-row"><span>{tr('Timestamp')}</span><span>{tr('Symbol')}</span><span>{tr('Close price')}</span></div>{rows.slice(0, 12).map((row, index) => <div className="dense-row dataset-market-row" key={index}><time dateTime={String(row.timestamp)}>{readableTimestamp(row.timestamp)}</time><code>{String(row.symbol)}</code><code>{String(row.close ?? '')}</code></div>)}</div>
    </section>}
    <section className="workspace-panel evidence-library" id="corporate-actions">
      <div className="section-heading"><div><span className="section-kicker">{tr('IMMUTABLE EVENT EVIDENCE')}</span><h2>{tr('Corporate Actions')}</h2></div><div className="section-actions"><span>{corporateActions.length} {tr('saved datasets')}</span><label className="secondary-button file-button">{tr('Import actions JSON')}<input aria-label={tr('Import actions JSON')} type="file" accept="application/json,.json" disabled={busy} onChange={(event) => void importCorporateActions(event.target.files?.[0])} /></label></div></div>
      {corporateActions.length === 0 ? <p className="empty-state">{tr('No Corporate Action datasets yet.')}</p> : <div className="evidence-split-view">
        <div className="evidence-selector" role="list" aria-label={tr('Corporate Action datasets')}>{corporateActions.map((item) => {
          const splitCount = item.actions.filter((action) => action.action_type === 'SPLIT').length
          const dividendCount = item.actions.filter((action) => action.action_type === 'CASH_DIVIDEND').length
          const delistingCount = item.actions.filter((action) => action.action_type === 'DELISTING').length
          return <button key={item.corporate_action_dataset_id} className={selectedActions?.corporate_action_dataset_id === item.corporate_action_dataset_id ? 'selected' : ''} onClick={() => setSelectedActionId(item.corporate_action_dataset_id)}><strong>{item.name}</strong><span>{item.provider} · {item.symbols.join(', ')}</span><small>{tr('Split')} {splitCount} · {tr('Dividend')} {dividendCount} · {tr('Delisting')} {delistingCount}</small></button>
        })}</div>
        {selectedActions && <article className="evidence-detail"><header><div><h3>{selectedActions.name}</h3><code>{selectedActions.corporate_action_dataset_id}</code></div><span className={`status-badge ${selectedActions.point_in_time_safe ? 'ok' : 'warning'}`}>{tr(selectedActions.point_in_time_safe ? 'PIT SAFE' : 'PIT WARNING')}</span></header><div className="evidence-facts"><span>{tr('Provider')}<strong>{selectedActions.provider}</strong></span><span>{tr('Date range')}<strong>{formatTimestamp(selectedActions.start_time).date} — {formatTimestamp(selectedActions.end_time).date}</strong></span><span>{tr('Fingerprint')}<code>{selectedActions.content_fingerprint.slice(0, 22)}…</code></span></div><p>{selectedActions.disclosure}</p><div className="event-timeline">{selectedActions.actions.map((action) => <div className="event-card" key={action.action_id}><time dateTime={action.effective_at}>{formatTimestamp(action.effective_at).date}</time><strong>{action.symbol} · {tr(action.action_type.replaceAll('_', ' '))}</strong><span>{action.action_type === 'SPLIT' ? `${tr('Ratio')} ${action.split_ratio}` : action.action_type === 'CASH_DIVIDEND' ? `${action.currency} ${action.cash_amount}` : action.settlement_price == null ? tr('Settlement unresolved') : `${tr('Settlement')} ${action.settlement_price}`}</span><small>{action.source} · {action.evidence}</small>{action.available_at > action.effective_at && <em className="inline-warning">{tr('Evidence became available after the effective time.')}</em>}{action.action_type === 'DELISTING' && action.settlement_price == null && <em className="inline-warning">{tr('No reliable settlement price; the position is not silently removed.')}</em>}</div>)}</div></article>}
      </div>}
    </section>
    <section className="workspace-panel evidence-library" id="historical-universes">
      <div className="section-heading"><div><span className="section-kicker">{tr('MEMBERSHIP THROUGH TIME')}</span><h2>{tr('Historical Universes')}</h2></div><div className="section-actions"><span>{universes.length} {tr('saved universes')}</span><label className="secondary-button file-button">{tr('Import universe JSON')}<input aria-label={tr('Import universe JSON')} type="file" accept="application/json,.json" disabled={busy} onChange={(event) => void importUniverse(event.target.files?.[0])} /></label></div></div>
      {universes.length === 0 ? <p className="empty-state">{tr('No Historical Universes yet.')}</p> : <div className="evidence-split-view">
        <div className="evidence-selector" role="list" aria-label={tr('Historical Universes')}>{universes.map((item) => <button key={item.universe_id} className={selectedUniverse?.universe_id === item.universe_id ? 'selected' : ''} onClick={() => setSelectedUniverseId(item.universe_id)}><strong>{item.name}</strong><span>{tr(item.mode.replaceAll('_', ' '))} · {item.source}</span><small>{item.snapshots.length} {tr('snapshots')} · {item.snapshots.at(-1)?.symbols.length ?? 0} {tr('members')}</small></button>)}</div>
        {selectedUniverse && <article className="evidence-detail"><header><div><h3>{selectedUniverse.name}</h3><code>{selectedUniverse.universe_id}</code></div><span className={`status-badge ${selectedUniverse.survivorship_bias_free ? 'ok' : 'warning'}`}>{tr(selectedUniverse.survivorship_bias_free ? 'SURVIVORSHIP SAFE' : 'SURVIVORSHIP RISK')}</span></header><div className="evidence-facts"><span>{tr('Mode')}<strong>{tr(selectedUniverse.mode.replaceAll('_', ' '))}</strong></span><span>{tr('Source')}<strong>{selectedUniverse.source}</strong></span><span>{tr('Snapshots')}<strong>{selectedUniverse.snapshots.length}</strong></span></div><p>{selectedUniverse.disclosure}</p><ol className="universe-timeline">{selectedUniverse.snapshots.map((snapshot) => { const missingEvidence = snapshot.symbols.filter((symbol) => !snapshot.membership_provenance.some((item) => item.symbol === symbol && item.source && item.evidence)); return <li key={snapshot.effective_date}><time dateTime={snapshot.effective_date}>{formatTimestamp(snapshot.effective_date).date}</time><div><strong>{snapshot.symbols.join(', ')}</strong><span>{snapshot.symbols.length} {tr('members')} · {snapshot.membership_provenance.length} {tr('provenance records')}</span>{missingEvidence.length > 0 && <em className="inline-warning">{tr('Missing membership evidence')}: {missingEvidence.join(', ')}</em>}</div></li> })}</ol></article>}
      </div>}
    </section>
  </main>
}
