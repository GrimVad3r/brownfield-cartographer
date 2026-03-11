"""
src/models/nodes.py
────────────────────
Pydantic v2 node schemas for the Cartographer knowledge graph.

Every node type maps to a vertex in the NetworkX DiGraph and an entry
in the ChromaDB vector store (for ModuleNode and FunctionNode).

Branch: feature/02-data-models
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class Language(str, Enum):
    PYTHON     = "python"
    SQL        = "sql"
    YAML       = "yaml"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    NOTEBOOK   = "notebook"
    OTHER      = "other"


class StorageType(str, Enum):
    TABLE  = "table"
    FILE   = "file"
    STREAM = "stream"
    API    = "api"


class TransformationType(str, Enum):
    READ       = "read"
    WRITE      = "write"
    TRANSFORM  = "transform"
    JOIN       = "join"
    AGGREGATE  = "aggregate"
    FILTER     = "filter"
    UNKNOWN    = "unknown"


class DomainCluster(str, Enum):
    INGESTION      = "ingestion"
    TRANSFORMATION = "transformation"
    SERVING        = "serving"
    MONITORING     = "monitoring"
    CONFIGURATION  = "configuration"
    TESTING        = "testing"
    UNKNOWN        = "unknown"


# ── Base ──────────────────────────────────────────────────────────────────────

class BaseNode(BaseModel):
    """All knowledge-graph nodes share this base."""

    node_id: str = Field(..., description="Unique node identifier (usually the file path or qualified name)")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"frozen": False, "extra": "forbid"}


# ── Node Types ────────────────────────────────────────────────────────────────

class ModuleNode(BaseNode):
    """
    Represents a single source file (Python module, SQL file, YAML config, etc.).
    Produced by: The Surveyor agent.
    """

    path: str                              = Field(..., description="Repo-relative file path")
    language: Language                     = Language.OTHER
    purpose_statement: str | None          = None   # filled by Semanticist
    domain_cluster: DomainCluster          = DomainCluster.UNKNOWN
    complexity_score: float                = 0.0    # cyclomatic proxy
    lines_of_code: int                     = 0
    comment_ratio: float                   = 0.0    # comments / total lines
    change_velocity_30d: int               = 0      # commit count (last 30 days)
    change_velocity_90d: int               = 0      # commit count (last 90 days)
    is_dead_code_candidate: bool           = False
    last_modified: datetime | None         = None
    imports: list[str]                     = Field(default_factory=list)
    exported_symbols: list[str]            = Field(default_factory=list)
    has_docstring_drift: bool              = False
    docstring_drift_details: str | None    = None
    parse_error: str | None               = None    # set if AST parse failed

    @field_validator("complexity_score")
    @classmethod
    def _clamp_complexity(cls, v: float) -> float:
        return max(0.0, v)


class DatasetNode(BaseNode):
    """
    Represents a data asset (table, file, stream, API endpoint).
    Produced by: The Hydrologist agent.
    """

    name: str                              = Field(..., description="Dataset / table name")
    storage_type: StorageType             = StorageType.TABLE
    schema_snapshot: dict[str, Any]       = Field(default_factory=dict)
    freshness_sla: str | None             = None     # e.g. "daily", "hourly"
    owner: str | None                     = None
    is_source_of_truth: bool              = False
    source_files: list[str]               = Field(default_factory=list)


class FunctionNode(BaseNode):
    """
    Represents a callable (function or method) within a module.
    Produced by: The Surveyor agent.
    """

    qualified_name: str                   = Field(..., description="module.ClassName.method_name")
    parent_module: str                    = Field(..., description="Repo-relative path of parent file")
    signature: str                        = ""
    purpose_statement: str | None         = None
    call_count_within_repo: int           = 0
    is_public_api: bool                   = False
    start_line: int                       = 0
    end_line: int                         = 0


class TransformationNode(BaseNode):
    """
    Represents a data transformation step (e.g. a SQL CTE, a pandas pipeline).
    Produced by: The Hydrologist agent.
    """

    source_datasets: list[str]            = Field(default_factory=list)
    target_datasets: list[str]            = Field(default_factory=list)
    transformation_type: TransformationType = TransformationType.UNKNOWN
    source_file: str                      = ""
    line_range: tuple[int, int]           = (0, 0)
    sql_query: str | None                 = None
    description: str | None              = None
