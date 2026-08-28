import { useMemo, type KeyboardEvent, type MouseEvent } from 'react'

import type { CorporateActionEvent, TimelineEvent } from '../../types/trace'
import { useI18n } from '../../i18n/I18nProvider'
import { formatCurrency, formatTimestamp } from './utils/format'

interface ReplayTimelineProps {
  events: TimelineEvent[]
  selectedEventId: string
  onSelect: (eventId: string) => void
  corporateActions?: CorporateActionEvent[]
}

const VIEWBOX_WIDTH = 1000
const VIEWBOX_HEIGHT = 200
const PLOT_START_X = 30
const PLOT_END_X = 970

function ReplayTimeline({ events, selectedEventId, onSelect, corporateActions = [] }: ReplayTimelineProps) {
  const { tr } = useI18n()
  const chart = useMemo(() => {
    const equities = events.map((event) => event.pnl_snapshot.equity)
    const minimum = Math.min(...equities)
    const maximum = Math.max(...equities)
    const range = maximum - minimum || 1
    return events.map((event, index) => ({
      event,
      x: PLOT_START_X + (index / Math.max(events.length - 1, 1)) * (PLOT_END_X - PLOT_START_X),
      y: 160 - ((event.pnl_snapshot.equity - minimum) / range) * 110,
    }))
  }, [events])

  const selected = events.find((event) => event.event_id === selectedEventId) ?? events[0]
  const selectedTime = formatTimestamp(selected.timestamp)
  const points = chart.map((point) => `${point.x},${point.y}`).join(' ')
  const actionTargets = corporateActions.map((action) => ({
    action,
    event: events.find((event) => event.timestamp >= action.timestamp) ?? events.at(-1)!,
  }))

  function handleKeyDown(event: KeyboardEvent<SVGCircleElement>, eventId: string) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect(eventId)
    }
  }

  function handleChartHover(event: MouseEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    if (bounds.width <= 0 || bounds.height <= 0) return

    // The default SVG preserveAspectRatio mode is xMidYMid meet. On wide
    // containers this creates invisible horizontal gutters, which must not be
    // treated as part of the plot when translating the pointer coordinate.
    const scale = Math.min(bounds.width / VIEWBOX_WIDTH, bounds.height / VIEWBOX_HEIGHT)
    const renderedViewBoxWidth = VIEWBOX_WIDTH * scale
    const horizontalGutter = (bounds.width - renderedViewBoxWidth) / 2
    const viewBoxX = (event.clientX - bounds.left - horizontalGutter) / scale
    const pointerX = Math.min(PLOT_END_X, Math.max(PLOT_START_X, viewBoxX))
    const nearest = chart.reduce((closest, point) => (
      Math.abs(point.x - pointerX) < Math.abs(closest.x - pointerX) ? point : closest
    ))

    if (nearest.event.event_id !== selectedEventId) onSelect(nearest.event.event_id)
  }

  return (
    <section className="timeline-section" aria-labelledby="timeline-title">
      <div className="section-heading timeline-heading">
        <h2 id="timeline-title">{tr('Equity timeline')}</h2>
        <div className="current-time" aria-live="polite">
          <span>{selectedTime.date}</span>
          <strong>{selectedTime.time}</strong>
          <small>{formatCurrency(selected.pnl_snapshot.equity)} {tr('Equity')}</small>
        </div>
      </div>
      <svg
        className="equity-chart"
        viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
        role="img"
        aria-label={tr('Equity curve with signal and execution markers')}
        onMouseMove={handleChartHover}
      >
        <line className="chart-baseline" x1={PLOT_START_X} x2={PLOT_END_X} y1="176" y2="176" />
        <polyline className="equity-line" points={points} />
        {chart.map(({ event, x, y }) => {
          const isSelected = event.event_id === selectedEventId
          const isSignal = event.signal_evaluation.signal_id !== null
          const isExecution = event.execution_events.length > 0
          const actionCount = actionTargets.filter((item) => item.event.event_id === event.event_id).length
          const signalY = Math.max(18, y - 13)
          const executionY = Math.min(171, y + 13)
          const label = `Select ${formatTimestamp(event.timestamp).date}${isSignal ? ', signal' : ''}${isExecution ? ', execution' : ''}${actionCount ? `, ${actionCount} corporate action` : ''}`
          return (
            <g key={event.event_id} data-event-id={event.event_id}>
              {isSelected && <line className="selected-guide" x1={x} x2={x} y1="28" y2="176" />}
              {isSignal && <><line className="event-marker-link signal" x1={x} x2={x} y1={y} y2={signalY} /><rect className="signal-marker" x={x - 4} y={signalY - 4} width="8" height="8" rx="1.5" /></>}
              {isExecution && <><line className="event-marker-link execution" x1={x} x2={x} y1={y} y2={executionY} /><circle className="execution-marker" cx={x} cy={executionY} r="4" /></>}
              {actionCount > 0 && <><line className="event-marker-link corporate-action" x1={x} x2={x} y1={y} y2="31" /><path className="corporate-action-marker" d={`M ${x} 20 l 6 6 l -6 6 l -6 -6 z`} /></>}
              <circle
                className={isSelected ? 'event-hit selected-event' : 'event-hit'}
                cx={x}
                cy={y}
                r={isSelected ? 7 : 4}
                role="button"
                tabIndex={0}
                aria-label={label}
                onClick={() => onSelect(event.event_id)}
                onKeyDown={(keyboardEvent) => handleKeyDown(keyboardEvent, event.event_id)}
              />
            </g>
          )
        })}
        <text x={PLOT_START_X} y="196">{formatTimestamp(events[0].timestamp).date}</text>
        <text x={PLOT_END_X} y="196" textAnchor="end">{formatTimestamp(events.at(-1)!.timestamp).date}</text>
      </svg>
      <div className="chart-legend" aria-label={tr('Timeline legend')}>
        <span><i className="legend-current" /> {tr('Current event')}</span>
        <span><i className="legend-signal" /> {tr('Trading signal')}</span>
        <span><i className="legend-execution" /> {tr('Execution')}</span>
        {corporateActions.length > 0 && <span><i className="legend-corporate-action" /> {tr('Corporate Action')}</span>}
      </div>
      {actionTargets.length > 0 && <div className="corporate-action-jumps" aria-label={tr('Corporate Action events')}>{actionTargets.map(({ action, event }) => <button key={action.action_id} onClick={() => onSelect(event.event_id)}><strong>{action.symbol} · {tr(action.action_type.replaceAll('_', ' '))}</strong><span>{formatTimestamp(action.timestamp).date}</span><em>{tr(action.status.replaceAll('_', ' '))}</em></button>)}</div>}
    </section>
  )
}

export default ReplayTimeline
