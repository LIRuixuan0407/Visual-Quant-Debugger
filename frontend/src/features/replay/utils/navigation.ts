import type {
  BacktestTrace,
  ExecutionEvent,
  FeatureSnapshot,
  OrderEvent,
  TimelineEvent,
} from '../../../types/trace'

export interface ReplayIndex {
  eventById: Map<string, TimelineEvent>
  eventIndexById: Map<string, number>
  signalEventBySignalId: Map<string, TimelineEvent>
  orderById: Map<string, { order: OrderEvent; event: TimelineEvent }>
  executionById: Map<string, { execution: ExecutionEvent; event: TimelineEvent }>
  featureById: Map<string, FeatureSnapshot>
}

export function createReplayIndex(trace: BacktestTrace): ReplayIndex {
  const eventById = new Map<string, TimelineEvent>()
  const eventIndexById = new Map<string, number>()
  const signalEventBySignalId = new Map<string, TimelineEvent>()
  const orderById = new Map<string, { order: OrderEvent; event: TimelineEvent }>()
  const executionById = new Map<string, { execution: ExecutionEvent; event: TimelineEvent }>()
  const featureById = new Map<string, FeatureSnapshot>()
  trace.timeline.forEach((event, index) => {
    eventById.set(event.event_id, event)
    eventIndexById.set(event.event_id, index)
    if (event.signal_evaluation.signal_id) {
      signalEventBySignalId.set(event.signal_evaluation.signal_id, event)
    }
    event.order_events.forEach((order) => orderById.set(order.order_id, { order, event }))
    event.execution_events.forEach((execution) => {
      executionById.set(execution.execution_id, { execution, event })
    })
    event.feature_snapshots.forEach((feature) => featureById.set(feature.feature_id, feature))
  })
  return {
    eventById,
    eventIndexById,
    signalEventBySignalId,
    orderById,
    executionById,
    featureById,
  }
}

export function adjacentBarId(
  trace: BacktestTrace,
  selectedEventId: string,
  direction: -1 | 1,
): string | null {
  const index = trace.timeline.findIndex((event) => event.event_id === selectedEventId)
  const target = index + direction
  return index >= 0 && target >= 0 && target < trace.timeline.length
    ? trace.timeline[target].event_id
    : null
}

export function adjacentSignalId(
  trace: BacktestTrace,
  selectedEventId: string,
  direction: -1 | 1,
): string | null {
  const index = trace.timeline.findIndex((event) => event.event_id === selectedEventId)
  if (index < 0) return null
  for (
    let candidate = index + direction;
    candidate >= 0 && candidate < trace.timeline.length;
    candidate += direction
  ) {
    if (trace.timeline[candidate].signal_evaluation.signal_id) {
      return trace.timeline[candidate].event_id
    }
  }
  return null
}

export function findExecutionEventForSignal(
  trace: BacktestTrace,
  signalId: string,
): TimelineEvent | null {
  return (
    trace.timeline.find((event) =>
      event.order_events.some((order) => order.source_signal_id === signalId),
    ) ?? null
  )
}

export function findSourceSignalEvent(
  event: TimelineEvent,
  index: ReplayIndex,
): TimelineEvent | null {
  const execution = event.execution_events[0]
  if (!execution) return null
  const linkedOrder = index.orderById.get(execution.source_order_id)
  return linkedOrder
    ? (index.signalEventBySignalId.get(linkedOrder.order.source_signal_id) ?? null)
    : null
}

