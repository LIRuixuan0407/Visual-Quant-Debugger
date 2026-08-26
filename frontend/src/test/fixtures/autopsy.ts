import type { PnLAutopsyReport, TradeAttribution } from '../../types/autopsy'

const closedTrade: TradeAttribution = {
  trade_id: 'trade-000001', direction: 'SHORT_SPREAD', status: 'CLOSED',
  opened_at: '2024-01-18T16:00:00Z', closed_at: '2024-01-22T16:00:00Z',
  entry_event_id: 'timeline-000013', exit_event_id: 'timeline-000013', gross_pnl: 180,
  fees: 10, slippage: 10, net_pnl: 160, trade_return: 0.0016, event_count: 3,
}

const period = {
  label: '2024-01', start: '2024-01-01T16:00:00Z', end: '2024-01-31T16:00:00Z',
  gross_pnl: -280, fees: 100, slippage: 100, net_pnl: -480,
  start_equity: 100000, end_equity: 99520, period_return: -0.0048, event_count: 23,
}

export const autopsyReport: PnLAutopsyReport = {
  report_version: '1.0',
  source_run: { trace_id: 'trace-custom', trace_version: '1.0', strategy_id: 'pairs-trading', dataset_id: 'sha256:test', dataset_name: 'in-memory-bars', bar_count: 40 },
  summary: { initial_equity: 100000, gross_pnl: -280, fees: 100, slippage: 100, total_cost: 200, net_pnl: -480, final_equity: 99520 },
  reconciliation: { gross_less_costs: -480, reported_net_pnl: -480, pnl_difference: 0, initial_plus_net: 99520, reported_final_equity: 99520, equity_difference: 0, reconciled: true },
  periods: {
    monthly: [period, { ...period, label: '2024-02', start: '2024-02-01T16:00:00Z', end: '2024-02-23T16:00:00Z' }],
    quarterly: [{ ...period, label: '2024 Q1', event_count: 40 }],
    yearly: [{ ...period, label: '2024', event_count: 40 }],
  },
  trades: {
    method: 'Each trade receives timeline P&L from entry through exit.', closed_trades: [closedTrade], open_trades: [],
    best_closed: [closedTrade], worst_closed: [{ ...closedTrade, trade_id: 'trade-000002', net_pnl: -240 }],
    attributed_net_pnl: -480, unattributed_net_pnl: 0, reconciliation_status: 'RECONCILED',
  },
  drawdowns: [{
    episode_id: 'drawdown-0001', rank_by_depth: 1, peak_event_id: 'timeline-000001',
    drawdown_start_event_id: 'timeline-000013', trough_event_id: 'timeline-000013', recovery_event_id: null,
    peak_time: '2024-01-17T16:00:00Z', drawdown_start_time: '2024-01-18T16:00:00Z',
    trough_time: '2024-01-18T16:00:00Z', recovery_time: null, peak_equity: 100000,
    trough_equity: 99520, max_drawdown: -0.0048, duration_bars: 39, recovery_bars: null, recovered: false,
  }],
}
