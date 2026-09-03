import type { DiagnosisReport, DiagnosticMetrics, WhatIfScenario } from '../../types/diagnostics'

const metrics: DiagnosticMetrics = {
  status: 'OK', return: 0.012, sharpe: 1.4, max_drawdown: -0.03, turnover: 0.8,
  trade_count: 3, final_equity: 101200, bar_count: 28, note: null,
}

const whatIfBaseline = {
  total_return: 0.012, sharpe: 1.4, max_drawdown: -0.03, turnover: 0.8,
  trade_count: 3, net_pnl: 1200,
}

const baselineInputs = {
  fee_bps: 5, slippage_bps: 5, spread_bps: 0, market_impact_bps: 0,
  additional_execution_delay_bars: 0 as const,
  strategy_parameters: { lookback: 5 },
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
  statistical_diagnostics: {
    returns: {
      status: 'OK', observation_count: 39,
      return_acf: [0.18, -0.12, 0.08, -0.04, 0.02, -0.01, 0.06, -0.03, 0.01, -0.02].map((value, index) => ({ lag: index + 1, status: 'OK', value })),
      squared_return_acf: [0.31, 0.22, 0.16, 0.1, 0.08, 0.04, 0.03, 0.01, -0.02, -0.01].map((value, index) => ({ lag: index + 1, status: 'OK', value })),
      lag_1_return_autocorrelation: 0.18, lag_1_squared_return_autocorrelation: 0.31, note: null,
    },
    pair_mean_reversion: {
      status: 'OK', observation_count: 36, consecutive_pair_count: 35, hedge_ratio_observation_count: 36,
      phi: 0.72, spread_lag_1_autocorrelation: 0.69, half_life_bars: 2.11,
      hedge_ratio_mean: 1.08, hedge_ratio_std: 0.07, note: null,
    },
  },
  volatility_diagnostics: {
    status: 'OK', dataset_frequency: '1D', rolling_window: 21, ewma_decay: 0.94, annualization_factor: 252,
    market_return_method: "At each bar, compute each recorded symbol's simple close-to-close return, then take their equal-weight mean.",
    thresholds: { low_upper_bound: 0.15, high_lower_bound: 0.30 },
    points: Array.from({ length: 40 }, (_, index) => ({
      timestamp: new Date(Date.UTC(2024, 0, index + 1, 16)).toISOString(),
      market_return: index === 0 ? null : (index % 3 - 1) * 0.008,
      rolling_historical_vol: index < 21 ? null : 0.18 + index * 0.002,
      ewma_vol: index === 0 ? null : 0.14 + index * 0.004,
      regime: index === 0 ? null : index >= 39 ? 'HIGH' as const : index >= 3 ? 'NORMAL' as const : 'LOW' as const,
    })),
    current_regime: 'HIGH', current_historical_vol: 0.258, current_ewma_vol: 0.296,
    drawdown_overlap: [{
      episode_id: 'drawdown-0001', rank_by_depth: 1,
      start_time: '2024-01-25T16:00:00.000Z', trough_time: '2024-01-28T16:00:00.000Z', end_time: '2024-02-02T16:00:00.000Z',
      max_drawdown: -0.03, start_regime: 'NORMAL', ewma_rising_at_start: true, regime_changed_at_start: false,
    }],
    evaluable_drawdown_count: 1, rising_volatility_start_count: 1, regime_change_start_count: 0,
    verdict: 'RISING_VOLATILITY_OVERLAP',
    summary: '1 of the 1 evaluable largest drawdowns began while EWMA volatility was rising.',
    calculation_details: [
      'Historical volatility uses the sample standard deviation of 21 equal-weight market returns, annualized by sqrt(252).',
      'EWMA variance uses lambda=0.94 and zero-mean returns.',
      'Drawdown overlap is descriptive and does not establish causality.',
    ],
  },

  regime_diagnostics: {
    status: 'OK', trend_window: 21, trend_threshold: 0.02,
    performance: [
      { volatility_regime: 'LOW', trend_regime: 'UPTREND', observation_count: 8, status: 'OK', total_return: 0.018, sharpe: 1.6, max_drawdown: -0.012, hit_rate: 0.625, trade_count: 1, turnover: 0.24 },
      { volatility_regime: 'NORMAL', trend_regime: 'SIDEWAYS', observation_count: 9, status: 'OK', total_return: 0.004, sharpe: 0.35, max_drawdown: -0.018, hit_rate: 0.556, trade_count: 1, turnover: 0.31 },
      { volatility_regime: 'HIGH', trend_regime: 'DOWNTREND', observation_count: 7, status: 'OK', total_return: -0.014, sharpe: -0.8, max_drawdown: -0.031, hit_rate: 0.286, trade_count: 1, turnover: 0.25 },
    ],
    verdict: 'REGIME_DEPENDENT',
    summary: 'Best regime Sharpe 1.60 (LOW / UPTREND); worst -0.80 (HIGH / DOWNTREND).',
    calculation_details: [
      'Trend regime uses the compounded equal-weight market return over 21 observations.',
      'Regime differences are descriptive evidence and do not establish causality or future performance.',
    ],
  },
  failure_fingerprint: {
    high_severity_count: 2, medium_severity_count: 2, available_dimension_count: 6,
    summary: '2 high-severity and 2 medium-severity failure modes across 6 evidence-backed dimensions.',
    calculation_details: [
      'Failure Fingerprint uses deterministic thresholds over recorded diagnostics and rerun evidence.',
      'It is a triage summary, not a forecast, recommendation, or composite AI score.',
    ],
    dimensions: [
      { key: 'OOS_DEGRADATION', title: 'Out-of-sample degradation', severity: 'HIGH', evidence: ['Train Sharpe 1.40; test Sharpe -0.50.'], calculation_details: ['High when test Sharpe is non-positive or retention is under 40%.'] },
      { key: 'PARAMETER_INSTABILITY', title: 'Parameter instability', severity: 'MEDIUM', evidence: ['3 of 6 alternatives retain at least 80% of the current test Sharpe.'], calculation_details: ['Medium when retention share is under 60%.'] },
      { key: 'COST_SENSITIVITY', title: 'Transaction-cost sensitivity', severity: 'LOW', evidence: ['Return remains positive at the highest tested friction.'], calculation_details: ['Derived from deterministic cost-stress reruns.'] },
      { key: 'EXECUTION_DELAY_SENSITIVITY', title: 'Execution-delay sensitivity', severity: 'MEDIUM', evidence: ['Delayed return retention 50.0%; unfilled signals 2.'], calculation_details: ['Derived from deterministic delayed reruns.'] },
      { key: 'REGIME_DEPENDENCE', title: 'Market-regime dependence', severity: 'HIGH', evidence: ['Best regime Sharpe 1.60; worst -0.80.'], calculation_details: ['Severity comes from regime Sharpe dispersion.'] },
      { key: 'MEAN_REVERSION_EVIDENCE', title: 'Mean-reversion evidence', severity: 'LOW', evidence: ['AR(1) phi 0.720; estimated half-life 2.11 bars.'], calculation_details: ['This is evidence, not a stationarity proof.'] },
    ],
  },
  what_if: {
    status: 'AVAILABLE', baseline_inputs: baselineInputs, baseline_metrics: whatIfBaseline,
    parameter: { key: 'lookback', label: 'Lookback', value_type: 'integer', current_value: 5, minimum: 2, maximum: null, step: 1, unit: 'bars' },
    calculation_details: [
      "Each scenario is a full deterministic rerun on the source run's recorded dataset and strategy revision.",
      'Baseline inputs remain the immutable assumptions recorded on the source run.',
    ],
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

export const whatIfScenario: WhatIfScenario = {
  baseline_inputs: baselineInputs,
  inputs: { ...baselineInputs, fee_bps: 20, slippage_bps: 12, additional_execution_delay_bars: 1 },
  baseline_metrics: whatIfBaseline,
  stressed_metrics: { total_return: 0.006, sharpe: 0.71, max_drawdown: -0.045, turnover: 0.76, trade_count: 2, net_pnl: 600 },
  deltas: { total_return: -0.006, sharpe: -0.69, max_drawdown: -0.015, turnover: -0.04, trade_count: -1, net_pnl: -600 },
  unfilled_signal_count: 1,
  verdict: 'LOWER_NET_PNL',
  evidence: ['Sharpe changes from 1.400 to 0.710.', 'Net P&L changes from 1200.00 to 600.00.'],
  calculation_details: [
    "Each scenario is a full deterministic rerun on the source run's recorded dataset and strategy revision.",
    'Baseline inputs remain the immutable assumptions recorded on the source run.',
  ],
}
