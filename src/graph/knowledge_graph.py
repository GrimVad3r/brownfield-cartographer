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
from typing import Any

import networkx as nx

from src.models.edges import BaseEdge, EdgeType
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
        Serialise the graph to a JSON-compatible dict using NetworkX's
        node-link format, extended with Pydantic model data.
        """
        node_link = nx.node_link_data(self._graph)

        # Enrich nodes with Pydantic model data
        enriched_nodes = []
        for node_entry in node_link.get("nodes", []):
            nid = node_entry["id"]
            node_data = self._graph.nodes[nid].get("data")
            if node_data is not None:
                enriched_nodes.append({
                    "id": nid,
                    "type": type(node_data).__name__,
                    **node_data.model_dump(mode="json"),
                })
            else:
                enriched_nodes.append(node_entry)

        node_link["nodes"] = enriched_nodes
        return node_link

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
        # Minimal reconstruction: restore graph topology (without typed models)
        for node in data.get("nodes", []):
            kg._graph.add_node(node["id"])
        for link in data.get("links", []):
            kg._graph.add_edge(link["source"], link["target"])
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
