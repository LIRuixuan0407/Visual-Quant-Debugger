import { useCallback, useEffect, useMemo, useState } from 'react'

import { cancelPaperOrder, createPaperAccount, createPaperSession, getMarketDataProviders, getPaperHealth, getPaperOperations, getPaperRecovery, getPaperSession, getPaperTrace, isPaperSession, listPaperAccounts, listPaperSessions, recoverPaperSession, transitionPaperSession } from '../../api/paper'
import { searchStocks } from '../../api/marketData'
import { useI18n } from '../../i18n/I18nProvider'
import type { MarketDataProviderStatus, PaperAccount, PaperExecutionMode, PaperOperationalHealth, PaperOperationEvent, PaperRecoveryReport, PaperSessionSnapshot, PaperTrace } from '../../types/paper'
import type { StockSecurity } from '../../types/dataset'
import type { StrategyDefinition } from '../../types/strategy'
import ReplayTimeline from '../replay/ReplayTimeline'
import { ExecutionOutcomePanel, MarketPositionPanel, StrategyDecisionPanel } from '../replay/ReplayInspectors'
import SignalLineage from '../replay/SignalLineage'
import { formatCurrency, formatPercent, formatTimestamp } from '../replay/utils/format'
import { createReplayIndex, findSourceSignalEvent } from '../replay/utils/navigation'

const EXECUTION_PARAMETERS = new Set(['initial_cash', 'fee_bps', 'slippage_bps', 'gross_target'])

function EquityTimeline({ trace }: { trace: PaperTrace }) {
  const { tr } = useI18n()
  const values = trace.timeline.map((event) => event.pnl_snapshot.equity)
  if (values.length < 2) return <p className="empty-state">{tr('Equity appears after received one-minute bars are evaluated.')}</p>
  const low = Math.min(...values); const high = Math.max(...values); const span = high - low || 1
  const points = values.map((value, index) => `${(index / (values.length - 1)) * 100},${36 - ((value - low) / span) * 32}`).join(' ')
  return <div className="live-equity-chart"><svg viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label={tr('Live paper equity timeline')}><polyline points={points} /></svg><div><code>{formatCurrency(low)}</code><code>{formatCurrency(high)}</code></div></div>
}

function SessionHistory({ sessions, activeId, onOpen }: { sessions: PaperSessionSnapshot[]; activeId: string | null; onOpen: (id: string) => void }) {
  const { tr } = useI18n()
  return <section className="workspace-panel live-history"><div className="section-heading"><h2>{tr('Recent Paper Sessions')}</h2><span>{sessions.length} {tr('retained locally')}</span></div>
    {sessions.length === 0 ? <p className="empty-state">{tr('No live paper sessions have been created.')}</p> : <div className="live-session-table" role="table">
      <div className="live-session-row header" role="row"><span>{tr('Account')}</span><span>{tr('Strategy')}</span><span>{tr('Status')}</span><span>{tr('Feed')}</span><span>{tr('Broker')}</span><span>{tr('Recovery')}</span><span>{tr('Last event')}</span><span>{tr('Equity')}</span><span>{tr('Open orders')}</span></div>
      {sessions.map((session) => <button className={`live-session-row ${activeId === session.session_id ? 'selected' : ''}`} key={session.session_id} onClick={() => onOpen(session.session_id)}>
        <code>{session.account_id}</code><span>{tr(session.strategy_name)}</span><span>{tr(session.status)}</span><span>{tr(session.feed_status)}</span><span>{tr(session.broker_status)}</span><span>{tr(session.recovery_status)}</span><code>{session.last_market_event ? formatTimestamp(session.last_market_event).time : '-'}</code><strong>{formatCurrency(session.account.equity)}</strong><strong>{session.orders.filter((order) => !['FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'REPLACED', 'DONE_FOR_DAY'].includes(order.status)).length}</strong>
      </button>)}
    </div>}
  </section>
}

function friendlySetupError(message: string, tr: (value: string) => string) {
  if (/Strategy requires \d+ symbol\(s\); received \d+/i.test(message)) return tr('Choose every stock required by this strategy before continuing.')
  if (/Selected paper account is unavailable/i.test(message)) return tr('This paper account is no longer available. Choose another account.')
  if (/credentials are not configured/i.test(message)) return tr('Connect Alpaca in My before creating a live paper session.')
  return tr('We could not create this paper session. Review the setup and try again.')
}

function LiveSetup({ definition, definitions, onDefinitionChange, onOpenProfile, provider, accounts, sessions, onAccountCreated, onCreated, onOpen }: { definition: StrategyDefinition; definitions: StrategyDefinition[]; onDefinitionChange?: (strategyId: string) => void; onOpenProfile?: () => void; provider: MarketDataProviderStatus | null; accounts: PaperAccount[]; sessions: PaperSessionSnapshot[]; onAccountCreated: (account: PaperAccount) => void; onCreated: (snapshot: PaperSessionSnapshot) => void; onOpen: (id: string) => void }) {
  const { tr } = useI18n()
  const strategyParameters = definition.parameters.filter((item) => !EXECUTION_PARAMETERS.has(item.key))
  const defaults = Object.fromEntries(strategyParameters.map((item) => [item.key, item.default_value]))
  const requiredSymbols = definition.data_requirements?.symbols ?? []
  const requiredCount = definition.data_requirements?.symbol_count ?? Math.max(requiredSymbols.length, 1)
  const [stockQuery, setStockQuery] = useState('')
  const [stockResults, setStockResults] = useState<StockSecurity[]>([])
  const [securities, setSecurities] = useState<StockSecurity[]>([])
  const [feed, setFeed] = useState<'iex' | 'sip'>((provider?.selected_feed === 'sip' ? 'sip' : 'iex'))
  const [initialCash, setInitialCash] = useState(100_000)
  const [accountId, setAccountId] = useState(accounts.find((item) => !item.active_session_id)?.account_id ?? '')
  const [accountName, setAccountName] = useState('My Paper Account')
  const [feeBps, setFeeBps] = useState(definition.parameters.find((item) => item.key === 'fee_bps')?.default_value ?? 5)
  const [slippageBps, setSlippageBps] = useState(definition.parameters.find((item) => item.key === 'slippage_bps')?.default_value ?? 5)
  const [executionMode, setExecutionMode] = useState<PaperExecutionMode>('VQD_SIMULATED')
  const [parameters, setParameters] = useState<Record<string, number>>(defaults)
  const [searching, setSearching] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [technicalError, setTechnicalError] = useState<string | null>(null)
  const selectedAccountId = accountId || accounts.find((item) => !item.active_session_id)?.account_id || '__new__'
  const selectedAccount = accounts.find((item) => item.account_id === selectedAccountId)
  const hasRequiredSelection = securities.length === requiredCount && (requiredSymbols.length === 0 || requiredSymbols.every((symbol, index) => securities[index]?.symbol === symbol))
  const accountReady = selectedAccountId !== '__new__' || (accountName.trim().length > 0 && initialCash > 0)
  const canCreate = Boolean(provider?.configured && accountReady && hasRequiredSelection && !creating)

  async function findStocks() {
    if (!stockQuery.trim()) return
    setSearching(true); setError(null); setTechnicalError(null)
    try { setStockResults(await searchStocks(stockQuery)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : tr('Stock search failed.')) }
    finally { setSearching(false) }
  }

  function chooseStock(item: StockSecurity) {
    if (securities.some((value) => value.symbol === item.symbol) || securities.length >= requiredCount) return
    setSecurities((current) => [...current, item]); setStockResults([]); setStockQuery('')
  }

  async function create() {
    if (creating || !provider?.configured) return
    setCreating(true); setError(null)
    try {
      const account = selectedAccountId !== '__new__'
        ? accounts.find((item) => item.account_id === selectedAccountId)
        : await createPaperAccount(accountName, initialCash)
      if (!account) throw new Error('Selected paper account is unavailable.')
      if (selectedAccountId === '__new__') onAccountCreated(account)
      onCreated(await createPaperSession({
        account_id: account.account_id,
        strategy_id: definition.strategy_id,
        symbols: securities.map((item) => item.symbol),
        securities: securities.map(({ symbol, name, exchange, status }) => ({ symbol, name, exchange, status })),
        parameters,
        provider: 'alpaca', feed, timeframe: '1Min', market_session: 'US_REGULAR',
        fee_bps: feeBps, slippage_bps: slippageBps,
        execution_mode: executionMode,
      }))
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : tr('Paper session creation failed.')
      setTechnicalError(detail); setError(friendlySetupError(detail, tr))
    }
    finally { setCreating(false) }
  }

  return <main className="forward-shell live-paper-shell">
    <header className="workspace-title paper-hero"><div><span className="eyebrow">{tr('REAL-MARKET PRACTICE')}</span><h1>{tr('Create a paper portfolio')}</h1><p>{tr('Choose a strategy, stocks, and how orders should be filled. Alpaca Paper never uses real money.')}</p></div><span className={`connection-pill ${provider?.configured ? 'connected' : 'disconnected'}`}><i />{tr(provider?.configured ? 'Alpaca connected' : 'Alpaca not connected')}</span></header>
    {!provider?.configured && <section className="paper-connection-callout"><div><strong>{tr('Connect Alpaca to continue')}</strong><span>{tr('Add your Alpaca Paper credentials in My. They are stored encrypted and never shown here.')}</span></div><button type="button" onClick={onOpenProfile}>{tr('Open My settings')}</button></section>}
    <section className="workspace-panel paper-builder">
      <ol className="paper-progress" aria-label={tr('Setup progress')}><li className="complete"><span>1</span>{tr('Account & strategy')}</li><li className={hasRequiredSelection ? 'complete' : 'active'}><span>2</span>{tr('Choose stocks')}</li><li className={canCreate ? 'active' : ''}><span>3</span>{tr('Review & create')}</li></ol>

      <section className="paper-step"><div className="paper-step-heading"><span>1</span><div><h2>{tr('Account and strategy')}</h2><p>{tr('Choose where simulated cash is tracked and which rules make decisions.')}</p></div></div>
        <div className="paper-choice-grid"><label>{tr('Paper Account')}<select aria-label={tr('Paper Account')} value={selectedAccountId} onChange={(event) => setAccountId(event.target.value)}><option value="__new__">{tr('Create a new account')}</option>{accounts.map((account) => <option key={account.account_id} value={account.account_id} disabled={Boolean(account.active_session_id)}>{account.name} · {formatCurrency(account.equity)}{account.active_session_id ? ` · ${tr('In use')}` : ''}</option>)}</select><small>{selectedAccount ? `${tr('Available equity')}: ${formatCurrency(selectedAccount.equity)}` : tr('A separate virtual balance keeps this test easy to review.')}</small></label>
          <label>{tr('Strategy')}<select aria-label={tr('Strategy')} value={definition.strategy_id} onChange={(event) => onDefinitionChange?.(event.target.value)}>{definitions.filter((item) => !item.historical_research_only).map((item) => <option key={item.strategy_id} value={item.strategy_id}>{tr(item.name)}</option>)}</select><small>{tr(definition.description)}</small></label>
          {selectedAccountId === '__new__' && <><label>{tr('Account name')}<input value={accountName} onChange={(event) => setAccountName(event.target.value)} /></label><label>{tr('Starting virtual cash')}<input type="number" min="1" value={initialCash} onChange={(event) => setInitialCash(Number(event.target.value))} /></label></>}
        </div>
      </section>

      <section className="paper-step"><div className="paper-step-heading"><span>2</span><div><h2>{tr('Choose stocks')}</h2><p>{requiredCount === 1 ? tr('This strategy needs one stock.') : tr('This strategy needs {count} stocks. Selection order defines their strategy roles.').replace('{count}', String(requiredCount))}</p></div><strong className={hasRequiredSelection ? 'selection-count ready' : 'selection-count'}>{securities.length} / {requiredCount}</strong></div>
        <div className="security-slots">{Array.from({ length: requiredCount }, (_, index) => { const security = securities[index]; const slotLabel = requiredCount === 1 ? 'Selected stock' : index === 0 ? 'First stock' : index === 1 ? 'Second stock' : 'Next stock'; return <div className={`security-slot ${security ? 'filled' : ''}`} key={index}><span className="slot-index">{index + 1}</span>{security ? <><div><strong>{security.symbol}</strong><span>{security.name}</span><small>{security.exchange}</small></div><button type="button" aria-label={`${tr('Remove')} ${security.symbol}`} onClick={() => setSecurities((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></> : <div><strong>{tr(slotLabel)}</strong><span>{tr('Search and add a listed US stock below.')}</span></div>}</div> })}</div>
        <form className="paper-stock-search" onSubmit={(event) => { event.preventDefault(); void findStocks() }}><label htmlFor="paper-stock-query">{tr('Find a stock')}</label><div className="input-action"><input id="paper-stock-query" value={stockQuery} onChange={(event) => setStockQuery(event.target.value)} placeholder={tr('Type a symbol or company name')} /><button type="submit" disabled={searching || !stockQuery.trim()}>{searching ? tr('Searching…') : tr('Search')}</button></div>{securities.length >= requiredCount && <small>{tr('All slots are filled. Remove a stock to choose another.')}</small>}</form>
        {stockResults.length > 0 && <div className="paper-stock-results" role="listbox" aria-label={tr('Stock search results')}>{stockResults.map((item) => { const added = securities.some((value) => value.symbol === item.symbol); const blocked = added || securities.length >= requiredCount; return <button type="button" key={item.symbol} disabled={blocked} onClick={() => chooseStock(item)}><span className="stock-mark">{item.symbol.slice(0, 1)}</span><span><strong>{item.symbol}</strong><small>{item.name}</small></span><code>{added ? tr('Added') : item.exchange}</code></button> })}</div>}
      </section>

      <section className="paper-step paper-options"><div className="paper-step-heading"><span>3</span><div><h2>{tr('Review and create')}</h2><p>{tr('Recommended defaults are already applied. You can change them before creating.')}</p></div></div>
        <fieldset className="execution-mode-picker"><legend>{tr('How should orders be filled?')}</legend><label className={executionMode === 'VQD_SIMULATED' ? 'selected' : ''}><input type="radio" name="execution-mode" value="VQD_SIMULATED" checked={executionMode === 'VQD_SIMULATED'} onChange={() => setExecutionMode('VQD_SIMULATED')} /><span className="execution-mode-icon" aria-hidden="true">VQ</span><span><strong>{tr('VQD simulated execution')}</strong><small>{tr('VQD fills at the next bar close. Fast and deterministic for strategy checks.')}</small></span><em>{tr('Local only')}</em></label><label className={executionMode === 'ALPACA_PAPER' ? 'selected broker' : ''}><input type="radio" name="execution-mode" value="ALPACA_PAPER" checked={executionMode === 'ALPACA_PAPER'} onChange={() => setExecutionMode('ALPACA_PAPER')} /><span className="execution-mode-icon alpaca" aria-hidden="true">A</span><span><strong>{tr('Alpaca Paper broker')}</strong><small>{tr('Orders are sent to your Alpaca Paper account. Alpaca controls fills, rejects, and cancels.')}</small></span><em>{tr('No real money')}</em></label></fieldset>
        {executionMode === 'ALPACA_PAPER' && <p className="broker-consent-note"><strong>{tr('Paper orders leave VQD')}</strong><span>{tr('Starting this session lets VQD submit simulated broker orders to your connected Alpaca Paper account.')}</span></p>}
        <div className="paper-review"><div><span>{tr('Account')}</span><strong>{selectedAccount?.name ?? accountName}</strong></div><div><span>{tr('Strategy')}</span><strong>{tr(definition.name)}</strong></div><div><span>{tr('Stocks')}</span><strong>{securities.length ? securities.map((item) => item.symbol).join(' + ') : tr('Not selected')}</strong></div><div><span>{tr('Execution')}</span><strong>{tr(executionMode === 'ALPACA_PAPER' ? 'Alpaca Paper broker' : 'VQD simulated execution')}</strong></div></div>
        {strategyParameters.length > 0 && <details className="paper-disclosure"><summary><span><strong>{tr('Strategy settings')}</strong><small>{tr('Using recommended defaults')}</small></span><i /></summary><div className="paper-parameter-grid">{strategyParameters.map((parameter) => <label key={parameter.key}><span>{tr(parameter.label)} <small>{tr(parameter.unit)}</small></span><input type="number" value={parameters[parameter.key]} min={parameter.minimum} max={parameter.maximum ?? undefined} step={parameter.step} onChange={(event) => setParameters((current) => ({ ...current, [parameter.key]: Number(event.target.value) }))} /><small>{tr(parameter.description)}</small></label>)}</div></details>}
        <details className="paper-disclosure"><summary><span><strong>{tr('Advanced execution settings')}</strong><small>{tr('Alpaca · 1 minute · regular US session')}</small></span><i /></summary><div className="paper-choice-grid"><label>{tr('Market feed')}<select value={feed} onChange={(event) => setFeed(event.target.value as 'iex' | 'sip')}><option value="iex">{tr('IEX · single exchange')}</option><option value="sip">{tr('SIP · consolidated US market')}</option></select></label><label>{tr('Reference fee / slippage (bps)')}<div className="paired-input"><input aria-label={tr('Fee bps')} type="number" min="0" value={feeBps} onChange={(event) => setFeeBps(Number(event.target.value))} /><input aria-label={tr('Slippage bps')} type="number" min="0" value={slippageBps} onChange={(event) => setSlippageBps(Number(event.target.value))} /></div></label></div><p>{tr(executionMode === 'ALPACA_PAPER' ? 'These assumptions are used only for the VQD comparison run. Alpaca reports the broker fill.' : 'IEX shows one exchange. SIP requires the matching Alpaca subscription.')}</p></details>
      </section>

      {error && <div className="paper-error" role="alert"><strong>{error}</strong><span>{tr('Your selections have been kept.')}</span>{technicalError && <details><summary>{tr('Technical details')}</summary><code>{technicalError}</code></details>}</div>}
      <footer className="paper-create-bar"><div><strong>{canCreate ? tr('Ready to create') : tr('Complete the steps above')}</strong><span>{!provider?.configured ? tr('Market data connection is required.') : !accountReady ? tr('Finish the account details.') : !hasRequiredSelection ? tr('Choose every required stock.') : tr('You can start the strategy after the portfolio is created.')}</span></div><button className="primary-button" disabled={!canCreate} onClick={() => void create()}>{creating ? tr('Creating…') : tr('Create paper portfolio')}</button></footer>
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
  const [error, setError] = useState<string | null>(null)
  const [operationalError, setOperationalError] = useState<string | null>(null)
  const [health, setHealth] = useState<PaperOperationalHealth | null>(null)
  const [operations, setOperations] = useState<PaperOperationEvent[]>([])
  const [recovery, setRecovery] = useState<PaperRecoveryReport | null>(null)
  const [recovering, setRecovering] = useState(false)
  const [cancellingOrderId, setCancellingOrderId] = useState<string | null>(null)
  const refreshOperations = useCallback(async () => {
    try {
      const [nextHealth, nextOperations, nextRecovery] = await Promise.all([getPaperHealth(snapshot.session_id), getPaperOperations(snapshot.session_id), getPaperRecovery(snapshot.session_id)])
      setHealth(nextHealth); setOperations(nextOperations); setRecovery(nextRecovery); setOperationalError(null)
    } catch (reason) { setOperationalError(reason instanceof Error ? reason.message : tr('Operational data failed.')) }
  }, [snapshot.session_id, tr])
  useEffect(() => {
    const timer = window.setTimeout(() => void refreshOperations(), 0)
    return () => window.clearTimeout(timer)
  }, [refreshOperations, snapshot.status, snapshot.feed_status, snapshot.broker_status, snapshot.recovery_status, snapshot.last_event_sequence])
  useEffect(() => {
    const merge = (next: PaperSessionSnapshot) => onSnapshot(next)
    if (typeof EventSource === 'undefined') {
      const timer = window.setInterval(() => void getPaperSession(snapshot.session_id).then(merge), 5_000)
      return () => window.clearInterval(timer)
    }
    const source = new EventSource(`/api/paper-sessions/${encodeURIComponent(snapshot.session_id)}/events`)
    source.addEventListener('snapshot', (message) => { try { const value: unknown = JSON.parse((message as MessageEvent<string>).data); if (isPaperSession(value)) merge(value) } catch { setError(tr('Live session stream returned malformed data.')) } })
    source.onerror = () => setError(tr('Live update channel is reconnecting; the backend session continues independently.'))
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
    {error && <p className="inline-warning">{error}</p>}{operationalError && <p className="inline-warning">{operationalError}</p>}{snapshot.error_message && <p className="inline-error"><strong>{snapshot.error_code}</strong> · {snapshot.error_message}</p>}
    {snapshot.research_run_id && <p className="inline-success"><strong>{tr('Research evidence saved')}</strong> · <a href={`/runs/${snapshot.research_run_id}`}>{snapshot.research_run_id}</a></p>}
    <section className="workspace-panel paper-overview-panel" id="paper-overview"><div className="section-heading"><h2>{tr('Overview')}</h2><span>{tr('Backend recorded')}</span></div>{brokerMode && snapshot.broker_account && <div className="broker-balance-strip"><div><span>{tr('Alpaca Paper status')}</span><strong>{snapshot.broker_account.status}</strong></div><div><span>{tr('Paper cash')}</span><strong>{formatCurrency(snapshot.broker_account.cash)}</strong></div><div><span>{tr('Paper equity')}</span><strong>{formatCurrency(snapshot.broker_account.equity)}</strong></div><div><span>{tr('Paper buying power')}</span><strong>{formatCurrency(snapshot.broker_account.buying_power)}</strong></div></div>}<div className="metric-strip"><div><span>{tr(brokerMode ? 'VQD Trace cash' : 'Cash')}</span><strong>{formatCurrency(snapshot.account.cash)}</strong></div><div><span>{tr(brokerMode ? 'VQD Trace equity' : 'Equity')}</span><strong>{formatCurrency(snapshot.account.equity)}</strong></div><div><span>{tr('Net P&L')}</span><strong>{formatCurrency(snapshot.account.net_pnl)}</strong></div><div><span>{tr('Fees')}</span><strong>{formatCurrency(snapshot.account.cumulative_fees)}</strong></div><div><span>{tr('Slippage')}</span><strong>{formatCurrency(snapshot.account.cumulative_slippage)}</strong></div><div><span>{tr('Max drawdown')}</span><strong>{formatPercent(snapshot.account.max_drawdown)}</strong></div></div></section>
    <section className="workspace-panel"><div className="section-heading"><h2>{tr('Live Equity Timeline')}</h2><span>{tr('received events only')}</span></div><EquityTimeline trace={trace} /></section>
    <section className="workspace-panel market-data-inspector"><div className="section-heading"><h2>{tr('Market Data Inspector')}</h2><span>{snapshot.correction_count} {tr('corrections')}</span></div><div className="market-data-grid"><div><span>{tr('Provider / Feed')}</span><strong>{snapshot.provider.toUpperCase()} · {snapshot.feed.toUpperCase()}</strong></div><div><span>{tr('Connection')}</span><strong>{tr(snapshot.feed_status)}</strong></div><div><span>{tr('Last event')}</span><code>{latestMarket ? formatTimestamp(latestMarket.event_time).time : '-'}</code></div><div><span>{tr('Received at')}</span><code>{latestMarket ? formatTimestamp(latestMarket.received_at).time : '-'}</code></div><div><span>{tr('Market time')}</span><code>{snapshot.market_clock ? formatTimestamp(snapshot.market_clock.timestamp).time : '-'}</code></div><div><span>{tr('Observed delivery latency')}</span><code>{latestMarket ? `${latestMarket.latency_ms.toFixed(0)} ms` : '-'}</code></div></div></section>
    <PaperOperationsPanels health={health} operations={operations} recovery={recovery} recovering={recovering} onRecover={() => void recover()} onStop={() => void action('stop')} />
    {snapshot.recent_revisions.length > 0 && <section className="workspace-panel correction-ledger"><div className="section-heading"><h2>{tr('Market Data Revisions')}</h2><span>{tr('prior decisions remain immutable')}</span></div>{snapshot.recent_revisions.map((revision) => <div className="correction-row" key={`${revision.symbol}-${revision.event_time}-${revision.later_revision}`}><strong>{tr('MARKET DATA REVISED LATER')}</strong><code>{revision.symbol} · {formatTimestamp(revision.event_time).time}</code><span>{tr('Used by the original decision')}: {tr('revision')} {revision.used_revision}, close {revision.used_close.toFixed(2)}</span><span>{tr('Later revision')} {revision.later_revision}, close {revision.later_close.toFixed(2)} · {tr('available')} {formatTimestamp(revision.revision_available_at).time}</span></div>)}</section>}
    <div className="inspector-grid"><section className="workspace-panel"><div className="section-heading"><h2>{tr('Positions')}</h2><span>{Object.keys(snapshot.account.positions).length}</span></div>{Object.keys(snapshot.account.positions).length === 0 ? <p className="empty-state">{tr('No open positions.')}</p> : Object.entries(snapshot.account.positions).map(([symbol, quantity]) => <div className="position-line" key={symbol}><code>{symbol}</code><strong>{quantity.toFixed(4)}</strong></div>)}</section><section className="workspace-panel"><div className="section-heading"><h2>{tr('Open Orders')}</h2><span>{openOrders.length}</span></div>{openOrders.length === 0 ? <p className="empty-state">{tr('No open orders.')}</p> : <div className="broker-order-list">{openOrders.map((order) => <article className="broker-order" key={order.order_id}><div><strong>{order.symbol} · {tr(order.side)}</strong><span>{tr(order.status)}</span></div><div className="order-fill-progress"><span style={{ width: `${Math.min(100, (order.filled_quantity / order.quantity) * 100)}%` }} /></div><small>{tr('Filled')} {order.filled_quantity.toFixed(4)} / {order.quantity.toFixed(4)}</small>{brokerMode && !['PENDING_CANCEL', 'HELD', 'SUSPENDED'].includes(order.status) && <button type="button" disabled={cancellingOrderId === order.order_id} onClick={() => void cancelOrder(order.order_id)}>{cancellingOrderId === order.order_id ? tr('Cancelling…') : tr('Cancel order')}</button>}</article>)}</div>}</section></div>
    <section className="workspace-panel" id="paper-orders"><div className="section-heading"><h2>{tr('Orders')}</h2><span>{snapshot.orders.length} · {tr('Order Lifecycle')}</span></div>{snapshot.orders.length === 0 ? <p className="empty-state">{tr('Orders will appear after the strategy produces a target change.')}</p> : <div className="order-ledger" role="table"><div className="order-ledger-row header" role="row"><span>{tr('Order')}</span><span>{tr('Status')}</span><span>{tr('Filled')}</span><span>{tr('VQD reference')}</span><span>{tr('Broker average')}</span></div>{snapshot.orders.slice().reverse().map((order) => <div className="order-ledger-row" role="row" key={order.order_id}><span><strong>{order.symbol} · {tr(order.side)}</strong><small>{formatTimestamp(order.submitted_at).time}</small></span><span className={`order-status ${order.status.toLowerCase()}`}>{tr(order.status)}</span><code>{order.filled_quantity.toFixed(4)} / {order.quantity.toFixed(4)}</code><code>{order.reference_price?.toFixed(2) ?? '-'}</code><code>{order.average_fill_price?.toFixed(2) ?? '-'}</code>{order.rejection_reason && <small className="order-rejection">{order.rejection_reason}</small>}</div>)}</div>}</section>
    <section className="workspace-panel" id="paper-fills"><div className="section-heading"><h2>{tr('Fills')}</h2><span>{snapshot.fills.length}</span></div>{snapshot.fills.length === 0 ? <p className="empty-state">{tr(brokerMode ? 'Waiting for Alpaca Paper to report a fill.' : 'Executions fill locally at close(t+1).')}</p> : <div className="dense-table fill-comparison-table"><div className="dense-row header"><span>{tr('Time')}</span><span>{tr('Symbol / Side')}</span><span>{tr('Quantity')}</span><span>{tr('VQD reference')}</span><span>{tr('Fill price')}</span><span>{tr('Difference')}</span></div>{snapshot.fills.slice().reverse().map((execution) => <div className="dense-row" key={execution.fill_id}><code>{formatTimestamp(execution.executed_at).time}</code><span>{execution.symbol} · {tr(execution.side)}</span><code>{execution.quantity.toFixed(4)}</code><code>{execution.reference_price.toFixed(2)}</code><code>{execution.fill_price.toFixed(2)}</code><code className={execution.slippage > 0 ? 'negative' : ''}>{formatCurrency(execution.slippage)}</code></div>)}</div>}</section>
    <LiveTraceInspector snapshot={snapshot} trace={trace} />
    <SessionHistory sessions={sessions} activeId={snapshot.session_id} onOpen={onOpen} />
  </main>
}

export default function LivePaperPage({ definition, definitions = [definition], onDefinitionChange, onOpenProfile }: { definition: StrategyDefinition; definitions?: StrategyDefinition[]; onDefinitionChange?: (strategyId: string) => void; onOpenProfile?: () => void }) {
  const { tr } = useI18n()
  const [provider, setProvider] = useState<MarketDataProviderStatus | null>(null)
  const [sessions, setSessions] = useState<PaperSessionSnapshot[]>([])
  const [accounts, setAccounts] = useState<PaperAccount[]>([])
  const [snapshot, setSnapshot] = useState<PaperSessionSnapshot | null>(null)
  const [trace, setTrace] = useState<PaperTrace | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refreshList = useCallback(async () => { try { setSessions(await listPaperSessions()) } catch (reason) { setError(reason instanceof Error ? reason.message : tr('Session history failed.')) } }, [tr])
  useEffect(() => {
    const timer = window.setTimeout(() => { void Promise.all([getMarketDataProviders().then((items) => setProvider(items.find((item) => item.provider === 'alpaca') ?? null)), listPaperAccounts().then(setAccounts), refreshList()]).catch((reason) => setError(reason instanceof Error ? reason.message : tr('Live paper setup failed.'))) }, 0)
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
        if (sequence !== current.timeline.length + 1) {
          void getPaperTrace(next.session_id).then(setTrace).catch(() => undefined)
        } else {
          current = { ...current, timeline: [...current.timeline, latest] }
        }
      }
      return { ...current, market_revisions: [...current.market_revisions, ...next.recent_revisions.filter((revision) => !current.market_revisions.some((item) => item.symbol === revision.symbol && item.event_time === revision.event_time && item.later_revision === revision.later_revision))] }
    })
  }, [])
  if (error && !snapshot) return <main className="forward-shell"><section className="compact-error" role="alert"><strong>{tr('Live Paper unavailable')}</strong><span>{error}</span><button onClick={() => void refreshList()}>{tr('Retry')}</button></section></main>
  if (!snapshot || !trace) return <LiveSetup key={definition.strategy_id} definition={definition} definitions={definitions} onDefinitionChange={onDefinitionChange} onOpenProfile={onOpenProfile} provider={provider} accounts={accounts} sessions={sessions} onAccountCreated={(account) => setAccounts((current) => [account, ...current])} onCreated={(created) => { updateSnapshot(created); setTrace({ trace_version: '1.0', session_id: created.session_id, strategy_id: created.strategy_id, parameters: created.parameters, timeline: [], diagnostics: [], market_revisions: [], execution_mode: created.execution_mode, broker_events: [] }) }} onOpen={(id) => void open(id)} />
  return <LiveWorkspace snapshot={snapshot} trace={trace} sessions={sessions} onSnapshot={updateSnapshot} onOpen={(id) => void open(id)} onNew={() => { setSnapshot(null); setTrace(null); void refreshList() }} />
}
