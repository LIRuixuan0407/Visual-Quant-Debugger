import { useEffect, useState } from 'react'

import { getDatasetRows, importDataset, previewDataset } from '../../api/datasets'
import { getStockSnapshot, saveHistoricalDataset, searchStocks } from '../../api/marketData'
import { useI18n } from '../../i18n/I18nProvider'
import type { DatasetDefinition, DatasetPreview, StockSecurity, StockSnapshot } from '../../types/dataset'
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
  const selected = datasets.find((item) => item.dataset_id === selectedId) ?? datasets[0]

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

  return <main className="data-shell">
    <header className="workspace-title"><div><h1>{tr('Data')}</h1><span>{tr('Real US equities and local datasets in one research workspace.')}</span></div><label className="secondary-button file-button">{tr('Import CSV')}<input aria-label={tr('Choose CSV')} type="file" accept=".csv,text/csv" onChange={(event) => void chooseFile(event.target.files?.[0])} /></label></header>
    {error && <div className="compact-error" role="alert"><strong>{tr('Data operation failed')}</strong><span>{tr(error)}</span></div>}
    <section className="workspace-panel market-workspace">
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
  </main>
}
