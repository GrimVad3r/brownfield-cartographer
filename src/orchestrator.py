"""
src/orchestrator.py
─────────────────────
Orchestrates the full four-agent Cartographer pipeline:

  Surveyor → Hydrologist → Semanticist → Archivist

Also supports:
- GitHub URL cloning to a temp directory.
- Incremental update mode: re-analyse only git-diff'd files since last run.
- Graceful degradation at every phase (a failed phase does not abort the run).

Branch: feature/09-cli-orchestrator
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agents.archivist import Archivist
from src.agents.hydrologist import Hydrologist
from src.agents.navigator import Navigator, NavigatorTools
from src.agents.semanticist import Semanticist
from src.agents.surveyor import Surveyor
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.graph import CartographyRun, Settings
from src.utils.logging_config import get_logger
from src.utils.security import (
    SecurityError,
    check_repo_size,
    validate_github_url,
    validate_local_path,
)
from src.utils.token_budget import ContextWindowBudget

logger = get_logger(__name__)


class Orchestrator:
    """
    Top-level coordinator for a single Cartographer analysis run.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._temp_dir: Path | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def run_full(self, target: str, output_dir: Path | None = None) -> CartographyRun:
        """
        Execute the full analysis pipeline on *target*
        (local path or GitHub URL).

        Returns the CartographyRun record.
        """
        run = CartographyRun(
            run_id=str(uuid.uuid4()),
            repo_path=target,
            started_at=datetime.now(tz=timezone.utc),
            output_dir=str(output_dir or ".cartography"),
        )

        try:
            repo_path = self._resolve_target(target)
        except SecurityError as exc:
            logger.error("target_validation_failed", error=str(exc))
            run.errors.append(str(exc))
            return run

        run.repo_path   = str(repo_path)
        run.git_commit  = self._get_head_commit(repo_path)

        graph    = KnowledgeGraph()
        budget   = ContextWindowBudget(
            bulk_model=self._settings.bulk_llm_model,
            synthesis_model=self._settings.synthesis_llm_model,
        )
        archivist = Archivist(graph, output_dir=(output_dir or Path(".cartography")))

        # ── Phase 1: Surveyor ─────────────────────────────────────────────────
        archivist.log_trace("phase_start", "orchestrator", {"phase": "surveyor"})
        try:
            surveyor_summary = Surveyor(graph).analyse(repo_path)
            run.total_files_analysed += surveyor_summary.get("files_analysed", 0)
            run.total_files_skipped  += surveyor_summary.get("files_skipped", 0)
            run.phases_completed.append("surveyor")
            archivist.log_trace("phase_complete", "orchestrator", {
                "phase": "surveyor", **surveyor_summary
            })
        except Exception as exc:  # noqa: BLE001
            logger.error("surveyor_phase_failed", error=str(exc))
            run.errors.append(f"Surveyor: {exc}")

        # ── Phase 2: Hydrologist ──────────────────────────────────────────────
        archivist.log_trace("phase_start", "orchestrator", {"phase": "hydrologist"})
        try:
            hydro_summary = Hydrologist(graph).analyse(repo_path)
            run.phases_completed.append("hydrologist")
            archivist.log_trace("phase_complete", "orchestrator", {
                "phase": "hydrologist", **hydro_summary
            })
        except Exception as exc:  # noqa: BLE001
            logger.error("hydrologist_phase_failed", error=str(exc))
            run.errors.append(f"Hydrologist: {exc}")

        # ── Phase 3: Semanticist ──────────────────────────────────────────────
        if self._settings.has_llm():
            archivist.log_trace("phase_start", "orchestrator", {"phase": "semanticist"})
            try:
                sem_summary = Semanticist(graph, self._settings, budget).analyse(repo_path)
                run.phases_completed.append("semanticist")
                run.llm_cost_usd += budget.summary.get("total_cost_usd", 0.0)
                archivist.log_trace("phase_complete", "orchestrator", {
                    "phase": "semanticist", **sem_summary
                })
            except Exception as exc:  # noqa: BLE001
                logger.error("semanticist_phase_failed", error=str(exc))
                run.errors.append(f"Semanticist: {exc}")
        else:
            logger.warning(
                "semanticist_skipped",
                reason="No LLM API key configured. Set ANTHROPIC_API_KEY in .env.",
            )

        # ── Phase 4: Archivist ────────────────────────────────────────────────
        archivist.log_trace("phase_start", "orchestrator", {"phase": "archivist"})
        try:
            # Day-One answers (if LLM available)
            day_one = None
            if self._settings.has_llm() and "semanticist" in run.phases_completed:
                semanticist = Semanticist(graph, self._settings, budget)
                day_one = semanticist.answer_day_one_questions(repo_path)

            archivist.produce_artifacts(run, day_one)
            run.phases_completed.append("archivist")
        except Exception as exc:  # noqa: BLE001
            logger.error("archivist_phase_failed", error=str(exc))
            run.errors.append(f"Archivist: {exc}")

        # ── Cleanup ───────────────────────────────────────────────────────────
        if self._temp_dir and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)

        run.finished_at = datetime.now(tz=timezone.utc)
        logger.info(
            "run_complete",
            run_id=run.run_id,
            phases=run.phases_completed,
            errors=len(run.errors),
            cost_usd=run.llm_cost_usd,
        )
        return run

    def build_navigator(
        self,
        repo_path: Path,
        output_dir: Path | None = None,
    ) -> Navigator:
        """
        Load a previously analysed codebase and return a Navigator for queries.
        """
        cartography_dir = output_dir or Path(".cartography")
        graph_path = cartography_dir / "module_graph.json"

        if graph_path.exists():
            graph = KnowledgeGraph.load(graph_path)
        else:
            logger.warning("no_cached_graph_running_surveyor", path=str(graph_path))
            graph = KnowledgeGraph()
            Surveyor(graph).analyse(repo_path)

        tools = NavigatorTools(graph, repo_path, self._settings)
        return Navigator(tools)

    # ── Target resolution ──────────────────────────────────────────────────────

    def _resolve_target(self, target: str) -> Path:
        """
        Resolve *target* to a local Path, cloning if it is a GitHub URL.
        Validates security constraints before returning.
        """
        if target.startswith("https://github.com"):
            return self._clone_github(target)

        repo_path = validate_local_path(target)
        check_repo_size(repo_path, self._settings.max_repo_size_mb)
        return repo_path

    def _clone_github(self, url: str) -> Path:
        """
        Clone a GitHub repository to a temporary directory.
        Uses GITHUB_TOKEN if configured (for private repos).
        """
        validated_url = validate_github_url(url)

        # Inject token into URL for private repos (token is NOT logged)
        clone_url = validated_url
        if self._settings.github_token:
            # Insert token before the host: https://token@github.com/...
            clone_url = validated_url.replace(
                "https://github.com",
                f"https://{self._settings.github_token}@github.com",
            )

        self._temp_dir = Path(tempfile.mkdtemp(prefix="cartographer_"))
        logger.info("cloning_repository", url=validated_url, dest=str(self._temp_dir))

        try:
            subprocess.run(  # noqa: S603
                ["git", "clone", "--depth=1", clone_url, str(self._temp_dir)],
                check=True,
                capture_output=True,
                timeout=300,
            )
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            # Never include clone_url in the error (may contain token)
            raise SecurityError(
                f"git clone failed for '{validated_url}'. "
                "Check URL and GITHUB_TOKEN."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            raise SecurityError("git clone timed out after 300 seconds.") from exc

        check_repo_size(self._temp_dir, self._settings.max_repo_size_mb)
        return self._temp_dir

    # ── Git helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _get_head_commit(repo_path: Path) -> str | None:
        try:
            result = subprocess.run(  # noqa: S603
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:  # noqa: BLE001
            pass
        return None

    def get_changed_files_since_last_run(
        self, repo_path: Path, last_commit: str
    ) -> list[str]:
        """
        Return a list of files changed between *last_commit* and HEAD.
        Used for incremental update mode.
        """
        try:
            result = subprocess.run(  # noqa: S603
                ["git", "-C", str(repo_path), "diff", "--name-only", last_commit, "HEAD"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return [f.strip() for f in result.stdout.splitlines() if f.strip()]
        except Exception as exc:  # noqa: BLE001
            logger.debug("incremental_diff_failed", error=str(exc))
        return []
