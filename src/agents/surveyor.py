"""
src/agents/surveyor.py
───────────────────────
Agent 1: The Surveyor — Static Structure Analyst.

Responsibilities
----------------
* Walk the repository and analyse every source file with LanguageRouter.
* Build the module import graph as a NetworkX DiGraph.
* Run PageRank to identify architectural hubs.
* Detect circular dependencies (strongly connected components).
* Compute git change velocity per file (last 30/90 days).
* Flag dead code candidates (exported symbols with zero internal references).
* Persist the module graph to .cartography/module_graph.json.

Branch: feature/03-surveyor-agent
"""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import networkx as nx

from src.analyzers.tree_sitter_analyzer import LanguageRouter
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.edges import ImportsEdge
from src.models.nodes import Language, ModuleNode
from src.utils.logging_config import get_logger
from src.utils.security import DEFAULT_MAX_DEPTH, is_safe_file

logger = get_logger(__name__)


class Surveyor:
    """
    Performs deep static analysis of a repository and populates the
    KnowledgeGraph with ModuleNode objects and IMPORTS edges.
    """

    def __init__(
        self,
        graph: KnowledgeGraph,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self._graph   = graph
        self._router  = LanguageRouter()
        self._max_depth = max_depth

    # ── Main entry point ──────────────────────────────────────────────────────

    def analyse(self, repo_path: Path) -> dict[str, Any]:
        """
        Run the full Surveyor analysis on *repo_path*.

        Returns a summary dict suitable for logging / reporting.
        """
        logger.info("surveyor_started", repo=str(repo_path))

        files_analysed  = 0
        files_skipped   = 0
        parse_errors    = 0

        # ── Step 1: Walk and parse every file ─────────────────────────────────
        for fpath in self._iter_source_files(repo_path):
            node = self._router.analyse_file(fpath, repo_path)
            if node is None:
                files_skipped += 1
                continue

            if node.parse_error:
                parse_errors += 1
                logger.warning(
                    "parse_error",
                    path=node.path,
                    error=node.parse_error,
                )

            self._graph.add_node(node)
            files_analysed += 1

        # ── Step 2: Build import edges ─────────────────────────────────────────
        self._build_import_edges(repo_path)

        # ── Step 3: Git velocity ───────────────────────────────────────────────
        self._enrich_git_velocity(repo_path)

        # ── Step 4: PageRank ───────────────────────────────────────────────────
        pagerank_scores = self._graph.pagerank()
        self._annotate_pagerank(pagerank_scores)

        # ── Step 5: Dead code candidates ───────────────────────────────────────
        self._detect_dead_code()

        summary = {
            "files_analysed": files_analysed,
            "files_skipped":  files_skipped,
            "parse_errors":   parse_errors,
            "total_modules":  len(self._graph.all_nodes_of_type(ModuleNode)),
            "circular_deps":  len(self._graph.strongly_connected_components()),
        }

        logger.info("surveyor_completed", **summary)
        return summary

    # ── File iteration ─────────────────────────────────────────────────────────

    def _iter_source_files(self, repo_path: Path):
        """
        Yield source files under *repo_path*, respecting depth limits and
        skipping hidden directories (e.g. .git, .venv, node_modules).
        """
        skip_dirs = {
            ".git", ".venv", "venv", "env", "__pycache__",
            "node_modules", ".tox", "dist", "build", ".eggs",
            ".mypy_cache", ".ruff_cache", ".cartography",
        }

        for root, dirs, files in os.walk(repo_path):
            # Depth check
            rel_root = Path(root).relative_to(repo_path)
            depth = len(rel_root.parts)
            if depth > self._max_depth:
                dirs.clear()
                continue

            # Prune hidden / irrelevant dirs in-place (modifies os.walk traversal)
            dirs[:] = [
                d for d in dirs
                if d not in skip_dirs and not d.startswith(".")
            ]

            for fname in files:
                fpath = Path(root) / fname
                yield fpath

    # ── Import graph ──────────────────────────────────────────────────────────

    def _build_import_edges(self, repo_path: Path) -> None:
        """
        For every ModuleNode, resolve its imports to other known modules
        and add an IMPORTS edge.
        """
        # Build a lookup: module-stem → node_id
        stem_map: dict[str, str] = {}
        for node in self._graph.all_nodes_of_type(ModuleNode):
            stem = Path(node.path).stem
            stem_map[stem] = node.node_id

        edges_added = 0
        for node in self._graph.all_nodes_of_type(ModuleNode):
            for imp in node.imports:
                # Resolve dotted import to stem (e.g. "src.utils.security" → "security")
                imp_stem = imp.split(".")[-1]
                target_id = stem_map.get(imp_stem)
                if target_id and target_id != node.node_id:
                    edge = ImportsEdge(
                        source_id=node.node_id,
                        target_id=target_id,
                        is_relative=imp.startswith("."),
                    )
                    self._graph.add_edge(edge)
                    edges_added += 1

        logger.debug("import_edges_built", count=edges_added)

    # ── Git velocity ──────────────────────────────────────────────────────────

    def _enrich_git_velocity(self, repo_path: Path) -> None:
        """
        Parse git log to compute commit frequency per file.
        Gracefully degrades if git is unavailable or the directory is not
        a git repository.
        """
        velocity_30d  = self._extract_git_velocity(repo_path, days=30)
        velocity_90d  = self._extract_git_velocity(repo_path, days=90)

        for node in self._graph.all_nodes_of_type(ModuleNode):
            node.change_velocity_30d = velocity_30d.get(node.path, 0)
            node.change_velocity_90d = velocity_90d.get(node.path, 0)

    @staticmethod
    def _extract_git_velocity(repo_path: Path, days: int) -> dict[str, int]:
        """
        Return a dict mapping repo-relative file path → commit count
        within the last *days* days.
        """
        since = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%d"
        )
        velocity: dict[str, int] = defaultdict(int)

        try:
            result = subprocess.run(  # noqa: S603
                [
                    "git", "-C", str(repo_path),
                    "log", f"--since={since}",
                    "--name-only", "--pretty=format:",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.debug("git_log_failed", stderr=result.stderr[:200])
                return {}

            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    velocity[line] += 1

        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.debug("git_unavailable", error=str(exc))

        return dict(velocity)

    # ── PageRank enrichment ────────────────────────────────────────────────────

    def _annotate_pagerank(self, scores: dict[str, float]) -> None:
        """
        Store PageRank scores as a node attribute for downstream reporting.
        Nodes are not modified via Pydantic model (no field for it) — stored
        separately in the graph node attributes dict.
        """
        for node_id, score in scores.items():
            if node_id in self._graph._graph:  # noqa: SLF001
                self._graph._graph.nodes[node_id]["pagerank"] = score  # noqa: SLF001

    # ── Dead code detection ────────────────────────────────────────────────────

    def _detect_dead_code(self) -> None:
        """
        Flag ModuleNodes as dead code candidates if they:
        - export at least one symbol, AND
        - have in-degree == 0 in the import graph (nothing imports them), AND
        - are not an entry point (main.py, cli.py, __init__.py, setup.py).
        """
        entry_point_names = {"main", "cli", "__init__", "__main__", "setup", "conftest"}
        in_degrees = dict(self._graph._graph.in_degree())  # noqa: SLF001

        flagged = 0
        for node in self._graph.all_nodes_of_type(ModuleNode):
            stem = Path(node.path).stem
            if stem in entry_point_names:
                continue
            if node.language not in {Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT}:
                continue
            if node.exported_symbols and in_degrees.get(node.node_id, 0) == 0:
                node.is_dead_code_candidate = True
                flagged += 1

        logger.info("dead_code_candidates_flagged", count=flagged)
