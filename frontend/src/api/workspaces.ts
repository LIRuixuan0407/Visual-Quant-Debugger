import { readJson } from './client'
import type {
  Workspace,
  WorkspaceIntegrity,
  WorkspaceMembership,
  WorkspaceMembershipView,
  WorkspaceObjectType,
  WorkspaceOverview,
} from '../types/workspace'

export const CURRENT_WORKSPACE_STORAGE_KEY = 'vqd.currentWorkspaceId'
export const WORKSPACE_MEMBERSHIP_CHANGED_EVENT = 'vqd:workspace-membership-changed'

export function readCurrentWorkspaceId(): string | null {
  try { return window.localStorage.getItem(CURRENT_WORKSPACE_STORAGE_KEY) }
  catch { return null }
}

export async function getWorkspaces(includeArchived = false): Promise<Workspace[]> {
  const endpoint = `/api/workspaces${includeArchived ? '?include_archived=true' : ''}`
  return readJson(await fetch(endpoint), `GET ${endpoint}`) as Promise<Workspace[]>
}

export async function getWorkspace(workspaceId: string): Promise<WorkspaceOverview> {
  const endpoint = `/api/workspaces/${encodeURIComponent(workspaceId)}`
  return readJson(await fetch(endpoint), `GET ${endpoint}`) as Promise<WorkspaceOverview>
}

export async function createWorkspace(input: { name: string; description?: string | null }): Promise<Workspace> {
  const response = await fetch('/api/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return readJson(response, 'POST /api/workspaces') as Promise<Workspace>
}

export async function updateWorkspace(workspaceId: string, input: { name?: string; description?: string | null }): Promise<Workspace> {
  const endpoint = `/api/workspaces/${encodeURIComponent(workspaceId)}`
  const response = await fetch(endpoint, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return readJson(response, `PATCH ${endpoint}`) as Promise<Workspace>
}

async function changeArchiveState(workspaceId: string, action: 'archive' | 'restore'): Promise<Workspace> {
  const endpoint = `/api/workspaces/${encodeURIComponent(workspaceId)}/${action}`
  return readJson(await fetch(endpoint, { method: 'POST' }), `POST ${endpoint}`) as Promise<Workspace>
}

export const archiveWorkspace = (workspaceId: string) => changeArchiveState(workspaceId, 'archive')
export const restoreWorkspace = (workspaceId: string) => changeArchiveState(workspaceId, 'restore')

export async function getWorkspaceMemberships(workspaceId: string): Promise<WorkspaceMembershipView[]> {
  const endpoint = `/api/workspaces/${encodeURIComponent(workspaceId)}/memberships`
  return readJson(await fetch(endpoint), `GET ${endpoint}`) as Promise<WorkspaceMembershipView[]>
}

export async function addWorkspaceMembership(workspaceId: string, objectType: WorkspaceObjectType, objectId: string): Promise<WorkspaceMembership> {
  const endpoint = `/api/workspaces/${encodeURIComponent(workspaceId)}/memberships`
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ object_type: objectType, object_id: objectId }),
  })
  return readJson(response, `POST ${endpoint}`) as Promise<WorkspaceMembership>
}

export async function addCreatedObjectToCurrentWorkspace(objectType: WorkspaceObjectType, objectId: string): Promise<void> {
  const workspaceId = readCurrentWorkspaceId()
  if (workspaceId) {
    await addWorkspaceMembership(workspaceId, objectType, objectId)
    window.dispatchEvent(new CustomEvent(WORKSPACE_MEMBERSHIP_CHANGED_EVENT, { detail: { workspaceId } }))
  }
}

export async function removeWorkspaceMembership(workspaceId: string, objectType: WorkspaceObjectType, objectId: string): Promise<void> {
  const endpoint = `/api/workspaces/${encodeURIComponent(workspaceId)}/memberships/${encodeURIComponent(objectType)}/${encodeURIComponent(objectId)}`
  const response = await fetch(endpoint, { method: 'DELETE' })
  if (!response.ok) await readJson(response, `DELETE ${endpoint}`)
}

export async function getWorkspaceIntegrity(workspaceId: string): Promise<WorkspaceIntegrity> {
  const endpoint = `/api/workspaces/${encodeURIComponent(workspaceId)}/integrity`
  return readJson(await fetch(endpoint), `GET ${endpoint}`) as Promise<WorkspaceIntegrity>
}
