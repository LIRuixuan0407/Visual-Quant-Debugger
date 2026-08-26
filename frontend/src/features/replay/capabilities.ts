import type { RuntimeDescriptor, TraceCapabilitySet } from '../../types/trace'

const AVAILABLE = 'AVAILABLE' as const

export const nativeTraceCapabilities: TraceCapabilitySet = {
  market_timeline: AVAILABLE,
  feature_values: AVAILABLE,
  feature_lineage: AVAILABLE,
  decision_events: AVAILABLE,
  decision_conditions: AVAILABLE,
  data_dependencies: AVAILABLE,
  orders: AVAILABLE,
  executions: AVAILABLE,
  positions: AVAILABLE,
  trades: AVAILABLE,
  equity: AVAILABLE,
  pnl: AVAILABLE,
  point_in_time_proven: AVAILABLE,
  gross_pnl: AVAILABLE,
  fees: AVAILABLE,
  slippage: AVAILABLE,
  trade_attribution: AVAILABLE,
  drawdowns: AVAILABLE,
}

export function capabilitiesFor(runtime?: RuntimeDescriptor): TraceCapabilitySet {
  return runtime?.trace_capabilities ?? nativeTraceCapabilities
}

export function runtimeLabel(runtime?: RuntimeDescriptor): string {
  if (!runtime || runtime.kind === 'native') return 'VQD Native'
  return runtime.framework_name ?? runtime.adapter_id ?? 'Framework'
}
