import type { RuntimeDescriptor, TraceCapabilitySet, TraceFidelity } from './trace'

export type StrategyParameterKey = string

export type StrategyParameters = Record<StrategyParameterKey, number>

export interface StrategyParameterDefinition {
  key: StrategyParameterKey
  label: string
  description: string
  value_type: 'integer' | 'number'
  default_value: number
  minimum: number
  exclusive_minimum: boolean
  maximum: number | null
  step: number
  unit: string
  impact_hint: string
}

export interface StrategyPreset {
  preset_id: string
  name: string
  description: string
  parameters: StrategyParameters
}

export interface ParameterValidationRule {
  left_parameter: StrategyParameterKey
  operator: 'less_than'
  right_parameter: StrategyParameterKey
  message: string
}

export type PipelineCategory = 'DATA' | 'FEATURE' | 'DECISION' | 'POSITION' | 'EXECUTION'

export interface PipelineNode {
  node_id: string
  label: string
  category: PipelineCategory
  description: string
  formula: string | null
  inputs: string[]
  outputs: string[]
  related_parameters: StrategyParameterKey[]
  used_by: string[]
}

export interface ExecutionAssumption {
  key: string
  label: string
  value: string
  description: string
}

export interface StrategyDefinition {
  strategy_id: string
  name: string
  description: string
  version: string
  parameters: StrategyParameterDefinition[]
  validation_rules: ParameterValidationRule[]
  presets: StrategyPreset[]
  pipeline: PipelineNode[]
  execution_assumptions: ExecutionAssumption[]
  data_requirements?: {
    required_fields: string[]
    symbol_count: number | null
    symbols: string[]
    minimum_history: number
  }
  diagnostic_capabilities?: { parameter_sensitivity: string | null; train_test?: boolean; cost_stress?: boolean; execution_delay?: boolean }
  trace_fidelity?: TraceFidelity
  trace_capabilities?: TraceCapabilitySet
  runtime?: RuntimeDescriptor
  source_type?: 'BUILT_IN' | 'LOCAL_PYTHON' | 'FRAMEWORK_PYTHON'
  source_fingerprint?: string | null
  available?: boolean
  unavailable_reason?: string | null
  historical_research_only?: boolean
}
