import { useCallback, useEffect, useMemo, useState } from 'react'

import { cancelPaperOrder, createPaperAccount, createPaperSession, getMarketDataProviders, getPaperHealth, getPaperOperations, getPaperRecovery, getPaperSession, getPaperTrace, isPaperSession, listPaperAccounts, listPaperSessions, recoverPaperSession, transitionPaperSession } from '../../api/paper'
import { searchStocks, type MarketProvider, type MarketRegion } from '../../api/marketData'
import { useI18n } from '../../i18n/I18nProvider'
import type { MarketDataProviderStatus, PaperAccount, PaperOperationalHealth, PaperOperationEvent, PaperRecoveryReport, PaperSessionSnapshot, PaperTrace } from '../../types/paper'
import type { StockSecurity } from '../../types/dataset'
import type { StrategyDefinition } from '../../types/strategy'
import ReplayTimeline from '../replay/ReplayTimeline'
import { ExecutionOutcomePanel, MarketPositionPanel, StrategyDecisionPanel } from '../replay/ReplayInspectors'
import SignalLineage from '../replay/SignalLineage'
import { formatCurrency, formatPercent, formatTimestamp } from '../replay/utils/format'
import { createReplayIndex, findSourceSignalEvent } from '../replay/utils/navigation'

const EXECUTION_PARAMETERS = new Set(['initial_cash', 'fee_bps', 'slippage_bps', 'gross_target'])

function paperCurrency(session: PaperSessionSnapshot): 'CNY' | 'HKD' | 'USD' {
  if (session.market_session === 'CN_REGULAR') return 'CNY'
  if (session.market_session === 'HK_REGULAR') return 'HKD'
  return 'USD'
}

function EquityTimeline({ trace, currency }: { trace: PaperTrace; currency: 'CNY' | 'HKD' | 'USD' }) {
  const { tr } = useI18n()
  const values = trace.timeline.map((event) => event.pnl_snapshot.equity)
  if (values.length < 2) return <p className="empty-state">{tr('Equity appears after received one-minute bars are evaluated.')}</p>
  const low = Math.min(...values); const high = Math.max(...values); const span = high - low || 1
  const points = values.map((value, index) => `${(index / (values.length - 1)) * 100},${36 - ((value - low) / span) * 32}`).join(' ')
  return <div className="live-equity-chart"><svg viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label={tr('Live paper equity timeline')}><polyline points={points} /></svg><div><code>{formatCurrency(low, currency)}</code><code>{formatCurrency(high, currency)}</code></div></div>
}

type PaperTimelineEvent = PaperTrace['timeline'][number]

interface PairChartPoint {
  eventId: string
  timestamp: string
  signal: string
  leftClose: number
  rightClose: number
  leftNormalized: number
  rightNormalized: number
  hedgeRatio: number | null
  spread: number | null
  zscore: number | null
}

function pairFeature(event: PaperTimelineEvent, name: string): number | null {
  const value = event.feature_snapshots.find((item) => item.name === name)?.value
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function pairClose(event: PaperTimelineEvent, symbol: string): number | null {
  const value = event.market_snapshot.values.find((item) => item.symbol === symbol && item.field === 'close')?.value
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function pairTimestamp(event: PaperTimelineEvent, symbol: string): string {
  return event.data_dependencies.find((item) => item.symbol === symbol && item.field === 'close')?.source_timestamp ?? event.timestamp
}

function chartPath<T>(items: T[], select: (item: T) => number | null, low: number, high: number): string {
  const span = high - low || 1
  let connected = false
  return items.map((item, index) => {
    const value = select(item)
    if (value === null || !Number.isFinite(value)) { connected = false; return '' }
    const x = items.length === 1 ? 50 : (index / (items.length - 1)) * 100
    const y = 44 - ((value - low) / span) * 40
    const command = connected ? 'L' : 'M'
    connected = true
    return `${command}${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')
}

function nearestChartPoint(clientX: number, left: number, width: number, count: number): number {
  if (count <= 1 || width <= 0) return 0
  const ratio = Math.min(1, Math.max(0, (clientX - left) / width))
  return Math.round(ratio * (count - 1))
}

function normalizedChange(value: number): string {
  const change = value - 100
  return `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`
}

function PairStructurePanel({ snapshot, trace }: { snapshot: PaperSessionSnapshot; trace: PaperTrace }) {
  const { tr } = useI18n()
  const [hoveredPriceIndex, setHoveredPriceIndex] = useState<number | null>(null)
  const [pinnedPriceIndex, setPinnedPriceIndex] = useState<number | null>(null)
  const [priceChartFocused, setPriceChartFocused] = useState(false)
  if (snapshot.strategy_id !== 'pairs-trading' || snapshot.symbols.length !== 2) return null
  const [leftSymbol, rightSymbol] = snapshot.symbols
  const configuredLookback = Number(snapshot.parameters.lookback ?? 60)
  const lookback = Number.isFinite(configuredLookback) ? Math.max(2, Math.round(configuredLookback)) : 60
  const configuredEntryZ = Number(snapshot.parameters.entry_z ?? 2)
  const configuredExitZ = Number(snapshot.parameters.exit_z ?? 0.5)
  const entryZ = Number.isFinite(configuredEntryZ) ? Math.abs(configuredEntryZ) : 2
  const exitZ = Number.isFinite(configuredExitZ) ? Math.abs(configuredExitZ) : 0.5
  const rawPoints = trace.timeline.flatMap((event) => {
    const leftClose = pairClose(event, leftSymbol)
    const rightClose = pairClose(event, rightSymbol)
    if (leftClose === null || rightClose === null || leftClose === 0 || rightClose === 0) return []
    return [{ event, leftClose, rightClose }]
  })
  const first = rawPoints[0]
  const points: PairChartPoint[] = rawPoints.map(({ event, leftClose, rightClose }) => ({
    eventId: event.event_id,
    timestamp: pairTimestamp(event, leftSymbol),
    signal: event.signal_evaluation.signal,
    leftClose,
    rightClose,
    leftNormalized: first ? (leftClose / first.leftClose) * 100 : 100,
    rightNormalized: first ? (rightClose / first.rightClose) * 100 : 100,
    hedgeRatio: pairFeature(event, 'hedge_ratio'),
    spread: pairFeature(event, 'spread'),
    zscore: pairFeature(event, 'zscore'),
  }))
  const activeSpreads = trace.timeline
    .filter((event) => event.signal_evaluation.signal !== 'EVALUATION_SKIPPED_PAUSED')
    .map((event) => pairFeature(event, 'spread'))
  let validSpreadObservations = 0
  for (let index = activeSpreads.length - 1; index >= 0 && activeSpreads[index] !== null; index -= 1) validSpreadObservations += 1
  validSpreadObservations = Math.min(validSpreadObservations, lookback)
  const remainingBars = validSpreadObservations > 0 || snapshot.evaluated_bar_count >= lookback
    ? Math.max(0, lookback - validSpreadObservations)
    : Math.max(0, lookback * 2 - 1 - snapshot.evaluated_bar_count)
  const latestFeaturePoint = points.slice().reverse().find((item) => item.zscore !== null)
  const latestZscore = latestFeaturePoint?.zscore ?? null
  const latestSpread = latestFeaturePoint?.spread ?? null
  const phase = snapshot.status === 'PAUSED' ? 'PAUSED' : latestZscore === null ? 'WARMUP' : 'ACTIVE'
  const verdict = phase === 'PAUSED'
    ? tr('The strategy is paused. Prices keep updating, but pair features and decisions do not.')
    : phase === 'WARMUP'
      ? tr('The pair is warming up; no tradeable z-score exists yet.')
      : tr('Pair features are available; inspect the recorded z-score and decision evidence.')
  const noExposure = Object.keys(snapshot.account.positions).length === 0 && snapshot.orders.length === 0
  const normalizedValues = points.flatMap((item) => [item.leftNormalized, item.rightNormalized])
  const normalizedMin = normalizedValues.length ? Math.min(...normalizedValues) : 99
  const normalizedMax = normalizedValues.length ? Math.max(...normalizedValues) : 101
  const normalizedPadding = Math.max((normalizedMax - normalizedMin) * 0.12, 0.25)
  const chartLow = normalizedMin - normalizedPadding
  const chartHigh = normalizedMax + normalizedPadding
  const zValues = points.flatMap((item) => item.zscore === null ? [] : [item.zscore])
  const zBound = Math.max(entryZ + 0.5, exitZ + 0.5, ...zValues.map(Math.abs), 2.5)
  const zY = (value: number) => 24 - (value / zBound) * 20
  let pausedStart = points.length
  for (let index = points.length - 1; index >= 0 && points[index].signal === 'EVALUATION_SKIPPED_PAUSED'; index -= 1) pausedStart = index
  const pausedX = points.length < 2 || pausedStart === points.length ? null : Math.max(0, ((pausedStart - 0.5) / (points.length - 1)) * 100)
  const latest = points.at(-1)
  const safePinnedPriceIndex = pinnedPriceIndex === null || points.length === 0 ? null : Math.min(pinnedPriceIndex, points.length - 1)
  const inspectedPriceIndex = hoveredPriceIndex === null ? safePinnedPriceIndex ?? points.length - 1 : Math.min(hoveredPriceIndex, points.length - 1)
  const inspectedPricePoint = points[inspectedPriceIndex]
  const inspectedPriceX = points.length < 2 ? 50 : (inspectedPriceIndex / (points.length - 1)) * 100
  const priceY = (value: number) => 44 - ((value - chartLow) / (chartHigh - chartLow || 1)) * 40
  const showPriceTooltip = Boolean(inspectedPricePoint && (hoveredPriceIndex !== null || safePinnedPriceIndex !== null || priceChartFocused))
  const inspectPriceAt = (clientX: number, left: number, width: number) => nearestChartPoint(clientX, left, width, points.length)
  const detailRows = points.slice(-10)
  return <section className="workspace-panel pair-structure-panel" id="paper-pair-structure">
    <div className="section-heading"><div><span className="section-kicker">{tr('PAIR RESEARCH EVIDENCE')}</span><h2>{tr('Pair Price & Signal Structure')}</h2></div><span className={`pair-phase ${phase.toLowerCase()}`}>{tr(phase)}</span></div>
    <div className={`pair-verdict ${phase.toLowerCase()}`}><span>{tr('Verdict')}</span><strong>{verdict}</strong>{noExposure && <p>{tr('No position or order exists, so equity remains equal to cash.')}</p>}</div>
    <div className="pair-progress-strip">
      <div><span>{tr('Aligned pair bars')}</span><strong>{snapshot.evaluated_bar_count}</strong></div>
      <div><span>{tr('Valid spread observations')}</span><strong>{validSpreadObservations} / {lookback}</strong></div>
      <div><span>{tr('Remaining active pair bars')}</span><strong>{remainingBars}</strong></div>
      <div><span>{tr('Current phase')}</span><strong>{tr(phase)}</strong></div>
    </div>
    <div className="pair-chart-grid">
      <article className="pair-chart-card">
        <header><div><span>{tr('Evidence')}</span><strong>{tr('Normalized Pair Prices')}</strong></div><small>{tr('Loaded window rebased to 100')}</small></header>
        {points.length < 2 ? <p className="pair-chart-empty">{tr('Two aligned pair bars are required to draw the price comparison.')}</p> : <>
          <p className="pair-chart-instruction">{tr('Hover or focus the chart; use arrow keys to inspect and click or tap to pin.')}</p>
          <div className="pair-chart-plot">
            <svg
              viewBox="0 0 100 48"
              preserveAspectRatio="none"
              role="img"
              tabIndex={0}
              aria-label={tr('Interactive normalized pair price chart')}
              onFocus={() => setPriceChartFocused(true)}
              onBlur={() => setPriceChartFocused(false)}
              onMouseMove={(event) => {
                const bounds = event.currentTarget.getBoundingClientRect()
                setHoveredPriceIndex(inspectPriceAt(event.clientX, bounds.left, bounds.width))
              }}
              onMouseLeave={() => setHoveredPriceIndex(null)}
              onClick={(event) => {
                const bounds = event.currentTarget.getBoundingClientRect()
                const nextIndex = inspectPriceAt(event.clientX, bounds.left, bounds.width)
                setPinnedPriceIndex((current) => current === nextIndex ? null : nextIndex)
              }}
              onKeyDown={(event) => {
                const current = inspectedPriceIndex < 0 ? points.length - 1 : inspectedPriceIndex
                let next: number | null = null
                if (event.key === 'ArrowLeft') next = Math.max(0, current - 1)
                else if (event.key === 'ArrowRight') next = Math.min(points.length - 1, current + 1)
                else if (event.key === 'Home') next = 0
                else if (event.key === 'End') next = points.length - 1
                else if (event.key === 'Escape') { setHoveredPriceIndex(null); setPinnedPriceIndex(null) }
                if (next !== null) { event.preventDefault(); setHoveredPriceIndex(null); setPinnedPriceIndex(next) }
              }}
            >
              <title>{tr('Interactive normalized pair price chart')}</title>
              <desc>{tr('Hover or focus the chart; use arrow keys to inspect and click or tap to pin.')}</desc>
              {[4, 14, 24, 34, 44].map((y) => <line className="pair-grid-line" x1="0" x2="100" y1={y} y2={y} key={y} />)}
              {pausedX !== null && <rect className="pair-paused-region" x={pausedX} y="0" width={100 - pausedX} height="48" />}
              <path className="pair-price-line left" d={chartPath(points, (item) => item.leftNormalized, chartLow, chartHigh)} />
              <path className="pair-price-line right" d={chartPath(points, (item) => item.rightNormalized, chartLow, chartHigh)} />
              {inspectedPricePoint && <>
                <line className="pair-hover-guide" x1={inspectedPriceX} x2={inspectedPriceX} y1="4" y2="44" />
                <circle className="pair-hover-marker left" cx={inspectedPriceX} cy={priceY(inspectedPricePoint.leftNormalized)} r="0.9" />
                <circle className="pair-hover-marker right" cx={inspectedPriceX} cy={priceY(inspectedPricePoint.rightNormalized)} r="0.9" />
              </>}
              <rect className="pair-chart-hit" x="0" y="0" width="100" height="48" />
            </svg>
            {showPriceTooltip && inspectedPricePoint && <div className="pair-price-tooltip" role="tooltip" style={{ left: `clamp(8px, ${inspectedPriceX}%, calc(100% - 224px))` }}>
              <strong>{formatTimestamp(inspectedPricePoint.timestamp).time}</strong>
              <span><i className="left" />{leftSymbol}<code>{inspectedPricePoint.leftClose.toFixed(2)}</code><small>{inspectedPricePoint.leftNormalized.toFixed(2)} · {normalizedChange(inspectedPricePoint.leftNormalized)}</small></span>
              <span><i className="right" />{rightSymbol}<code>{inspectedPricePoint.rightClose.toFixed(2)}</code><small>{inspectedPricePoint.rightNormalized.toFixed(2)} · {normalizedChange(inspectedPricePoint.rightNormalized)}</small></span>
            </div>}
          </div>
          <div className="pair-chart-legend"><span><i className="left" />{leftSymbol}</span><span><i className="right" />{rightSymbol}</span>{pausedX !== null && <span><i className="paused" />{tr('Paused region')}</span>}</div>
          {inspectedPricePoint && <div className="pair-price-inspection" data-testid="pair-price-inspection">
            <div><span>{tr('Market time')}</span><strong>{formatTimestamp(inspectedPricePoint.timestamp).time}</strong><small>{safePinnedPriceIndex === null ? tr('Current inspection') : tr('Pinned inspection')}</small></div>
            <div><span>{leftSymbol} · {tr('Actual close')}</span><strong>{inspectedPricePoint.leftClose.toFixed(2)}</strong><small>{tr('Normalized')} {inspectedPricePoint.leftNormalized.toFixed(2)} · Δ {normalizedChange(inspectedPricePoint.leftNormalized)}</small></div>
            <div><span>{rightSymbol} · {tr('Actual close')}</span><strong>{inspectedPricePoint.rightClose.toFixed(2)}</strong><small>{tr('Normalized')} {inspectedPricePoint.rightNormalized.toFixed(2)} · Δ {normalizedChange(inspectedPricePoint.rightNormalized)}</small></div>
          </div>}
          <footer><code>{formatTimestamp(points[0].timestamp).time}</code><span>{tr('Loaded market-time range')}</span><code>{latest ? formatTimestamp(latest.timestamp).time : '-'}</code></footer>
        </>}
      </article>
      <article className="pair-chart-card">
        <header><div><span>{tr('Evidence')}</span><strong>{tr('Spread / Z-score')}</strong></div><small>{tr('Entry threshold')} ±{entryZ.toFixed(2)}</small></header>
        {zValues.length === 0 ? <p className="pair-chart-empty">{tr('Z-score is unavailable until warm-up completes.')}</p> : <>
          <svg viewBox="0 0 100 48" preserveAspectRatio="none" role="img" aria-label={tr('Pair z-score chart')}>
            {pausedX !== null && <rect className="pair-paused-region" x={pausedX} y="0" width={100 - pausedX} height="48" />}
            <line className="pair-zero-line" x1="0" x2="100" y1={zY(0)} y2={zY(0)} />
            {[entryZ, -entryZ].map((value) => <line className="pair-entry-line" x1="0" x2="100" y1={zY(value)} y2={zY(value)} key={`entry-${value}`} />)}
            {[exitZ, -exitZ].map((value) => <line className="pair-exit-line" x1="0" x2="100" y1={zY(value)} y2={zY(value)} key={`exit-${value}`} />)}
            <path className="pair-zscore-line" d={chartPath(points, (item) => item.zscore, -zBound, zBound)} />
          </svg>
          <div className="pair-chart-legend"><span><i className="zscore" />Z-score</span><span><i className="entry" />{tr('Entry threshold')}</span><span><i className="exit" />{tr('Exit threshold')}</span></div>
          <footer><code>{latestSpread === null ? 'Spread —' : `Spread ${latestSpread.toFixed(4)}`}</code><span>{latestZscore === null ? 'Z —' : `Z ${latestZscore.toFixed(3)}`}</span><code>{tr('Last calculated')} {latestFeaturePoint ? formatTimestamp(latestFeaturePoint.timestamp).time : '—'}</code></footer>
        </>}
      </article>
    </div>
    <details className="evidence-calculation-details pair-calculation-details">
      <summary>{tr('Calculation details')}</summary>
      <p>{tr('Prices are rebased to 100 only for visual comparison. Strategy calculations continue to use actual closes.')}</p>
      <p><code>hedge = dot(B, A) / dot(B, B)</code> · <code>spread = A − hedge × B</code> · <code>z = (spread − mean) / σ</code></p>
      <div className="pair-calculation-table" role="table">
        <div className="header" role="row"><span>{tr('Market time')}</span><span>{leftSymbol}</span><span>{rightSymbol}</span><span>{tr('Normalized')}</span><span>{tr('Hedge ratio')}</span><span>Spread</span><span>Z-score</span></div>
        {detailRows.map((item) => <div role="row" key={item.eventId}><code>{formatTimestamp(item.timestamp).time}</code><code>{item.leftClose.toFixed(2)}</code><code>{item.rightClose.toFixed(2)}</code><code>{item.leftNormalized.toFixed(2)} / {item.rightNormalized.toFixed(2)}</code><code>{item.hedgeRatio?.toFixed(4) ?? '—'}</code><code>{item.spread?.toFixed(4) ?? '—'}</code><code>{item.zscore?.toFixed(3) ?? '—'}</code></div>)}
      </div>
      <p className="relationship-disclosure">{tr('This chart is diagnostic evidence only; it does not recommend a trade or an optimal pair.')}</p>
    </details>
  </section>
}

function SessionHistory({ sessions, activeId, onOpen }: { sessions: PaperSessionSnapshot[]; activeId: string | null; onOpen: (id: string) => void }) {
  const { tr } = useI18n()
  return <section className="workspace-panel live-history"><div className="section-heading"><h2>{tr('Recent Paper Sessions')}</h2><span>{sessions.length} {tr('retained locally')}</span></div>
    {sessions.length === 0 ? <p className="empty-state">{tr('No live paper sessions have been created.')}</p> : <div className="live-session-table" role="table">
      <div className="live-session-row header" role="row"><span>{tr('Account')}</span><span>{tr('Strategy')}</span><span>{tr('Status')}</span><span>{tr('Feed')}</span><span>{tr('Broker')}</span><span>{tr('Recovery')}</span><span>{tr('Last event')}</span><span>{tr('Equity')}</span><span>{tr('Open orders')}</span></div>
      {sessions.map((session) => <button className={`live-session-row ${activeId === session.session_id ? 'selected' : ''}`} key={session.session_id} onClick={() => onOpen(session.session_id)}>
        <code>{session.account_id}</code><span>{tr(session.strategy_name)}</span><span>{tr(session.status)}</span><span>{tr(session.feed_status)}</span><span>{tr(session.broker_status)}</span><span>{tr(session.recovery_status)}</span><code>{session.last_market_event ? formatTimestamp(session.last_market_event).time : '-'}</code><strong>{formatCurrency(session.account.equity, paperCurrency(session))}</strong><strong>{session.orders.filter((order) => !['FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'REPLACED', 'DONE_FOR_DAY'].includes(order.status)).length}</strong>
      </button>)}
    </div>}
  </section>
}

function friendlySetupError(message: string, tr: (value: string) => string) {
  if (/Strategy requires \d+ symbol\(s\); received \d+/i.test(message)) return tr('Choose every stock required by this strategy before continuing.')
  if (/Selected paper account is unavailable/i.test(message)) return tr('This paper account is no longer available. Choose another account.')
  if (/Paper account currency .* does not match/i.test(message)) return tr('This account uses a different currency. Create a new account for the selected market.')
  if (/credentials are not configured/i.test(message)) return tr('Connect Alpaca in My before creating a live paper session.')
  return tr('We could not create this paper session. Review the setup and try again.')
}

function LiveSetup({ definition, definitions, onDefinitionChange, onOpenProfile, providers, accounts, sessions, onAccountCreated, onCreated, onOpen }: { definition: StrategyDefinition; definitions: StrategyDefinition[]; onDefinitionChange?: (strategyId: string) => void; onOpenProfile?: () => void; providers: MarketDataProviderStatus[]; accounts: PaperAccount[]; sessions: PaperSessionSnapshot[]; onAccountCreated: (account: PaperAccount) => void; onCreated: (snapshot: PaperSessionSnapshot) => void; onOpen: (id: string) => void }) {
  const { tr } = useI18n()
  const strategyParameters = definition.parameters.filter((item) => !EXECUTION_PARAMETERS.has(item.key))
  const defaults = Object.fromEntries(strategyParameters.map((item) => [item.key, item.default_value]))
  const requiredSymbols = definition.data_requirements?.symbols ?? []
  const requiredCount = definition.data_requirements?.symbol_count ?? Math.max(requiredSymbols.length, 1)
  const inferredMarket: MarketRegion = requiredSymbols.some((symbol) => symbol.endsWith('.HK'))
    ? 'HK'
    : requiredSymbols.some((symbol) => symbol.endsWith('.SH') || symbol.endsWith('.SZ') || symbol.endsWith('.BJ') || /^\d{6}$/.test(symbol))
      ? 'CN'
      : requiredSymbols.length > 0
        ? 'US'
        : 'CN'
  const initialQuery = requiredSymbols[0] ?? (inferredMarket === 'CN' ? '600519' : inferredMarket === 'HK' ? '00700' : 'AAPL')
  const [marketRegion, setMarketRegion] = useState<MarketRegion>(inferredMarket)
  const [providerId, setProviderId] = useState<MarketProvider>('tdx')
  const provider = providers.find((item) => item.provider === providerId) ?? null
  const [stockQuery, setStockQuery] = useState(initialQuery)
  const [stockResults, setStockResults] = useState<StockSecurity[]>([])
  const [securities, setSecurities] = useState<StockSecurity[]>([])
  const [feed, setFeed] = useState<'iex' | 'sip'>('iex')
  const [initialCash, setInitialCash] = useState(1_000_000)
  const currency = marketRegion === 'CN' ? 'CNY' : marketRegion === 'HK' ? 'HKD' : 'USD'
  const eligibleAccounts = accounts.filter((item) => item.currency === currency)
  const [accountId, setAccountId] = useState('')
  const [accountName, setAccountName] = useState('My Paper Account')
  const [feeBps, setFeeBps] = useState(definition.parameters.find((item) => item.key === 'fee_bps')?.default_value ?? 5)
  const [slippageBps, setSlippageBps] = useState(definition.parameters.find((item) => item.key === 'slippage_bps')?.default_value ?? 5)
  const [parameters, setParameters] = useState<Record<string, number>>(defaults)
  const [searching, setSearching] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [technicalError, setTechnicalError] = useState<string | null>(null)
  const retainedAccountId = accountId === '__new__' || eligibleAccounts.some((item) => item.account_id === accountId) ? accountId : ''
  const selectedAccountId = retainedAccountId || eligibleAccounts.find((item) => !item.active_session_id)?.account_id || '__new__'
  const selectedAccount = eligibleAccounts.find((item) => item.account_id === selectedAccountId)
  const hasRequiredSelection = securities.length === requiredCount && (requiredSymbols.length === 0 || requiredSymbols.every((symbol, index) => securities[index]?.symbol === symbol))
  const accountReady = selectedAccountId === '__new__' ? accountName.trim().length > 0 && initialCash > 0 : Boolean(selectedAccount)
  const providerReady = Boolean(provider?.configured && (provider.markets ?? ['US']).includes(marketRegion))
  const canCreate = Boolean(providerReady && accountReady && hasRequiredSelection && !creating)

  function switchMarket(next: MarketRegion) {
    setMarketRegion(next)
    if (next !== 'US') setProviderId('tdx')
    setSecurities([]); setStockResults([]); setStockQuery(next === 'CN' ? '600519' : next === 'HK' ? '00700' : 'AAPL'); setAccountId('')
  }

  async function findStocks() {
    if (!stockQuery.trim()) return
    setSearching(true); setError(null); setTechnicalError(null)
    try { setStockResults(await searchStocks(stockQuery, { provider: providerId, market: marketRegion })) }
    catch (reason) { setError(reason instanceof Error ? reason.message : tr('Stock search failed.')) }
    finally { setSearching(false) }
  }

  function chooseStock(item: StockSecurity) {
    if (securities.some((value) => value.symbol === item.symbol) || securities.length >= requiredCount) return
    setSecurities((current) => [...current, item]); setStockResults([]); setStockQuery('')
  }

  async function create() {
    if (creating || !providerReady) return
    setCreating(true); setError(null)
    try {
      const account = selectedAccountId !== '__new__'
        ? eligibleAccounts.find((item) => item.account_id === selectedAccountId)
        : await createPaperAccount(accountName, initialCash, currency)
      if (!account) throw new Error('Selected paper account is unavailable.')
      if (account.currency !== currency) throw new Error(`Paper account currency ${account.currency} does not match ${currency}`)
      if (selectedAccountId === '__new__') onAccountCreated(account)
      onCreated(await createPaperSession({
        account_id: account.account_id,
        strategy_id: definition.strategy_id,
        symbols: securities.map((item) => item.symbol),
        securities: securities.map(({ symbol, name, exchange, status }) => ({ symbol, name, exchange, status })),
        parameters,
        provider: providerId,
        feed: providerId === 'tdx' ? 'tdx' : feed,
        timeframe: '1Min',
        market_session: marketRegion === 'CN' ? 'CN_REGULAR' : marketRegion === 'HK' ? 'HK_REGULAR' : 'US_REGULAR',
        fee_bps: feeBps,
        slippage_bps: slippageBps,
        execution_mode: 'VQD_SIMULATED',
      }))
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : tr('Paper session creation failed.')
      setTechnicalError(detail); setError(friendlySetupError(detail, tr))
    }
    finally { setCreating(false) }
  }

  return <main className="forward-shell live-paper-shell">
    <header className="workspace-title paper-hero"><div><span className="eyebrow">{tr('REAL-MARKET PRACTICE')}</span><h1>{tr('Create a paper portfolio')}</h1><p>{tr('Use current market data while VQD keeps every cash balance, position, order, and fill virtual and local.')}</p></div><span className={`connection-pill ${providerReady ? 'connected' : 'disconnected'}`}><i />{providerId.toUpperCase()} · {tr(providerReady ? 'Market data ready' : 'Market data unavailable')}</span></header>
    {!providerReady && providerId === 'tdx' && <section className="paper-connection-callout"><div><strong>{tr('TDX market data is unavailable')}</strong><span>{tr(provider?.note ?? 'Install the backend dependencies to enable easy-tdx.')}</span></div><code>pip install -e backend</code></section>}
    {!providerReady && providerId === 'alpaca' && <section className="paper-connection-callout"><div><strong>{tr('Connect Alpaca to continue')}</strong><span>{tr('Alpaca is optional and used only for US market data in this setup.')}</span></div><button type="button" onClick={onOpenProfile}>{tr('Open My settings')}</button></section>}
    <section className="workspace-panel paper-builder">
      <ol className="paper-progress" aria-label={tr('Setup progress')}><li className="complete"><span>1</span>{tr('Account & strategy')}</li><li className={hasRequiredSelection ? 'complete' : 'active'}><span>2</span>{tr('Choose stocks')}</li><li className={canCreate ? 'active' : ''}><span>3</span>{tr('Review & create')}</li></ol>

      <section className="paper-step"><div className="paper-step-heading"><span>1</span><div><h2>{tr('Market, account and strategy')}</h2><p>{tr('Choose one market. TDX is the default no-key data source; Alpaca remains optional for US data.')}</p></div></div>
        <div className="paper-choice-grid"><label>{tr('Market')}<select aria-label={tr('Market')} value={marketRegion} onChange={(event) => switchMarket(event.target.value as MarketRegion)}><option value="CN">{tr('China A-shares')}</option><option value="HK">{tr('Hong Kong')}</option><option value="US">{tr('United States')}</option></select></label><label>{tr('Market data provider')}<select aria-label={tr('Market data provider')} value={providerId} onChange={(event) => { setProviderId(event.target.value as MarketProvider); setSecurities([]); setStockResults([]) }}><option value="tdx">TDX · {tr('No API key')}</option>{marketRegion === 'US' && <option value="alpaca">Alpaca</option>}</select></label>{providerId === 'alpaca' && <label>{tr('Market feed')}<select value={feed} onChange={(event) => setFeed(event.target.value as 'iex' | 'sip')}><option value="iex">IEX</option><option value="sip">SIP</option></select></label>}<label>{tr('Paper Account')}<select aria-label={tr('Paper Account')} value={selectedAccountId} onChange={(event) => setAccountId(event.target.value)}><option value="__new__">{tr('Create a new account')}</option>{eligibleAccounts.map((account) => <option key={account.account_id} value={account.account_id} disabled={Boolean(account.active_session_id)}>{account.name} · {account.currency} {account.equity.toFixed(2)}{account.active_session_id ? ` · ${tr('In use')}` : ''}</option>)}</select><small>{selectedAccount ? `${tr('Available equity')}: ${selectedAccount.currency} ${selectedAccount.equity.toFixed(2)}` : `${tr('A separate virtual balance keeps this test easy to review.')} · ${currency}`}</small></label><label>{tr('Strategy')}<select aria-label={tr('Strategy')} value={definition.strategy_id} onChange={(event) => onDefinitionChange?.(event.target.value)}>{definitions.filter((item) => !item.historical_research_only).map((item) => <option key={item.strategy_id} value={item.strategy_id}>{tr(item.name)}</option>)}</select><small>{tr(definition.description)}</small></label>
          {selectedAccountId === '__new__' && <><label>{tr('Account name')}<input value={accountName} onChange={(event) => setAccountName(event.target.value)} /></label><label>{tr('Starting virtual cash')}<input type="number" min="1" value={initialCash} onChange={(event) => setInitialCash(Number(event.target.value))} /><small>{currency}</small></label></>}
        </div>
      </section>

      <section className="paper-step"><div className="paper-step-heading"><span>2</span><div><h2>{tr('Choose stocks')}</h2><p>{requiredCount === 1 ? tr('This strategy needs one stock.') : tr('This strategy needs {count} stocks. Selection order defines their strategy roles.').replace('{count}', String(requiredCount))}</p></div><strong className={hasRequiredSelection ? 'selection-count ready' : 'selection-count'}>{securities.length} / {requiredCount}</strong></div>
        <div className="security-slots">{Array.from({ length: requiredCount }, (_, index) => { const security = securities[index]; const slotLabel = requiredCount === 1 ? 'Selected stock' : index === 0 ? 'First stock' : index === 1 ? 'Second stock' : 'Next stock'; return <div className={`security-slot ${security ? 'filled' : ''}`} key={index}><span className="slot-index">{index + 1}</span>{security ? <><div><strong>{security.symbol}</strong><span>{security.name}</span><small>{security.exchange} · {security.currency ?? currency} · {tr('Lot')} {security.lot_size ?? 1}</small></div><button type="button" aria-label={`${tr('Remove')} ${security.symbol}`} onClick={() => setSecurities((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></> : <div><strong>{tr(slotLabel)}</strong><span>{tr('Search by exchange symbol or code below.')}</span></div>}</div> })}</div>
        <form className="paper-stock-search" onSubmit={(event) => { event.preventDefault(); void findStocks() }}><label htmlFor="paper-stock-query">{tr('Find a stock')}</label><div className="input-action"><input id="paper-stock-query" value={stockQuery} onChange={(event) => setStockQuery(event.target.value)} placeholder={marketRegion === 'CN' ? '600519' : marketRegion === 'HK' ? '00700' : 'AAPL'} /><button type="submit" disabled={searching || !stockQuery.trim()}>{searching ? tr('Searching…') : tr('Search')}</button></div><small>{tr('TDX zero-key search currently uses exact security codes; company-name search remains available through Alpaca for US equities.')}</small></form>
        {stockResults.length > 0 && <div className="paper-stock-results" role="listbox" aria-label={tr('Stock search results')}>{stockResults.map((item) => { const added = securities.some((value) => value.symbol === item.symbol); const blocked = added || securities.length >= requiredCount; return <button type="button" key={item.symbol} disabled={blocked} onClick={() => chooseStock(item)}><span className="stock-mark">{item.symbol.slice(0, 1)}</span><span><strong>{item.symbol}</strong><small>{item.name}</small></span><code>{added ? tr('Added') : item.exchange}</code></button> })}</div>}
      </section>

      <section className="paper-step paper-options"><div className="paper-step-heading"><span>3</span><div><h2>{tr('Review and create')}</h2><p>{tr('No broker account is used. VQD owns the virtual cash ledger and simulated fills.')}</p></div></div>
        <div className="execution-mode-picker"><div className="selected"><span className="execution-mode-icon" aria-hidden="true">VQ</span><span><strong>{tr('VQD local paper execution')}</strong><small>{tr('Strategy decisions use received one-minute bars; fills remain inside VQD and never reach a broker.')}</small></span><em>{tr('Virtual money only')}</em></div></div>
        <div className="paper-review"><div><span>{tr('Account')}</span><strong>{selectedAccount?.name ?? accountName}</strong></div><div><span>{tr('Market')}</span><strong>{marketRegion}</strong></div><div><span>{tr('Provider')}</span><strong>{providerId.toUpperCase()}</strong></div><div><span>{tr('Strategy')}</span><strong>{tr(definition.name)}</strong></div><div><span>{tr('Stocks')}</span><strong>{securities.length ? securities.map((item) => item.symbol).join(' + ') : tr('Not selected')}</strong></div><div><span>{tr('Execution')}</span><strong>{tr('VQD local paper execution')}</strong></div></div>
        {strategyParameters.length > 0 && <details className="paper-disclosure"><summary><span><strong>{tr('Strategy settings')}</strong><small>{tr('Using recommended defaults')}</small></span><i /></summary><div className="paper-parameter-grid">{strategyParameters.map((parameter) => <label key={parameter.key}><span>{tr(parameter.label)} <small>{tr(parameter.unit)}</small></span><input type="number" value={parameters[parameter.key]} min={parameter.minimum} max={parameter.maximum ?? undefined} step={parameter.step} onChange={(event) => setParameters((current) => ({ ...current, [parameter.key]: Number(event.target.value) }))} /><small>{tr(parameter.description)}</small></label>)}</div></details>}
        <details className="paper-disclosure"><summary><span><strong>{tr('Advanced execution settings')}</strong><small>{tr('Local next-bar model')}</small></span><i /></summary><div className="paper-choice-grid"><label>{tr('Reference fee / slippage (bps)')}<div className="paired-input"><input aria-label={tr('Fee bps')} type="number" min="0" value={feeBps} onChange={(event) => setFeeBps(Number(event.target.value))} /><input aria-label={tr('Slippage bps')} type="number" min="0" value={slippageBps} onChange={(event) => setSlippageBps(Number(event.target.value))} /></div></label></div><p>{tr('VQD records the market-data provider separately from the virtual account so another provider can be substituted without changing paper balances.')}</p></details>
      </section>

      {error && <div className="paper-error" role="alert"><strong>{error}</strong><span>{tr('Your selections have been kept.')}</span>{technicalError && <details><summary>{tr('Technical details')}</summary><code>{technicalError}</code></details>}</div>}
      <footer className="paper-create-bar"><div><strong>{canCreate ? tr('Ready to create') : tr('Complete the steps above')}</strong><span>{!providerReady ? tr('Market data connection is required.') : !accountReady ? tr('Finish the account details.') : !hasRequiredSelection ? tr('Choose every required stock.') : tr('You can start the strategy after the portfolio is created.')}</span></div><button className="primary-button" disabled={!canCreate} onClick={() => void create()}>{creating ? tr('Creating…') : tr('Create paper portfolio')}</button></footer>
    </section>
    <SessionHistory sessions={sessions} activeId={null} onOpen={onOpen} />
  </main>
}

function LiveTraceInspector({ snapshot, trace }: { snapshot: PaperSessionSnapshot; trace: PaperTrace }) {
  const { tr } = useI18n()
  const [selectedEventId, setSelectedEventId] = useState(trace.timeline.at(-1)?.event_id ?? '')
  const index = useMemo(() => createReplayIndex({ trace_version: '1.0', metadata: { dataset_id: `live:${snapshot.provider}:${snapshot.feed}`, dataset_name: `${snapshot.provider} ${snapshot.feed}`, bar_count: trace.timeline.length, data_start: trace.timeline[0]?.timestamp ?? new Date(0).toISOString(), data_end: trace.timeline.at(-1)?.timestamp ?? new Date(0).toISOString(), execution_model: snapshot.execution_mode === 'ALPACA_PAPER' ? 'Alpaca Paper broker execution' : 'signal at close(t); execute at close(t+1)' }, strategy: { strategy_id: snapshot.strategy_id, name: snapshot.strategy_name }, parameters: trace.parameters, timeline: trace.timeline, trades: [], metrics: {}, diagnostics: trace.diagnostics }), [snapshot, trace])
  const effectiveId = index.eventById.has(selectedEventId) ? selectedEventId : trace.timeline.at(-1)?.event_id ?? ''
  const event = index.eventById.get(effectiveId) ?? null
  const features = useMemo(() => Array.from(index.featureById.values()), [index])
  const rootFeatureId = event?.signal_evaluation.dependencies[0] ?? event?.feature_snapshots[0]?.feature_id ?? null
  const selectedFeature = rootFeatureId ? index.featureById.get(rootFeatureId) ?? null : null
  if (!event) return <section className="workspace-panel"><div className="section-heading"><h2>{tr('Live Trace / Lineage')}</h2><span>0 {tr('evaluated events')}</span></div><p className="empty-state">{tr('Only received bars will appear. No future market timeline is preloaded.')}</p></section>
  return <>
    <section className="workspace-panel"><div className="section-heading"><h2>{tr('Live Trace / Lineage')}</h2><span>{trace.timeline.length} {tr('immutable events')}</span></div><ReplayTimeline events={trace.timeline} selectedEventId={effectiveId} onSelect={setSelectedEventId} /></section>
    <div className="inspector-grid"><MarketPositionPanel event={event} /><StrategyDecisionPanel event={event} /></div>
    <section className="workspace-panel"><SignalLineage evaluation={event.signal_evaluation} allFeatures={features} rootFeatureId={rootFeatureId} selectedFeature={selectedFeature} onSelectFeature={() => undefined} /></section>
    <ExecutionOutcomePanel event={event} index={index} sourceSignalEvent={findSourceSignalEvent(event, index)} executionEvent={null} onSelect={setSelectedEventId} />
  </>
}

function PaperOperationsPanels({ health, operations, recovery, recovering, onRecover, onStop }: { health: PaperOperationalHealth | null; operations: PaperOperationEvent[]; recovery: PaperRecoveryReport | null; recovering: boolean; onRecover: () => void; onStop: () => void }) {
  const { tr } = useI18n()
  const divergence = recovery?.status === 'RECOVERY_DIVERGENCE'
  return <>
    <section className="workspace-panel paper-health-panel" id="paper-health"><div className="section-heading"><h2>{tr('Health')}</h2><span>{health ? tr(health.status) : tr('Loading…')}</span></div>
      {!health ? <p className="empty-state">{tr('Health data is loading.')}</p> : <div className="market-data-grid paper-health-grid">
        <div><span>{tr('Feed')}</span><strong>{tr(health.feed_status)}</strong></div><div><span>{tr('Broker')}</span><strong>{tr(health.broker_status)}</strong></div><div><span>{tr('Recovery')}</span><strong>{tr(health.recovery_status)}</strong></div>
        <div><span>{tr('Last received')}</span><code>{health.last_received_at ? formatTimestamp(health.last_received_at).time : '-'}</code></div><div><span>{tr('Observed delivery latency')}</span><code>{health.last_latency_ms === null ? '-' : `${health.last_latency_ms.toFixed(0)} ms`}</code></div><div><span>{tr('Stale for')}</span><code>{health.last_received_at ? `${Math.round(health.stale_seconds)} s` : '-'}</code></div>
        <div><span>{tr('Reconnects')}</span><strong>{health.reconnect_count}</strong></div><div><span>{tr('Backfills')}</span><strong>{health.backfill_count} · {health.backfilled_bar_count} {tr('bars')}</strong></div><div><span>{tr('Open / partial orders')}</span><strong>{health.open_order_count} / {health.partially_filled_order_count}</strong></div>
        {health.broker_account_status && <><div><span>{tr('Broker account')}</span><strong>{health.broker_account_status}</strong></div><div><span>{tr('Broker equity')}</span><strong>{health.broker_equity === null ? '-' : formatCurrency(health.broker_equity)}</strong></div><div><span>{tr('Rejected orders')}</span><strong>{health.rejected_order_count}</strong></div></>}
      </div>}
    </section>
    <section className="workspace-panel paper-operation-panel" id="paper-operations"><div className="section-heading"><h2>{tr('Operations')}</h2><span>{operations.length} {tr('events')}</span></div>
      {operations.length === 0 ? <p className="empty-state">{tr('No operational events have been recorded.')}</p> : <div className="paper-operation-list">{operations.slice().reverse().map((operation) => <article key={operation.operation_id}><code>#{operation.sequence}</code><strong>{tr(operation.operation_type)}</strong><span>{tr(operation.message)}</span><time>{formatTimestamp(operation.occurred_at).time}</time></article>)}</div>}
    </section>
    <section className={`workspace-panel paper-recovery-panel ${divergence ? 'divergence' : ''}`} id="paper-recovery"><div className="section-heading"><h2>{tr('Recovery')}</h2><span>{recovery ? tr(recovery.status) : tr('Loading…')}</span></div>
      {!recovery ? <p className="empty-state">{tr('Recovery data is loading.')}</p> : <>
        {divergence && <div className="recovery-divergence" role="alert"><strong>{tr('Session was not resumed automatically.')}</strong><span>{tr('Recovered runtime state does not match the persisted checkpoint.')}</span></div>}
        <div className="market-data-grid recovery-report-grid"><div><span>{tr('Journal events')}</span><strong>{recovery.journal_event_count}</strong></div><div><span>{tr('Broker events')}</span><strong>{recovery.broker_event_count}</strong></div><div><span>{tr('Account reconciled')}</span><strong>{tr(recovery.account_reconciled ? 'YES' : 'NO')}</strong></div><div><span>{tr('Broker reconciled')}</span><strong>{tr(recovery.broker_reconciled ? 'YES' : 'NO')}</strong></div><div><span>{tr('Portfolio checkpoint')}</span><strong>{tr(recovery.recorded_portfolio_hash === recovery.recovered_portfolio_hash ? 'MATCH' : 'MISMATCH')}</strong></div><div><span>{tr('Trace checkpoint')}</span><strong>{tr(recovery.recorded_trace_hash === recovery.recovered_trace_hash ? 'MATCH' : 'MISMATCH')}</strong></div></div>
        {recovery.warnings.map((warning) => <p className="inline-warning" key={warning}>{tr(warning)}</p>)}
        {divergence && <div className="recovery-actions"><button type="button" disabled={recovering} onClick={onRecover}>{recovering ? tr('Recovering…') : tr('Retry recovery')}</button><button type="button" className="ghost-button" onClick={onStop}>{tr('Stop Session')}</button></div>}
      </>}
    </section>
  </>
}

function LiveWorkspace({ snapshot, trace, sessions, onSnapshot, onOpen, onNew }: { snapshot: PaperSessionSnapshot; trace: PaperTrace; sessions: PaperSessionSnapshot[]; onSnapshot: (snapshot: PaperSessionSnapshot) => void; onOpen: (id: string) => void; onNew: () => void }) {
  const { tr } = useI18n()
  const currency = paperCurrency(snapshot)
  const [error, setError] = useState<string | null>(null)
  const [operationalError, setOperationalError] = useState<string | null>(null)
  const [health, setHealth] = useState<PaperOperationalHealth | null>(null)
  const [operations, setOperations] = useState<PaperOperationEvent[]>([])
  const [recovery, setRecovery] = useState<PaperRecoveryReport | null>(null)
  const [recovering, setRecovering] = useState(false)
  const [cancellingOrderId, setCancellingOrderId] = useState<string | null>(null)
  const [streamReconnecting, setStreamReconnecting] = useState(false)
  const refreshOperations = useCallback(async () => {
    try {
      const [nextHealth, nextOperations, nextRecovery] = await Promise.all([getPaperHealth(snapshot.session_id), getPaperOperations(snapshot.session_id), getPaperRecovery(snapshot.session_id)])
      setHealth(nextHealth); setOperations(nextOperations); setRecovery(nextRecovery); setOperationalError(null)
    } catch (reason) { setOperationalError(reason instanceof Error ? reason.message : tr('Operational data failed.')) }
  }, [snapshot.session_id, tr])
  useEffect(() => {
    const initial = window.setTimeout(() => void refreshOperations(), 0)
    const interval = window.setInterval(() => void refreshOperations(), 30_000)
    return () => { window.clearTimeout(initial); window.clearInterval(interval) }
  }, [refreshOperations])
  useEffect(() => {
    const merge = (next: PaperSessionSnapshot) => onSnapshot(next)
    if (typeof EventSource === 'undefined') {
      const timer = window.setInterval(() => void getPaperSession(snapshot.session_id).then((next) => { setStreamReconnecting(false); merge(next) }).catch(() => setStreamReconnecting(true)), 5_000)
      return () => window.clearInterval(timer)
    }
    const source = new EventSource(`/api/paper-sessions/${encodeURIComponent(snapshot.session_id)}/events`)
    source.onopen = () => setStreamReconnecting(false)
    source.addEventListener('snapshot', (message) => { try { const value: unknown = JSON.parse((message as MessageEvent<string>).data); if (isPaperSession(value)) { setStreamReconnecting(false); merge(value) } } catch { setError(tr('Live session stream returned malformed data.')) } })
    source.onerror = () => setStreamReconnecting(true)
    return () => source.close()
  }, [onSnapshot, snapshot.session_id, tr])

  async function action(kind: 'start' | 'pause' | 'resume' | 'stop') { try { onSnapshot(await transitionPaperSession(snapshot.session_id, kind)); setError(null) } catch (reason) { setError(reason instanceof Error ? reason.message : tr('Session command failed.')) } }
  async function recover() {
    setRecovering(true)
    try { setRecovery(await recoverPaperSession(snapshot.session_id)); onSnapshot(await getPaperSession(snapshot.session_id)); await refreshOperations(); setError(null) }
    catch (reason) { setError(reason instanceof Error ? reason.message : tr('Recovery command failed.')) }
    finally { setRecovering(false) }
  }
  async function cancelOrder(orderId: string) {
    setCancellingOrderId(orderId)
    try { onSnapshot(await cancelPaperOrder(snapshot.session_id, orderId)); setError(null) }
    catch (reason) { setError(reason instanceof Error ? reason.message : tr('Order cancellation failed.')) }
    finally { setCancellingOrderId(null) }
  }
  const latestMarket = snapshot.recent_market_events.at(-1)
  const terminalStatuses = new Set(['FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'REPLACED'])
  const openOrders = snapshot.orders.filter((order) => !terminalStatuses.has(order.status))
  const brokerMode = snapshot.execution_mode === 'ALPACA_PAPER'
  const marketLabel = (snapshot.securities?.length ? snapshot.securities.map((item) => `${item.symbol} · ${item.name}`) : snapshot.symbols).join(' / ')
  return <main className="forward-shell live-paper-shell">
    <header className="workspace-title forward-title"><div><h1>{tr('Paper Trading')}</h1><span>{tr('Persistent paper account')}</span></div><div className="live-status-stack"><span className={`status-badge ${snapshot.status.toLowerCase()}`}>{tr(snapshot.status)}</span><span className={`status-badge ${snapshot.feed_status.toLowerCase()}`}>{tr(snapshot.feed_status)}</span></div></header>
    <div className={`live-safety-strip sticky ${brokerMode ? 'broker' : ''}`}><strong>{tr('REAL MARKET DATA')}</strong><span>{tr(brokerMode ? 'ALPACA PAPER BROKER' : 'VQD SIMULATED EXECUTION')}</span><span>{tr(brokerMode ? 'NO REAL MONEY' : 'NO BROKER ORDER')}</span></div>
    <div className="live-context-strip"><strong>{tr('Account')} <code>{snapshot.account_id}</code></strong><span>{tr('Strategy')} · {tr(snapshot.strategy_name)}</span><span>{tr('Market')} · {marketLabel}</span><span>{tr('Feed')} · {snapshot.provider.toUpperCase()} {snapshot.feed.toUpperCase()}</span></div>
    <div className="toolbar forward-controls">{snapshot.status === 'CREATED' && <button className="primary-button" onClick={() => void action('start')}>{tr(brokerMode ? 'Start and allow Paper orders' : 'Start')}</button>}{snapshot.status === 'RUNNING' && <button onClick={() => void action('pause')}>{tr('Pause strategy')}</button>}{snapshot.status === 'PAUSED' && <button className="primary-button" onClick={() => void action('resume')}>{tr('Resume strategy')}</button>}{['CREATED', 'RUNNING', 'PAUSED', 'ERROR'].includes(snapshot.status) && <button className="ghost-button" onClick={() => void action('stop')}>{tr('Stop')}</button>}<button className="ghost-button" onClick={onNew}>{tr('New session')}</button><span className="toolbar-spacer" /><code>{brokerMode ? `${tr('Broker')} · ${tr(snapshot.broker_status)}` : tr('market ingestion continues while PAUSED')}</code></div>
    {error && <p className="inline-warning">{error}</p>}{streamReconnecting && <p className="inline-warning">{tr('Live update channel is reconnecting; the backend session continues independently.')}</p>}{operationalError && <p className="inline-warning">{operationalError}</p>}{snapshot.error_message && <p className="inline-error"><strong>{snapshot.error_code}</strong> · {snapshot.error_message}</p>}
    {snapshot.research_run_id && <p className="inline-success"><strong>{tr('Research evidence saved')}</strong> · <a href={`/runs/${snapshot.research_run_id}`}>{snapshot.research_run_id}</a></p>}
    <section className="workspace-panel paper-overview-panel" id="paper-overview"><div className="section-heading"><h2>{tr('Overview')}</h2><span>{tr('Backend recorded')}</span></div>{brokerMode && snapshot.broker_account && <div className="broker-balance-strip"><div><span>{tr('Alpaca Paper status')}</span><strong>{snapshot.broker_account.status}</strong></div><div><span>{tr('Paper cash')}</span><strong>{formatCurrency(snapshot.broker_account.cash)}</strong></div><div><span>{tr('Paper equity')}</span><strong>{formatCurrency(snapshot.broker_account.equity)}</strong></div><div><span>{tr('Paper buying power')}</span><strong>{formatCurrency(snapshot.broker_account.buying_power)}</strong></div></div>}<div className="metric-strip"><div><span>{tr(brokerMode ? 'VQD Trace cash' : 'Cash')}</span><strong>{formatCurrency(snapshot.account.cash, currency)}</strong></div><div><span>{tr(brokerMode ? 'VQD Trace equity' : 'Equity')}</span><strong>{formatCurrency(snapshot.account.equity, currency)}</strong></div><div><span>{tr('Net P&L')}</span><strong>{formatCurrency(snapshot.account.net_pnl, currency)}</strong></div><div><span>{tr('Fees')}</span><strong>{formatCurrency(snapshot.account.cumulative_fees, currency)}</strong></div><div><span>{tr('Slippage')}</span><strong>{formatCurrency(snapshot.account.cumulative_slippage, currency)}</strong></div><div><span>{tr('Max drawdown')}</span><strong>{formatPercent(snapshot.account.max_drawdown)}</strong></div></div></section>
    <section className="workspace-panel"><div className="section-heading"><h2>{tr('Live Equity Timeline')}</h2><span>{tr('received events only')}</span></div><EquityTimeline trace={trace} currency={currency} /></section>
    <PairStructurePanel key={snapshot.session_id} snapshot={snapshot} trace={trace} />
    <section className="workspace-panel market-data-inspector"><div className="section-heading"><h2>{tr('Market Data Inspector')}</h2><span>{snapshot.correction_count} {tr('corrections')}</span></div><div className="market-data-grid"><div><span>{tr('Provider / Feed')}</span><strong>{snapshot.provider.toUpperCase()} · {snapshot.feed.toUpperCase()}</strong></div><div><span>{tr('Connection')}</span><strong>{tr(snapshot.feed_status)}</strong></div><div><span>{tr('Last event')}</span><code>{latestMarket ? formatTimestamp(latestMarket.event_time).time : '-'}</code></div><div><span>{tr('Received at')}</span><code>{latestMarket ? formatTimestamp(latestMarket.received_at).time : '-'}</code></div><div><span>{tr('Market time')}</span><code>{snapshot.market_clock ? formatTimestamp(snapshot.market_clock.timestamp).time : '-'}</code></div><div><span>{tr('Observed delivery latency')}</span><code>{latestMarket ? `${latestMarket.latency_ms.toFixed(0)} ms` : '-'}</code></div></div></section>
    <PaperOperationsPanels health={health} operations={operations} recovery={recovery} recovering={recovering} onRecover={() => void recover()} onStop={() => void action('stop')} />
    {snapshot.recent_revisions.length > 0 && <section className="workspace-panel correction-ledger"><div className="section-heading"><h2>{tr('Market Data Revisions')}</h2><span>{tr('prior decisions remain immutable')}</span></div>{snapshot.recent_revisions.map((revision) => <div className="correction-row" key={`${revision.symbol}-${revision.event_time}-${revision.later_revision}`}><strong>{tr('MARKET DATA REVISED LATER')}</strong><code>{revision.symbol} · {formatTimestamp(revision.event_time).time}</code><span>{tr('Used by the original decision')}: {tr('revision')} {revision.used_revision}, close {revision.used_close.toFixed(2)}</span><span>{tr('Later revision')} {revision.later_revision}, close {revision.later_close.toFixed(2)} · {tr('available')} {formatTimestamp(revision.revision_available_at).time}</span></div>)}</section>}
    <div className="inspector-grid"><section className="workspace-panel"><div className="section-heading"><h2>{tr('Positions')}</h2><span>{Object.keys(snapshot.account.positions).length}</span></div>{Object.keys(snapshot.account.positions).length === 0 ? <p className="empty-state">{tr('No open positions.')}</p> : Object.entries(snapshot.account.positions).map(([symbol, quantity]) => <div className="position-line" key={symbol}><code>{symbol}</code><strong>{quantity.toFixed(4)}</strong></div>)}</section><section className="workspace-panel"><div className="section-heading"><h2>{tr('Open Orders')}</h2><span>{openOrders.length}</span></div>{openOrders.length === 0 ? <p className="empty-state">{tr('No open orders.')}</p> : <div className="broker-order-list">{openOrders.map((order) => <article className="broker-order" key={order.order_id}><div><strong>{order.symbol} · {tr(order.side)}</strong><span>{tr(order.status)}</span></div><div className="order-fill-progress"><span style={{ width: `${Math.min(100, (order.filled_quantity / order.quantity) * 100)}%` }} /></div><small>{tr('Filled')} {order.filled_quantity.toFixed(4)} / {order.quantity.toFixed(4)}</small>{brokerMode && !['PENDING_CANCEL', 'HELD', 'SUSPENDED'].includes(order.status) && <button type="button" disabled={cancellingOrderId === order.order_id} onClick={() => void cancelOrder(order.order_id)}>{cancellingOrderId === order.order_id ? tr('Cancelling…') : tr('Cancel order')}</button>}</article>)}</div>}</section></div>
    <section className="workspace-panel" id="paper-orders"><div className="section-heading"><h2>{tr('Orders')}</h2><span>{snapshot.orders.length} · {tr('Order Lifecycle')}</span></div>{snapshot.orders.length === 0 ? <p className="empty-state">{tr('Orders will appear after the strategy produces a target change.')}</p> : <div className="order-ledger" role="table"><div className="order-ledger-row header" role="row"><span>{tr('Order')}</span><span>{tr('Status')}</span><span>{tr('Filled')}</span><span>{tr('VQD reference')}</span><span>{tr('Broker average')}</span></div>{snapshot.orders.slice().reverse().map((order) => <div className="order-ledger-row" role="row" key={order.order_id}><span><strong>{order.symbol} · {tr(order.side)}</strong><small>{formatTimestamp(order.submitted_at).time}</small></span><span className={`order-status ${order.status.toLowerCase()}`}>{tr(order.status)}</span><code>{order.filled_quantity.toFixed(4)} / {order.quantity.toFixed(4)}</code><code>{order.reference_price?.toFixed(2) ?? '-'}</code><code>{order.average_fill_price?.toFixed(2) ?? '-'}</code>{order.rejection_reason && <small className="order-rejection">{order.rejection_reason}</small>}</div>)}</div>}</section>
    <section className="workspace-panel" id="paper-fills"><div className="section-heading"><h2>{tr('Fills')}</h2><span>{snapshot.fills.length}</span></div>{snapshot.fills.length === 0 ? <p className="empty-state">{tr(brokerMode ? 'Waiting for Alpaca Paper to report a fill.' : 'Executions fill locally at close(t+1).')}</p> : <div className="dense-table fill-comparison-table"><div className="dense-row header"><span>{tr('Time')}</span><span>{tr('Symbol / Side')}</span><span>{tr('Quantity')}</span><span>{tr('VQD reference')}</span><span>{tr('Fill price')}</span><span>{tr('Difference')}</span></div>{snapshot.fills.slice().reverse().map((execution) => <div className="dense-row" key={execution.fill_id}><code>{formatTimestamp(execution.executed_at).time}</code><span>{execution.symbol} · {tr(execution.side)}</span><code>{execution.quantity.toFixed(4)}</code><code>{execution.reference_price.toFixed(2)}</code><code>{execution.fill_price.toFixed(2)}</code><code className={execution.slippage > 0 ? 'negative' : ''}>{formatCurrency(execution.slippage, currency)}</code></div>)}</div>}</section>
    <LiveTraceInspector snapshot={snapshot} trace={trace} />
    <SessionHistory sessions={sessions} activeId={snapshot.session_id} onOpen={onOpen} />
  </main>
}

export default function LivePaperPage({ definition, definitions = [definition], onDefinitionChange, onOpenProfile }: { definition: StrategyDefinition; definitions?: StrategyDefinition[]; onDefinitionChange?: (strategyId: string) => void; onOpenProfile?: () => void }) {
  const { tr } = useI18n()
  const [providers, setProviders] = useState<MarketDataProviderStatus[]>([])
  const [sessions, setSessions] = useState<PaperSessionSnapshot[]>([])
  const [accounts, setAccounts] = useState<PaperAccount[]>([])
  const [snapshot, setSnapshot] = useState<PaperSessionSnapshot | null>(null)
  const [trace, setTrace] = useState<PaperTrace | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refreshList = useCallback(async () => { try { setSessions(await listPaperSessions()) } catch (reason) { setError(reason instanceof Error ? reason.message : tr('Session history failed.')) } }, [tr])
  useEffect(() => {
    const timer = window.setTimeout(() => { void Promise.all([getMarketDataProviders().then(setProviders), listPaperAccounts().then(setAccounts), refreshList()]).catch((reason) => setError(reason instanceof Error ? reason.message : tr('Live paper setup failed.'))) }, 0)
    return () => window.clearTimeout(timer)
  }, [refreshList, tr])
  const open = useCallback(async (id: string) => { try { const [next, nextTrace] = await Promise.all([getPaperSession(id), getPaperTrace(id)]); setSnapshot(next); setTrace(nextTrace); setError(null) } catch (reason) { setError(reason instanceof Error ? reason.message : tr('Paper session failed.')) } }, [tr])
  const updateSnapshot = useCallback((next: PaperSessionSnapshot) => {
    setSnapshot(next)
    setSessions((current) => [next, ...current.filter((item) => item.session_id !== next.session_id)])
    setTrace((current) => {
      if (!current || current.session_id !== next.session_id) return current
      const latest = next.latest_event
      const missingLatest = latest && !current.timeline.some((event) => event.event_id === latest.event_id)
      if (missingLatest) {
        const sequence = Number(latest.event_id.match(/(\d+)$/)?.[1] ?? 0)
        const previousSequence = Number(current.timeline.at(-1)?.event_id.match(/(\d+)$/)?.[1] ?? 0)
        if (sequence !== previousSequence + 1) {
          void getPaperTrace(next.session_id).then(setTrace).catch(() => undefined)
        } else {
          current = { ...current, timeline: [...current.timeline, latest] }
        }
      }
      return { ...current, market_revisions: [...current.market_revisions, ...next.recent_revisions.filter((revision) => !current.market_revisions.some((item) => item.symbol === revision.symbol && item.event_time === revision.event_time && item.later_revision === revision.later_revision))] }
    })
  }, [])
  if (error && !snapshot) return <main className="forward-shell"><section className="compact-error" role="alert"><strong>{tr('Live Paper unavailable')}</strong><span>{error}</span><button onClick={() => void refreshList()}>{tr('Retry')}</button></section></main>
  if (!snapshot || !trace) return <LiveSetup key={definition.strategy_id} definition={definition} definitions={definitions} onDefinitionChange={onDefinitionChange} onOpenProfile={onOpenProfile} providers={providers} accounts={accounts} sessions={sessions} onAccountCreated={(account) => setAccounts((current) => [account, ...current])} onCreated={(created) => { updateSnapshot(created); setTrace({ trace_version: '1.0', session_id: created.session_id, strategy_id: created.strategy_id, parameters: created.parameters, timeline: [], diagnostics: [], market_revisions: [], execution_mode: created.execution_mode, broker_events: [] }) }} onOpen={(id) => void open(id)} />
  return <LiveWorkspace snapshot={snapshot} trace={trace} sessions={sessions} onSnapshot={updateSnapshot} onOpen={(id) => void open(id)} onNew={() => { setSnapshot(null); setTrace(null); void refreshList() }} />
}
