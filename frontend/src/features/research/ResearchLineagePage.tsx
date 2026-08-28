import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

import { getResearchLineage, getResearchLineageSummary } from '../../api/researchLineage'
import { useI18n } from '../../i18n/I18nProvider'
import type { LineageDirection, LineageEdge, LineageNode, LineageNodeType, ResearchLineageGraph, ResearchLineageSummary } from '../../types/researchLineage'

const NODE_TYPES: LineageNodeType[] = ['DATASET', 'UNIVERSE', 'CORPORATE_ACTION_DATASET', 'FACTOR', 'FACTOR_RESEARCH', 'FACTOR_RELATIONSHIP', 'WALK_FORWARD', 'PORTFOLIO_RESEARCH', 'HYPOTHESIS', 'STRATEGY', 'RUN', 'TRACE', 'SNAPSHOT']
const COLUMN_BY_TYPE: Record<LineageNodeType, number> = { DATASET: 0, UNIVERSE: 0, CORPORATE_ACTION_DATASET: 0, FACTOR: 1, FACTOR_RESEARCH: 2, FACTOR_RELATIONSHIP: 3, WALK_FORWARD: 3, PORTFOLIO_RESEARCH: 3, HYPOTHESIS: 4, STRATEGY: 5, RUN: 6, TRACE: 7, SNAPSHOT: 7 }
const COLUMN_LABELS = ['Dataset', 'Factor', 'Factor Research', 'Relationship / Walk-Forward / Portfolio', 'Hypothesis', 'Strategy', 'Run', 'Trace / Snapshot']
const NODE_WIDTH = 184
const NODE_HEIGHT = 82
const COLUMN_PITCH = 232
const ROW_PITCH = 108
const CANVAS_PADDING = 30

const shortId = (value: string) => value.length > 28 ? `${value.slice(0, 13)}…${value.slice(-9)}` : value
const dateTime = (value: string | null) => value == null ? '—' : new Date(value).toLocaleString()
const displayValue = (value: string | number | boolean | null) => value == null ? '—' : String(value)

interface PositionedNode extends LineageNode { x: number; y: number }
interface LineageRoot { type: LineageNodeType; id: string }

function layout(nodes: LineageNode[]): { nodes: PositionedNode[]; width: number; height: number } {
  const rows = new Map<number, number>()
  const positioned = nodes.map((node) => {
    const column = COLUMN_BY_TYPE[node.node_type]
    const row = rows.get(column) ?? 0
    rows.set(column, row + 1)
    return { ...node, x: CANVAS_PADDING + column * COLUMN_PITCH, y: 76 + row * ROW_PITCH }
  })
  const longestColumn = Math.max(1, ...rows.values())
  return { nodes: positioned, width: CANVAS_PADDING * 2 + COLUMN_PITCH * 7 + NODE_WIDTH, height: Math.max(520, 100 + longestColumn * ROW_PITCH) }
}

function traverse(nodeId: string, edges: LineageEdge[], direction: 'upstream' | 'downstream'): Set<string> {
  const visited = new Set<string>()
  const queue = [nodeId]
  while (queue.length > 0) {
    const current = queue.shift()!
    const neighbors = edges.flatMap((edge) => {
      if (direction === 'upstream' && edge.target_node_id === current) return [edge.source_node_id]
      if (direction === 'downstream' && edge.source_node_id === current) return [edge.target_node_id]
      return []
    })
    for (const neighbor of neighbors) {
      if (neighbor === nodeId || visited.has(neighbor)) continue
      visited.add(neighbor)
      queue.push(neighbor)
    }
  }
  return visited
}

function queryRoot(): LineageRoot | null {
  const parameters = new URLSearchParams(window.location.search)
  const type = parameters.get('root_type') as LineageNodeType | null
  const id = parameters.get('root_id')
  return type && NODE_TYPES.includes(type) && id ? { type, id } : null
}

function queryDirection(): LineageDirection {
  const value = new URLSearchParams(window.location.search).get('direction')
  return value === 'UPSTREAM' || value === 'DOWNSTREAM' ? value : 'BOTH'
}

function queryDepth(): number {
  const value = Number(new URLSearchParams(window.location.search).get('max_depth') ?? 8)
  return Number.isInteger(value) && value >= 1 && value <= 8 ? value : 8
}

interface ResearchLineagePageProps { onOpenNode: (node: LineageNode) => void }

export default function ResearchLineagePage({ onOpenNode }: ResearchLineagePageProps) {
  const { tr } = useI18n()
  const [root, setRoot] = useState<LineageRoot | null>(queryRoot)
  const [direction, setDirection] = useState<LineageDirection>(queryDirection)
  const [maxDepth, setMaxDepth] = useState(queryDepth)
  const [graph, setGraph] = useState<ResearchLineageGraph | null>(null)
  const [summary, setSummary] = useState<ResearchLineageSummary | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [visibleTypes, setVisibleTypes] = useState<Set<LineageNodeType>>(() => new Set(NODE_TYPES))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const panRef = useRef<{ pointerId: number; x: number; left: number } | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [nextGraph, nextSummary] = await Promise.all([
        getResearchLineage({ root_type: root?.type, root_id: root?.id, direction, max_depth: maxDepth }),
        getResearchLineageSummary(),
      ])
      setGraph(nextGraph); setSummary(nextSummary)
      setSelectedId((current) => {
        if (current && nextGraph.nodes.some((node) => node.node_id === current)) return current
        return nextGraph.nodes.find((node) => root && node.node_type === root.type && (node.artifact_id === root.id || node.node_id === root.id))?.node_id
          ?? nextGraph.nodes.find((node) => node.node_type === 'HYPOTHESIS')?.node_id
          ?? nextGraph.nodes[0]?.node_id
          ?? null
      })
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setLoading(false) }
  }, [direction, maxDepth, root])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const visibleNodes = useMemo(() => graph?.nodes.filter((node) => visibleTypes.has(node.node_type)) ?? [], [graph, visibleTypes])
  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map((node) => node.node_id)), [visibleNodes])
  const visibleEdges = useMemo(() => graph?.edges.filter((edge) => visibleNodeIds.has(edge.source_node_id) && visibleNodeIds.has(edge.target_node_id)) ?? [], [graph, visibleNodeIds])
  const positioned = useMemo(() => layout(visibleNodes), [visibleNodes])
  const positionById = useMemo(() => new Map(positioned.nodes.map((node) => [node.node_id, node])), [positioned.nodes])
  const selected = graph?.nodes.find((node) => node.node_id === selectedId) ?? null
  const hovered = graph?.nodes.find((node) => node.node_id === hoveredId) ?? null
  const upstream = useMemo(() => selectedId ? traverse(selectedId, graph?.edges ?? [], 'upstream') : new Set<string>(), [graph?.edges, selectedId])
  const downstream = useMemo(() => selectedId ? traverse(selectedId, graph?.edges ?? [], 'downstream') : new Set<string>(), [graph?.edges, selectedId])
  const incoming = selected ? graph?.edges.filter((edge) => edge.target_node_id === selected.node_id) ?? [] : []
  const outgoing = selected ? graph?.edges.filter((edge) => edge.source_node_id === selected.node_id) ?? [] : []
  const missingSourceCount = graph ? graph.nodes.filter((node) => node.status === 'MISSING_SOURCE').length : summary?.missing_source_count
  const orphanCount = graph ? graph.nodes.filter((node) => node.status === 'ORPHAN').length : summary?.orphan_count

  function updateUrl(nextRoot: LineageRoot | null, nextDirection = direction, nextDepth = maxDepth) {
    const parameters = new URLSearchParams()
    if (nextRoot) { parameters.set('root_type', nextRoot.type); parameters.set('root_id', nextRoot.id) }
    parameters.set('direction', nextDirection); parameters.set('max_depth', String(nextDepth))
    window.history.pushState({}, '', `/research-lineage?${parameters.toString()}`)
  }

  function focusSelected() {
    if (!selected) return
    const next = { type: selected.node_type, id: selected.node_id }
    setRoot(next); updateUrl(next)
  }

  function resetGlobal() { setRoot(null); updateUrl(null) }

  function changeDirection(next: LineageDirection) { setDirection(next); updateUrl(root, next) }
  function changeDepth(next: number) { setMaxDepth(next); updateUrl(root, direction, next) }

  function toggleType(nodeType: LineageNodeType) {
    setVisibleTypes((current) => {
      const next = new Set(current)
      if (next.has(nodeType)) next.delete(nodeType); else next.add(nodeType)
      return next
    })
  }

  function pointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest('button')) return
    panRef.current = { pointerId: event.pointerId, x: event.clientX, left: event.currentTarget.scrollLeft }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  function pointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (!panRef.current || panRef.current.pointerId !== event.pointerId) return
    event.currentTarget.scrollLeft = panRef.current.left - (event.clientX - panRef.current.x)
  }
  function pointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    if (panRef.current?.pointerId === event.pointerId) panRef.current = null
  }

  function nodeClass(node: LineageNode) {
    if (node.node_id === selectedId) return 'selected'
    if (upstream.has(node.node_id)) return 'upstream'
    if (downstream.has(node.node_id)) return 'downstream'
    return selectedId ? 'dimmed' : ''
  }

  function edgeClass(edge: LineageEdge) {
    const upstreamPath = (upstream.has(edge.source_node_id) || edge.source_node_id === selectedId) && (upstream.has(edge.target_node_id) || edge.target_node_id === selectedId)
    const downstreamPath = (downstream.has(edge.source_node_id) || edge.source_node_id === selectedId) && (downstream.has(edge.target_node_id) || edge.target_node_id === selectedId)
    return upstreamPath ? 'upstream' : downstreamPath ? 'downstream' : selectedId ? 'dimmed' : ''
  }

  return <main className="discover-shell research-workbench lineage-explorer">
    <section className="discover-title">
      <div><span className="section-kicker">{tr('Explicit global provenance')}</span><h1>{tr('Research Lineage')}</h1><p>{tr('Browse where every stored research object came from, which explicit records it passed through, and what it produced.')}</p></div>
      <span className="bias-tag">{tr('No inferred edges')}</span>
    </section>

    {error && <section className="workspace-panel research-error" role="alert">{tr(error)} <button onClick={() => void load()}>{tr('Retry')}</button></section>}

    <section className="workspace-panel lineage-toolbar">
      <div className="lineage-summary-strip">
        <span><small>{tr('Scope')}</small><strong>{tr(root ? 'Focused graph' : 'Global graph')}</strong></span>
        <span><small>{tr('Nodes')}</small><strong>{graph?.nodes.length ?? summary?.node_count ?? '—'}</strong></span>
        <span><small>{tr('Edges')}</small><strong>{graph?.edges.length ?? summary?.edge_count ?? '—'}</strong></span>
        <span className={(missingSourceCount ?? 0) > 0 ? 'warning' : ''}><small>{tr('Missing sources')}</small><strong>{missingSourceCount ?? '—'}</strong></span>
        <span><small>{tr('Orphans')}</small><strong>{orphanCount ?? '—'}</strong></span>
      </div>
      <div className="lineage-controls">
        <label><span>{tr('Direction')}</span><select aria-label={tr('Direction')} value={direction} onChange={(event) => changeDirection(event.target.value as LineageDirection)}><option value="BOTH">{tr('Both')}</option><option value="UPSTREAM">{tr('Upstream')}</option><option value="DOWNSTREAM">{tr('Downstream')}</option></select></label>
        <label><span>{tr('Maximum depth')}</span><select aria-label={tr('Maximum depth')} value={maxDepth} onChange={(event) => changeDepth(Number(event.target.value))}>{[1, 2, 3, 4, 5, 6, 7, 8].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <button disabled={!selected} onClick={focusSelected}>{tr('Focus selected')}</button>
        <button disabled={!root} onClick={resetGlobal}>{tr('Show global graph')}</button>
      </div>
      <fieldset className="lineage-type-filter"><legend>{tr('Node type filter')}</legend>{NODE_TYPES.map((nodeType) => <label key={nodeType}><input type="checkbox" checked={visibleTypes.has(nodeType)} onChange={() => toggleType(nodeType)} /><span>{tr(nodeType.replaceAll('_', ' '))}</span><b>{graph ? graph.nodes.filter((node) => node.node_type === nodeType).length : summary?.nodes_by_type.find((item) => item.node_type === nodeType)?.count ?? 0}</b></label>)}</fieldset>
      {hovered && <div className="lineage-hover"><b>{tr(hovered.node_type.replaceAll('_', ' '))}</b><span>{hovered.label}</span><code>{shortId(hovered.artifact_id)}</code></div>}
    </section>

    <section className="lineage-main-grid">
      <section className="workspace-panel lineage-graph-panel">
        <div className="section-heading"><div><span className="section-kicker">{tr('Deterministic layered layout')}</span><h2>{tr('Research dependency graph')}</h2></div><span>{tr('Drag or scroll horizontally')}</span></div>
        {loading && <div className="lineage-loading">{tr('Loading Research Lineage…')}</div>}
        {!loading && graph && <div ref={viewportRef} className="lineage-viewport" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
          <div className="lineage-canvas" style={{ width: positioned.width, height: positioned.height }}>
            {COLUMN_LABELS.map((label, index) => <span key={label} className="lineage-column-label" style={{ left: CANVAS_PADDING + index * COLUMN_PITCH }}>{String(index + 1).padStart(2, '0')} · {tr(label)}</span>)}
            <svg aria-hidden="true" width={positioned.width} height={positioned.height}>{visibleEdges.map((edge) => {
              const source = positionById.get(edge.source_node_id); const target = positionById.get(edge.target_node_id)
              if (!source || !target) return null
              return <path key={edge.edge_id} className={edgeClass(edge)} d={`M ${source.x + NODE_WIDTH} ${source.y + NODE_HEIGHT / 2} C ${source.x + NODE_WIDTH + 28} ${source.y + NODE_HEIGHT / 2}, ${target.x - 28} ${target.y + NODE_HEIGHT / 2}, ${target.x} ${target.y + NODE_HEIGHT / 2}`} />
            })}</svg>
            {positioned.nodes.map((node) => <button key={node.node_id} className={`lineage-node ${node.node_type.toLowerCase()} ${node.status.toLowerCase()} ${nodeClass(node)}`} style={{ left: node.x, top: node.y }} title={`${node.node_type} · ${node.artifact_id}`} onClick={() => setSelectedId(node.node_id)} onMouseEnter={() => setHoveredId(node.node_id)} onMouseLeave={() => setHoveredId(null)}>
              <span><b>{tr(node.node_type.replaceAll('_', ' '))}</b><em>{tr(node.status)}</em></span><strong>{node.label}</strong><code>{shortId(node.artifact_id)}</code>{node.revision != null && <small>r · {shortId(String(node.revision))}</small>}
            </button>)}
          </div>
        </div>}
      </section>

      <aside className="workspace-panel lineage-inspector">
        <div className="section-heading"><div><span className="section-kicker">{tr('Selected record')}</span><h2>{tr('Node Inspector')}</h2></div></div>
        {!selected && <p className="empty-copy">{tr('Select a node to inspect its recorded identity and edges.')}</p>}
        {selected && <>
          <div className={`lineage-inspector-status ${selected.status.toLowerCase()}`}><span>{tr(selected.node_type.replaceAll('_', ' '))}</span><b>{tr(selected.status)}</b></div>
          <h3>{selected.label}</h3>
          <dl><div><dt>{tr('ID')}</dt><dd><code>{selected.artifact_id}</code></dd></div><div><dt>{tr('Revision')}</dt><dd><code>{displayValue(selected.revision)}</code></dd></div><div><dt>{tr('Created at')}</dt><dd>{dateTime(selected.created_at)}</dd></div><div><dt>{tr('Incoming edges')}</dt><dd>{incoming.length}</dd></div><div><dt>{tr('Outgoing edges')}</dt><dd>{outgoing.length}</dd></div></dl>
          {(selected.metadata.integrity_mismatch === true || (selected.metadata.integrity_status != null && selected.metadata.integrity_status !== 'PASS')) && <div className="lineage-integrity-warning"><strong>{tr('Integrity warning')}</strong><p>{selected.metadata.integrity_mismatch === true ? tr('The explicitly attached Run records another Strategy ID. The edge is preserved for inspection.') : `${tr('Research Integrity')} · ${tr(String(selected.metadata.integrity_status))}`}</p></div>}
          <section><h4>{tr('Incoming edges')}</h4>{incoming.length === 0 ? <p>{tr('None')}</p> : incoming.map((edge) => <div key={edge.edge_id}><b>{tr(edge.edge_type.replaceAll('_', ' '))}</b><code>{edge.source_field}</code></div>)}</section>
          <section><h4>{tr('Outgoing edges')}</h4>{outgoing.length === 0 ? <p>{tr('None')}</p> : outgoing.map((edge) => <div key={edge.edge_id}><b>{tr(edge.edge_type.replaceAll('_', ' '))}</b><code>{edge.source_field}</code></div>)}</section>
          <button className="primary-button" disabled={selected.status === 'MISSING_SOURCE' || selected.route == null} onClick={() => onOpenNode(selected)}>{tr(selected.node_type === 'RUN' ? 'Open Run' : selected.node_type === 'TRACE' ? 'Open Replay' : selected.node_type === 'SNAPSHOT' ? 'Open Snapshot' : 'Open source record')}</button>
        </>}
      </aside>
    </section>
    {graph && <p className="workspace-disclosure">{tr(graph.disclosure)}</p>}
  </main>
}
