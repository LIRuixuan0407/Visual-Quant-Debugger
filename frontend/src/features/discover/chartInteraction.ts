export interface ChartBounds {
  left: number
  width: number
  height: number
}

/** Translate a browser pointer into an SVG viewBox x-coordinate.
 *
 * SVG's default xMidYMid/meet behavior can leave invisible horizontal
 * gutters. Accounting for them keeps the selected point directly above the
 * pointer even when the chart is constrained by a max-height.
 */
export function pointerToViewBoxX(
  clientX: number,
  bounds: ChartBounds,
  viewBoxWidth: number,
  viewBoxHeight: number,
) {
  if (bounds.width <= 0 || bounds.height <= 0) return 0
  const scale = Math.min(bounds.width / viewBoxWidth, bounds.height / viewBoxHeight)
  const renderedWidth = viewBoxWidth * scale
  const horizontalGutter = (bounds.width - renderedWidth) / 2
  return (clientX - bounds.left - horizontalGutter) / scale
}

export function nearestChartIndex(
  pointerX: number,
  count: number,
  plotStart: number,
  plotEnd: number,
) {
  if (count <= 1 || plotEnd <= plotStart) return 0
  const clamped = Math.min(plotEnd, Math.max(plotStart, pointerX))
  return Math.round(((clamped - plotStart) / (plotEnd - plotStart)) * (count - 1))
}
