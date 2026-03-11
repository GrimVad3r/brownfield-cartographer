"""
src/models/edges.py
────────────────────
Pydantic v2 edge schemas for the Cartographer knowledge graph.

Branch: feature/02-data-models
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class EdgeType(str, Enum):
    IMPORTS    = "IMPORTS"     # module → module
    PRODUCES   = "PRODUCES"    # transformation → dataset
    CONSUMES   = "CONSUMES"    # transformation ← dataset
    CALLS      = "CALLS"       # function → function
    CONFIGURES = "CONFIGURES"  # config_file → module/pipeline


class BaseEdge(BaseModel):
    source_id: str = Field(..., description="node_id of the source node")
    target_id: str = Field(..., description="node_id of the target node")
    edge_type:  EdgeType

    model_config = {"frozen": False, "extra": "forbid"}


class ImportsEdge(BaseEdge):
    edge_type: EdgeType   = EdgeType.IMPORTS
    import_count: int     = 1    # how many times the import appears (re-exports)
    is_relative: bool     = False


class ProducesEdge(BaseEdge):
    edge_type: EdgeType             = EdgeType.PRODUCES
    source_file: str               = ""
    line_range: tuple[int, int]    = (0, 0)


class ConsumesEdge(BaseEdge):
    edge_type: EdgeType             = EdgeType.CONSUMES
    source_file: str               = ""
    line_range: tuple[int, int]    = (0, 0)


class CallsEdge(BaseEdge):
    edge_type: EdgeType   = EdgeType.CALLS
    call_count: int       = 1


class ConfiguresEdge(BaseEdge):
    edge_type: EdgeType   = EdgeType.CONFIGURES
    config_key: str | None = None
