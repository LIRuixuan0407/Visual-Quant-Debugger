import type { StrategyDefinition, StrategyParameters } from '../../types/strategy'

export const strategyDefaults: StrategyParameters = {
  lookback: 60,
  entry_z: 2,
  exit_z: 0.5,
  fee_bps: 5,
  slippage_bps: 5,
}

export const demoParameters: StrategyParameters = {
  lookback: 5,
  entry_z: 1,
  exit_z: 0.8,
  fee_bps: 5,
  slippage_bps: 5,
}

export const strategyDefinition: StrategyDefinition = {
  strategy_id: 'pairs-trading',
  name: 'Pairs Trading',
  description: 'Models the changing price relationship between two assets.',
  version: '0.1',
  parameters: [
    { key: 'lookback', label: 'Lookback', description: 'Historical observations used by rolling features.', value_type: 'integer', default_value: strategyDefaults.lookback, minimum: 2, exclusive_minimum: false, maximum: null, step: 1, unit: 'bars', impact_hint: 'Shorter is reactive; longer is steadier.' },
    { key: 'entry_z', label: 'Entry Z', description: 'Distance required before entry.', value_type: 'number', default_value: strategyDefaults.entry_z, minimum: 0, exclusive_minimum: true, maximum: null, step: 0.1, unit: 'σ', impact_hint: 'Higher values wait for larger moves.' },
    { key: 'exit_z', label: 'Exit Z', description: 'Distance required before exit.', value_type: 'number', default_value: strategyDefaults.exit_z, minimum: 0, exclusive_minimum: false, maximum: null, step: 0.1, unit: 'σ', impact_hint: 'Must be below Entry Z.' },
    { key: 'fee_bps', label: 'Fee', description: 'Transaction fee assumption.', value_type: 'number', default_value: strategyDefaults.fee_bps, minimum: 0, exclusive_minimum: false, maximum: null, step: 1, unit: 'bps', impact_hint: 'A cost assumption.' },
    { key: 'slippage_bps', label: 'Slippage', description: 'Fill deterioration assumption.', value_type: 'number', default_value: strategyDefaults.slippage_bps, minimum: 0, exclusive_minimum: false, maximum: null, step: 1, unit: 'bps', impact_hint: 'Higher is more conservative.' },
  ],
  validation_rules: [{ left_parameter: 'exit_z', operator: 'less_than', right_parameter: 'entry_z', message: 'Exit Z must be smaller than Entry Z.' }],
  presets: [
    { preset_id: 'strategy-default', name: 'Strategy Default', description: 'Quant Engine defaults.', parameters: strategyDefaults },
    { preset_id: 'demo-active-signals', name: 'Demo: Active Signals', description: 'Golden Replay preset.', parameters: demoParameters },
  ],
  pipeline: [
    { node_id: 'market-data', label: 'Market Data', category: 'DATA', description: 'Synchronized closes.', formula: null, inputs: [], outputs: ['hedge-ratio', 'spread'], related_parameters: [], used_by: ['Features'] },
    { node_id: 'hedge-ratio', label: 'Hedge Ratio', category: 'FEATURE', description: 'Estimated pair relationship.', formula: 'dot(price_B, price_A) / dot(price_B, price_B)', inputs: ['market-data'], outputs: ['spread'], related_parameters: ['lookback'], used_by: ['Spread'] },
    { node_id: 'spread', label: 'Spread', category: 'FEATURE', description: 'Relative adjusted price.', formula: 'price_A - hedge_ratio * price_B', inputs: ['market-data', 'hedge-ratio'], outputs: ['zscore'], related_parameters: [], used_by: ['Z-score'] },
    { node_id: 'zscore', label: 'Z-score', category: 'FEATURE', description: 'Measures how far spread is from its recent mean.', formula: '(spread - rolling_mean) / rolling_std', inputs: ['spread'], outputs: ['signal-rules'], related_parameters: ['lookback'], used_by: ['Entry decisions', 'Exit decisions'] },
    { node_id: 'signal-rules', label: 'Signal Rules', category: 'DECISION', description: 'Applies stateful thresholds.', formula: null, inputs: ['zscore'], outputs: ['target-position'], related_parameters: ['entry_z', 'exit_z'], used_by: ['Target Position'] },
    { node_id: 'target-position', label: 'Target Position', category: 'POSITION', description: 'Desired spread state.', formula: null, inputs: ['signal-rules'], outputs: ['execution'], related_parameters: [], used_by: ['Execution'] },
    { node_id: 'execution', label: 'Execution', category: 'EXECUTION', description: 'Fills on the next close.', formula: null, inputs: ['target-position'], outputs: [], related_parameters: ['fee_bps', 'slippage_bps'], used_by: ['Replay'] },
  ],
  execution_assumptions: [
    { key: 'signal_timing', label: 'Signal timing', value: 'close(t)', description: 'Uses the current close.' },
    { key: 'execution_timing', label: 'Execution timing', value: 'close(t+1)', description: 'Fills at the next close.' },
  ],
}
