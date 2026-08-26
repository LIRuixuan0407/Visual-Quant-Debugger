import type { Diagnostic, TimelineEvent, TraceCapabilitySet } from '../../types/trace'
import { useI18n } from '../../i18n/I18nProvider'
import { nativeTraceCapabilities } from './capabilities'
import type { ReplayIndex } from './utils/navigation'
import { formatCurrency, formatNumber, formatPrice, formatQuantity, formatTimestamp, humanize } from './utils/format'

export function MarketPositionPanel({ event }: { event: TimelineEvent }) {
  const { tr } = useI18n()
  const position = event.position_snapshot
  return (
    <section className="inspector market-panel" aria-labelledby="market-heading">
      <div className="section-heading">
        <h2 id="market-heading">{tr('Observed at this bar')}</h2>
        <span className={`state-badge state-${position.position_state.toLowerCase()}`}>{tr(humanize(position.position_state))}</span>
      </div>
      <div className="market-values">
        {event.market_snapshot.values.map((value) => (
          <div key={value.dependency_id}>
            <span>{value.symbol}</span>
            <strong>{formatPrice(value.value)}</strong>
            <small>{value.field}</small>
          </div>
        ))}
      </div>
      <div className="position-strip">
        <div><span>{tr('Target')}</span><strong>{position.target_position}</strong></div>
        <div><span>{tr('Gross exposure')}</span><strong>{formatCurrency(position.gross_exposure)}</strong></div>
        <div><span>{tr('Net exposure')}</span><strong>{formatCurrency(position.net_exposure)}</strong></div>
      </div>
      <details className="asset-positions">
        <summary>{tr('Asset positions')}</summary>
        {position.asset_positions.map((asset) => (
          <div key={asset.symbol}>
            <strong>{asset.symbol}</strong>
            <span>{formatQuantity(asset.quantity)} {tr('units')}</span>
            <span>{formatCurrency(asset.market_value)}</span>
          </div>
        ))}
      </details>
    </section>
  )
}

export function StrategyDecisionPanel({ event, capabilities = nativeTraceCapabilities }: { event: TimelineEvent; capabilities?: TraceCapabilitySet }) {
  const { tr } = useI18n()
  const evaluation = event.signal_evaluation
  return (
    <section className="inspector decision-panel" aria-labelledby="decision-heading">
      <div className="section-heading">
        <h2 id="decision-heading">{tr(humanize(evaluation.signal))}</h2>
        <span className={evaluation.signal_id ? 'signal-status active' : 'signal-status'}>
          {evaluation.signal_id ? `${tr('Signal')} · ${evaluation.signal_id}` : tr('Evaluation only')}
        </span>
      </div>
      <p className="decision-reason">{tr(evaluation.reason)}</p>
      <div className="state-transition" aria-label={`State transition from ${evaluation.previous_state} to ${evaluation.next_state}`}>
        <span>{tr(humanize(evaluation.previous_state))}</span><i aria-hidden="true">→</i><strong>{tr(humanize(evaluation.next_state))}</strong>
      </div>
      <details className="advanced-disclosure decision-details">
        <summary>{tr('Decision details')}</summary>
        {capabilities.decision_conditions === 'UNAVAILABLE'
          ? <p className="capability-unavailable"><strong>{tr('Decision conditions not available')}</strong><span>{tr('The adapter did not provide auditable condition evaluations.')}</span></p>
          : <div className="conditions">
          {evaluation.conditions.map((condition, index) => (
            <div className="condition" key={`${condition.left_operand}-${index}`}>
              <div className="condition-expression">
                <span><small>{tr(humanize(condition.left_operand))}</small>{formatNumber(condition.left_value, 6)}</span>
                <b>{condition.operator}</b>
                <span><small>{condition.right_operand ? tr(humanize(condition.right_operand)) : tr('Expected')}</small>{formatNumber(condition.right_value, 6)}</span>
              </div>
              <strong className={condition.result ? 'condition-true' : 'condition-false'}>{tr(condition.result ? 'TRUE' : 'FALSE')}</strong>
            </div>
          ))}
          </div>}
        <div className="evaluation-id"><span>{tr('Evaluation')}</span><code>{evaluation.evaluation_id}</code></div>
      </details>
    </section>
  )
}

export function DependenciesPanel({ event, diagnostics, capabilities = nativeTraceCapabilities }: { event: TimelineEvent; diagnostics: Diagnostic[]; capabilities?: TraceCapabilitySet }) {
  const { tr } = useI18n()
  const warnings = new Map(diagnostics.map((diagnostic) => [diagnostic.dependency_id, diagnostic]))
  const eventWarnings = diagnostics.filter((diagnostic) => diagnostic.event_id === event.event_id)
  return (
    <section className="inspector dependency-panel" aria-labelledby="dependency-heading">
      <div className="section-heading">
        <h2 id="dependency-heading">{tr('Data quality')}</h2>
        <span className={eventWarnings.length ? 'diagnostic-count warning' : 'diagnostic-count'}>{eventWarnings.length} {tr('warnings')}</span>
      </div>
      {capabilities.data_dependencies === 'UNAVAILABLE' || capabilities.point_in_time_proven === 'UNAVAILABLE' ? (
        <p className="capability-unavailable"><strong>{tr('Point-in-time provenance not available')}</strong><span>{tr('Point-in-time data dependency provenance is not available for this adapter.')}</span></p>
      ) : diagnostics.length === 0 ? (
        <p className="diagnostic-clear">{tr('No look-ahead issues')}</p>
      ) : eventWarnings.length === 0 ? (
        <p className="diagnostic-clear">{tr('No issues at this event')} <span>{diagnostics.length} {tr('elsewhere in this run')}</span></p>
      ) : (
        <div className="diagnostics-list">
          {eventWarnings.map((diagnostic) => (
            <article key={diagnostic.diagnostic_id}>
              <p><strong>{diagnostic.code}</strong>{diagnostic.message}</p>
              <dl className="dependency-times">
                <div><dt>{tr('Severity')}</dt><dd>{diagnostic.severity}</dd></div>
                <div><dt>{tr('Event')}</dt><dd><code>{diagnostic.event_id}</code></dd></div>
                <div><dt>{tr('Dependency')}</dt><dd><code>{diagnostic.dependency_id}</code></dd></div>
              </dl>
            </article>
          ))}
        </div>
      )}
      {capabilities.data_dependencies !== 'UNAVAILABLE' && <details className="advanced-disclosure dependency-disclosure" open={eventWarnings.length > 0}>
        <summary>{tr('Data used at this bar')} <span>{event.data_dependencies.length}</span></summary>
        <div className="dependency-list">
          {event.data_dependencies.map((dependency) => {
            const warning = warnings.get(dependency.dependency_id)
            return (
              <details key={dependency.dependency_id}>
                <summary>
                  <span><strong>{dependency.symbol ?? dependency.source}</strong><small>{dependency.field}</small></span>
                  <b className={warning ? 'dependency-warning' : 'dependency-available'}>{tr(warning ? 'LOOK-AHEAD' : 'AVAILABLE')}</b>
                </summary>
                <dl className="dependency-times">
                  <div><dt>{tr('Source')}</dt><dd>{dependency.source}</dd></div>
                  <div><dt>{tr('Field')}</dt><dd>{dependency.field}</dd></div>
                  <div><dt>{tr('Source timestamp')}</dt><dd>{formatTimestamp(dependency.source_timestamp).date} {formatTimestamp(dependency.source_timestamp).time}</dd></div>
                  <div><dt>{tr('Available at')}</dt><dd>{formatTimestamp(dependency.available_at).date} {formatTimestamp(dependency.available_at).time}</dd></div>
                  <div><dt>{tr('Used at')}</dt><dd>{formatTimestamp(dependency.used_at).date} {formatTimestamp(dependency.used_at).time}</dd></div>
                </dl>
              </details>
            )
          })}
        </div>
      </details>}
    </section>
  )
}

interface ExecutionOutcomePanelProps {
  event: TimelineEvent
  index: ReplayIndex
  sourceSignalEvent: TimelineEvent | null
  executionEvent: TimelineEvent | null
  onSelect: (eventId: string) => void
  capabilities?: TraceCapabilitySet
}

export function ExecutionOutcomePanel({ event, index, sourceSignalEvent, executionEvent, onSelect, capabilities = nativeTraceCapabilities }: ExecutionOutcomePanelProps) {
  const { tr } = useI18n()
  const costs = event.cost_snapshot
  const pnl = event.pnl_snapshot
  const hasExecutions = event.execution_events.length > 0
  return (
    <section className="outcome-section" aria-labelledby="execution-heading">
      <div className="section-heading">
        <h2 id="execution-heading">{tr('Execution and P&L')}</h2>
        {sourceSignalEvent && <button className="link-button" type="button" onClick={() => onSelect(sourceSignalEvent.event_id)}>← {tr('View source signal')}</button>}
        {!sourceSignalEvent && executionEvent && <button className="link-button" type="button" onClick={() => onSelect(executionEvent.event_id)}>{tr('View execution')} →</button>}
      </div>
      <div className={`outcome-grid${hasExecutions ? "" : " outcome-grid-empty"}`}>
        <div className={`execution-list${hasExecutions ? "" : " empty"}`}>
          <h3>{tr('Executions')}</h3>
          {!hasExecutions ? <p className="empty-state execution-empty">{tr('No execution at this timestamp.')}</p> : event.execution_events.map((execution) => {
            const order = index.orderById.get(execution.source_order_id)?.order
            return (
              <article className="execution-row" key={execution.execution_id}>
                <span className={`side side-${execution.side.toLowerCase()}`}>{execution.side}</span>
                <div><strong>{execution.symbol}</strong><small>{formatQuantity(execution.quantity)} {tr('units')}</small></div>
                <dl>
                  <div><dt>{tr('Reference')}</dt><dd>{formatPrice(execution.reference_price)}</dd></div>
                  <div><dt>{tr('Fill')}</dt><dd>{formatPrice(execution.fill_price)}</dd></div>
                  <div><dt>{tr('Fee')}</dt><dd>{capabilities.fees === 'UNAVAILABLE' ? tr('Not available') : formatCurrency(execution.fee)}</dd></div>
                  <div><dt>{tr('Slippage')}</dt><dd>{capabilities.slippage === 'UNAVAILABLE' ? tr('Not available') : formatCurrency(execution.slippage)}</dd></div>
                  <div><dt>{tr('Executed at')}</dt><dd>{formatTimestamp(execution.executed_at).date} · {formatTimestamp(execution.executed_at).time}</dd></div>
                </dl>
                <code>{order?.order_id ?? execution.source_order_id}</code>
              </article>
            )
          })}
        </div>
        <div className="cost-pnl-grid">
          <div><h3>{tr('Costs')}</h3><dl className="metric-list"><div><dt>{tr('Fees')}</dt><dd>{capabilities.fees === 'UNAVAILABLE' ? tr('Not available') : formatCurrency(costs.fees)}</dd></div><div><dt>{tr('Slippage')}</dt><dd>{capabilities.slippage === 'UNAVAILABLE' ? tr('Not available') : formatCurrency(costs.slippage)}</dd></div><div className="metric-total"><dt>{tr('Total cost')}</dt><dd>{capabilities.fees === 'UNAVAILABLE' && capabilities.slippage === 'UNAVAILABLE' ? tr('Not available') : formatCurrency(costs.total_cost)}</dd></div></dl></div>
          <div><h3>{tr('P&L')}</h3><dl className="metric-list"><div><dt>{tr('Period gross')}</dt><dd>{formatCurrency(pnl.period_gross_pnl)}</dd></div><div><dt>{tr('Period net')}</dt><dd>{formatCurrency(pnl.period_net_pnl)}</dd></div><div><dt>{tr('Cumulative net')}</dt><dd>{formatCurrency(pnl.cumulative_net_pnl)}</dd></div><div className="metric-total"><dt>{tr('Equity')}</dt><dd>{formatCurrency(pnl.equity)}</dd></div></dl></div>
        </div>
      </div>
    </section>
  )
}
