export type AuditRootType = 'DATASET' | 'FACTOR_RESEARCH' | 'RUN'
export type AuditSeverity = 'PASS' | 'INFO' | 'WARNING' | 'VIOLATION' | 'INSUFFICIENT_EVIDENCE'
export type AuditStatus = 'PASS' | 'WARNING' | 'VIOLATION' | 'INCOMPLETE'
export type AuditSourceState = 'MATCHES' | 'CHANGED' | 'MISSING'

export interface DataAuditFinding {
  code: string
  severity: AuditSeverity
  subject: string
  reason: string
  evidence: string[]
  checked_count: number
  affected_count: number
}

export interface DataAuditRecord {
  audit_version: '1.0'
  audit_id: string
  root_type: AuditRootType
  root_id: string
  created_at: string
  source_fingerprints: Record<string, string>
  status: AuditStatus
  findings: DataAuditFinding[]
  checked_observations: number
  checked_dependencies: number
  checked_future_returns: number
  checked_fundamental_inputs: number
  disclosures: string[]
}

export interface DataAuditSummary {
  audit_id: string
  root_type: AuditRootType
  root_id: string
  created_at: string
  status: AuditStatus
  finding_count: number
  violation_count: number
  warning_count: number
}

export interface DataAuditDetail {
  audit: DataAuditRecord
  source_state: AuditSourceState
  current_source_fingerprints: Record<string, string>
  newer_dataset_revision_available?: boolean
  latest_dataset_id?: string | null
  latest_dataset_revision?: number | null
}

export interface DataAuditSourceVerification {
  audit_id: string
  source_state: AuditSourceState
  recorded_source_fingerprints: Record<string, string>
  current_source_fingerprints: Record<string, string>
  newer_dataset_revision_available?: boolean
  latest_dataset_id?: string | null
  latest_dataset_revision?: number | null
}
