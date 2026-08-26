import type { FeatureSnapshot } from '../../../types/trace'

export type LineageStatus = 'ok' | 'missing' | 'cycle'

export interface FeatureLineageNode {
  featureId: string
  feature: FeatureSnapshot | null
  label: string
  status: LineageStatus
  children: FeatureLineageNode[]
}

export function buildFeatureLineage(
  features: Iterable<FeatureSnapshot>,
  rootFeatureId: string,
): FeatureLineageNode {
  const featureById = new Map(Array.from(features, (feature) => [feature.feature_id, feature]))

  function visit(featureId: string, ancestors: Set<string>): FeatureLineageNode {
    const feature = featureById.get(featureId)
    if (!feature) {
      return {
        featureId,
        feature: null,
        label: `Missing referenced feature: ${featureId}`,
        status: 'missing',
        children: [],
      }
    }
    if (ancestors.has(featureId)) {
      return {
        featureId,
        feature,
        label: `Cycle detected at ${feature.name}`,
        status: 'cycle',
        children: [],
      }
    }
    const nextAncestors = new Set(ancestors)
    nextAncestors.add(featureId)
    return {
      featureId,
      feature,
      label: feature.name,
      status: 'ok',
      children: feature.inputs.map((input) => visit(input, nextAncestors)),
    }
  }

  return visit(rootFeatureId, new Set())
}
