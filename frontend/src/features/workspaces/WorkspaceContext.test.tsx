import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { Workspace } from '../../types/workspace'
import WorkspaceSelector from './WorkspaceSelector'
import { WorkspaceProvider, useWorkspace } from './WorkspaceContext'

const defaultWorkspace: Workspace = {
  workspace_id: 'workspace-default',
  name: 'Default Workspace',
  description: null,
  created_at: '2026-08-29T00:00:00Z',
  updated_at: '2026-08-29T00:00:00Z',
  archived_at: null,
  is_default: true,
}
const researchWorkspace: Workspace = {
  ...defaultWorkspace,
  workspace_id: 'workspace-0123456789abcdef01234567',
  name: 'Momentum Research',
  is_default: false,
}

function installApi(workspaces: Workspace[] = [defaultWorkspace, researchWorkspace]) {
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input)
    const body = url.startsWith('/api/workspaces?') ? workspaces : []
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function CurrentName() {
  const { currentWorkspace } = useWorkspace()
  return <output>{currentWorkspace?.name ?? 'none'}</output>
}

afterEach(() => {
  vi.unstubAllGlobals(); vi.clearAllMocks(); window.localStorage.clear()
})

it('restores and persists the current Workspace through the selector', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  window.localStorage.setItem('vqd.currentWorkspaceId', researchWorkspace.workspace_id)
  installApi()
  const onManage = vi.fn()
  render(<I18nProvider><WorkspaceProvider><WorkspaceSelector onManage={onManage} /><CurrentName /></WorkspaceProvider></I18nProvider>)

  expect(await screen.findByText('Momentum Research', { selector: 'output' })).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Current Workspace'), { target: { value: defaultWorkspace.workspace_id } })
  expect(await screen.findByText('Default Workspace', { selector: 'output' })).toBeInTheDocument()
  expect(window.localStorage.getItem('vqd.currentWorkspaceId')).toBe(defaultWorkspace.workspace_id)
  fireEvent.click(screen.getByRole('button', { name: 'Manage Workspaces' }))
  expect(onManage).toHaveBeenCalledOnce()
})

it('falls back to the default Workspace when the persisted selection is archived', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  window.localStorage.setItem('vqd.currentWorkspaceId', researchWorkspace.workspace_id)
  installApi([{ ...researchWorkspace, archived_at: '2026-08-29T01:00:00Z' }, defaultWorkspace])
  render(<I18nProvider><WorkspaceProvider><CurrentName /></WorkspaceProvider></I18nProvider>)

  await waitFor(() => expect(screen.getByText('Default Workspace')).toBeInTheDocument())
  expect(window.localStorage.getItem('vqd.currentWorkspaceId')).toBe(defaultWorkspace.workspace_id)
})
