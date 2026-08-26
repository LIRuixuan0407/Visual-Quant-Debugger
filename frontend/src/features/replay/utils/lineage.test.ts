import type { FeatureSnapshot } from '../../../types/trace'
import { buildFeatureLineage } from './lineage'

const base = (id: string, name: string, inputs: string[] = []): FeatureSnapshot => ({
  feature_id: id,
  name,
  value: 1,
  formula: name,
  inputs,
  parameters: {},
  window_start: null,
  window_end: null,
  available_at: '2024-01-01T00:00:00Z',
  data_dependencies: [],
})

test('builds a recursive feature lineage and supports leaves', () => {
  const tree = buildFeatureLineage([
    base('z', 'zscore', ['spread', 'mean', 'std']),
    base('spread', 'spread'),
    base('mean', 'rolling_mean'),
    base('std', 'rolling_std'),
  ], 'z')
  expect(tree.label).toBe('zscore')
  expect(tree.children.map((child) => child.label)).toEqual(['spread', 'rolling_mean', 'rolling_std'])
  expect(tree.children[0].children).toEqual([])
})

test('reports missing references instead of silently dropping them', () => {
  const tree = buildFeatureLineage([base('z', 'zscore', ['missing'])], 'z')
  expect(tree.children[0].status).toBe('missing')
  expect(tree.children[0].label).toContain('Missing referenced feature')
})

test('stops cycles without infinite recursion', () => {
  const tree = buildFeatureLineage([base('a', 'a', ['b']), base('b', 'b', ['a'])], 'a')
  expect(tree.children[0].children[0].status).toBe('cycle')
  expect(tree.children[0].children[0].label).toContain('Cycle detected')
})
