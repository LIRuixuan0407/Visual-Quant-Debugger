import { readJson } from './client'
import type {
  AuditRootType,
  DataAuditDetail,
  DataAuditSourceVerification,
  DataAuditSummary,
} from '../types/dataAudit'

export async function getDataAudits(): Promise<DataAuditSummary[]> {
  return await readJson(await fetch('/api/data-audits'), 'Data Audit list') as DataAuditSummary[]
}

export async function getDataAudit(auditId: string): Promise<DataAuditDetail> {
  return await readJson(await fetch(`/api/data-audits/${encodeURIComponent(auditId)}`), 'Data Audit detail') as DataAuditDetail
}

export async function createDataAudit(rootType: AuditRootType, rootId: string): Promise<DataAuditDetail> {
  return await readJson(await fetch('/api/data-audits', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ root_type: rootType, root_id: rootId }),
  }), 'Data Audit creation') as DataAuditDetail
}

export async function verifyDataAuditSource(auditId: string): Promise<DataAuditSourceVerification> {
  return await readJson(await fetch(`/api/data-audits/${encodeURIComponent(auditId)}/verify-source`, { method: 'POST' }), 'Data Audit source verification') as DataAuditSourceVerification
}
