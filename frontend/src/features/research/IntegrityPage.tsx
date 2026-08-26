import { useEffect, useState } from 'react'

import { getHypothesisIntegrity, getWorkspaceIntegrity } from '../../api/research'
import { useI18n } from '../../i18n/I18nProvider'
import type {
  HypothesisIntegrityReport,
  IntegrityFinding,
  WorkspaceIntegrityReport,
} from '../../types/research'

const shortId = (value: string) => value.length > 26 ? `${value.slice(0, 12)}…${value.slice(-9)}` : value

function dateTime(value: string) {
  return new Date(value).toLocaleString()
}

function FindingCard({ finding }: { finding: IntegrityFinding }) {
  const { tr } = useI18n()
  return <article className={`integrity-finding ${finding.severity.toLowerCase()}`}>
    <header>
      <span>{tr(finding.code.replaceAll('_', ' '))}</span>
      <b className={`integrity-severity ${finding.severity.toLowerCase()}`}>{tr(finding.severity)}</b>
    </header>
    <p>{tr(finding.reason)}</p>
    {finding.evidence.length > 0 && <ul>{finding.evidence.map((item, index) => <li key={index}><code>{item}</code></li>)}</ul>}
  </article>
}

export default function IntegrityPage() {
  const { tr } = useI18n()
  const [overview, setOverview] = useState<WorkspaceIntegrityReport | null>(null)
  const [report, setReport] = useState<HypothesisIntegrityReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let mounted = true
    void getWorkspaceIntegrity().then(
      async (result) => {
        if (!mounted) return
        setOverview(result)
        if (result.hypotheses[0]) {
          try {
            const detail = await getHypothesisIntegrity(result.hypotheses[0].hypothesis_id)
            if (mounted) setReport(detail)
          } catch (reason) {
            if (mounted) setError(reason instanceof Error ? reason.message : String(reason))
          }
        }
      },
      (reason) => { if (mounted) setError(reason instanceof Error ? reason.message : String(reason)) },
    )
    return () => { mounted = false }
  }, [])

  async function selectHypothesis(id: string) {
    setBusy(true)
    setError(null)
    try { setReport(await getHypothesisIntegrity(id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  const orderedFindings = report == null ? [] : [...report.findings].sort((a, b) => {
    const rank = { VIOLATION: 0, WARNING: 1, PASS: 2 } as const
    return rank[a.severity] - rank[b.severity]
  })

  return <main className="discover-shell research-workbench integrity-workspace">
    <section className="discover-title">
      <div><span className="section-kicker">{tr('Research Integrity Guardrails')}</span><h1>{tr('Research Integrity')}</h1><p>{tr('Audit every hypothesis against its recorded ledger, dataset revisions, time boundaries, and strategy semantics. Each check reports an explicit status and reason; nothing is modified automatically.')}</p></div>
      {overview && <span className={`bias-tag integrity-${overview.overall_status.toLowerCase()}`}>{tr(overview.overall_status)} · {overview.total_violations} {tr('violations')} · {overview.total_warnings} {tr('warnings')}</span>}
    </section>

    {error && <section className="workspace-panel research-error" role="alert">{tr(error)}</section>}

    <section className="snapshot-workspace-grid">
      <aside className="workspace-panel snapshot-ledger">
        <div className="section-heading"><h2>{tr('Hypotheses')}</h2><span>{overview?.hypotheses.length ?? 0}</span></div>
        {(overview == null || overview.hypotheses.length === 0) && <p className="empty-copy">{tr('No hypotheses to audit yet.')}</p>}
        {overview?.hypotheses.map((item) => <article key={item.hypothesis_id} className="snapshot-ledger-item">
          <button
            className={`snapshot-detail-button ${report?.hypothesis_id === item.hypothesis_id ? 'selected' : ''}`}
            disabled={busy}
            onClick={() => void selectHypothesis(item.hypothesis_id)}
          >
            <strong>{item.title}</strong>
            <span>{tr('Hypothesis')} r{item.revision} · {tr(item.lifecycle_status)}</span>
            <span className={`integrity-severity ${item.overall_status.toLowerCase()}`}>{tr(item.overall_status)} · {item.violation_count}/{item.warning_count}</span>
          </button>
        </article>)}
      </aside>

      <div className="snapshot-stack">
        {report == null && overview != null && overview.hypotheses.length === 0 && (
          <section className="workspace-panel"><p className="empty-copy">{tr('Create a hypothesis in Strategy Discovery first; the guardrails audit recorded research automatically.')}</p></section>
        )}

        {report && <>
          <section className="workspace-panel snapshot-identity">
            <div className="section-heading">
              <div><span className="section-kicker">{tr('Audited experiment')}</span><h2>{report.title}</h2></div>
              <b className={`integrity-severity ${report.overall_status.toLowerCase()}`}>{tr(report.overall_status)}</b>
            </div>
            <div className="snapshot-hashes">
              <span><small>{tr('Hypothesis ID')}</small><code>{shortId(report.hypothesis_id)}</code></span>
              <span><small>{tr('Revision')}</small><code>r{report.revision}</code></span>
              <span><small>{tr('Lifecycle')}</small><code>{tr(report.lifecycle_status)}</code></span>
              <span><small>{tr('Checked')}</small><strong>{dateTime(report.checked_at)}</strong></span>
            </div>
            <p>{tr(report.disclosure)}</p>
          </section>

          <section className="workspace-panel">
            <div className="section-heading"><div><span className="section-kicker">{tr('Explicit status and reason')}</span><h2>{tr('Guardrail checks')}</h2></div><span>{report.violation_count} {tr('violations')} · {report.warning_count} {tr('warnings')}</span></div>
            <div className="integrity-findings">{orderedFindings.map((finding) => <FindingCard key={finding.code} finding={finding} />)}</div>
          </section>
        </>}
      </div>
    </section>
  </main>
}
