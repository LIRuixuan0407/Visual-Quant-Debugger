/* eslint-disable react-refresh/only-export-components -- The provider and hook form one workspace boundary. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { CURRENT_WORKSPACE_STORAGE_KEY, WORKSPACE_MEMBERSHIP_CHANGED_EVENT, getWorkspaceMemberships, getWorkspaces, readCurrentWorkspaceId } from '../../api/workspaces'
import type { Workspace, WorkspaceMembershipView } from '../../types/workspace'

interface WorkspaceContextValue {
  workspaces: Workspace[]
  currentWorkspace: Workspace | null
  memberships: WorkspaceMembershipView[]
  loading: boolean
  error: string | null
  setCurrentWorkspace: (workspaceId: string) => void
  refresh: () => Promise<void>
}

const WorkspaceContext = createContext<WorkspaceContextValue>({
  workspaces: [], currentWorkspace: null, memberships: [], loading: false, error: null,
  setCurrentWorkspace: () => undefined,
  refresh: async () => undefined,
})

function persistCurrent(workspaceId: string) {
  try { window.localStorage.setItem(CURRENT_WORKSPACE_STORAGE_KEY, workspaceId) }
  catch { /* Storage may be unavailable. */ }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState<string | null>(readCurrentWorkspaceId)
  const [memberships, setMemberships] = useState<WorkspaceMembershipView[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const next = await getWorkspaces(true)
      const active = next.filter((workspace) => workspace.archived_at === null)
      const selected = active.find((workspace) => workspace.workspace_id === currentWorkspaceId)
        ?? active.find((workspace) => workspace.is_default)
        ?? active[0]
        ?? null
      setWorkspaces(next)
      setCurrentWorkspaceId(selected?.workspace_id ?? null)
      if (selected) {
        persistCurrent(selected.workspace_id)
        setMemberships(await getWorkspaceMemberships(selected.workspace_id))
      } else setMemberships([])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setLoading(false) }
  }, [currentWorkspaceId])

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(timer)
  }, [refresh])

  useEffect(() => {
    const membershipChanged = () => void refresh()
    window.addEventListener(WORKSPACE_MEMBERSHIP_CHANGED_EVENT, membershipChanged)
    return () => window.removeEventListener(WORKSPACE_MEMBERSHIP_CHANGED_EVENT, membershipChanged)
  }, [refresh])

  const setCurrentWorkspace = useCallback((workspaceId: string) => {
    const selected = workspaces.find((workspace) => workspace.workspace_id === workspaceId && workspace.archived_at === null)
    if (!selected) return
    setCurrentWorkspaceId(workspaceId)
    persistCurrent(workspaceId)
  }, [workspaces])

  const currentWorkspace = workspaces.find((workspace) => workspace.workspace_id === currentWorkspaceId && workspace.archived_at === null) ?? null
  const value = useMemo<WorkspaceContextValue>(() => ({ workspaces, currentWorkspace, memberships, loading, error, setCurrentWorkspace, refresh }), [workspaces, currentWorkspace, memberships, loading, error, setCurrentWorkspace, refresh])
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace() { return useContext(WorkspaceContext) }
