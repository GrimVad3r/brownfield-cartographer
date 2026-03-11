"""
src/agents/semanticist.py
──────────────────────────
Agent 3: The Semanticist — LLM-Powered Purpose Analyst.

Responsibilities
----------------
* Generate Purpose Statements for every ModuleNode from code (not docstrings).
* Detect Documentation Drift (docstring contradicts implementation).
* Cluster modules into inferred domain groups via embedding + k-means.
* Answer the Five FDE Day-One Questions with evidence citations.
* Enforce ContextWindowBudget: cheap model for bulk, expensive for synthesis.

Branch: feature/05-semanticist-agent
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from src.graph.knowledge_graph import KnowledgeGraph
from src.models.graph import DayOneAnswers, Settings
from src.models.nodes import DomainCluster, FunctionNode, ModuleNode
from src.utils.logging_config import get_logger
from src.utils.token_budget import BudgetExceededError, ContextWindowBudget

logger = get_logger(__name__)

_DOCSTRING_RE = re.compile(r'^\s*["\'{3}]{3}(.*?)["\'{3}]{3}', re.DOTALL)


class Semanticist:
    """
    LLM-powered semantic analysis layer.
    Gracefully degrades if no API key is configured.
    """

    def __init__(
        self,
        graph: KnowledgeGraph,
        settings: Settings,
        budget: ContextWindowBudget | None = None,
    ) -> None:
        self._graph    = graph
        self._settings = settings
        self._budget   = budget or ContextWindowBudget(
            bulk_model=settings.bulk_llm_model,
            synthesis_model=settings.synthesis_llm_model,
        )
        self._client   = self._init_client()

    # ── LLM client initialisation ─────────────────────────────────────────────

    def _init_client(self) -> Any | None:
        """
        Initialise the Anthropic client if a key is available.
        Returns None and logs a warning if no key is configured.
        """
        if self._settings.anthropic_api_key:
            try:
                import anthropic
                return anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
            except ImportError:
                logger.warning("anthropic_sdk_not_installed")
        elif self._settings.openai_api_key:
            try:
                import openai
                return openai.OpenAI(api_key=self._settings.openai_api_key)
            except ImportError:
                logger.warning("openai_sdk_not_installed")

        logger.warning(
            "no_llm_key_configured",
            detail="Semanticist will produce placeholder purpose statements. "
                   "Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env to enable LLM analysis.",
        )
        return None

    # ── Main entry point ──────────────────────────────────────────────────────

    def analyse(self, repo_path: Path, only_modules: set[str] | None = None) -> dict[str, Any]:
        logger.info("semanticist_started")

        modules = self._graph.all_nodes_of_type(ModuleNode)
        if only_modules:
            modules = [m for m in modules if m.path in only_modules]

        purpose_generated = 0
        drift_flagged     = 0

        for module in modules:
            if module.parse_error:
                continue  # Skip unparseable files
            try:
                self._generate_purpose_statement(module, repo_path)
                purpose_generated += 1
                if module.has_docstring_drift:
                    drift_flagged += 1
            except BudgetExceededError as exc:
                logger.error("budget_exceeded_halting_semanticist", error=str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("purpose_statement_failed", module=module.path, error=str(exc))


        # Function-level purpose statements (Python only for now)
        functions = self._graph.all_nodes_of_type(FunctionNode)
        if only_modules:
            functions = [f for f in functions if f.parent_module in only_modules]

        function_purpose_generated = 0
        for fn in functions:
            try:
                self._generate_function_purpose_statement(fn, repo_path)
                function_purpose_generated += 1
            except BudgetExceededError as exc:
                logger.error("budget_exceeded_halting_semanticist", error=str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("function_purpose_failed", function=fn.qualified_name, error=str(exc))

        # Domain clustering
        self._cluster_into_domains()

        summary = {
            "purpose_statements_generated": purpose_generated,
            "documentation_drift_flags":    drift_flagged,
            "function_purposes_generated":   function_purpose_generated,
        }

        self._budget.log_summary()
        logger.info("semanticist_completed", **summary)
        return summary

    # ── Purpose statement ─────────────────────────────────────────────────────

    def _generate_purpose_statement(self, module: ModuleNode, repo_path: Path) -> None:
        """Generate and attach a Purpose Statement to *module*."""
        source_path = repo_path / module.path
        try:
            source = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        # Extract existing docstring for drift comparison
        docstring_match = _DOCSTRING_RE.search(source)
        existing_docstring = docstring_match.group(1).strip() if docstring_match else ""

        if self._client is None:
            # Fallback: derive a minimal purpose statement from the module name
            module.purpose_statement = (
                f"Module '{module.path}' ({module.language.value}). "
                f"Exports: {', '.join(module.exported_symbols[:5]) or 'none'}. "
                f"[LLM analysis unavailable — configure an API key for full semantic analysis]"
            )
            return

        prompt = self._build_purpose_prompt(source, module.path, existing_docstring)
        response = self._call_llm(prompt, tier="bulk")

        if response:
            # Parse JSON response
            parsed = self._safe_parse_json(response)
            module.purpose_statement = parsed.get("purpose_statement", response[:500])

            # Check for doc drift
            drift = parsed.get("docstring_drift")
            if drift:
                module.has_docstring_drift   = True
                module.docstring_drift_details = str(drift)
                logger.info(
                    "documentation_drift_detected",
                    module=module.path,
                    detail=str(drift)[:120],
                )

    def _generate_function_purpose_statement(self, fn: FunctionNode, repo_path: Path) -> None:
        """Generate and attach a Purpose Statement to a FunctionNode."""
        source_path = repo_path / fn.parent_module
        try:
            source = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        snippet = self._extract_function_source(source, fn.start_line, fn.end_line)

        if self._client is None:
            fn.purpose_statement = (
                f"Function '{fn.qualified_name}' in {fn.parent_module}. "
                "[LLM analysis unavailable — configure an API key for full semantic analysis]"
            )
            return

        prompt = self._build_function_prompt(snippet, fn.qualified_name, fn.parent_module)
        response = self._call_llm(prompt, tier="bulk")
        if response:
            parsed = self._safe_parse_json(response)
            fn.purpose_statement = parsed.get("purpose_statement", response[:500])

    @staticmethod
    def _extract_function_source(source: str, start_line: int, end_line: int) -> str:
        lines = source.splitlines()
        if start_line <= 0:
            return "\n".join(lines[:50])
        if end_line <= start_line:
            end_line = min(start_line + 20, len(lines))
        end_line = min(end_line, len(lines))
        return "\n".join(lines[start_line - 1:end_line])

    @staticmethod
    def _build_function_prompt(snippet: str, qualified_name: str, module_path: str) -> str:
        truncated = snippet[:3000] + ("\n... [truncated]" if len(snippet) > 3000 else "")
        return f"""Analyse this function and return a JSON object with one field:

1. \"purpose_statement\": A 1-2 sentence description of what this function DOES (business effect),
   not how it does it, grounded entirely in the code below.

Function: {qualified_name}
Module: {module_path}

Source code:
```
{truncated}
```

Return ONLY valid JSON. No preamble, no markdown fences."""


    @staticmethod
    def _build_purpose_prompt(source: str, path: str, docstring: str) -> str:
        # Truncate large files to keep token costs manageable
        truncated = source[:6000] + ("\n... [truncated]" if len(source) > 6000 else "")
        return f"""Analyse this source file and return a JSON object with two fields:

1. "purpose_statement": A 2-3 sentence description of what this module DOES (business function),
   NOT how it does it, grounded entirely in the actual code below — NOT the docstring.
   Focus on: What data does it process? What decisions does it make? What does it produce?

2. "docstring_drift": If the existing docstring contradicts or significantly misrepresents
   the actual code behaviour, describe the contradiction in 1-2 sentences. Otherwise null.

File: {path}
Existing docstring: {docstring[:300] or '(none)'}

Source code:
```
{truncated}
```

Return ONLY valid JSON. No preamble, no markdown fences."""

    # ── Domain clustering ─────────────────────────────────────────────────────

    def _cluster_into_domains(self) -> None:
        """
        Assign DomainCluster labels to ModuleNodes based on their path and
        purpose statement keywords.

        Uses heuristic keyword matching as the primary method (no external
        embedding dependency required). Upgrades to k-means if sentence-
        transformers is available and there are ≥10 modules.
        """
        modules = [m for m in self._graph.all_nodes_of_type(ModuleNode) if not m.parse_error]

        if len(modules) >= 10:
            try:
                self._cluster_with_embeddings(modules)
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug("embedding_cluster_failed_falling_back", error=str(exc))

        # Keyword-based heuristic fallback
        for m in modules:
            m.domain_cluster = self._infer_domain_heuristic(m)

    @staticmethod
    def _infer_domain_heuristic(module: ModuleNode) -> DomainCluster:
        text = (module.path + " " + (module.purpose_statement or "")).lower()
        if any(k in text for k in ["ingest", "fetch", "extract", "source", "consumer", "reader", "loader"]):
            return DomainCluster.INGESTION
        if any(k in text for k in ["transform", "clean", "enrich", "process", "pipeline", "etl"]):
            return DomainCluster.TRANSFORMATION
        if any(k in text for k in ["serve", "api", "endpoint", "export", "output", "report"]):
            return DomainCluster.SERVING
        if any(k in text for k in ["monitor", "alert", "health", "metric", "observ"]):
            return DomainCluster.MONITORING
        if any(k in text for k in ["config", "setting", "env", "secret", "yaml"]):
            return DomainCluster.CONFIGURATION
        if any(k in text for k in ["test", "spec", "mock", "fixture", "conftest"]):
            return DomainCluster.TESTING
        return DomainCluster.UNKNOWN

    def _cluster_with_embeddings(self, modules: list[ModuleNode]) -> None:
        """k-means clustering on Purpose Statement embeddings."""
        from sentence_transformers import SentenceTransformer
        from sklearn.cluster import KMeans

        texts = [
            (m.purpose_statement or m.path) for m in modules
        ]
        model = SentenceTransformer("all-MiniLM-L6-v2")
        embeddings = model.encode(texts, show_progress_bar=False)

        k = min(7, max(2, len(modules) // 5))
        km = KMeans(n_clusters=k, random_state=42, n_init="auto")
        labels = km.fit_predict(embeddings)

        # Map cluster integers to DomainCluster using majority-vote heuristic
        cluster_domains: dict[int, DomainCluster] = {}
        for cluster_id in range(k):
            cluster_modules = [modules[i] for i, l in enumerate(labels) if l == cluster_id]
            votes = [self._infer_domain_heuristic(m) for m in cluster_modules]
            # Pick most common vote
            cluster_domains[cluster_id] = max(set(votes), key=votes.count)

        for module, label in zip(modules, labels):
            module.domain_cluster = cluster_domains[int(label)]

        logger.info("domain_clustering_complete", k=k, method="embeddings")

    # ── Day-One question synthesis ────────────────────────────────────────────

    def answer_day_one_questions(self, repo_path: Path) -> DayOneAnswers | None:
        """
        Synthesise the Five FDE Day-One Answers from Surveyor + Hydrologist
        output, using the synthesis LLM model.
        """
        if self._client is None:
            logger.warning("day_one_answers_skipped_no_llm")
            return None

        modules  = self._graph.all_nodes_of_type(ModuleNode)
        pagerank = self._graph.pagerank()
        sources  = self._graph.find_sources()
        sinks    = self._graph.find_sinks()

        # Build a compact architectural summary to feed to the LLM
        top_modules = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:10]
        high_vel    = sorted(modules, key=lambda m: m.change_velocity_90d, reverse=True)[:10]

        context = {
            "top_modules_by_pagerank": [k for k, _ in top_modules],
            "data_sources":            sources[:10],
            "data_sinks":              sinks[:10],
            "high_velocity_files":     [m.path for m in high_vel],
            "module_purposes":         {
                m.path: m.purpose_statement
                for m in modules[:40]
                if m.purpose_statement
            },
        }

        prompt = f"""You are a Forward Deployed Engineer analysing a codebase for the first time.
Based on the architectural intelligence below, answer the Five FDE Day-One Questions.
Return ONLY a JSON object with these exact keys:
- primary_ingestion_path: string (1-2 sentences + file path citation)
- critical_output_datasets: list of strings (3-5 dataset/table names)
- blast_radius_critical_module: string (most critical module path + why)
- business_logic_concentration: string (where business logic lives, with evidence)
- high_velocity_files: list of strings (top 5 most frequently changed files)
- evidence_citations: list of objects with keys: claim, file_path, line_range

Architecture Intelligence:
{json.dumps(context, indent=2)[:8000]}

Return ONLY valid JSON."""

        response = self._call_llm(prompt, tier="synthesis")
        if not response:
            return None

        parsed = self._safe_parse_json(response)
        try:
            return DayOneAnswers(**parsed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("day_one_parse_failed", error=str(exc))
            return None

    # ── LLM call with retry ───────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _call_llm(self, prompt: str, tier: str = "bulk") -> str | None:
        if self._client is None:
            return None

        model = self._budget.select_model(tier)  # type: ignore[arg-type]
        input_tokens = self._budget.count_tokens(prompt, model)

        try:
            import anthropic
            if isinstance(self._client, anthropic.Anthropic):
                response = self._client.messages.create(
                    model=model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": prompt}],
                )
                output_text = response.content[0].text
                self._budget.record_usage(
                    model=model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
                return output_text
        except ImportError:
            pass

        # OpenAI fallback
        try:
            import openai
            if isinstance(self._client, openai.OpenAI):
                response = self._client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1024,
                )
                output_text = response.choices[0].message.content or ""
                usage = response.usage
                self._budget.record_usage(
                    model=model,
                    input_tokens=usage.prompt_tokens if usage else input_tokens,
                    output_tokens=usage.completion_tokens if usage else 300,
                )
                return output_text
        except ImportError:
            pass

        return None

    @staticmethod
    def _safe_parse_json(text: str) -> dict[str, Any]:
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"purpose_statement": text[:500], "docstring_drift": None}
