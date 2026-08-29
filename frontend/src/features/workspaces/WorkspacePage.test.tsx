import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { Workspace, WorkspaceMembershipView } from '../../types/workspace'
import { WorkspaceProvider } from './WorkspaceContext'
import WorkspacePage from './WorkspacePage'

const workspace: Workspace = {
  workspace_id: 'workspace-default', name: 'Default Workspace', description: 'Uncategorized research',
  created_at: '2026-08-29T00:00:00Z', updated_at: '2026-08-29T00:00:00Z', archived_at: null, is_default: true,
}
const missing: WorkspaceMembershipView = {
  workspace_id: workspace.workspace_id,
  object_type: 'DATASET',
  object_id: 'dataset-missing-revision',
  added_at: '2026-08-29T00:30:00Z',
  reference_status: 'MISSING_REFERENCE',
}

afterEach(() => {
  vi.unstubAllGlobals(); vi.clearAllMocks(); window.localStorage.clear()
})

it('shows missing references and removes only the Workspace membership', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }))
    let body: unknown
    if (url.startsWith('/api/workspaces?')) body = [workspace]
    else if (url.endsWith('/integrity')) body = { workspace_id: workspace.workspace_id, status: 'DEGRADED', membership_count: 1, missing_references: [missing] }
    else if (url.endsWith('/memberships')) body = [missing]
    else body = { workspace, statistics: { membership_count: 1, counts: { DATASET: 1 } }, recent_activity: [missing] }
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<I18nProvider><WorkspaceProvider><WorkspacePage /></WorkspaceProvider></I18nProvider>)

  expect(await screen.findByText('Missing reference')).toBeInTheDocument()
  expect(screen.getByText('dataset-missing-revision')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Remove from Workspace' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input).includes('/memberships/DATASET/dataset-missing-revision') && (init as RequestInit | undefined)?.method === 'DELETE')).toBe(true))
  expect(fetchMock.mock.calls.some(([input, init]) => String(input).startsWith('/api/datasets/') && (init as RequestInit | undefined)?.method === 'DELETE')).toBe(false)
})
