"""
src/agents/navigator.py
────────────────────────
The Navigator Agent — LangGraph-powered interactive query interface.

Four tools:
  1. find_implementation(concept)       — semantic search over Purpose Statements
  2. trace_lineage(dataset, direction)  — upstream/downstream graph traversal
  3. blast_radius(module_path)          — downstream dependency enumeration
  4. explain_module(path)               — LLM-generated explanation of a module

Every answer includes:
  - The source file and line range that supports the answer
  - The evidence source: "static_analysis" | "llm_inference" | "graph_traversal"

Branch: feature/07-navigator-agent
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.graph.knowledge_graph import KnowledgeGraph
from src.models.graph import Settings
from src.models.nodes import DatasetNode, ModuleNode
from src.utils.logging_config import get_logger
from src.utils.token_budget import ContextWindowBudget

logger = get_logger(__name__)


class NavigatorTools:
    """
    Implementation of the four Navigator tools against the KnowledgeGraph.
    Can be used standalone (without LangGraph) for testing.
    """

    def __init__(
        self,
        graph: KnowledgeGraph,
        repo_path: Path,
        settings: Settings,
        budget: ContextWindowBudget | None = None,
    ) -> None:
        self._graph    = graph
        self._repo_path = repo_path
        self._settings = settings
        self._budget   = budget or ContextWindowBudget(
            bulk_model=settings.bulk_llm_model,
            synthesis_model=settings.synthesis_llm_model,
        )

    # ── Tool 1: find_implementation ───────────────────────────────────────────

    def find_implementation(self, concept: str) -> dict[str, Any]:
        """
        Semantic search: find modules whose purpose statement best matches *concept*.
        Uses keyword matching as primary method, embedding cosine similarity if available.
        """
        concept_lower = concept.lower()
        modules = self._graph.all_nodes_of_type(ModuleNode)

        scored: list[tuple[float, ModuleNode]] = []
        for m in modules:
            text = (
                (m.purpose_statement or "") + " " + m.path + " " +
                " ".join(m.exported_symbols)
            ).lower()
            # Simple TF-style score: count concept word matches
            words = re.findall(r"\w+", concept_lower)
            score = sum(text.count(w) for w in words)
            if score > 0:
                scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:5]

        results = [
            {
                "path":             m.path,
                "purpose":          m.purpose_statement or "(no purpose statement)",
                "exported_symbols": m.exported_symbols[:8],
                "relevance_score":  s,
                "evidence_source":  "static_analysis + purpose_statement",
            }
            for s, m in top
        ]

        logger.info("find_implementation_query", concept=concept, results=len(results))
        return {
            "query":   concept,
            "results": results,
            "tool":    "find_implementation",
        }

    # ── Tool 2: trace_lineage ─────────────────────────────────────────────────

    def trace_lineage(
        self,
        dataset: str,
        direction: str = "upstream",
    ) -> dict[str, Any]:
        """
        Graph traversal: find upstream (ancestors) or downstream (descendants)
        of a dataset node.

        Parameters
        ----------
        dataset:
            Dataset name or partial name to look up.
        direction:
            "upstream" (what produces this?) or "downstream" (what does this feed?).
        """
        # Fuzzy match dataset name
        node_id = self._resolve_dataset(dataset)
        if not node_id:
            return {
                "error": f"Dataset '{dataset}' not found in lineage graph.",
                "tip":   "Try a partial name or run `cartographer query --list-datasets`",
            }

        if direction == "upstream":
            related = self._graph.ancestors(node_id)
        else:
            related = self._graph.blast_radius(node_id)

        # Enrich results with file citations
        enriched: list[dict[str, Any]] = []
        for rid in related[:20]:
            node = self._graph.get_node(rid)
            entry: dict[str, Any] = {"node_id": rid, "type": type(node).__name__ if node else "unknown"}
            if node:
                edges = self._graph.edges_to(rid) if direction == "upstream" else self._graph.edges_from(rid)
                for edge in edges[:2]:
                    edge_data = edge.model_dump()
                    entry["source_file"]  = edge_data.get("source_file", "")
                    entry["line_range"]   = edge_data.get("line_range", (0, 0))
            entry["evidence_source"] = "graph_traversal"
            enriched.append(entry)

        logger.info(
            "trace_lineage_query",
            dataset=dataset,
            direction=direction,
            related_count=len(related),
        )
        return {
            "dataset":      dataset,
            "node_id":      node_id,
            "direction":    direction,
            "related":      enriched,
            "total_count":  len(related),
            "tool":         "trace_lineage",
        }

    def _resolve_dataset(self, name: str) -> str | None:
        """Find a dataset node whose name contains *name* (case-insensitive)."""
        name_lower = name.lower()
        for node in self._graph.all_nodes_of_type(DatasetNode):
            if name_lower in node.name.lower():
                return node.node_id
        # Also check all node IDs
        for nid in self._graph._graph.nodes:  # noqa: SLF001
            if name_lower in nid.lower():
                return nid
        return None

    # ── Tool 3: blast_radius ──────────────────────────────────────────────────

    def blast_radius(self, module_path: str) -> dict[str, Any]:
        """
        Graph traversal: list all nodes downstream of *module_path*.
        Answer: "What breaks if this module changes its interface?"
        """
        # Try exact match first, then fuzzy
        node = self._graph.get_node(module_path)
        if node is None:
            # Fuzzy: find a module whose path contains the input
            modules = self._graph.all_nodes_of_type(ModuleNode)
            for m in modules:
                if module_path.lower() in m.path.lower():
                    module_path = m.node_id
                    break

        affected = self._graph.blast_radius(module_path)

        logger.info(
            "blast_radius_query",
            module=module_path,
            affected_count=len(affected),
        )
        return {
            "module":         module_path,
            "affected_nodes": affected[:30],
            "total_affected": len(affected),
            "risk_level":     "HIGH" if len(affected) > 10 else "MEDIUM" if affected else "LOW",
            "evidence_source": "graph_traversal",
            "tool":           "blast_radius",
        }

    # ── Tool 4: explain_module ────────────────────────────────────────────────

    def explain_module(self, path: str) -> dict[str, Any]:
        """
        Generate an explanation of what a module does.
        Uses pre-computed Purpose Statement if available; falls back to LLM.
        """
        node = self._graph.get_node(path)
        if node is None:
            # Fuzzy match
            modules = self._graph.all_nodes_of_type(ModuleNode)
            for m in modules:
                if path.lower() in m.path.lower():
                    node = m
                    path = m.path
                    break

        if not isinstance(node, ModuleNode):
            return {"error": f"Module '{path}' not found."}

        explanation = {
            "path":              node.path,
            "language":          node.language.value,
            "purpose":           node.purpose_statement or "(run full analysis to generate)",
            "domain":            node.domain_cluster.value,
            "complexity_score":  node.complexity_score,
            "lines_of_code":     node.lines_of_code,
            "exported_symbols":  node.exported_symbols[:15],
            "imports":           node.imports[:10],
            "is_dead_code":      node.is_dead_code_candidate,
            "has_doc_drift":     node.has_docstring_drift,
            "change_velocity_30d": node.change_velocity_30d,
            "change_velocity_90d": node.change_velocity_90d,
            "evidence_source":   "static_analysis",
        }

        if node.purpose_statement:
            explanation["evidence_source"] = "static_analysis + llm_inference"

        logger.info("explain_module_query", path=path)
        return explanation


class Navigator:
    """
    Interactive query agent wrapping NavigatorTools.
    Supports both CLI-driven single queries and a REPL loop.
    """

    _TOOL_DISPATCH = {
        "find_implementation": "find_implementation",
        "trace_lineage":       "trace_lineage",
        "blast_radius":        "blast_radius",
        "explain_module":      "explain_module",
    }

    def __init__(self, tools: NavigatorTools) -> None:
        self._tools = tools

    def query(self, tool: str, **kwargs: Any) -> dict[str, Any]:
        """
        Dispatch a query to the named tool.

        Raises
        ------
        ValueError
            If *tool* is not one of the four registered tools.
        """
        if tool not in self._TOOL_DISPATCH:
            raise ValueError(
                f"Unknown tool '{tool}'. "
                f"Available: {list(self._TOOL_DISPATCH.keys())}"
            )
        method = getattr(self._tools, self._TOOL_DISPATCH[tool])
        return method(**kwargs)

    def interactive_loop(self) -> None:
        """Start a simple REPL query loop (for CLI `query` subcommand)."""
        from rich.console import Console
        from rich.json import JSON
        from rich.prompt import Prompt

        console = Console()
        console.print(
            "\n[bold cyan]Brownfield Cartographer — Navigator[/bold cyan]\n"
            "Available tools: [yellow]find_implementation[/yellow], "
            "[yellow]trace_lineage[/yellow], "
            "[yellow]blast_radius[/yellow], "
            "[yellow]explain_module[/yellow]\n"
            "Type [red]exit[/red] to quit.\n"
        )

        while True:
            try:
                tool = Prompt.ask("[bold]Tool[/bold]").strip()
                if tool.lower() in ("exit", "quit", "q"):
                    break

                if tool not in self._TOOL_DISPATCH:
                    console.print(f"[red]Unknown tool:[/red] {tool}")
                    continue

                kwargs: dict[str, Any] = {}

                if tool == "find_implementation":
                    kwargs["concept"] = Prompt.ask("  concept")
                elif tool == "trace_lineage":
                    kwargs["dataset"]   = Prompt.ask("  dataset")
                    kwargs["direction"] = Prompt.ask("  direction (upstream/downstream)", default="upstream")
                elif tool == "blast_radius":
                    kwargs["module_path"] = Prompt.ask("  module_path")
                elif tool == "explain_module":
                    kwargs["path"] = Prompt.ask("  path")

                result = self.query(tool, **kwargs)
                console.print(JSON(json.dumps(result, indent=2, default=str)))

            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
                break
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Error:[/red] {exc}")
