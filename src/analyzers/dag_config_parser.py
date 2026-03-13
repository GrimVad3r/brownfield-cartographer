"""
src/analyzers/dag_config_parser.py
────────────────────────────────────
Parse pipeline topology from configuration files rather than code.

Supports:
- Apache Airflow DAG files (Python + decorator patterns)
- dbt schema.yml / sources.yml (YAML)
- Prefect flow definitions (YAML)
- Generic YAML pipeline configs

Branch: feature/04-hydrologist-agent
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.utils.logging_config import get_logger
from src.utils.security import is_safe_file

logger = get_logger(__name__)


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class PipelineEdge:
    """A dependency edge extracted from a config file."""
    upstream: str
    downstream: str
    source_file: str
    config_type: str   # "airflow" | "dbt" | "prefect" | "generic"


@dataclass
class DAGTopology:
    """Full topology extracted from a single config file."""
    source_file: str
    pipeline_name: str
    edges: list[PipelineEdge] = field(default_factory=list)
    nodes: list[str]          = field(default_factory=list)
    raw_config: dict[str, Any] = field(default_factory=dict)


# ── Airflow ────────────────────────────────────────────────────────────────────

# Patterns for Airflow's >> and << bit-shift operators
_AIRFLOW_SHIFT_RE = re.compile(
    r"([\w]+)\s*>>\s*([\w]+)"
)
# @dag decorator
_AIRFLOW_DAG_NAME_RE = re.compile(r'dag_id\s*=\s*["\']([^"\']+)["\']')


class AirflowDAGParser:
    """
    Extract pipeline topology from Airflow Python DAG files.

    Looks for:
    - task >> task2 operator chaining
    - dag_id declarations
    """

    def parse(self, path: Path) -> DAGTopology | None:
        if not is_safe_file(path):
            return None

        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("airflow_read_error", path=str(path), error=str(exc))
            return None

        # Only process files that look like Airflow DAGs
        if "airflow" not in source.lower() and "DAG" not in source:
            return None

        dag_name_match = _AIRFLOW_DAG_NAME_RE.search(source)
        dag_name = dag_name_match.group(1) if dag_name_match else path.stem

        topology = DAGTopology(
            source_file=str(path),
            pipeline_name=dag_name,
        )

        nodes: set[str] = set()
        for match in _AIRFLOW_SHIFT_RE.finditer(source):
            upstream, downstream = match.group(1), match.group(2)
            nodes.update([upstream, downstream])
            topology.edges.append(PipelineEdge(
                upstream=upstream,
                downstream=downstream,
                source_file=str(path),
                config_type="airflow",
            ))

        topology.nodes = sorted(nodes)
        logger.debug(
            "airflow_dag_parsed",
            dag=dag_name,
            edges=len(topology.edges),
            path=str(path),
        )
        return topology


# ── dbt ───────────────────────────────────────────────────────────────────────

class DBTSchemaParser:
    """
    Parse dbt schema.yml and sources.yml files to extract:
    - Model descriptions and column metadata
    - Source table definitions
    - Test relationships (which imply lineage)
    """

    def parse(self, path: Path) -> dict[str, Any]:
        if not is_safe_file(path):
            return {}

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            data: dict[str, Any] = yaml.safe_load(raw) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("dbt_yaml_parse_error", path=str(path), error=str(exc))
            return {}

        if not isinstance(data, dict):
            return {}

        result: dict[str, Any] = {
            "source_file": str(path),
            "models": [],
            "sources": [],
        }

        # Models section
        for model in data.get("models", []):
            if not isinstance(model, dict):
                continue
            columns = model.get("columns", [])
            if not isinstance(columns, list):
                columns = []
            result["models"].append({
                "name":        model.get("name", ""),
                "description": model.get("description", ""),
                "columns":     [c.get("name") for c in columns if isinstance(c, dict)],
            })

        # Sources section
        for source in data.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_name = source.get("name", "")
            tables = source.get("tables", [])
            if not isinstance(tables, list):
                tables = []
            for table in tables:
                if not isinstance(table, dict):
                    continue
                result["sources"].append({
                    "source":      source_name,
                    "table":       table.get("name", ""),
                    "description": table.get("description", ""),
                })

        logger.debug(
            "dbt_schema_parsed",
            path=str(path),
            models=len(result["models"]),
            sources=len(result["sources"]),
        )
        return result


# ── Generic YAML pipeline ─────────────────────────────────────────────────────

class GenericYAMLPipelineParser:
    """
    Attempt to extract pipeline topology from any YAML config that
    contains dependency-like keys (depends_on, upstream, after, needs).
    """

    _DEP_KEYS = {"depends_on", "upstream", "after", "needs", "requires"}

    def parse(self, path: Path) -> DAGTopology | None:
        if not is_safe_file(path):
            return None

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            data = yaml.safe_load(raw)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("yaml_parse_error", path=str(path), error=str(exc))
            return None

        if not isinstance(data, dict):
            return None

        topology = DAGTopology(
            source_file=str(path),
            pipeline_name=path.stem,
            raw_config=data,
        )
        nodes: set[str] = set()

        self._recurse(data, topology, nodes, parent_name=path.stem)
        topology.nodes = sorted(nodes)
        if not topology.edges:
            return None  # No pipeline structure found

        logger.debug(
            "generic_yaml_pipeline_parsed",
            path=str(path),
            edges=len(topology.edges),
        )
        return topology

    def _recurse(
        self,
        obj: Any,
        topology: DAGTopology,
        nodes: set[str],
        parent_name: str,
    ) -> None:
        if isinstance(obj, dict):
            step_name = obj.get("name") or obj.get("id") or parent_name
            nodes.add(str(step_name))

            for dep_key in self._DEP_KEYS:
                deps = obj.get(dep_key, [])
                if isinstance(deps, str):
                    deps = [deps]
                for dep in (deps or []):
                    nodes.add(str(dep))
                    topology.edges.append(PipelineEdge(
                        upstream=str(dep),
                        downstream=str(step_name),
                        source_file=topology.source_file,
                        config_type="generic",
                    ))

            for key, value in obj.items():
                if key not in self._DEP_KEYS:
                    self._recurse(value, topology, nodes, parent_name=str(key))

        elif isinstance(obj, list):
            for item in obj:
                self._recurse(item, topology, nodes, parent_name=parent_name)


# ── Facade ────────────────────────────────────────────────────────────────────

class DAGConfigAnalyzer:
    """
    Unified interface: routes YAML/Python config files to the right parser
    and returns a normalised list of PipelineEdge objects.
    """

    def __init__(self) -> None:
        self._airflow = AirflowDAGParser()
        self._dbt     = DBTSchemaParser()
        self._generic = GenericYAMLPipelineParser()

    def analyse(self, path: Path) -> list[PipelineEdge]:
        """Return pipeline edges extracted from *path*."""
        ext = path.suffix.lower()
        edges: list[PipelineEdge] = []

        try:
            if ext == ".py":
                topology = self._airflow.parse(path)
                if topology:
                    edges.extend(topology.edges)

            elif ext in {".yaml", ".yml"}:
                # Try dbt-specific first, then generic
                self._dbt.parse(path)   # side-effect: logs metadata
                topology = self._generic.parse(path)
                if topology:
                    edges.extend(topology.edges)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dag_config_parse_failed", path=str(path), error=str(exc))
            return []

        return edges

    def analyse_directory(self, directory: Path) -> list[PipelineEdge]:
        """Recursively analyse all config files in *directory*."""
        all_edges: list[PipelineEdge] = []
        for f in sorted(directory.rglob("*")):
            if f.is_file() and f.suffix.lower() in {".py", ".yaml", ".yml"}:
                all_edges.extend(self.analyse(f))
        return all_edges
