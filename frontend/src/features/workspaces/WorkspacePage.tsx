import { useCallback, useEffect, useState } from 'react'

import {
  archiveWorkspace,
  createWorkspace,
  getWorkspace,
  getWorkspaceIntegrity,
  getWorkspaceMemberships,
  removeWorkspaceMembership,
  restoreWorkspace,
  updateWorkspace,
} from '../../api/workspaces'
import { useI18n } from '../../i18n/I18nProvider'
import type { WorkspaceIntegrity, WorkspaceMembershipView, WorkspaceOverview } from '../../types/workspace'
import { useWorkspace } from './WorkspaceContext'

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toISOString().replace('T', ' ').slice(0, 16) + ' UTC'
}

export default function WorkspacePage() {
  const { tr } = useI18n()
  const { workspaces, currentWorkspace, refresh: refreshContext, setCurrentWorkspace } = useWorkspace()
  const [selectedId, setSelectedId] = useState<string | null>(currentWorkspace?.workspace_id ?? null)
  const [overview, setOverview] = useState<WorkspaceOverview | null>(null)
  const [memberships, setMemberships] = useState<WorkspaceMembershipView[]>([])
  const [integrity, setIntegrity] = useState<WorkspaceIntegrity | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [createName, setCreateName] = useState('')
  const [createDescription, setCreateDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (workspaceId: string) => {
    setError(null)
    try {
      const [nextOverview, nextMemberships, nextIntegrity] = await Promise.all([
        getWorkspace(workspaceId), getWorkspaceMemberships(workspaceId), getWorkspaceIntegrity(workspaceId),
      ])
      setOverview(nextOverview); setMemberships(nextMemberships); setIntegrity(nextIntegrity)
      setName(nextOverview.workspace.name); setDescription(nextOverview.workspace.description ?? '')
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }, [])

  useEffect(() => {
    const chosen = workspaces.find((workspace) => workspace.workspace_id === selectedId)
      ?? workspaces.find((workspace) => workspace.workspace_id === currentWorkspace?.workspace_id)
      ?? workspaces[0]
    if (!chosen) return
    const timer = window.setTimeout(() => {
      if (chosen.workspace_id !== selectedId) setSelectedId(chosen.workspace_id)
      void load(chosen.workspace_id)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [currentWorkspace?.workspace_id, load, selectedId, workspaces])

  async function create() {
    if (!createName.trim()) return
    setBusy(true); setError(null)
    try {
      const created = await createWorkspace({ name: createName.trim(), description: createDescription.trim() || null })
      await refreshContext()
      setSelectedId(created.workspace_id); setCreateName(''); setCreateDescription('')
      await load(created.workspace_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function save() {
    if (!overview || !name.trim()) return
    setBusy(true); setError(null)
    try {
      await updateWorkspace(overview.workspace.workspace_id, { name: name.trim(), description: description.trim() || null })
      await refreshContext(); await load(overview.workspace.workspace_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function changeArchive(action: 'archive' | 'restore') {
    if (!overview) return
    setBusy(true); setError(null)
    try {
      if (action === 'archive') await archiveWorkspace(overview.workspace.workspace_id)
      else await restoreWorkspace(overview.workspace.workspace_id)
      await refreshContext(); await load(overview.workspace.workspace_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function remove(item: WorkspaceMembershipView) {
    if (!overview) return
    setBusy(true); setError(null)
    try {
      await removeWorkspaceMembership(overview.workspace.workspace_id, item.object_type, item.object_id)
      await refreshContext(); await load(overview.workspace.workspace_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  const workspace = overview?.workspace
  return <main className="workspace-management-shell">
    <header className="workspace-title">
      <div><h1>{tr('Workspaces')}</h1><span>{tr('Organize research assets without copying their underlying evidence.')}</span></div>
      {currentWorkspace && <div className="workspace-current-chip"><small>{tr('Current Workspace')}</small><strong>{currentWorkspace.name}</strong></div>}
    </header>
    {error && <div className="error-banner" role="alert">{tr(error)}</div>}
    <div className="workspace-management-grid">
      <aside className="workspace-list-panel">
        <div className="run-section-title"><h2>{tr('Workspace list')}</h2><span>{workspaces.length}</span></div>
        <div className="workspace-list">
          {workspaces.map((item) => <button key={item.workspace_id} type="button" aria-current={selectedId === item.workspace_id ? 'true' : undefined} onClick={() => setSelectedId(item.workspace_id)}>
            <span><strong>{item.name}</strong><small>{item.is_default ? tr('Default Workspace') : item.archived_at ? tr('Archived') : tr('Active')}</small></span>
            {currentWorkspace?.workspace_id === item.workspace_id && <i>{tr('Current')}</i>}
          </button>)}
        </div>
        <form className="workspace-create-form" onSubmit={(event) => { event.preventDefault(); void create() }}>
          <h3>{tr('Create Workspace')}</h3>
          <label>{tr('Name')}<input value={createName} onChange={(event) => setCreateName(event.target.value)} /></label>
          <label>{tr('Description')}<textarea value={createDescription} onChange={(event) => setCreateDescription(event.target.value)} /></label>
          <button className="primary-button" type="submit" disabled={busy || !createName.trim()}>{tr('Create Workspace')}</button>
        </form>
      </aside>
      <section className="workspace-detail-panel">
        {!workspace && <div className="workspace-empty"><h2>{tr('No Workspaces')}</h2></div>}
        {workspace && <>
          <div className="workspace-detail-heading">
            <div><h2>{workspace.name}</h2><code>{workspace.workspace_id}</code></div>
            <div className="workspace-detail-actions">
              {workspace.archived_at === null && currentWorkspace?.workspace_id !== workspace.workspace_id && <button type="button" onClick={() => setCurrentWorkspace(workspace.workspace_id)}>{tr('Make Current')}</button>}
              {workspace.archived_at ? <button type="button" onClick={() => void changeArchive('restore')} disabled={busy}>{tr('Restore')}</button> : !workspace.is_default && <button className="danger-button" type="button" onClick={() => void changeArchive('archive')} disabled={busy}>{tr('Archive')}</button>}
            </div>
          </div>
          <div className="workspace-metrics">
            <div><small>{tr('Research assets')}</small><strong>{overview.statistics.membership_count}</strong></div>
            <div><small>{tr('Integrity')}</small><strong className={integrity?.status === 'DEGRADED' ? 'missing-reference' : ''}>{tr(integrity?.status ?? 'OK')}</strong></div>
            <div><small>{tr('Updated')}</small><strong>{formatTime(workspace.updated_at)}</strong></div>
          </div>
          <section className="workspace-editor">
            <label>{tr('Name')}<input value={name} disabled={Boolean(workspace.archived_at)} onChange={(event) => setName(event.target.value)} /></label>
            <label>{tr('Description')}<textarea value={description} disabled={Boolean(workspace.archived_at)} onChange={(event) => setDescription(event.target.value)} /></label>
            <button type="button" className="secondary-button" disabled={busy || Boolean(workspace.archived_at) || !name.trim()} onClick={() => void save()}>{tr('Save changes')}</button>
          </section>
          <section className="workspace-memberships">
            <div className="run-section-title"><h3>{tr('Research assets')}</h3><span>{memberships.length}</span></div>
            {workspace.archived_at && <p className="workspace-notice">{tr('Archived Workspaces are read-only. Restore this Workspace to edit memberships.')}</p>}
            {memberships.length === 0 && <div className="workspace-empty"><h3>{tr('No research assets yet')}</h3><p>{tr('New research created in this Workspace will appear here.')}</p></div>}
            {memberships.map((item) => <div className="workspace-membership-row" key={`${item.object_type}:${item.object_id}`}>
              <span><strong>{tr(item.object_type.replaceAll('_', ' '))}</strong><code>{item.object_id}</code></span>
              <span className={item.reference_status === 'MISSING_REFERENCE' ? 'missing-reference' : ''}>{tr(item.reference_status === 'MISSING_REFERENCE' ? 'Missing reference' : 'Available')}</span>
              <time dateTime={item.added_at}>{formatTime(item.added_at)}</time>
              <button type="button" disabled={busy || Boolean(workspace.archived_at)} onClick={() => void remove(item)}>{tr('Remove from Workspace')}</button>
            </div>)}
          </section>
        </>}
      </section>
    </div>
  </main>
}
