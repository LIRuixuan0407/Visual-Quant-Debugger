import type { PipelineNode, StrategyDefinition } from '../../types/strategy'
import { useI18n } from '../../i18n/I18nProvider'

interface StrategyAnatomyProps {
  definition: StrategyDefinition
  selectedNodeId: string
  onSelect: (nodeId: string) => void
}

function StrategyAnatomy({ definition, selectedNodeId, onSelect }: StrategyAnatomyProps) {
  const { tr } = useI18n()
  return (
    <section className="anatomy-panel" aria-labelledby="anatomy-heading">
      <div className="section-heading">
        <h2 id="anatomy-heading">{tr('Strategy logic')}</h2>
      </div>
      <ol className="anatomy-spine">
        {definition.pipeline.map((node, index) => (
          <li key={node.node_id} className={node.node_id === selectedNodeId ? 'selected' : ''}>
            <button type="button" onClick={() => onSelect(node.node_id)} aria-pressed={node.node_id === selectedNodeId}>
              <span className="node-index">{String(index + 1).padStart(2, '0')}</span>
              <span className="node-copy"><strong>{tr(node.label)}</strong><small>{tr(node.category)}</small></span>
              <span className="node-output" aria-hidden="true">{node.outputs.length ? '↓' : '●'}</span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  )
}

function NodeList({ title, ids, nodes }: { title: string; ids: string[]; nodes: Map<string, PipelineNode> }) {
  const { tr } = useI18n()
  if (ids.length === 0) return null
  return (
    <div className="concept-list">
      <dt>{tr(title)}</dt>
      <dd>{ids.map((id) => tr(nodes.get(id)?.label ?? id)).join(' / ')}</dd>
    </div>
  )
}

export function ConceptInspector({ definition, node }: { definition: StrategyDefinition; node: PipelineNode }) {
  const { tr } = useI18n()
  const nodes = new Map(definition.pipeline.map((item) => [item.node_id, item]))
  const parameters = new Map(definition.parameters.map((parameter) => [parameter.key, parameter]))
  return (
    <aside className="concept-inspector" aria-labelledby="concept-heading">
      <h2 id="concept-heading">{tr(node.label)}</h2>
      <div className="concept-section"><p>{tr(node.description)}</p></div>
      <details className="advanced-disclosure" open>
        <summary>{tr('Calculation details')}</summary>
        {node.formula && <div className="formula-block"><p className="eyebrow">{tr('Formula')}</p><code>{node.formula}</code></div>}
        <dl className="concept-details">
          <NodeList title="Inputs" ids={node.inputs} nodes={nodes} />
          <NodeList title="Outputs" ids={node.outputs} nodes={nodes} />
          {node.related_parameters.length > 0 && (
            <div className="concept-list"><dt>{tr('Related parameters')}</dt><dd>{node.related_parameters.map((key) => tr(parameters.get(key)?.label ?? key)).join(' / ')}</dd></div>
          )}
          {node.used_by.length > 0 && <div className="concept-list"><dt>{tr('Used by')}</dt><dd>{node.used_by.map(tr).join(' / ')}</dd></div>}
        </dl>
        {node.category === 'EXECUTION' && (
          <div className="assumption-list">
            <h3>{tr('Execution assumptions')}</h3>
            {definition.execution_assumptions.map((assumption) => (
              <div key={assumption.key}><span>{tr(assumption.label)}</span><strong>{tr(assumption.value)}</strong><p>{tr(assumption.description)}</p></div>
            ))}
          </div>
        )}
      </details>
    </aside>
  )
}

export default StrategyAnatomy
