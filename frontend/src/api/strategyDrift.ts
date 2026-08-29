import { readJson } from './client'
import type { CreateStrategyDriftReport, StrategyDriftReport, StrategyDriftSummary } from '../types/strategyDrift'
import { addCreatedObjectToCurrentWorkspace } from './workspaces'

export async function getStrategyDriftReports(): Promise<StrategyDriftSummary[]> {
  return (await readJson(await fetch('/api/strategy-drift'), 'Strategy Drift list')) as StrategyDriftSummary[]
}

export async function getStrategyDriftReport(reportId: string): Promise<StrategyDriftReport> {
  return (await readJson(
    await fetch(`/api/strategy-drift/${encodeURIComponent(reportId)}`),
    'Strategy Drift detail',
  )) as StrategyDriftReport
}

export async function createStrategyDriftReport(request: CreateStrategyDriftReport): Promise<StrategyDriftReport> {
  const created = (await readJson(
    await fetch('/api/strategy-drift', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    }),
    'Strategy Drift creation',
  )) as StrategyDriftReport
  await addCreatedObjectToCurrentWorkspace('DRIFT_REPORT', created.drift_report_id)
  return created
}
