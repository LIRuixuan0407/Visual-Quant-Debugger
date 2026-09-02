import { useCallback, useEffect, useState } from 'react'

import { getPnLAutopsy } from '../../api/autopsy'
import { useI18n } from '../../i18n/I18nProvider'
import type { PeriodAttribution, PnLAutopsyReport, TradeAttribution } from '../../types/autopsy'
import { formatCurrency, formatTimestamp } from '../replay/utils/format'

type PeriodTab = 'monthly' | 'quarterly' | 'yearly'
type TradeTab = 'best_closed' | 'worst_closed'

function percent(value: number) { return `${(value * 100).toFixed(2)}%` }
function signedCurrency(value: number) { return `${value >= 0 ? '+' : '−'}${formatCurrency(Math.abs(value))}` }

function Waterfall({ report }: { report: PnLAutopsyReport }) {
  const { tr } = useI18n()
  const { summary } = report
  const steps = [
    { label: tr('Gross P&L'), value: summary.gross_pnl, tone: 'gross' },
    { label: tr('Fees'), value: -summary.fees, tone: 'cost' },
    { label: tr('Slippage'), value: -summary.slippage, tone: 'cost' },
    { label: tr('Net P&L'), value: summary.net_pnl, tone: 'net' },
  ]
  const scale = Math.max(...steps.map((step) => Math.abs(step.value)), 1)
  return (
    <div className="waterfall" aria-label={tr('Gross P&L less fees and slippage equals net P&L')}>
      {steps.map((step) => <div className={`waterfall-step ${step.tone}`} key={step.label}>
        <div><small>{step.label}</small><strong>{signedCurrency(step.value)}</strong></div>
        <i style={{ width: `${Math.max(4, Math.abs(step.value) / scale * 100)}%` }} />
      </div>)}
    </div>
  )
}

function PeriodTable({ periods }: { periods: PeriodAttribution[] }) {
  const { tr } = useI18n()
  return (
    <div className="period-table" role="table" aria-label={tr('UTC period P&L attribution')}>
      <div className="period-row header" role="row"><span>{tr('Period')}</span><span>{tr('Gross')}</span><span>{tr('Fees')}</span><span>{tr('Slippage')}</span><span>{tr('Net')}</span><span>{tr('Return')}</span></div>
      {periods.map((period) => <div className="period-row" role="row" key={period.label}><strong>{period.label}</strong><code>{signedCurrency(period.gross_pnl)}</code><code>−{formatCurrency(period.fees)}</code><code>−{formatCurrency(period.slippage)}</code><code>{signedCurrency(period.net_pnl)}</code><code>{percent(period.period_return)}</code></div>)}
    </div>
  )
}

function TradeList({ trades, empty, onReplay }: { trades: TradeAttribution[]; empty: string; onReplay: (eventId: string) => void }) {
  const { tr } = useI18n()
  if (!trades.length) return <p className="empty-state">{empty}</p>
  return <div className="trade-attribution-list">{trades.map((trade, index) => <article key={trade.trade_id}>
    <span className="trade-rank">#{index + 1}</span><div><small>{tr(trade.direction.replace('_', ' ').toLowerCase().replace(/^./, (letter) => letter.toUpperCase()))}</small><h3>{signedCurrency(trade.net_pnl)}</h3><code>{formatTimestamp(trade.opened_at).date} · {trade.event_count} {tr('Event')}</code></div>
    <dl><div><dt>{tr('Gross')}</dt><dd>{signedCurrency(trade.gross_pnl)}</dd></div><div><dt>{tr('Costs')}</dt><dd>−{formatCurrency(trade.fees + trade.slippage)}</dd></div><div><dt>{tr('Return')}</dt><dd>{percent(trade.trade_return)}</dd></div></dl>
    <button className="link-button" onClick={() => onReplay(trade.entry_event_id)}>{tr('Replay entry')} →</button>
  </article>)}</div>
}


function FailureFingerprintSnapshot({ report }: { report: PnLAutopsyReport }) {
  const { tr } = useI18n()
  const fingerprint = report.failure_fingerprint
  if (!fingerprint) return null
  return <section className="autopsy-section autopsy-fingerprint" aria-labelledby="autopsy-fingerprint-title">
    <div className="section-heading"><h2 id="autopsy-fingerprint-title">{tr('Strategy failure fingerprint')}</h2><span>{tr('Diagnosis snapshot')}</span></div>
    <p className="autopsy-fingerprint-summary">{tr(fingerprint.summary)}</p>
    <div className="autopsy-fingerprint-grid">{fingerprint.dimensions.filter((item) => item.severity !== 'NOT_AVAILABLE').map((item) => <article key={item.key}>
      <div><strong>{tr(item.title)}</strong><span className={`fingerprint-severity ${item.severity.toLowerCase().replace('_', '-')}`}>{tr(item.severity)}</span></div>
      <p>{tr(item.evidence[0] ?? '')}</p>
    </article>)}</div>
    <p className="fingerprint-boundary">{tr('This snapshot comes from the trace-bound Diagnose report; Autopsy does not infer new failure scores.')}</p>
  </section>
}

function AutopsyContent({ report, onReplay }: { report: PnLAutopsyReport; onReplay: (eventId: string) => void }) {
  const { tr } = useI18n()
  const [periodTab, setPeriodTab] = useState<PeriodTab>('monthly')
  const [tradeTab, setTradeTab] = useState<TradeTab>('best_closed')
  const { summary, reconciliation, trades } = report
  return (
    <main className="autopsy-shell">
      <header className="autopsy-header"><h1>{tr('P&L Autopsy')}</h1><div className="source-run"><button className="link-button" onClick={() => onReplay('')}>{tr('Open Replay')} →</button></div></header>

      <section className="autopsy-section summary-section" aria-labelledby="waterfall-title">
        <div className="section-heading"><h2 id="waterfall-title">{tr('P&L breakdown')}</h2><span className={reconciliation.reconciled ? 'reconcile-badge reconciled' : 'reconcile-badge'}>{tr(reconciliation.reconciled ? 'Reconciled' : 'Review difference')}</span></div>
        <div className="waterfall-layout"><Waterfall report={report} /><dl className="equity-bookends"><div><dt>{tr('Initial equity')}</dt><dd>{formatCurrency(summary.initial_equity)}</dd></div><div><dt>{tr('Net P&L')}</dt><dd>{signedCurrency(summary.net_pnl)}</dd></div><div><dt>{tr('Final equity')}</dt><dd>{formatCurrency(summary.final_equity)}</dd></div><div className="equation"><dt>{tr('Check')}</dt><dd><code>{tr('initial + net = final')}</code></dd></div></dl></div>
      </section>

      <FailureFingerprintSnapshot report={report} />

      <section className="autopsy-section" aria-labelledby="period-title">
        <div className="section-heading"><h2 id="period-title">{tr('P&L by period')}</h2><div className="segmented-tabs" role="tablist">{(['monthly', 'quarterly', 'yearly'] as const).map((tab) => <button role="tab" aria-selected={periodTab === tab} key={tab} onClick={() => setPeriodTab(tab)}>{tr(tab.slice(0, -2))}</button>)}</div></div>
        <PeriodTable periods={report.periods[periodTab]} />
      </section>

      <section className="autopsy-section" aria-labelledby="trade-title">
        <div className="section-heading"><h2 id="trade-title">{tr('Trade attribution')}</h2><div className="segmented-tabs" role="tablist"><button role="tab" aria-selected={tradeTab === 'best_closed'} onClick={() => setTradeTab('best_closed')}>{tr('Best')}</button><button role="tab" aria-selected={tradeTab === 'worst_closed'} onClick={() => setTradeTab('worst_closed')}>{tr('Worst')}</button></div></div>
        <TradeList trades={trades[tradeTab]} empty={tr('No closed trades are available.')} onReplay={onReplay} />
        <div className="trade-reconciliation"><span>{tr(trades.reconciliation_status)}</span><p>{tr('Attributed')} {signedCurrency(trades.attributed_net_pnl)}</p><p>{tr('Unattributed')} {signedCurrency(trades.unattributed_net_pnl)}</p></div>
        {trades.open_trades.length > 0 && <div className="open-trades"><h3>{tr('Still open at final event')}</h3><TradeList trades={trades.open_trades} empty={tr('No open trades.')} onReplay={onReplay} /></div>}
      </section>

      <section className="autopsy-section" aria-labelledby="drawdown-title">
        <div className="section-heading"><h2 id="drawdown-title">{tr('Drawdowns')}</h2><span className="definition-version">{report.drawdowns.length} {tr('episodes')}</span></div>
        {report.drawdowns.length === 0 ? <p className="empty-state">{tr('No drawdown episode was recorded.')}</p> : <div className="drawdown-list">{[...report.drawdowns].sort((a, b) => a.rank_by_depth - b.rank_by_depth).map((episode) => <article key={episode.episode_id}>
          <div className="drawdown-rank"><span>#{episode.rank_by_depth}</span><strong>{percent(episode.max_drawdown)}</strong><small>{tr(episode.recovered ? 'Recovered' : 'Unrecovered')}</small></div>
          <div className="drawdown-path"><span>{tr('Peak')}<br /><code>{formatTimestamp(episode.peak_time).date}</code></span><i>→</i><span>{tr('Trough')}<br /><code>{formatTimestamp(episode.trough_time).date}</code></span><i>→</i><span>{tr('Recovery')}<br /><code>{episode.recovery_time ? formatTimestamp(episode.recovery_time).date : tr('Not reached')}</code></span></div>
          <div className="drawdown-actions"><button className="link-button" onClick={() => onReplay(episode.peak_event_id)}>{tr('Replay peak')}</button><button className="link-button" onClick={() => onReplay(episode.trough_event_id)}>{tr('Replay trough')}</button>{episode.recovery_event_id && <button className="link-button" onClick={() => onReplay(episode.recovery_event_id!)}>{tr('Replay recovery')}</button>}</div>
        </article>)}</div>}
      </section>
    </main>
  )
}

function AutopsyPage({ traceId, onReplay }: { traceId: string | null; onReplay: (eventId: string) => void }) {
  const { tr } = useI18n()
  const [report, setReport] = useState<PnLAutopsyReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    if (!traceId) return
    setLoading(true); setError(null); setReport(null)
    try { setReport(await getPnLAutopsy(traceId)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'P&L Autopsy failed with an unknown error.') }
    finally { setLoading(false) }
  }, [traceId])
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer) }, [load])

  if (!traceId) return <main className="diagnose-empty"><section><h1>{tr('P&L Autopsy')}</h1><p>{tr('Run a backtest from Strategy before opening its P&L Autopsy.')}</p></section></main>
  if (loading || (!report && !error)) return <main className="diagnose-empty"><section><h1>{tr('Building P&L Autopsy…')}</h1><div className="loading-track"><span /></div></section></main>
  if (error) return <main className="diagnose-empty"><section role="alert"><h1>{tr('Could not open P&L Autopsy.')}</h1><p>{error}</p><button className="primary-button" onClick={() => void load()}>{tr('Retry')}</button></section></main>
  return <AutopsyContent report={report!} onReplay={onReplay} />
}

export default AutopsyPage
