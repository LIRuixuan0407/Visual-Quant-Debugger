from __future__ import annotations

from collections import Counter, deque

from .builder import NODE_TYPE_ORDER, ResearchLineageBuilder
from .models import (
    LineageDirection,
    LineageNodeType,
    LineageTypeCount,
    ResearchLineageGraph,
    ResearchLineageSummary,
)


class ResearchLineageService:
    def __init__(self, builder: ResearchLineageBuilder) -> None:
        self.builder = builder

    def graph(
        self,
        *,
        root_type: LineageNodeType | None = None,
        root_id: str | None = None,
        direction: LineageDirection = "BOTH",
        max_depth: int = 8,
        node_types: tuple[LineageNodeType, ...] = (),
        workspace_members: frozenset[tuple[str, str]] | None = None,
    ) -> ResearchLineageGraph:
        if (root_type is None) != (root_id is None):
            raise ValueError("root_type and root_id must be provided together")
        complete = self.builder.build()
        if root_type is not None and root_id is not None and workspace_members is not None:
            roots_in_workspace = tuple(
                node
                for node in complete.nodes
                if node.node_type == root_type
                and (node.artifact_id == root_id or node.node_id == root_id)
                and (node.node_type, node.artifact_id) in workspace_members
            )
            if not roots_in_workspace:
                raise ValueError(
                    f"Lineage root '{root_type}:{root_id}' is outside the selected Workspace"
                )
        selected_ids = {node.node_id for node in complete.nodes}
        if root_type is not None and root_id is not None:
            roots = tuple(
                node.node_id
                for node in complete.nodes
                if node.node_type == root_type
                and (node.artifact_id == root_id or node.node_id == root_id)
            )
            if not roots:
                raise KeyError(f"{root_type}:{root_id}")
            incoming: dict[str, list[str]] = {}
            outgoing: dict[str, list[str]] = {}
            for edge in complete.edges:
                incoming.setdefault(edge.target_node_id, []).append(edge.source_node_id)
                outgoing.setdefault(edge.source_node_id, []).append(edge.target_node_id)
            selected_ids = set(roots)
            frontier = deque((root, 0) for root in roots)
            while frontier:
                node_id, depth = frontier.popleft()
                if depth >= max_depth:
                    continue
                neighbors: list[str] = []
                if direction in {"UPSTREAM", "BOTH"}:
                    neighbors.extend(incoming.get(node_id, ()))
                if direction in {"DOWNSTREAM", "BOTH"}:
                    neighbors.extend(outgoing.get(node_id, ()))
                for neighbor in sorted(set(neighbors)):
                    if neighbor in selected_ids:
                        continue
                    selected_ids.add(neighbor)
                    frontier.append((neighbor, depth + 1))
        if node_types:
            allowed = set(node_types)
            selected_ids &= {node.node_id for node in complete.nodes if node.node_type in allowed}
        if workspace_members is not None and root_type is None:
            selected_ids &= {
                node.node_id
                for node in complete.nodes
                if (node.node_type, node.artifact_id) in workspace_members
            }
        nodes = tuple(
            node.model_copy(
                update={
                    "metadata": {
                        **node.metadata,
                        "workspace_member": (
                            (node.node_type, node.artifact_id) in workspace_members
                        ),
                    }
                }
            )
            if workspace_members is not None
            else node
            for node in complete.nodes
            if node.node_id in selected_ids
        )
        edges = tuple(
            edge
            for edge in complete.edges
            if edge.source_node_id in selected_ids and edge.target_node_id in selected_ids
        )
        return ResearchLineageGraph(
            root_type=root_type,
            root_id=root_id,
            direction=direction,
            max_depth=max_depth,
            nodes=nodes,
            edges=edges,
        )

    def summary(self) -> ResearchLineageSummary:
        graph = self.builder.build()
        counts = Counter(node.node_type for node in graph.nodes)
        return ResearchLineageSummary(
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            missing_source_count=sum(node.status == "MISSING_SOURCE" for node in graph.nodes),
            orphan_count=sum(node.status == "ORPHAN" for node in graph.nodes),
            nodes_by_type=tuple(
                LineageTypeCount(node_type=node_type, count=counts[node_type])
                for node_type in NODE_TYPE_ORDER
            ),
        )
