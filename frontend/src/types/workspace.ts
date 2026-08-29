export type WorkspaceObjectType =
  | 'DATASET'
  | 'UNIVERSE'
  | 'CORPORATE_ACTION_DATASET'
  | 'FACTOR_RESEARCH'
  | 'FACTOR_RELATIONSHIP'
  | 'WALK_FORWARD'
  | 'PORTFOLIO_RESEARCH'
  | 'HYPOTHESIS'
  | 'STRATEGY'
  | 'RUN'
  | 'SNAPSHOT'
  | 'FORWARD_SESSION'
  | 'PAPER_SESSION'
  | 'DRIFT_REPORT'
  | 'ATTRIBUTION_REPORT'
  | 'RESEARCH_BUNDLE'

export interface Workspace {
  workspace_id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
  archived_at: string | null
  is_default: boolean
}

export interface WorkspaceMembership {
  workspace_id: string
  object_type: WorkspaceObjectType
  object_id: string
  added_at: string
}

export interface WorkspaceMembershipView extends WorkspaceMembership {
  reference_status: 'AVAILABLE' | 'MISSING_REFERENCE'
}

export interface WorkspaceStatistics {
  membership_count: number
  counts: Partial<Record<WorkspaceObjectType, number>>
}

export interface WorkspaceOverview {
  workspace: Workspace
  statistics: WorkspaceStatistics
  recent_activity: WorkspaceMembershipView[]
}

export interface WorkspaceIntegrity {
  workspace_id: string
  status: 'OK' | 'DEGRADED'
  membership_count: number
  missing_references: WorkspaceMembership[]
}
