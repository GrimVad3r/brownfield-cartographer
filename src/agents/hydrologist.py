"""
src/agents/hydrologist.py
──────────────────────────
Agent 2: The Hydrologist — Data Flow & Lineage Analyst.

Responsibilities
----------------
* Run PythonDataFlowAnalyzer to detect pandas/SQLAlchemy/PySpark read/write calls.
* Run SQLLineageAnalyzer on all .sql and dbt model files.
* Run DAGConfigAnalyzer on Airflow/dbt YAML configs.
* Merge all findings into the DataLineageGraph (NetworkX DiGraph of datasets).
* Expose blast_radius(node), find_sources(), find_sinks().
* Persist lineage graph to .cartography/lineage_graph.json.

Branch: feature/04-hydrologist-agent
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.analyzers.dag_config_parser import DAGConfigAnalyzer, PipelineEdge
from src.analyzers.sql_lineage import SQLLineageAnalyzer, TableDependency
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.edges import ConsumesEdge, ProducesEdge
from src.models.nodes import DatasetNode, ModuleNode, StorageType, TransformationNode, TransformationType
from src.utils.logging_config import get_logger
from src.utils.security import is_safe_file

logger = get_logger(__name__)

# ── Python data-flow patterns ─────────────────────────────────────────────────

_PY_READ_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # (pattern, operation_label, storage_type_hint)
    (re.compile(r'read_csv\s*\(\s*["\']([^"\']+)["\']'),       "read_csv",  "file"),
    (re.compile(r'read_parquet\s*\(\s*["\']([^"\']+)["\']'),   "read_parquet","file"),
    (re.compile(r'read_sql\s*\(\s*["\']([^"\']+)["\']'),       "read_sql",  "table"),
    (re.compile(r'read_table\s*\(\s*["\']([^"\']+)["\']'),     "read_table","table"),
    (re.compile(r'spark\.read\.[^(]+\(\s*["\']([^"\']+)["\']'),"spark_read","file"),
    (re.compile(r'\.load\(\s*["\']([^"\']+)["\']'),            "spark_load","table"),
    (re.compile(r'execute\(\s*["\']([^"\']+)["\']'),           "sql_exec",  "table"),
    (re.compile(r'from_table\(\s*["\']([^"\']+)["\']'),        "from_table","table"),
]

_PY_WRITE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r'to_csv\s*\(\s*["\']([^"\']+)["\']'),        "to_csv",    "file"),
    (re.compile(r'to_parquet\s*\(\s*["\']([^"\']+)["\']'),    "to_parquet","file"),
    (re.compile(r'to_sql\s*\(\s*["\']([^"\']+)["\']'),        "to_sql",    "table"),
    (re.compile(r'spark.*write.*save\s*\(\s*["\']([^"\']+)["\']'), "spark_write","file"),
    (re.compile(r'insertInto\s*\(\s*["\']([^"\']+)["\']'),    "insertInto","table"),
]

_DYNAMIC_REF_RE = re.compile(r'(?:read_csv|read_sql|to_sql|to_csv|read_parquet)\s*\([^"\'(][^)]*\)')


class PythonDataFlowAnalyzer:
    """
    Regex-based Python data flow detector.
    Extracts dataset read/write operations from Python source files.
    Logs f-string / variable references as 'dynamic reference, cannot resolve'.
    """

    def analyse_file(self, path: Path, repo_path: Path) -> list[dict[str, Any]]:
        if not is_safe_file(path):
            return []

        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("py_dataflow_read_error", path=str(path), error=str(exc))
            return []

        rel_path = str(path.relative_to(repo_path))
        findings: list[dict[str, Any]] = []

        # Log dynamic references (f-strings, variables — cannot resolve)
        for m in _DYNAMIC_REF_RE.finditer(source):
            logger.debug(
                "dynamic_reference_cannot_resolve",
                path=rel_path,
                snippet=m.group(0)[:60],
            )

        # Static read patterns
        for pattern, op_label, storage_hint in _PY_READ_PATTERNS:
            for m in pattern.finditer(source):
                dataset_name = m.group(1).strip()
                lineno = source[: m.start()].count("\n") + 1
                findings.append({
                    "dataset":      dataset_name,
                    "operation":    op_label,
                    "direction":    "read",
                    "storage_type": storage_hint,
                    "source_file":  rel_path,
                    "line":         lineno,
                })

        # Static write patterns
        for pattern, op_label, storage_hint in _PY_WRITE_PATTERNS:
            for m in pattern.finditer(source):
                dataset_name = m.group(1).strip()
                lineno = source[: m.start()].count("\n") + 1
                findings.append({
                    "dataset":      dataset_name,
                    "operation":    op_label,
                    "direction":    "write",
                    "storage_type": storage_hint,
                    "source_file":  rel_path,
                    "line":         lineno,
                })

        return findings


class Hydrologist:
    """
    Constructs the DataLineageGraph by merging Python, SQL, and config
    analysis results into the shared KnowledgeGraph.
    """

    def __init__(self, graph: KnowledgeGraph) -> None:
        self._graph      = graph
        self._py_flow    = PythonDataFlowAnalyzer()
        self._sql_lin    = SQLLineageAnalyzer()
        self._dag_cfg    = DAGConfigAnalyzer()

    # ── Main entry point ──────────────────────────────────────────────────────

    def analyse(self, repo_path: Path) -> dict[str, Any]:
        logger.info("hydrologist_started", repo=str(repo_path))

        py_findings:   list[dict[str, Any]]  = []
        sql_deps:      list[TableDependency] = []
        config_edges:  list[PipelineEdge]    = []

        # ── Walk repository ───────────────────────────────────────────────────
        for fpath in sorted(repo_path.rglob("*")):
            if not fpath.is_file():
                continue
            # Skip hidden directories
            if any(part.startswith(".") for part in fpath.parts):
                continue

            ext = fpath.suffix.lower()

            if ext == ".py":
                py_findings.extend(self._py_flow.analyse_file(fpath, repo_path))
                config_edges.extend(self._dag_cfg.analyse(fpath))

            elif ext == ".sql":
                sql_deps.extend(self._sql_lin.analyse_file(fpath))

            elif ext in {".yaml", ".yml"}:
                config_edges.extend(self._dag_cfg.analyse(fpath))

        # ── Populate knowledge graph ──────────────────────────────────────────
        self._ingest_python_findings(py_findings)
        self._ingest_sql_dependencies(sql_deps)
        self._ingest_config_edges(config_edges)

        summary = {
            "python_dataflow_refs": len(py_findings),
            "sql_dependencies":     len(sql_deps),
            "config_edges":         len(config_edges),
            "dataset_nodes":        len(self._graph.all_nodes_of_type(DatasetNode)),
            "transformation_nodes": len(self._graph.all_nodes_of_type(TransformationNode)),
        }

        logger.info("hydrologist_completed", **summary)
        return summary

    # ── Ingestion helpers ─────────────────────────────────────────────────────

    def _get_or_create_dataset(self, name: str, storage_type: str) -> DatasetNode:
        node_id = f"dataset:{name}"
        existing = self._graph.get_node(node_id)
        if existing is not None:
            return existing  # type: ignore[return-value]

        node = DatasetNode(
            node_id=node_id,
            name=name,
            storage_type=StorageType(storage_type) if storage_type in StorageType._value2member_map_ else StorageType.TABLE,  # noqa: SLF001
        )
        self._graph.add_node(node)
        return node

    def _ingest_python_findings(self, findings: list[dict[str, Any]]) -> None:
        for f in findings:
            dataset = self._get_or_create_dataset(f["dataset"], f["storage_type"])
            source_file = f["source_file"]
            line = f["line"]
            t_id = f"transform:{source_file}:{line}:{f['operation']}"

            transform = TransformationNode(
                node_id=t_id,
                transformation_type=TransformationType.READ if f["direction"] == "read" else TransformationType.WRITE,
                source_file=source_file,
                line_range=(line, line),
                source_datasets=[dataset.name] if f["direction"] == "read" else [],
                target_datasets=[dataset.name] if f["direction"] == "write" else [],
            )
            self._graph.add_node(transform)

            if f["direction"] == "read":
                edge = ConsumesEdge(
                    source_id=t_id,
                    target_id=dataset.node_id,
                    source_file=source_file,
                    line_range=(line, line),
                )
            else:
                edge = ProducesEdge(  # type: ignore[assignment]
                    source_id=t_id,
                    target_id=dataset.node_id,
                    source_file=source_file,
                    line_range=(line, line),
                )
            self._graph.add_edge(edge)

    def _ingest_sql_dependencies(self, deps: list[TableDependency]) -> None:
        for dep in deps:
            src_dataset  = self._get_or_create_dataset(dep.source_table, "table")
            tgt_dataset  = self._get_or_create_dataset(dep.target_table, "table")
            t_id = f"transform:{dep.source_file}:{dep.line_number}:sql"

            # Create or reuse transformation node for this SQL model
            if self._graph.get_node(t_id) is None:
                transform = TransformationNode(
                    node_id=t_id,
                    transformation_type=TransformationType.TRANSFORM,
                    source_file=dep.source_file,
                    line_range=(dep.line_number, dep.line_number),
                    source_datasets=[dep.source_table],
                    target_datasets=[dep.target_table],
                    sql_query=None,
                )
                self._graph.add_node(transform)

            # source_dataset → transformation
            consumes = ConsumesEdge(
                source_id=t_id,
                target_id=src_dataset.node_id,
                source_file=dep.source_file,
                line_range=(dep.line_number, dep.line_number),
            )
            # transformation → target_dataset
            produces = ProducesEdge(
                source_id=t_id,
                target_id=tgt_dataset.node_id,
                source_file=dep.source_file,
                line_range=(dep.line_number, dep.line_number),
            )
            self._graph.add_edge(consumes)
            self._graph.add_edge(produces)

    def _ingest_config_edges(self, edges: list[PipelineEdge]) -> None:
        from src.models.edges import ImportsEdge
        for e in edges:
            up   = self._get_or_create_dataset(e.upstream,   "table")
            down = self._get_or_create_dataset(e.downstream, "table")
            edge = ProducesEdge(
                source_id=up.node_id,
                target_id=down.node_id,
                source_file=e.source_file,
            )
            self._graph.add_edge(edge)

    # ── Query interface ───────────────────────────────────────────────────────

    def blast_radius(self, node_id: str) -> list[str]:
        return self._graph.blast_radius(node_id)

    def find_sources(self) -> list[str]:
        return self._graph.find_sources()

    def find_sinks(self) -> list[str]:
        return self._graph.find_sinks()
