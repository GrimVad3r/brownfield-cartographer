"""
src/models/graph.py
────────────────────
Top-level graph container models and application settings.

Branch: feature/02-data-models
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Application Settings ──────────────────────────────────────────────────────

class Settings(BaseSettings):
    """
    Loaded from environment variables / .env file.
    All sensitive values (API keys) must be provided via environment — never
    hard-coded or logged.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM
    anthropic_api_key: str | None   = None
    openai_api_key: str | None      = None
    openrouter_api_key: str | None  = None
    bulk_llm_model: str             = "claude-haiku-4-5-20251001"
    synthesis_llm_model: str        = "claude-sonnet-4-6"

    # GitHub
    github_token: str | None        = None

    # Vector store
    chroma_mode: str                = "local"
    chroma_host: str                = "localhost"
    chroma_port: int                = 8000

    # Security / resource limits
    max_repo_size_mb: int           = 500
    max_file_size_kb: int           = 500
    max_depth: int                  = 20

    # Logging
    log_level: str                  = "INFO"
    log_format: str                 = "console"

    def has_llm(self) -> bool:
        """Return True if at least one LLM API key is configured."""
        return any(
            [self.anthropic_api_key, self.openai_api_key, self.openrouter_api_key]
        )

    def redacted(self) -> dict[str, Any]:
        """Return a copy of the settings with secret values masked (safe to log)."""
        d = self.model_dump()
        for key in ("anthropic_api_key", "openai_api_key", "openrouter_api_key", "github_token"):
            if d.get(key):
                d[key] = "***"
        return d


# ── Graph-Level Container ─────────────────────────────────────────────────────

class CartographyRun(BaseModel):
    """
    Top-level record of a single Cartographer analysis run.
    Written to .cartography/run_manifest.json after completion.
    """

    run_id: str
    repo_path: str
    started_at: datetime             = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None     = None
    git_commit: str | None           = None     # HEAD commit SHA at time of run
    total_files_analysed: int        = 0
    total_files_skipped: int         = 0
    phases_completed: list[str]      = Field(default_factory=list)
    errors: list[str]                = Field(default_factory=list)
    llm_cost_usd: float              = 0.0
    output_dir: str                  = ".cartography"

    model_config = {"extra": "forbid"}


class DayOneAnswers(BaseModel):
    """Structured answers to the Five FDE Day-One Questions."""

    primary_ingestion_path: str
    critical_output_datasets: list[str]
    blast_radius_critical_module: str
    business_logic_concentration: str
    high_velocity_files: list[str]
    evidence_citations: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "forbid"}
