import type { DiagnosisReport, DiagnosticMetrics } from '../../types/diagnostics'

const metrics: DiagnosticMetrics = {
  status: 'OK', return: 0.012, sharpe: 1.4, max_drawdown: -0.03, turnover: 0.8,
  trade_count: 3, final_equity: 101200, bar_count: 28, note: null,
}

export const diagnosisReport: DiagnosisReport = {
  report_version: '1.0',
  source_run: {
    trace_id: 'trace-custom', strategy_id: 'pairs-trading', dataset_id: 'sha256:test',
    dataset_name: 'in-memory-bars', dataset_source: '/sample/pairs_daily.csv', bar_count: 40,
    current_lookback: 5, fee_bps: 5, slippage_bps: 5,
  },
  train_test: {
    method: 'chronological-70-30', train_start: '2024-01-01T16:00:00Z', train_end: '2024-02-07T16:00:00Z',
    test_start: '2024-02-08T16:00:00Z', test_end: '2024-02-23T16:00:00Z', train_bar_count: 28, test_bar_count: 12,
    feature_context_policy: 'Test decisions may use earlier train history.',
    pnl_isolation_policy: 'Train P&L is not counted in test return.',
    train: metrics,
    test: { ...metrics, return: -0.004, sharpe: -0.5, max_drawdown: -0.018, trade_count: 1, bar_count: 12, final_equity: 100796 },
  },
  lookback_sensitivity: [2, 4, 5, 8, 10, 12, 14].map((lookback) => ({
    lookback, is_current: lookback === 5, train: { ...metrics, sharpe: lookback / 5 },
    test: { ...metrics, bar_count: 12, sharpe: 1 - lookback / 10 },
  })),
  cost_stress: [0, 5, 10, 15, 20].map((bps) => ({
    total_friction_bps: bps, fee_bps: bps / 2, slippage_bps: bps / 2,
    metrics: { ...metrics, return: 0.016 - bps * 0.0004 },
  })),
  execution_delay: [0, 1, 2].map((delay) => ({
    additional_delay_bars: delay as 0 | 1 | 2, execution_offset_bars: (delay + 1) as 1 | 2 | 3,
    unfilled_signal_count: delay, metrics: { ...metrics, return: 0.012 - delay * 0.003 },
  })),
  observations: [{
    observation_id: 'observation-delay', title: 'Execution timing changes are measured, not inferred',
    detail: 'Delay scenarios rerun the engine.', evidence: 't+1 return 1.20%; t+3 return 0.60%.',
  }],
}

