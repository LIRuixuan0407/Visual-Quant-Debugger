export type LineageNodeType = 'DATASET' | 'FACTOR' | 'FACTOR_RESEARCH' | 'FACTOR_RELATIONSHIP' | 'WALK_FORWARD' | 'PORTFOLIO_RESEARCH' | 'HYPOTHESIS' | 'STRATEGY' | 'RUN' | 'TRACE' | 'SNAPSHOT'
export type LineageEdgeType = 'USES_DATASET' | 'RESEARCHES_FACTOR' | 'RELATES_FACTORS' | 'VALIDATES_FACTOR' | 'COMBINES_FACTORS' | 'SUPPORTS_HYPOTHESIS' | 'USES_PORTFOLIO' | 'GENERATES_STRATEGY' | 'EXECUTES_STRATEGY' | 'PRODUCES_TRACE' | 'FREEZES_RESEARCH'
export type LineageNodeStatus = 'RESOLVED' | 'MISSING_SOURCE' | 'ORPHAN'
export type LineageDirection = 'UPSTREAM' | 'DOWNSTREAM' | 'BOTH'
export type LineageScalar = string | number | boolean | null

export interface LineageNode {
  node_id: string
  node_type: LineageNodeType
  artifact_id: string
  revision: string | number | null
  label: string
  created_at: string | null
  status: LineageNodeStatus
  route: string | null
  metadata: Record<string, LineageScalar>
}

export interface LineageEdge {
  edge_id: string
  edge_type: LineageEdgeType
  source_node_id: string
  target_node_id: string
  source_field: string
}

export interface ResearchLineageGraph {
  graph_version: '1.0'
  root_type: LineageNodeType | null
  root_id: string | null
  direction: LineageDirection
  max_depth: number
  nodes: LineageNode[]
  edges: LineageEdge[]
  disclosure: string
}

export interface LineageTypeCount { node_type: LineageNodeType; count: number }
export interface ResearchLineageSummary {
  graph_version: '1.0'
  node_count: number
  edge_count: number
  missing_source_count: number
  orphan_count: number
  nodes_by_type: LineageTypeCount[]
  disclosure: string
}
