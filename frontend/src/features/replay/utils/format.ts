const numberFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 })
const priceFormatter = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
})

export function formatNumber(value: number | null, digits = 4): string {
  if (value === null || !Number.isFinite(value)) return typeof document !== 'undefined' && document.documentElement.lang === 'zh-CN' ? '暂无数据' : 'Not available'
  if (digits === 4) return numberFormatter.format(value)
  return value.toFixed(digits)
}

export function formatPrice(value: number): string {
  return priceFormatter.format(value)
}

export function formatCurrency(value: number, currency = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatPercent(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'percent',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatQuantity(value: number): string {
  return numberFormatter.format(value)
}

export function formatTimestamp(value: string): { date: string; time: string } {
  const date = new Date(value)
  const locale = typeof document !== 'undefined' && document.documentElement.lang === 'zh-CN' ? 'zh-CN' : 'en-US'
  return {
    date: new Intl.DateTimeFormat(locale, {
      month: 'short',
      day: '2-digit',
      year: 'numeric',
      timeZone: 'UTC',
    }).format(date),
    time: `${new Intl.DateTimeFormat('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: 'UTC',
    }).format(date)} UTC`,
  }
}

export function humanize(value: string): string {
  return value.toLowerCase().replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
}
