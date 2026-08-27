import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../i18n/I18nProvider'
import type { GlobalSearchResponse, SearchOpenTarget, SearchResult } from '../types/search'
import { readRecentSearches, recordRecentSearch } from '../utils/recentSearches'
import GlobalSearch from './GlobalSearch'

const result = (entity_type: SearchResult['entity_type'], entity_id: string, title: string, route: string): SearchResult => ({
  entity_type, entity_id, title, route, subtitle: `${title} subtitle`, score: 900, highlights: ['title'], metadata: {},
})

const results = [
  result('FACTOR', 'momentum', 'Momentum factor', '/factor-lab?factor_id=momentum'),
  result('RUN', 'run-123', 'Momentum baseline', '/runs/run-123'),
]

function installApi(items: SearchResult[] = results) {
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = new URL(String(input), 'http://localhost')
    const filtered = url.searchParams.getAll('types').includes('RUN') ? items.filter((item) => item.entity_type === 'RUN') : items
    const response: GlobalSearchResponse = { query: url.searchParams.get('q') ?? '', normalized_query: 'momentum', results: filtered }
    return Promise.resolve(new Response(JSON.stringify(response), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function Harness({ onNavigate }: { onNavigate: (item: SearchOpenTarget) => void }) {
  const [open, setOpen] = useState(false)
  return <I18nProvider><button type="button" onClick={() => setOpen(true)}>Open test search</button><GlobalSearch open={open} onOpenChange={setOpen} onNavigate={onNavigate} /></I18nProvider>
}

afterEach(() => {
  vi.unstubAllGlobals(); vi.clearAllMocks(); window.localStorage.clear(); window.history.replaceState({}, '', '/')
})

it('opens with Ctrl/Cmd+K, supports arrow navigation and Enter opens the selected route', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  installApi()
  const onNavigate = vi.fn()
  render(<Harness onNavigate={onNavigate} />)

  fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
  const input = await screen.findByRole('textbox', { name: 'Search research workspace' })
  fireEvent.change(input, { target: { value: 'momentum' } })
  await screen.findByText('Momentum baseline')
  fireEvent.keyDown(input, { key: 'ArrowDown' })
  fireEvent.keyDown(input, { key: 'Enter' })

  expect(onNavigate).toHaveBeenCalledWith(expect.objectContaining({ entity_id: 'run-123', route: '/runs/run-123' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  fireEvent.keyDown(document, { key: 'k', metaKey: true })
  expect(await screen.findByRole('dialog', { name: 'Global Search' })).toBeInTheDocument()
})

it('ArrowUp wraps to the last result and Escape closes without navigation', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  installApi()
  const onNavigate = vi.fn()
  render(<Harness onNavigate={onNavigate} />)
  fireEvent.click(screen.getByRole('button', { name: 'Open test search' }))
  const input = screen.getByRole('textbox', { name: 'Search research workspace' })
  fireEvent.change(input, { target: { value: 'momentum' } })
  await screen.findByText('Momentum baseline')
  fireEvent.keyDown(input, { key: 'ArrowUp' })
  expect(screen.getByRole('option', { name: /Momentum baseline/ })).toHaveAttribute('aria-selected', 'true')
  fireEvent.keyDown(input, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(onNavigate).not.toHaveBeenCalled()
})

it('stores at most eight deduplicated lightweight recent objects and renders empty recent state', () => {
  for (let index = 0; index < 10; index += 1) {
    recordRecentSearch({ entity_type: 'RUN', entity_id: `run-${index}`, title: `Run ${index}`, route: `/runs/run-${index}`, metadata: { forbidden: 'not persisted' } })
  }
  recordRecentSearch({ entity_type: 'RUN', entity_id: 'run-5', title: 'Run 5 reopened', route: '/runs/run-5' })
  const recent = readRecentSearches()
  expect(recent).toHaveLength(8)
  expect(recent[0].entity_id).toBe('run-5')
  expect(Object.keys(recent[0]).sort()).toEqual(['entity_id', 'entity_type', 'last_opened_at', 'route', 'title'])

  window.localStorage.clear()
  window.localStorage.setItem('vqd-language', 'en')
  render(<Harness onNavigate={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Open test search' }))
  expect(screen.getByText('No recent research objects.')).toBeInTheDocument()
})

it('filters by entity type and sends the selected backend filter', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const fetchMock = installApi()
  render(<Harness onNavigate={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Open test search' }))
  fireEvent.change(screen.getByLabelText('Search type'), { target: { value: 'RUN' } })
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'momentum' } })

  expect(await screen.findByText('Momentum baseline')).toBeInTheDocument()
  expect(screen.queryByText('Momentum factor')).not.toBeInTheDocument()
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('types=RUN'))).toBe(true))
})

it('searches and renders a Chinese hypothesis in the Chinese interface', async () => {
  installApi([result('HYPOTHESIS', 'hypothesis-cn', '苹果动量研究', '/research-workspace/hypothesis-cn')])
  render(<Harness onNavigate={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Open test search' }))
  const input = screen.getByRole('textbox', { name: '搜索研究工作区' })
  fireEvent.change(input, { target: { value: '苹果动量' } })

  expect(await screen.findByText('苹果动量研究')).toBeInTheDocument()
  expect(screen.getByText('确定性本地搜索')).toBeInTheDocument()
  expect(screen.getByText('不搜索 Trace 事件或源码文件。')).toBeInTheDocument()
})
