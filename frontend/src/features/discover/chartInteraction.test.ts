import { describe, expect, it } from 'vitest'

import { nearestChartIndex, pointerToViewBoxX } from './chartInteraction'

describe('research chart pointer mapping', () => {
  it('removes SVG meet-mode gutters before translating the pointer', () => {
    const scale = 300 / 260
    const gutter = (1920 - 920 * scale) / 2
    const clientX = 25 + gutter + 460 * scale

    expect(pointerToViewBoxX(clientX, { left: 25, width: 1920, height: 300 }, 920, 260)).toBeCloseTo(460)
  })

  it('selects the nearest horizontal observation and clamps outside the plot', () => {
    expect(nearestChartIndex(460, 11, 12, 908)).toBe(5)
    expect(nearestChartIndex(-100, 11, 12, 908)).toBe(0)
    expect(nearestChartIndex(2_000, 11, 12, 908)).toBe(10)
  })
})
