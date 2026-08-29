import { afterEach, expect, test, vi } from 'vitest'

import { exportResearchBundle } from './research'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  window.localStorage.clear()
})

test('exporting a bundle does not create a workspace membership before import', async () => {
  window.localStorage.setItem('vqd.currentWorkspaceId', 'workspace-aaaaaaaaaaaaaaaaaaaaaaaa')
  const fetchMock = vi.fn(async () => new Response('bundle-bytes', {
    status: 200,
    headers: {
      'Content-Disposition': 'attachment; filename="research-bundle.vqd-bundle.zip"',
      'Content-Type': 'application/zip',
      'X-VQD-Bundle-ID': 'research-bundle-0123456789abcdef01234567',
    },
  }))
  vi.stubGlobal('fetch', fetchMock)

  const exported = await exportResearchBundle({
    mode: 'REFERENCE_ONLY',
    root_objects: [{ kind: 'SNAPSHOT', object_id: 'snapshot-0123456789abcdef01234567' }],
  })

  expect(exported.filename).toBe('research-bundle.vqd-bundle.zip')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock).toHaveBeenCalledWith('/api/research-bundles/export', expect.objectContaining({ method: 'POST' }))
})
