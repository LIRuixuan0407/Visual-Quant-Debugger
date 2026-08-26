import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, test, vi } from 'vitest'

import { goldenTrace } from '../../test/fixtures/goldenTrace'
import type { BacktestTrace } from '../../types/trace'
import { nativeTraceCapabilities } from './capabilities'
import ReplayPage from './ReplayPage'

test('replays warm-up, signal lineage, and next-bar execution from trace data', () => {
  render(<ReplayPage trace={goldenTrace} />)

  expect(screen.getByRole('button', { name: /Previous bar/ })).toBeDisabled()
  expect(screen.getByRole('heading', { name: 'Warmup' })).toBeInTheDocument()
  expect(screen.getByText('No execution at this timestamp.')).toBeInTheDocument()
  expect(screen.getByText('No look-ahead issues')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Next signal' }))

  expect(screen.getAllByText(/Jan 17, 2024/).length).toBeGreaterThan(0)
  expect(screen.getByRole('heading', { name: 'Short spread' })).toBeInTheDocument()
  expect(screen.getByText('Signal · signal-0001')).toBeInTheDocument()
  const market = screen.getByRole('heading', { name: 'Observed at this bar' }).closest<HTMLElement>('section')
  expect(market).not.toBeNull()
  expect(within(market!).getByText('109.55')).toBeInTheDocument()
  expect(within(market!).getByText('54.40')).toBeInTheDocument()
  fireEvent.click(screen.getByText('Decision details'))
  expect(screen.getByText('TRUE')).toBeInTheDocument()
  expect(screen.getByLabelText('State transition from FLAT to SHORT_SPREAD')).toBeInTheDocument()

  expect(screen.getByText('Calculation details').closest('details')).toHaveAttribute('open')
  fireEvent.click(screen.getByLabelText('Inspect Spread'))
  const featureInspector = screen.getByRole('heading', { name: 'Spread' }).closest<HTMLElement>('.feature-inspector')
  expect(featureInspector).not.toBeNull()
  expect(within(featureInspector!).getByRole('heading', { name: 'Spread' })).toBeInTheDocument()
  expect(within(featureInspector!).getByText('price_A - hedge_ratio * price_B')).toBeInTheDocument()
  expect(within(featureInspector!).getByText('Hedge ratio')).toBeInTheDocument()
  expect(within(featureInspector!).getByText('Jan 11, 2024 → Jan 17, 2024')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /Next bar/ }))

  expect(screen.getByRole('heading', { name: 'Hold' })).toBeInTheDocument()
  expect(screen.getAllByText('ASSET_A').length).toBeGreaterThan(0)
  expect(screen.getAllByText('ASSET_B').length).toBeGreaterThan(0)
  expect(screen.getAllByText('Executed at').length).toBe(2)
  expect(screen.getByText('$99,980.00')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '← View source signal' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Next bar/ })).toBeDisabled()

  fireEvent.click(screen.getByRole('button', { name: '← View source signal' }))
  expect(screen.getByText('Signal · signal-0001')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'View execution →' })).toBeInTheDocument()
})

test('renders explicit empty and no-signal states with safe navigation boundaries', () => {
  const { rerender } = render(<ReplayPage trace={{ ...goldenTrace, timeline: [] }} />)
  expect(screen.getByRole('heading', { name: 'No timeline events were recorded.' })).toBeInTheDocument()

  const noSignalTimeline = goldenTrace.timeline.map((event) => ({
    ...event,
    signal_evaluation: { ...event.signal_evaluation, signal_id: null },
    order_events: [],
    execution_events: [],
  }))
  rerender(<ReplayPage trace={{ ...goldenTrace, timeline: noSignalTimeline }} />)

  expect(screen.getByText('No trading signals were generated for this backtest.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Previous signal' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Next signal' })).toBeDisabled()
})

test('anchors signal and execution markers to their equity points', () => {
  const { container } = render(<ReplayPage trace={goldenTrace} />)
  const signalEvent = goldenTrace.timeline.find((event) => event.signal_evaluation.signal_id !== null)!
  const executionEvent = goldenTrace.timeline.find((event) => event.execution_events.length > 0)!
  const signalGroup = container.querySelector(`[data-event-id="${signalEvent.event_id}"]`)!
  const executionGroup = container.querySelector(`[data-event-id="${executionEvent.event_id}"]`)!
  const signalPointY = Number(signalGroup.querySelector('.event-hit')!.getAttribute('cy'))
  const signalPointX = Number(signalGroup.querySelector('.event-hit')!.getAttribute('cx'))
  const signalMarker = signalGroup.querySelector('.signal-marker')!
  const signalMarkerCenterX = Number(signalMarker.getAttribute('x')) + 4
  const signalMarkerCenterY = Number(signalMarker.getAttribute('y')) + 4
  const executionPointY = Number(executionGroup.querySelector('.event-hit')!.getAttribute('cy'))
  const executionPointX = Number(executionGroup.querySelector('.event-hit')!.getAttribute('cx'))
  const executionMarker = executionGroup.querySelector('.execution-marker')!
  const executionMarkerX = Number(executionMarker.getAttribute('cx'))
  const executionMarkerY = Number(executionMarker.getAttribute('cy'))

  expect(signalMarkerCenterX).toBe(signalPointX)
  expect(executionMarkerX).toBe(executionPointX)
  expect(Math.abs(signalPointY - signalMarkerCenterY)).toBeGreaterThan(0)
  expect(Math.abs(signalPointY - signalMarkerCenterY)).toBeLessThanOrEqual(13)
  expect(Math.abs(executionPointY - executionMarkerY)).toBeGreaterThan(0)
  expect(Math.abs(executionPointY - executionMarkerY)).toBeLessThanOrEqual(13)
  expect(signalMarkerCenterY).not.toBe(176)
  expect(executionMarkerY).not.toBe(188)
})

test('selects the nearest timeline event as the pointer moves across the chart', () => {
  const { container } = render(<ReplayPage trace={goldenTrace} />)
  const chart = screen.getByRole('img', { name: 'Equity curve with signal and execution markers' })
  vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({
    bottom: 230,
    height: 230,
    left: 0,
    right: 1600,
    top: 0,
    width: 1600,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect)

  // A 1600×230 element renders the 1000×200 viewBox at 1.15× with 225 px
  // gutters. The last plot point is therefore at 225 + 970 × 1.15 = 1340.5.
  fireEvent.mouseMove(chart, { clientX: 1340.5 })

  const finalEvent = goldenTrace.timeline.at(-1)!
  const finalPoint = container.querySelector(`[data-event-id="${finalEvent.event_id}"] .event-hit`)
  expect(finalPoint).toHaveClass('selected-event')
})

test('selects a trace event supplied by an Autopsy jump', () => {
  render(<ReplayPage trace={goldenTrace} initialEventId="timeline-000013" />)

  expect(screen.getByRole('heading', { name: 'Hold' })).toBeInTheDocument()
  expect(screen.getAllByText('Executed at').length).toBe(2)
  expect(screen.getByText('$99,980.00')).toBeInTheDocument()
})

test('does not turn missing framework evidence into a safety or zero-cost claim', () => {
  const frameworkTrace: BacktestTrace = {
    ...goldenTrace,
    metadata: {
      ...goldenTrace.metadata,
      runtime: {
        kind: 'framework', adapter_id: 'vectorbt', adapter_version: '1.0.0', framework_name: 'vectorbt', framework_version: '1.1.0',
        execution_owner: 'vectorbt', trace_fidelity: 'BASIC', determinism: 'UNVERIFIED', random_seed: null, python_executable: null, historical_research_only: true,
        trace_capabilities: { ...nativeTraceCapabilities, feature_lineage: 'UNAVAILABLE', decision_conditions: 'UNAVAILABLE', data_dependencies: 'UNAVAILABLE', point_in_time_proven: 'UNAVAILABLE', fees: 'UNAVAILABLE', slippage: 'UNAVAILABLE' },
      },
    },
  }
  render(<ReplayPage trace={frameworkTrace} />)
  expect(screen.getByText('vectorbt · BASIC')).toBeInTheDocument()
  expect(screen.getByText('This Framework Run does not provide verifiable feature dependencies. · BASIC')).toBeInTheDocument()
  expect(screen.getByText('Point-in-time data dependency provenance is not available for this adapter.')).toBeInTheDocument()
  expect(screen.queryByText('No look-ahead issues')).not.toBeInTheDocument()
  expect(screen.getAllByText('Not available').length).toBeGreaterThan(1)
})
