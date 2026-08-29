import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '../../i18n/I18nProvider'
import type { ExperimentComparisonReport, FrozenArtifact, ResearchHypothesis, ResearchSnapshot } from '../../types/research'
import ResearchSnapshotsPage from './ResearchSnapshotsPage'

function artifact(kind: FrozenArtifact['kind'], id: string): FrozenArtifact {
  return {
    kind,
    artifact_id: id,
    source_revision: 'sha256:source-revision',
    payload_sha256: 'sha256:frozen-payload',
    payload_json: '{}',
  }
}

const hypothesis: ResearchHypothesis = {
  hypothesis_id: 'hypothesis-complete', family_id: 'hypothesis-family', parent_hypothesis_id: null, revision: 3,
  title: 'Diversified signal stability', description: 'A completed research chain.', dataset_id: 'dataset-real', dataset_fingerprint: 'sha256:dataset', universe: ['AAPL', 'MSFT'], factor_research_ids: ['factor-research-a'], expected_relationship: 'The fixed combination may be stable.', holding_horizon: '20 trading days', rebalance_idea: 'MONTHLY', risk_assumptions: ['Long-only'], created_at: '2026-08-20T00:00:00Z', status: 'STRATEGY_CREATED', outcome: 'MIXED', created_with_known_stage: 'RESEARCH', source_revealed_stages: { 'factor-research-a': 'HOLDOUT' }, evidence: [],
  candidate: { combination: 'RANK_AVERAGE', selection: 'TOP_PERCENT', top_percent: 20, weighting: 'EQUAL_WEIGHT', max_single_position_weight: 1, rebalance: 'MONTHLY', long_only: true, portfolio_research_id: 'portfolio-a' },
  lineage: { factor_research_ids: ['factor-research-a'], factor_ids: ['momentum'], relationship_ids: ['relationship-a'], walk_forward_ids: ['walk-forward-a'], portfolio_research_id: 'portfolio-a', strategy_id: 'portfolio-strategy-a', run_ids: ['run-0123456789abcdef01234567'], trace_ids: ['trace-a'] },
  revision_reason: null, ai_boundary: 'AI cannot calculate quantitative metrics.',
}

const snapshot: ResearchSnapshot = {
  snapshot_version: '1.0', snapshot_id: 'research-snapshot-0123456789abcdef01234567', name: 'Diversified signals · frozen research', created_at: '2026-08-21T00:00:00Z', content_fingerprint: 'sha256:snapshot-content',
  lineage: { dataset_id: 'dataset-real', dataset_family_id: 'dataset-family-real', dataset_revision: 1, universe_ids: [], corporate_action_dataset_ids: [], factor_research_ids: ['factor-research-a'], factor_ids: ['momentum'], relationship_ids: ['relationship-a'], walk_forward_ids: ['walk-forward-a'], hypothesis_id: hypothesis.hypothesis_id, hypothesis_revision: 3, portfolio_research_id: 'portfolio-a', strategy_id: 'portfolio-strategy-a', run_ids: ['run-0123456789abcdef01234567'], trace_ids: ['trace-a'] },
  dataset: artifact('DATASET', 'dataset-real'), universes: [], corporate_actions: [], factors: [artifact('FACTOR_RESEARCH', 'factor-research-a')], relationships: [artifact('FACTOR_RELATIONSHIP', 'relationship-a')], walk_forward: [artifact('WALK_FORWARD', 'walk-forward-a')], hypothesis: artifact('HYPOTHESIS', hypothesis.hypothesis_id), portfolio: artifact('PORTFOLIO_RESEARCH', 'portfolio-a'), strategy: artifact('STRATEGY_SOURCE', 'portfolio-strategy-a'), runs: [artifact('RUN_MANIFEST', 'run-0123456789abcdef01234567')], traces: [artifact('TRACE', 'trace-a')],
  parameters: [{ owner_type: 'FACTOR', owner_id: 'factor-research-a', values: [{ key: 'lookback', value: 20 }] }],
  time_boundaries: {
    research: { label: 'RESEARCH', source_id: 'factor-research-a', start: '2024-01-01T00:00:00Z', end: '2024-06-30T00:00:00Z', cutoff: null },
    validation: { label: 'VALIDATION', source_id: 'factor-research-a', start: '2024-07-01T00:00:00Z', end: '2024-09-30T00:00:00Z', cutoff: null },
    holdout: { label: 'HOLDOUT', source_id: 'factor-research-a', start: '2024-10-01T00:00:00Z', end: '2024-12-31T00:00:00Z', cutoff: null },
    runs: [{ label: 'BACKTEST', source_id: 'run-0123456789abcdef01234567', start: '2024-01-01T00:00:00Z', end: '2024-12-31T00:00:00Z', cutoff: null }],
  },
  environment: { python_version: '3.12.8', python_implementation: 'CPython', platform: 'Linux', machine: 'x86_64', vqd_version: '0.1.0', dependencies: [{ name: 'pydantic', version: '2.11.0' }] },
  immutability_disclosure: 'This Research Snapshot is append-only and content-verified. Source records may evolve only through new revisions; this frozen record is never updated in place.',
}

const summary = {
  snapshot_id: snapshot.snapshot_id, name: snapshot.name, created_at: snapshot.created_at, content_fingerprint: snapshot.content_fingerprint,
  hypothesis_id: hypothesis.hypothesis_id, hypothesis_revision: 3, dataset_id: 'dataset-real', factor_count: 1, strategy_id: 'portfolio-strategy-a', run_count: 1, trace_count: 1,
}

const secondSnapshotId = 'research-snapshot-fedcba9876543210fedcba98'
const secondSummary = {
  ...summary,
  snapshot_id: secondSnapshotId,
  name: 'Diversified signals · weekly revision',
  content_fingerprint: 'sha256:second-snapshot-content',
  hypothesis_revision: 4,
  strategy_id: 'portfolio-strategy-b',
}
const secondSnapshot: ResearchSnapshot = {
  ...snapshot,
  snapshot_id: secondSummary.snapshot_id,
  name: secondSummary.name,
  content_fingerprint: secondSummary.content_fingerprint,
}

const comparison: ExperimentComparisonReport = {
  comparison_version: '1.0',
  snapshot_ids: [snapshot.snapshot_id, secondSnapshotId],
  snapshots: [
    { snapshot_id: snapshot.snapshot_id, name: snapshot.name, content_fingerprint: snapshot.content_fingerprint, hypothesis_id: hypothesis.hypothesis_id, hypothesis_revision: 3, run_id: snapshot.lineage.run_ids[0], trace_id: snapshot.lineage.trace_ids[0] },
    { snapshot_id: secondSnapshotId, name: secondSummary.name, content_fingerprint: secondSummary.content_fingerprint, hypothesis_id: 'hypothesis-weekly', hypothesis_revision: 4, run_id: 'run-fedcba9876543210fedcba98', trace_id: 'trace-b' },
  ],
  comparability: 'STRICTLY_COMPARABLE',
  context_diff: [
    { field: 'dataset_revision', same: true, significance: 'STRICT_CONTROL', values: ['sha256:dataset', 'sha256:dataset'] },
    { field: 'creation_environment', same: false, significance: 'INFORMATIONAL', values: ['Linux · Python 3.12.8', 'Linux · Python 3.12.9'] },
  ],
  artifact_diff: [{ kind: 'STRATEGY_SOURCE', semantic_key: 'strategy', artifact_ids: ['portfolio-strategy-a', 'portfolio-strategy-b'], source_revisions: ['sha256:strategy-a', 'sha256:strategy-b'], payload_fingerprints: ['sha256:payload-a', 'sha256:payload-b'], same_revision: false }],
  parameter_diff: [{ owner_type: 'HYPOTHESIS', owner_key: 'hypothesis', parameter: 'rebalance_idea', values: ['MONTHLY', 'WEEKLY'], changed: true }],
  metric_diff: [{ scope: 'PRIMARY_RUN', metric: 'total_return', values: [0.1, 0.125], differences_from_first: [0, 0.025] }],
  hypothesis_states: [
    { snapshot_id: snapshot.snapshot_id, status: 'STRATEGY_CREATED', outcome: 'MIXED', supporting_evidence: 2, contradicting_evidence: 1, neutral_evidence: 0 },
    { snapshot_id: secondSnapshotId, status: 'STRATEGY_CREATED', outcome: 'MIXED', supporting_evidence: 2, contradicting_evidence: 1, neutral_evidence: 0 },
  ],
  primary_run_comparison: {
    report_version: '1.0', run_ids: [snapshot.lineage.run_ids[0], 'run-fedcba9876543210fedcba98'], comparability: 'CONTEXTUALLY_COMPARABLE', context_diff: [], parameter_diff: [{ parameter: 'rebalance', values: [null, null], changed: true }], metric_diff: [], equity_comparison: [], signal_comparison: [], execution_comparison: [],
    first_behavioral_divergence: { status: 'NO_BEHAVIORAL_DIVERGENCE', kind: null, timestamp: null, event_ids: [null, null], summary: 'The two traces have no behavioral divergence.', run_values: [], associated_parameter_differences: ['rebalance'] },
  },
  comparison_disclosure: 'Experiment Compare describes controlled context, treatment, result, and recorded behavior differences. It does not select a winner, optimize parameters, infer causality from correlation, or make an investment recommendation.',
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it('shows immutable lineage, revisions, periods, parameters, environment, and verified artifacts', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
    const url = String(input)
    const body = url === '/api/research-snapshots'
      ? [summary]
      : url === '/api/hypotheses'
        ? [hypothesis]
        : url === '/api/dataset-families'
          ? [{ dataset_family_id: 'dataset-family-real', name: 'Real prices', created_at: '2024-01-01T00:00:00Z', latest_dataset_id: 'dataset-real-r4', revision_count: 4 }]
          : snapshot
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  }))
  const onOpenRuns = vi.fn()
  const onOpenReplay = vi.fn()

  render(<I18nProvider><ResearchSnapshotsPage onOpenRuns={onOpenRuns} onOpenReplay={onOpenReplay} /></I18nProvider>)

  expect(await screen.findByRole('heading', { name: 'Research Snapshots' })).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: snapshot.name })).toBeInTheDocument()
  expect(screen.getByText('Research lineage')).toBeInTheDocument()
  expect(screen.getByText('Time boundaries')).toBeInTheDocument()
  expect(screen.getByText('Frozen parameters')).toBeInTheDocument()
  expect(screen.getByText('Environment summary')).toBeInTheDocument()
  expect(screen.getAllByText('VERIFIED').length).toBeGreaterThan(1)
  expect(screen.getByText('Uses revision r1 · Latest revision is r4')).toBeInTheDocument()
  expect(screen.queryByText(/internal build label/i)).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Open frozen Run' }))
  fireEvent.click(screen.getByRole('button', { name: 'Open frozen Replay' }))
  expect(onOpenRuns).toHaveBeenCalledWith('run-0123456789abcdef01234567')
  expect(onOpenReplay).toHaveBeenCalledWith('trace-a')
})

it('creates a Snapshot only from a completed research chain', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    const body = init?.method === 'POST'
      ? snapshot
      : url === '/api/research-snapshots'
        ? []
        : [hypothesis]
    return Promise.resolve(new Response(JSON.stringify(body), { status: init?.method === 'POST' ? 201 : 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<I18nProvider><ResearchSnapshotsPage onOpenRuns={() => undefined} onOpenReplay={() => undefined} /></I18nProvider>)
  const button = await screen.findByRole('button', { name: 'Freeze Research' })
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.click(button)

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/research-snapshots',
    expect.objectContaining({ method: 'POST', body: expect.stringContaining(hypothesis.hypothesis_id) }),
  ))
})

it('opens the exact Snapshot requested by a Lineage deep link', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input)
    const body = url === '/api/research-snapshots'
      ? [summary, secondSummary]
      : url === '/api/hypotheses'
        ? [hypothesis]
        : secondSnapshot
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<I18nProvider><ResearchSnapshotsPage initialSnapshotId={secondSnapshotId} onOpenRuns={() => undefined} onOpenReplay={() => undefined} /></I18nProvider>)

  expect(await screen.findByRole('heading', { name: secondSnapshot.name })).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith(`/api/research-snapshots/${secondSnapshotId}`)
})

it('compares frozen experiment context, treatment, results, and Run / Trace behavior without ranking experiments', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    const body = init?.method === 'POST' && url === '/api/research-snapshots/compare'
      ? comparison
      : url === '/api/research-snapshots'
        ? [summary, secondSummary]
        : url === '/api/hypotheses'
          ? [hypothesis]
          : snapshot
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)
  const onOpenRuns = vi.fn()
  const onOpenReplay = vi.fn()

  render(<I18nProvider><ResearchSnapshotsPage onOpenRuns={onOpenRuns} onOpenReplay={onOpenReplay} /></I18nProvider>)

  fireEvent.click(await screen.findByRole('checkbox', { name: `Select ${summary.name} for comparison` }))
  fireEvent.click(screen.getByRole('checkbox', { name: `Select ${secondSummary.name} for comparison` }))
  const compareButton = screen.getByRole('button', { name: 'Compare Experiments' })
  await waitFor(() => expect(compareButton).toBeEnabled())
  fireEvent.click(compareButton)

  expect(await screen.findByRole('heading', { name: 'Experiment Compare' })).toBeInTheDocument()
  expect(screen.getByText('Context controls')).toBeInTheDocument()
  expect(screen.getByText('Changed artifact revisions')).toBeInTheDocument()
  expect(screen.getByText('rebalance idea')).toBeInTheDocument()
  expect(screen.getByText('Delta vs baseline', { exact: false })).toHaveTextContent('0.025')
  expect(screen.getByText('Primary Run / Trace comparison')).toBeInTheDocument()
  expect(screen.queryByText(/^winner$/i)).not.toBeInTheDocument()
  expect(screen.queryByText(/^recommended$/i)).not.toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/api/research-snapshots/compare', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ snapshot_ids: [snapshot.snapshot_id, secondSnapshotId] }),
  }))

  fireEvent.click(screen.getAllByRole('button', { name: 'Open Run' })[1])
  fireEvent.click(screen.getAllByRole('button', { name: 'Open Replay' })[1])
  expect(onOpenRuns).toHaveBeenCalledWith('run-fedcba9876543210fedcba98')
  expect(onOpenReplay).toHaveBeenCalledWith('trace-b')
})

it('validates bundle conflicts before importing and reports unavailable dependencies', async () => {
  window.localStorage.setItem('vqd-language', 'en')
  const preview = {
    preview_id: 'bundle-preview-0123456789abcdef0123',
    manifest: {
      bundle_format_version: '1.0',
      bundle_id: 'research-bundle-0123456789abcdef01234567',
      created_at: '2026-08-29T09:00:00Z',
      app_version: '0.30.0',
      mode: 'REFERENCE_ONLY',
      root_objects: [{ kind: 'SNAPSHOT', object_id: snapshot.snapshot_id }],
      objects: [
        { kind: 'SNAPSHOT', object_id: snapshot.snapshot_id, fingerprint: snapshot.content_fingerprint, path: `objects/snapshots/${snapshot.snapshot_id}.json`, portable: true },
        { kind: 'DATASET', object_id: 'dataset-real', fingerprint: 'sha256:dataset', path: 'objects/datasets/dataset-real/metadata.json', portable: false },
        { kind: 'RUN', object_id: snapshot.lineage.run_ids[0], fingerprint: 'sha256:run', path: `objects/runs/${snapshot.lineage.run_ids[0]}/manifest.json`, portable: false },
      ],
      object_count: 3,
      frozen_artifact_count: 8,
      checksums: {},
      external_dependencies: [],
    },
    valid: true,
    conflicts: [
      { kind: 'SNAPSHOT', object_id: snapshot.snapshot_id, status: 'REUSE', detail: 'Same Snapshot ID and content fingerprint already exist; reuse is safe.' },
      { kind: 'DATASET', object_id: 'dataset-real', status: 'UNAVAILABLE', detail: 'Dataset rows are not embedded in this bundle and are not available locally.' },
      { kind: 'RUN', object_id: snapshot.lineage.run_ids[0], status: 'UNAVAILABLE', detail: 'Run replay artifacts are incomplete; missing trace.json' },
    ],
    external_dependencies: [
      { kind: 'DATASET', object_id: 'dataset-real', reason: 'Dataset rows are not embedded in this bundle and are not available locally.' },
    ],
    errors: [],
  }
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input)
    let body: unknown
    if (url === '/api/research-snapshots') body = [summary]
    else if (url === '/api/hypotheses') body = [hypothesis]
    else if (url === '/api/dataset-families') body = []
    else if (url === '/api/research-bundles/preview') body = preview
    else if (url.startsWith('/api/research-bundles/import/')) body = { bundle_id: preview.manifest.bundle_id, imported: [], reused: [`SNAPSHOT:${snapshot.snapshot_id}`], unavailable: ['DATASET:dataset-real', `RUN:${snapshot.lineage.run_ids[0]}`], target_workspace_id: 'workspace-default' }
    else body = snapshot
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<I18nProvider><ResearchSnapshotsPage onOpenRuns={() => undefined} onOpenReplay={() => undefined} /></I18nProvider>)

  const fileInput = await screen.findByLabelText('Bundle file')
  const file = new File(['bundle'], 'research.vqd-bundle.zip', { type: 'application/zip' })
  fireEvent.change(fileInput, { target: { files: [file] } })

  expect(await screen.findByRole('heading', { name: preview.manifest.bundle_id })).toBeInTheDocument()
  expect(screen.getByText('Bundle Contents')).toBeInTheDocument()
  expect(screen.getByText(/Checksum inventory/)).toBeInTheDocument()
  expect(screen.getByText('Unavailable dependencies')).toBeInTheDocument()
  expect(screen.getAllByText('UNAVAILABLE').length).toBeGreaterThan(0)

  fireEvent.click(screen.getByRole('button', { name: 'Import validated Bundle' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    `/api/research-bundles/import/${preview.preview_id}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target_workspace_id: 'workspace-default' }) },
  ))
  expect(await screen.findByText(/Bundle import complete/)).toBeInTheDocument()
})
