"""
src/agents/archivist.py
────────────────────────
Agent 4: The Archivist — Living Context Maintainer.

Responsibilities
----------------
* Generate CODEBASE.md  — structured context file for AI agent injection.
* Generate onboarding_brief.md — FDE Day-One answers with evidence citations.
* Write lineage_graph.json — serialised DataLineageGraph.
* Maintain cartography_trace.jsonl — audit log of every analysis action.
* Manage the .cartography/ output directory.

Branch: feature/06-archivist-agent
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.graph.knowledge_graph import KnowledgeGraph
from src.models.graph import CartographyRun, DayOneAnswers
from src.models.nodes import DatasetNode, DomainCluster, ModuleNode
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_OUTPUT_DIR = ".cartography"


class Archivist:
    """
    Produces and maintains all Cartographer output artifacts.
    """

    def __init__(
        self,
        graph: KnowledgeGraph,
        output_dir: Path | None = None,
    ) -> None:
        self._graph      = graph
        self._output_dir = output_dir or Path(_OUTPUT_DIR)
        self._trace_path = self._output_dir / "cartography_trace.jsonl"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("archivist_initialised", output_dir=str(self._output_dir))

    # ── Trace logging ─────────────────────────────────────────────────────────

    def log_trace(
        self,
        action: str,
        agent: str,
        details: dict[str, Any] | None = None,
        confidence: float = 1.0,
        evidence_source: str = "static_analysis",
    ) -> None:
        """
        Append a structured trace entry to cartography_trace.jsonl.
        Follows the Week 1 audit pattern.
        """
        entry = {
            "timestamp":       datetime.now(tz=timezone.utc).isoformat(),
            "trace_id":        str(uuid.uuid4()),
            "agent":           agent,
            "action":          action,
            "confidence":      round(confidence, 3),
            "evidence_source": evidence_source,   # "static_analysis" | "llm_inference" | "git_analysis"
            "details":         details or {},
        }
        try:
            with self._trace_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError as exc:
            logger.warning("trace_write_error", error=str(exc))

    # ── Main entry point ──────────────────────────────────────────────────────

    def produce_artifacts(
        self,
        run: CartographyRun,
        day_one_answers: DayOneAnswers | None = None,
    ) -> list[Path]:
        """
        Generate all output artifacts. Returns list of paths written.
        """
        artifacts: list[Path] = []

        artifacts.append(self._write_codebase_md(run))
        artifacts.append(self._write_onboarding_brief(run, day_one_answers))
        artifacts.append(self._write_lineage_graph())
        artifacts.append(self._write_module_graph())
        artifacts.append(self._write_run_manifest(run))

        logger.info(
            "artifacts_produced",
            count=len(artifacts),
            paths=[str(a) for a in artifacts],
        )
        return artifacts

    # ── CODEBASE.md ───────────────────────────────────────────────────────────

    def _write_codebase_md(self, run: CartographyRun) -> Path:
        path = self._output_dir / "CODEBASE.md"
        modules     = self._graph.all_nodes_of_type(ModuleNode)
        pagerank    = self._graph.pagerank()
        sccs        = self._graph.strongly_connected_components()
        sources     = self._graph.find_sources()
        sinks       = self._graph.find_sinks()

        top_modules = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:5]
        high_vel    = sorted(modules, key=lambda m: m.change_velocity_90d, reverse=True)[:10]
        dead_code   = [m for m in modules if m.is_dead_code_candidate][:10]
        drift_mods  = [m for m in modules if m.has_docstring_drift][:10]

        # Group modules by domain
        domain_map: dict[str, list[str]] = {}
        for m in modules:
            cluster = m.domain_cluster.value
            domain_map.setdefault(cluster, []).append(m.path)

        lines = [
            "# CODEBASE.md — Cartographer Living Context",
            f"> Generated: {run.started_at.isoformat()} | Repo: {run.repo_path}",
            f"> Git commit: {run.git_commit or 'unknown'}",
            "",
            "---",
            "",
            "## Architecture Overview",
            "",
            f"This codebase contains **{len(modules)} analysed source files** across "
            f"{len(domain_map)} inferred domains. "
            f"The static analysis identified **{len(sccs)} circular dependency group(s)**, "
            f"**{len(dead_code)} dead code candidate(s)**, and "
            f"**{len(drift_mods)} documentation drift instance(s)**.",
            "",
            "---",
            "",
            "## Critical Path (Top 5 Modules by PageRank)",
            "",
            "These modules are imported most frequently — changes here have the highest blast radius.",
            "",
        ]

        for node_id, score in top_modules:
            node = self._graph.get_node(node_id)
            purpose = ""
            if isinstance(node, ModuleNode) and node.purpose_statement:
                purpose = f" — {node.purpose_statement[:120]}"
            lines.append(f"- `{node_id}` (score: {score:.4f}){purpose}")

        lines += [
            "",
            "---",
            "",
            "## Domain Architecture Map",
            "",
        ]
        for domain, paths in sorted(domain_map.items()):
            if domain == "unknown":
                continue
            lines.append(f"### {domain.capitalize()} ({len(paths)} modules)")
            for p in sorted(paths)[:8]:
                lines.append(f"  - `{p}`")
            if len(paths) > 8:
                lines.append(f"  - _(…and {len(paths) - 8} more)_")
            lines.append("")

        lines += [
            "---",
            "",
            "## Data Sources & Sinks",
            "",
            "### Sources (in-degree = 0 in lineage graph)",
            "",
        ]
        for s in sources[:10]:
            lines.append(f"- `{s}`")

        lines += [
            "",
            "### Sinks (out-degree = 0 in lineage graph)",
            "",
        ]
        for s in sinks[:10]:
            lines.append(f"- `{s}`")

        lines += [
            "",
            "---",
            "",
            "## Known Debt",
            "",
            "### Circular Dependencies",
            "",
        ]
        if sccs:
            for scc in sccs[:5]:
                lines.append(f"- {' ↔ '.join(scc)}")
        else:
            lines.append("_None detected._")

        lines += [
            "",
            "### Documentation Drift",
            "",
        ]
        if drift_mods:
            for m in drift_mods:
                lines.append(f"- `{m.path}`: {m.docstring_drift_details or 'Drift detected'}")
        else:
            lines.append("_None detected._")

        lines += [
            "",
            "---",
            "",
            "## High-Velocity Files (Last 90 Days)",
            "",
            "Files with the most commits are the most active pain points.",
            "",
        ]
        for m in high_vel:
            lines.append(
                f"- `{m.path}` — {m.change_velocity_90d} commits (30d: {m.change_velocity_30d})"
            )

        lines += [
            "",
            "---",
            "",
            "## Module Purpose Index",
            "",
        ]
        for m in sorted(modules, key=lambda x: x.path)[:50]:
            if m.purpose_statement:
                lines.append(f"### `{m.path}`")
                lines.append(f"_{m.domain_cluster.value}_ | LOC: {m.lines_of_code} | "
                             f"Complexity: {m.complexity_score:.0f}")
                lines.append("")
                lines.append(m.purpose_statement)
                lines.append("")

        content = "\n".join(lines)
        path.write_text(content, encoding="utf-8")
        logger.info("codebase_md_written", path=str(path), size_bytes=len(content))
        self.log_trace("generate_CODEBASE_md", "archivist", {"path": str(path)})
        return path

    # ── Onboarding brief ──────────────────────────────────────────────────────

    def _write_onboarding_brief(
        self,
        run: CartographyRun,
        answers: DayOneAnswers | None,
    ) -> Path:
        path = self._output_dir / "onboarding_brief.md"

        lines = [
            "# FDE Day-One Onboarding Brief",
            f"> Repo: {run.repo_path}  |  Generated: {run.started_at.isoformat()}",
            "",
            "---",
            "",
            "## The Five FDE Day-One Questions",
            "",
        ]

        if answers:
            lines += [
                "### 1. What is the primary data ingestion path?",
                "",
                answers.primary_ingestion_path,
                "",
                "### 2. What are the 3–5 most critical output datasets / endpoints?",
                "",
            ]
            for ds in answers.critical_output_datasets:
                lines.append(f"- {ds}")
            lines += [
                "",
                "### 3. What is the blast radius if the most critical module fails?",
                "",
                answers.blast_radius_critical_module,
                "",
                "### 4. Where is the business logic concentrated vs. distributed?",
                "",
                answers.business_logic_concentration,
                "",
                "### 5. What has changed most frequently in the last 90 days?",
                "",
            ]
            for f in answers.high_velocity_files:
                lines.append(f"- `{f}`")
            lines += [
                "",
                "---",
                "",
                "## Evidence Citations",
                "",
            ]
            for cite in answers.evidence_citations:
                lines.append(
                    f"- **{cite.get('claim', '')}** — `{cite.get('file_path', '')}` "
                    f"L{cite.get('line_range', '')}"
                )
        else:
            lines.append(
                "> _LLM analysis was not available. Configure an API key to generate "
                "Day-One answers with evidence citations._"
            )

        lines += [
            "",
            "---",
            "",
            "## Analysis Method",
            "",
            "| Source | Method |",
            "|--------|--------|",
            "| Module graph | Static analysis (tree-sitter AST) |",
            "| Data lineage | sqlglot SQL parsing + Python regex |",
            "| Purpose statements | LLM inference (grounded in code) |",
            "| Git velocity | `git log` history analysis |",
            "",
        ]

        content = "\n".join(lines)
        path.write_text(content, encoding="utf-8")
        logger.info("onboarding_brief_written", path=str(path))
        self.log_trace("generate_onboarding_brief", "archivist", {"path": str(path)})
        return path

    # ── Graph serialisation ───────────────────────────────────────────────────

    def _write_lineage_graph(self) -> Path:
        path = self._output_dir / "lineage_graph.json"
        self._graph.save(path)
        self.log_trace("serialise_lineage_graph", "archivist", {"path": str(path)})
        return path

    def _write_module_graph(self) -> Path:
        path = self._output_dir / "module_graph.json"
        self._graph.save(path)
        self.log_trace("serialise_module_graph", "archivist", {"path": str(path)})
        return path

    def _write_run_manifest(self, run: CartographyRun) -> Path:
        path = self._output_dir / "run_manifest.json"
        run.finished_at = datetime.now(tz=timezone.utc)
        path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        return path
