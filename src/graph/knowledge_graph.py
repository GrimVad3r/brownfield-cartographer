"""
src/graph/knowledge_graph.py
─────────────────────────────
Central in-memory knowledge graph backed by NetworkX.

Stores all ModuleNode, DatasetNode, FunctionNode, TransformationNode
objects as node attributes on a DiGraph, and all edge types as directed
edges with metadata.

Also maintains a ChromaDB vector collection for semantic search over
Purpose Statements.

Branch: feature/08-knowledge-graph
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import networkx as nx

from src.models.edges import BaseEdge, EdgeType, ImportsEdge, ProducesEdge, ConsumesEdge, CallsEdge, ConfiguresEdge
from src.models.nodes import (
    BaseNode,
    DatasetNode,
    FunctionNode,
    ModuleNode,
    TransformationNode,
)
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

NodeType = ModuleNode | DatasetNode | FunctionNode | TransformationNode


class KnowledgeGraph:
    """
    Thin wrapper around a NetworkX DiGraph providing typed node/edge
    operations, PageRank, SCC analysis, BFS blast-radius, and JSON
    serialisation.
    """

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        logger.debug("knowledge_graph_initialised")

    # ── Node operations ───────────────────────────────────────────────────────

    def add_node(self, node: NodeType) -> None:
        """Add or update a node in the graph."""
        self._graph.add_node(node.node_id, data=node)
        logger.debug("node_added", node_id=node.node_id, type=type(node).__name__)

    def remove_node(self, node_id: str) -> None:
        """Remove a node and all attached edges, if it exists."""
        if node_id in self._graph:
            self._graph.remove_node(node_id)
            logger.debug("node_removed", node_id=node_id)

    def remove_nodes_by_predicate(self, predicate: Callable[[BaseNode], bool]) -> int:
        """Remove all nodes for which *predicate* returns True. Returns count."""
        to_remove: list[str] = []
        for node_id, data in self._graph.nodes(data=True):
            node = data.get("data")
            if isinstance(node, BaseNode) and predicate(node):
                to_remove.append(node_id)
        for node_id in to_remove:
            self._graph.remove_node(node_id)
        if to_remove:
            logger.debug("nodes_removed_by_predicate", count=len(to_remove))
        return len(to_remove)

    def get_node(self, node_id: str) -> NodeType | None:
        """Retrieve a node by its ID, or None if not found."""
        if node_id not in self._graph:
            return None
        return self._graph.nodes[node_id].get("data")

    def all_nodes_of_type(self, node_type: type) -> list[NodeType]:
        """Return all nodes that are instances of *node_type*."""
        return [
            data["data"]
            for _, data in self._graph.nodes(data=True)
            if isinstance(data.get("data"), node_type)
        ]

    # ── Edge operations ───────────────────────────────────────────────────────

    def add_edge(self, edge: BaseEdge) -> None:
        """Add a directed edge to the graph."""
        self._graph.add_edge(
            edge.source_id,
            edge.target_id,
            edge_type=edge.edge_type.value,
            data=edge,
        )

    def remove_edges_by_type(self, edge_type: EdgeType) -> int:
        """Remove all edges of the given *edge_type*. Returns count."""
        to_remove: list[tuple[str, str]] = []
        for u, v, data in self._graph.edges(data=True):
            if data.get("edge_type") == edge_type.value:
                to_remove.append((u, v))
            else:
                edge = data.get("data")
                if isinstance(edge, BaseEdge) and edge.edge_type == edge_type:
                    to_remove.append((u, v))
        for u, v in to_remove:
            if self._graph.has_edge(u, v):
                self._graph.remove_edge(u, v)
        if to_remove:
            logger.debug("edges_removed_by_type", edge_type=edge_type.value, count=len(to_remove))
        return len(to_remove)

    def remove_edges_by_source_file(self, source_file: str) -> int:
        """Remove edges whose metadata source_file matches *source_file*."""
        to_remove: list[tuple[str, str]] = []
        for u, v, data in self._graph.edges(data=True):
            edge = data.get("data")
            edge_source = None
            if isinstance(edge, BaseEdge):
                edge_source = getattr(edge, "source_file", None)
            if edge_source == source_file:
                to_remove.append((u, v))
        for u, v in to_remove:
            if self._graph.has_edge(u, v):
                self._graph.remove_edge(u, v)
        if to_remove:
            logger.debug("edges_removed_by_source_file", source_file=source_file, count=len(to_remove))
        return len(to_remove)

    def prune_orphan_datasets(self) -> int:
        """Remove DatasetNodes with no connected edges. Returns count removed."""
        to_remove: list[str] = []
        for node_id, data in self._graph.nodes(data=True):
            node = data.get("data")
            if isinstance(node, DatasetNode):
                if self._graph.in_degree(node_id) == 0 and self._graph.out_degree(node_id) == 0:
                    to_remove.append(node_id)
        for node_id in to_remove:
            self._graph.remove_node(node_id)
        if to_remove:
            logger.debug("orphan_datasets_removed", count=len(to_remove))
        return len(to_remove)

    def edges_from(self, node_id: str) -> list[BaseEdge]:
        return [
            data["data"]
            for _, _, data in self._graph.out_edges(node_id, data=True)
            if "data" in data
        ]

    def edges_to(self, node_id: str) -> list[BaseEdge]:
        return [
            data["data"]
            for _, _, data in self._graph.in_edges(node_id, data=True)
            if "data" in data
        ]

    # ── Graph analytics ───────────────────────────────────────────────────────

    def pagerank(self) -> dict[str, float]:
        """
        Run PageRank over the full graph.
        High-score nodes are architectural hubs (most imported / most consumed).
        """
        try:
            scores: dict[str, float] = nx.pagerank(self._graph, alpha=0.85)
            return scores
        except Exception as exc:  # noqa: BLE001
            logger.warning("pagerank_failed", error=str(exc))
            return {}

    def strongly_connected_components(self) -> list[list[str]]:
        """Return SCC groups — non-trivial SCCs indicate circular dependencies."""
        sccs = list(nx.strongly_connected_components(self._graph))
        return [list(scc) for scc in sccs if len(scc) > 1]

    def blast_radius(self, node_id: str) -> list[str]:
        """
        Return the sorted list of all node IDs downstream of *node_id*
        (i.e. everything that would be affected if *node_id* changed).
        Uses BFS over successors in the directed graph.
        """
        if node_id not in self._graph:
            logger.warning("blast_radius_unknown_node", node_id=node_id)
            return []
        descendants = nx.descendants(self._graph, node_id)
        return sorted(descendants)

    def ancestors(self, node_id: str) -> list[str]:
        """Return all upstream dependencies of *node_id*."""
        if node_id not in self._graph:
            return []
        return sorted(nx.ancestors(self._graph, node_id))

    def find_sources(self) -> list[str]:
        """Nodes with in-degree == 0 (entry points / raw data sources)."""
        return [n for n in self._graph.nodes if self._graph.in_degree(n) == 0]

    def find_sinks(self) -> list[str]:
        """Nodes with out-degree == 0 (final outputs / endpoints)."""
        return [n for n in self._graph.nodes if self._graph.out_degree(n) == 0]

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """
        Serialise the graph to a JSON-compatible dict with typed node/edge data.
        """
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        for node_id, data in self._graph.nodes(data=True):
            node_data = data.get("data")
            if node_data is not None:
                nodes.append({
                    "id": node_id,
                    "type": type(node_data).__name__,
                    **node_data.model_dump(mode="json"),
                })
            else:
                nodes.append({"id": node_id})

        for u, v, data in self._graph.edges(data=True):
            edge_data = data.get("data")
            if edge_data is not None:
                edges.append({
                    "source": u,
                    "target": v,
                    "type": edge_data.edge_type.value,
                    **edge_data.model_dump(mode="json"),
                })
            else:
                edges.append({
                    "source": u,
                    "target": v,
                    "type": data.get("edge_type", ""),
                })

        return {"nodes": nodes, "edges": edges}

    def save(self, path: Path) -> None:
        """Write the graph to *path* as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        logger.info("graph_saved", path=str(path), nodes=self._graph.number_of_nodes(), edges=self._graph.number_of_edges())

    @classmethod
    def load(cls, path: Path) -> "KnowledgeGraph":
        """Load a previously serialised graph from *path*."""
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        kg = cls()
        node_type_map = {
            "ModuleNode": ModuleNode,
            "DatasetNode": DatasetNode,
            "FunctionNode": FunctionNode,
            "TransformationNode": TransformationNode,
        }
        edge_type_map = {
            EdgeType.IMPORTS.value: ImportsEdge,
            EdgeType.PRODUCES.value: ProducesEdge,
            EdgeType.CONSUMES.value: ConsumesEdge,
            EdgeType.CALLS.value: CallsEdge,
            EdgeType.CONFIGURES.value: ConfiguresEdge,
        }

        for node in data.get("nodes", []):
            node_id = node.get("id") or node.get("node_id")
            node_type = node.get("type")
            if node_type in node_type_map:
                payload = {k: v for k, v in node.items() if k not in {"id", "type"}}
                if "node_id" not in payload and node_id:
                    payload["node_id"] = node_id
                try:
                    model = node_type_map[node_type](**payload)
                    kg.add_node(model)
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.debug("node_reconstruct_failed", node_id=node_id, error=str(exc))
            if node_id:
                kg._graph.add_node(node_id)

        # Backward compatibility: accept "links" from legacy node-link format
        edges = data.get("edges", []) or data.get("links", [])
        for edge in edges:
            edge_type = edge.get("type") or edge.get("edge_type")
            if edge_type in edge_type_map:
                payload = {k: v for k, v in edge.items() if k not in {"type"}}
                if "source_id" not in payload and edge.get("source"):
                    payload["source_id"] = edge.get("source")
                if "target_id" not in payload and edge.get("target"):
                    payload["target_id"] = edge.get("target")
                try:
                    model = edge_type_map[edge_type](**payload)
                    kg.add_edge(model)
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.debug("edge_reconstruct_failed", error=str(exc))
            # Fallback: add topology only
            source = edge.get("source") or edge.get("source_id")
            target = edge.get("target") or edge.get("target_id")
            if source and target:
                kg._graph.add_edge(source, target)
        logger.info("graph_loaded", path=str(path))
        return kg

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, int]:
        return {
            "nodes": self._graph.number_of_nodes(),
            "edges": self._graph.number_of_edges(),
            "modules": len(self.all_nodes_of_type(ModuleNode)),
            "datasets": len(self.all_nodes_of_type(DatasetNode)),
            "functions": len(self.all_nodes_of_type(FunctionNode)),
            "transformations": len(self.all_nodes_of_type(TransformationNode)),
        }
