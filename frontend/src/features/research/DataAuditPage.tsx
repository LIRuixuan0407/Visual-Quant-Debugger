import { useEffect, useRef, useState } from 'react'

import {
  createDataAudit,
  getDataAudit,
  getDataAudits,
  verifyDataAuditSource,
} from '../../api/dataAudit'
import { useI18n } from '../../i18n/I18nProvider'
import type {
  AuditRootType,
  DataAuditDetail,
  DataAuditFinding,
  DataAuditSummary,
} from '../../types/dataAudit'

interface DataAuditServices {
  list: typeof getDataAudits
  get: typeof getDataAudit
  create: typeof createDataAudit
  verify: typeof verifyDataAuditSource
}

interface DataAuditPageProps {
  services?: Partial<DataAuditServices>
}

type FindingGroup = 'Data Quality' | 'PIT Dependencies' | 'Future Return' | 'Fundamentals' | 'Universe' | 'Provenance'

const ROOT_TYPES: AuditRootType[] = ['DATASET', 'FACTOR_RESEARCH', 'RUN']
const GROUPS: FindingGroup[] = ['Data Quality', 'PIT Dependencies', 'Future Return', 'Fundamentals', 'Universe', 'Provenance']
const shortId = (value: string) => value.length > 30 ? `${value.slice(0, 14)}…${value.slice(-10)}` : value
const dateTime = (value: string) => new Date(value).toLocaleString()

function findingGroup(code: string): FindingGroup {
  if (code.includes('FUNDAMENTAL')) return 'Fundamentals'
  if (code.includes('UNIVERSE')) return 'Universe'
  if (code.includes('FUTURE') || code.includes('TARGET')) return 'Future Return'
  if (code.includes('DEPENDENCY') || code.includes('AVAILABLE_AFTER') || code.includes('WINDOW') || code.includes('TRACE')) return 'PIT Dependencies'
  if (code.includes('PROVENANCE') || code.includes('FINGERPRINT') || code.includes('REVISION_DRIFT') || code.includes('TIMEZONE') || code.includes('COVERAGE')) return 'Provenance'
  return 'Data Quality'
}

function FindingCard({ finding }: { finding: DataAuditFinding }) {
  const { tr } = useI18n()
  return <article className={`data-audit-finding ${finding.severity.toLowerCase()}`}>
    <header>
      <div><strong>{tr(finding.code.replaceAll('_', ' '))}</strong><code title={finding.subject}>{shortId(finding.subject)}</code></div>
      <b className={`audit-severity ${finding.severity.toLowerCase()}`}>{tr(finding.severity)}</b>
    </header>
    <p>{tr(finding.reason)}</p>
    <span className="audit-checked">{finding.affected_count} {tr('affected')} · {finding.checked_count} {tr('checked')}</span>
    {finding.evidence.length > 0 && <ul>{finding.evidence.map((item, index) => <li key={`${index}:${item}`}><code>{item}</code></li>)}</ul>}
  </article>
}

export default function DataAuditPage({ services }: DataAuditPageProps) {
  const { tr } = useI18n()
  const listAudits = services?.list ?? getDataAudits
  const loadAudit = services?.get ?? getDataAudit
  const runAudit = services?.create ?? createDataAudit
  const verifySource = services?.verify ?? verifyDataAuditSource
  const parameters = new URLSearchParams(window.location.search)
  const requestedType = parameters.get('root_type')
  const requestedId = parameters.get('root_id') ?? ''
  const requestedAuditId = parameters.get('audit_id')
  const initialType = ROOT_TYPES.includes(requestedType as AuditRootType) ? requestedType as AuditRootType : 'DATASET'
  const autoRun = parameters.get('run') === '1' && requestedId.length > 0
  const autoRunStarted = useRef(false)
  const [rootType, setRootType] = useState<AuditRootType>(initialType)
  const [rootId, setRootId] = useState(requestedId)
  const [audits, setAudits] = useState<DataAuditSummary[]>([])
  const [detail, setDetail] = useState<DataAuditDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    async function load() {
      setLoading(true)
      setError(null)
      try {
        if (autoRun && !autoRunStarted.current) {
          autoRunStarted.current = true
          const created = await runAudit(initialType, requestedId)
          if (!active) return
          setDetail(created)
          setAudits(await listAudits())
        } else {
          const rows = await listAudits()
          if (!active) return
          setAudits(rows)
          const selected = rows.find((item) => item.audit_id === requestedAuditId) ?? rows[0]
          if (selected) {
            const loaded = await loadAudit(selected.audit_id)
            if (active) setDetail(loaded)
          }
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    return () => { active = false }
  }, [autoRun, initialType, listAudits, loadAudit, requestedAuditId, requestedId, runAudit])

  async function create() {
    if (!rootId.trim()) return
    setBusy(true)
    setError(null)
    try {
      const created = await runAudit(rootType, rootId.trim())
      setDetail(created)
      setAudits(await listAudits())
      window.history.replaceState({}, '', `/data-audits?audit_id=${encodeURIComponent(created.audit.audit_id)}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function select(auditId: string) {
    setBusy(true)
    setError(null)
    try {
      setDetail(await loadAudit(auditId))
      window.history.replaceState({}, '', `/data-audits?audit_id=${encodeURIComponent(auditId)}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function verify() {
    if (!detail) return
    setBusy(true)
    setError(null)
    try {
      const verification = await verifySource(detail.audit.audit_id)
      setDetail({ ...detail, source_state: verification.source_state, current_source_fingerprints: verification.current_source_fingerprints, newer_dataset_revision_available: verification.newer_dataset_revision_available, latest_dataset_id: verification.latest_dataset_id, latest_dataset_revision: verification.latest_dataset_revision })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const grouped = new Map<FindingGroup, DataAuditFinding[]>(GROUPS.map((group) => [group, []]))
  detail?.audit.findings.forEach((finding) => grouped.get(findingGroup(finding.code))?.push(finding))

  return <main className="discover-shell data-audit-workspace">
    <header className="workspace-title discover-title">
      <div><span className="section-kicker">{tr('IMMUTABLE EVIDENCE')}</span><h1>{tr('Data Quality & PIT Audit')}</h1><p>{tr('Inspect data quality, point-in-time dependencies, future-return boundaries, fundamentals, universe construction, and source provenance from backend evidence.')}</p></div>
      {detail && <div className="audit-title-status"><b className={`audit-status ${detail.audit.status.toLowerCase()}`}>{tr(detail.audit.status)}</b><span>{tr('Source')} · <strong className={`source-state ${detail.source_state.toLowerCase()}`}>{tr(detail.source_state)}</strong></span></div>}
    </header>

    {error && <section className="workspace-panel research-error" role="alert"><strong>{tr('Data Audit failed')}</strong><span>{tr(error)}</span></section>}

    <section className="workspace-panel audit-command-bar" aria-label={tr('Run Data Audit')}>
      <label><span>{tr('Root type')}</span><select value={rootType} onChange={(event) => setRootType(event.target.value as AuditRootType)}>{ROOT_TYPES.map((item) => <option key={item} value={item}>{tr(item.replaceAll('_', ' '))}</option>)}</select></label>
      <label className="audit-root-id"><span>{tr('Root ID')}</span><input value={rootId} onChange={(event) => setRootId(event.target.value)} placeholder={rootType === 'DATASET' ? 'dataset-id' : rootType === 'RUN' ? 'run-…' : 'factor-research-…'} /></label>
      <button className="primary-button" disabled={busy || !rootId.trim()} onClick={() => void create()}>{tr(busy ? 'Auditing…' : 'Run Data Audit')}</button>
    </section>

    <section className="audit-workspace-grid">
      <aside className="workspace-panel audit-ledger">
        <div className="section-heading"><div><span className="section-kicker">{tr('APPEND-ONLY')}</span><h2>{tr('Audit Ledger')}</h2></div><span>{audits.length}</span></div>
        {loading && <p className="loading-line">{tr('Loading Data Audits…')}</p>}
        {!loading && audits.length === 0 && <div className="research-empty"><strong>{tr('No Data Audits yet.')}</strong><p>{tr('Choose a recorded dataset, factor study, or run to create an immutable audit.')}</p></div>}
        {audits.map((audit) => <button key={audit.audit_id} className={`audit-ledger-item ${detail?.audit.audit_id === audit.audit_id ? 'selected' : ''}`} disabled={busy} onClick={() => void select(audit.audit_id)}>
          <span><strong>{tr(audit.root_type.replaceAll('_', ' '))}</strong><code title={audit.root_id}>{shortId(audit.root_id)}</code></span>
          <b className={`audit-status ${audit.status.toLowerCase()}`}>{tr(audit.status)}</b>
          <small>{dateTime(audit.created_at)} · {audit.finding_count} {tr('findings')} · {audit.violation_count}/{audit.warning_count}</small>
        </button>)}
      </aside>

      <div className="audit-detail-stack">
        {!loading && detail == null && audits.length > 0 && <section className="workspace-panel"><p className="empty-copy">{tr('Select an audit record to inspect its evidence.')}</p></section>}
        {detail && <>
          <section className="workspace-panel audit-identity">
            <div className="section-heading"><div><span className="section-kicker">{tr('AUDIT RECORD')}</span><h2>{tr(detail.audit.root_type.replaceAll('_', ' '))} · {shortId(detail.audit.root_id)}</h2></div><button className="secondary-button" disabled={busy} onClick={() => void verify()}>{tr(busy ? 'Verifying…' : 'Verify current source')}</button></div>
            {detail.newer_dataset_revision_available && <div className="audit-revision-notice"><strong>{tr('NEWER REVISION AVAILABLE')}</strong><span>{detail.latest_dataset_revision ? `r${detail.latest_dataset_revision}` : ''} · {detail.latest_dataset_id}</span><small>{tr('The recorded source still matches; a newer immutable revision is available separately.')}</small></div>}
            <div className="audit-counts"><div><span>{tr('Observations')}</span><strong>{detail.audit.checked_observations}</strong></div><div><span>{tr('Dependencies')}</span><strong>{detail.audit.checked_dependencies}</strong></div><div><span>{tr('Future returns')}</span><strong>{detail.audit.checked_future_returns}</strong></div><div><span>{tr('Fundamental inputs')}</span><strong>{detail.audit.checked_fundamental_inputs}</strong></div></div>
            <dl className="audit-source-facts"><div><dt>{tr('Audit ID')}</dt><dd><code>{detail.audit.audit_id}</code></dd></div><div><dt>{tr('Created')}</dt><dd>{dateTime(detail.audit.created_at)}</dd></div>{Object.entries(detail.audit.source_fingerprints).map(([source, fingerprint]) => <div key={source}><dt>{source}</dt><dd><span>{tr('Recorded')}</span><code title={fingerprint}>{shortId(fingerprint)}</code></dd><dd><span>{tr('Current')}</span><code title={detail.current_source_fingerprints[source] ?? tr('MISSING')}>{shortId(detail.current_source_fingerprints[source] ?? tr('MISSING'))}</code></dd></div>)}</dl>
          </section>

          {GROUPS.map((group) => {
            const findings = grouped.get(group) ?? []
            return findings.length > 0 && <section className="workspace-panel audit-finding-group" key={group}><div className="section-heading"><h2>{tr(group)}</h2><span>{findings.length} {tr('checks')}</span></div><div className="audit-findings">{findings.map((finding) => <FindingCard key={finding.code} finding={finding} />)}</div></section>
          })}

          <section className="workspace-panel audit-disclosures"><div className="section-heading"><h2>{tr('Disclosures')}</h2><span>{detail.audit.disclosures.length}</span></div><ul>{detail.audit.disclosures.map((item, index) => <li key={index}>{tr(item)}</li>)}</ul></section>
        </>}
      </div>
    </section>
  </main>
}
