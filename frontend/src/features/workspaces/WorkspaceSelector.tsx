import { useI18n } from '../../i18n/I18nProvider'
import { useWorkspace } from './WorkspaceContext'

export default function WorkspaceSelector({ onManage }: { onManage: () => void }) {
  const { tr } = useI18n()
  const { workspaces, currentWorkspace, loading, setCurrentWorkspace } = useWorkspace()
  const active = workspaces.filter((workspace) => workspace.archived_at === null)
  return <div className="workspace-selector">
    <label htmlFor="current-workspace">{tr('Current Workspace')}</label>
    <select
      id="current-workspace"
      value={currentWorkspace?.workspace_id ?? ''}
      disabled={loading || active.length === 0}
      onChange={(event) => setCurrentWorkspace(event.target.value)}
    >
      {active.length === 0 && <option value="">{tr(loading ? 'Loading Workspaces…' : 'No active Workspaces')}</option>}
      {active.map((workspace) => <option key={workspace.workspace_id} value={workspace.workspace_id}>{workspace.name}</option>)}
    </select>
    <button type="button" onClick={onManage}>{tr('Manage Workspaces')}</button>
  </div>
}
