import { readJson } from './client'
import type { PnLAutopsyReport } from '../types/autopsy'

function isAutopsyReport(value: unknown): value is PnLAutopsyReport {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  const summary = item.summary as Record<string, unknown> | undefined
  return (
    item.report_version === '1.0' &&
    typeof item.source_run === 'object' && item.source_run !== null &&
    typeof summary === 'object' && summary !== null &&
    typeof summary.gross_pnl === 'number' && typeof summary.net_pnl === 'number' &&
    typeof item.reconciliation === 'object' && item.reconciliation !== null &&
    typeof item.periods === 'object' && item.periods !== null &&
    typeof item.trades === 'object' && item.trades !== null &&
    Array.isArray(item.drawdowns)
  )
}

export async function getPnLAutopsy(traceId: string): Promise<PnLAutopsyReport> {
  const endpoint = `/api/traces/${encodeURIComponent(traceId)}/pnl-autopsy`
  const response = await fetch(endpoint)
  const body = await readJson(response, `GET ${endpoint}`)
  if (!isAutopsyReport(body)) throw new Error(`GET ${endpoint} returned a malformed P&L Autopsy report.`)
  return body
}

