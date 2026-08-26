import { goldenTrace } from '../../../test/fixtures/goldenTrace'
import {
  adjacentBarId,
  adjacentSignalId,
  createReplayIndex,
  findExecutionEventForSignal,
  findSourceSignalEvent,
} from './navigation'

test('bar and signal navigation respect boundaries and real signals', () => {
  expect(adjacentBarId(goldenTrace, 'timeline-000001', -1)).toBeNull()
  expect(adjacentBarId(goldenTrace, 'timeline-000001', 1)).toBe('timeline-000012')
  expect(adjacentBarId(goldenTrace, 'timeline-000013', 1)).toBeNull()
  expect(adjacentSignalId(goldenTrace, 'timeline-000001', 1)).toBe('timeline-000012')
  expect(adjacentSignalId(goldenTrace, 'timeline-000013', -1)).toBe('timeline-000012')
  expect(adjacentSignalId(goldenTrace, 'timeline-000012', 1)).toBeNull()
})

test('signal and execution indexes follow domain ids', () => {
  const index = createReplayIndex(goldenTrace)
  const execution = findExecutionEventForSignal(goldenTrace, 'signal-0001')
  expect(execution?.event_id).toBe('timeline-000013')
  expect(findSourceSignalEvent(execution!, index)?.event_id).toBe('timeline-000012')
  expect(index.featureById.get('feature-000060')?.name).toBe('zscore')
})

