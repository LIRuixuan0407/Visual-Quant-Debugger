import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { LineageNode, ResearchLineageGraph, ResearchLineageSummary } from '../../types/researchLineage'
import ResearchLineagePage from './ResearchLineagePage'

const node = (node_type: LineageNode['node_type'], artifact_id: string, revision: string | number | null, status: LineageNode['status'] = 'RESOLVED'): LineageNode => ({
  node_id: `${node_type}:${artifact_id}:${revision ?? 'none'}`,
  node_type, artifact_id, revision, label: `${node_type} · ${artifact_id}`, created_at: '2026-08-28T00:00:00Z', status,
  route: status === 'MISSING_SOURCE' ? null : `/${node_type.toLowerCase()}?id=${artifact_id}`,
  metadata: node_type === 'HYPOTHESIS' ? { integrity_status: 'PASS' } : {},
})

const dataset = node('DATASET', 'dataset-28', 'sha256:data')
const factor = node('FACTOR', 'momentum', 'sha256:factor')
const factorResearch = node('FACTOR_RESEARCH', 'factor-research-28', 'sha256:factor')
const missingRelationship = node('FACTOR_RELATIONSHIP', 'relationship-missing-28', null, 'MISSING_SOURCE')
const hypothesisR1 = node('HYPOTHESIS', 'hypothesis-r1', 1)
const hypothesisR2 = node('HYPOTHESIS', 'hypothesis-r2', 2)
const strategy = node('STRATEGY', 'strategy-28', 'sha256:strategy')
const run = node('RUN', 'run-28', 'sha256:run')
const trace = node('TRACE', 'trace-28', 'sha256:trace')
const snapshot = node('SNAPSHOT', 'snapshot-r1', 'sha256:snapshot')

const edge = (edge_type: ResearchLineageGraph['edges'][number]['edge_type'], source: LineageNode, target: LineageNode, source_field: string) => ({ edge_id: `${edge_type}:${source.artifact_id}:${target.artifact_id}`, edge_type, source_node_id: source.node_id, target_node_id: target.node_id, source_field })

const graph: ResearchLineageGraph = {
  graph_version: '1.0', root_type: null, root_id: null, direction: 'BOTH', max_depth: 8,
  nodes: [dataset, factor, factorResearch, missingRelationship, hypothesisR1, hypothesisR2, strategy, run, trace, snapshot],
  edges: [
    edge('USES_DATASET', dataset, factorResearch, 'FactorResearchRecord.dataset_id'),
    edge('RESEARCHES_FACTOR', factor, factorResearch, 'FactorResearchRecord.factor'),
    edge('SUPPORTS_HYPOTHESIS', factorResearch, hypothesisR1, 'ResearchHypothesis.factor_research_ids'),
    edge('SUPPORTS_HYPOTHESIS', missingRelationship, hypothesisR1, 'ResearchHypothesis.lineage.relationship_ids'),
    edge('GENERATES_STRATEGY', hypothesisR1, strategy, 'ResearchHypothesis.lineage.strategy_id'),
    edge('EXECUTES_STRATEGY', strategy, run, 'ResearchHypothesis.lineage.run_ids'),
    edge('PRODUCES_TRACE', run, trace, 'RunManifest.trace_id'),
    edge('FREEZES_RESEARCH', hypothesisR1, snapshot, 'ResearchSnapshot.lineage.hypothesis_id'),
  ],
  disclosure: 'Global Research Lineage is a deterministic read model over explicit identifiers stored by existing research records. It does not infer relationships from names, timestamps, shared datasets, strategies, parameters, or similarity, and it stores no new facts.',
}

const focusedRun: LineageNode = { ...run, status: 'ORPHAN' }
const focusedGraph: ResearchLineageGraph = {
  ...graph,
  root_type: 'RUN',
  root_id: focusedRun.node_id,
  nodes: [focusedRun, trace],
  edges: [edge('PRODUCES_TRACE', focusedRun, trace, 'RunManifest.trace_id')],
}

const summary: ResearchLineageSummary = {
  graph_version: '1.0', node_count: graph.nodes.length, edge_count: graph.edges.length, missing_source_count: 1, orphan_count: 1,
  nodes_by_type: ['DATASET', 'FACTOR', 'FACTOR_RESEARCH', 'FACTOR_RELATIONSHIP', 'WALK_FORWARD', 'PORTFOLIO_RESEARCH', 'HYPOTHESIS', 'STRATEGY', 'RUN', 'TRACE', 'SNAPSHOT'].map((nodeType) => ({ node_type: nodeType as LineageNode['node_type'], count: graph.nodes.filter((item) => item.node_type === nodeType).length })),
  disclosure: graph.disclosure,
}

function installApi() {
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input)
    const payload = url === '/api/research-lineage/summary' ? summary : url.includes('root_type=RUN') ? focusedGraph : graph
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals(); vi.clearAllMocks(); window.localStorage.clear(); window.history.replaceState({}, '', '/')
})

it('highlights selected upstream and downstream nodes while keeping missing sources visible', async () => {
  window.localStorage.setItem('vqd-language', 'en'); installApi()
  render(<I18nProvider><ResearchLineagePage onOpenNode={vi.fn()} /></I18nProvider>)

  expect(await screen.findByRole('heading', { name: 'Research Lineage' })).toBeInTheDocument()
  expect(await screen.findByTitle('HYPOTHESIS · hypothesis-r1')).toHaveClass('selected')
  expect(screen.getByTitle('FACTOR · momentum')).toHaveClass('upstream')
  expect(screen.getByTitle('STRATEGY · strategy-28')).toHaveClass('downstream')
  expect(screen.getByTitle('FACTOR_RELATIONSHIP · relationship-missing-28')).toHaveClass('missing_source')
  expect(screen.getAllByText('MISSING_SOURCE').length).toBeGreaterThan(0)
  expect(screen.getByTitle('HYPOTHESIS · hypothesis-r1')).toHaveTextContent('r · 1')
  expect(screen.getByTitle('HYPOTHESIS · hypothesis-r2')).toHaveTextContent('r · 2')
})

it('filters node types, focuses a selected root, changes direction, and opens the exact node', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const fetchMock = installApi()
  const onOpenNode = vi.fn()
  const { container } = render(<I18nProvider><ResearchLineagePage onOpenNode={onOpenNode} /></I18nProvider>)
  await screen.findByRole('heading', { name: 'Research Lineage' })

  fireEvent.click(screen.getByLabelText(/TRACE/))
  expect(screen.queryByTitle('TRACE · trace-28')).not.toBeInTheDocument()
  fireEvent.click(screen.getByTitle('RUN · run-28'))
  const inspector = screen.getByRole('complementary')
  fireEvent.click(within(inspector).getByRole('button', { name: 'Open Run' }))
  expect(onOpenNode).toHaveBeenCalledWith(run)
  fireEvent.click(screen.getByRole('button', { name: 'Focus selected' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('root_type=RUN') && String(input).includes('root_id=RUN%3Arun-28%3Asha256%3Arun'))).toBe(true))
  const summaryStrip = container.querySelector('.lineage-summary-strip') as HTMLElement
  await waitFor(() => expect(within(summaryStrip).getByText('Orphans').parentElement).toHaveTextContent('1'))
  fireEvent.change(screen.getByLabelText('Direction'), { target: { value: 'UPSTREAM' } })
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('direction=UPSTREAM'))).toBe(true))
})

it('renders the explorer and missing-source status in Chinese', async () => {
  installApi()
  render(<I18nProvider><ResearchLineagePage onOpenNode={vi.fn()} /></I18nProvider>)

  expect(await screen.findByRole('heading', { name: '全局研究链路' })).toBeInTheDocument()
  expect((await screen.findAllByText('来源缺失')).length).toBeGreaterThan(0)
  expect(screen.getByText('不推测任何边')).toBeInTheDocument()
  expect(screen.getByLabelText(/策略\s*1/)).toBeInTheDocument()
  expect(screen.getByLabelText(/运行\s*1/)).toBeInTheDocument()
  expect(screen.getByLabelText(/快照\s*1/)).toBeInTheDocument()
})
