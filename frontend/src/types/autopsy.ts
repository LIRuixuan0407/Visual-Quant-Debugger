export interface PnLAutopsyReport {
  report_version: '1.0'
  source_run: {
    trace_id: string
    trace_version: '1.0'
    strategy_id: string
    dataset_id: string
    dataset_name: string
    bar_count: number
  }
  summary: {
    initial_equity: number
    gross_pnl: number
    fees: number
    slippage: number
    total_cost: number
    net_pnl: number
    final_equity: number
  }
  reconciliation: {
    gross_less_costs: number
    reported_net_pnl: number
    pnl_difference: number
    initial_plus_net: number
    reported_final_equity: number
    equity_difference: number
    reconciled: boolean
  }
  periods: Record<'monthly' | 'quarterly' | 'yearly', PeriodAttribution[]>
  trades: {
    method: string
    closed_trades: TradeAttribution[]
    open_trades: TradeAttribution[]
    best_closed: TradeAttribution[]
    worst_closed: TradeAttribution[]
    attributed_net_pnl: number
    unattributed_net_pnl: number
    reconciliation_status: 'RECONCILED' | 'UNATTRIBUTED_REMAINS'
  }
  drawdowns: DrawdownEpisode[]
}

export interface PeriodAttribution {
  label: string
  start: string
  end: string
  gross_pnl: number
  fees: number
  slippage: number
  net_pnl: number
  start_equity: number
  end_equity: number
  period_return: number
  event_count: number
}

export interface TradeAttribution {
  trade_id: string
  direction: 'LONG_SPREAD' | 'SHORT_SPREAD'
  status: 'OPEN' | 'CLOSED'
  opened_at: string
  closed_at: string | null
  entry_event_id: string
  exit_event_id: string | null
  gross_pnl: number
  fees: number
  slippage: number
  net_pnl: number
  trade_return: number
  event_count: number
}

export interface DrawdownEpisode {
  episode_id: string
  rank_by_depth: number
  peak_event_id: string
  drawdown_start_event_id: string
  trough_event_id: string
  recovery_event_id: string | null
  peak_time: string
  drawdown_start_time: string
  trough_time: string
  recovery_time: string | null
  peak_equity: number
  trough_equity: number
  max_drawdown: number
  duration_bars: number
  recovery_bars: number | null
  recovered: boolean
}

