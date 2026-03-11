# Branch Map — Brownfield Cartographer

This document maps every file in the project to its Git feature branch.
Use this as the merge checklist for PR reviews.

## Branch Strategy

```
main (stable releases)
│
├── feature/01-project-setup
├── feature/02-data-models
├── feature/03-surveyor-agent
├── feature/04-hydrologist-agent
├── feature/05-semanticist-agent
├── feature/06-archivist-agent
├── feature/07-navigator-agent
├── feature/08-knowledge-graph
├── feature/09-cli-orchestrator
└── feature/10-tests
```

---

## feature/01-project-setup

**Purpose:** Project scaffolding, dependencies, cross-cutting utilities.

| File | Description |
|------|-------------|
| `pyproject.toml` | All Python dependencies, locked via uv |
| `.gitignore` | Excludes .env, .cartography/, secrets |
| `.env.example` | Environment variable template |
| `src/utils/__init__.py` | Package init |
| `src/utils/logging_config.py` | structlog setup, `get_logger()` |
| `src/utils/security.py` | Path validation, GitHub URL allow-list, size limits |
| `src/utils/token_budget.py` | LLM cost tracking, tiered model selection |
| `README.md` | Project documentation |
| `docs/BRANCH_MAP.md` | This file |

**PR checklist:**
- [ ] `.env` is NOT in the commit
- [ ] `pyproject.toml` has pinned versions (uv lock)
- [ ] All utility functions have docstrings

---

## feature/02-data-models

**Purpose:** Pydantic v2 schemas for all knowledge graph entities.

| File | Description |
|------|-------------|
| `src/models/__init__.py` | Package init |
| `src/models/nodes.py` | ModuleNode, DatasetNode, FunctionNode, TransformationNode |
| `src/models/edges.py` | ImportsEdge, ProducesEdge, ConsumesEdge, CallsEdge, ConfiguresEdge |
| `src/models/graph.py` | Settings (BaseSettings), CartographyRun, DayOneAnswers |

**PR checklist:**
- [ ] All models use `model_config = {"extra": "forbid"}` (no unexpected fields)
- [ ] No secret fields in models (API keys only in Settings, never in node models)
- [ ] Pydantic validators present where needed

---

## feature/03-surveyor-agent

**Purpose:** Static structure analysis — AST parsing, PageRank, git velocity, dead code.

| File | Description |
|------|-------------|
| `src/analyzers/__init__.py` | Package init |
| `src/analyzers/tree_sitter_analyzer.py` | LanguageRouter, multi-language AST parsing |
| `src/agents/__init__.py` | Package init |
| `src/agents/surveyor.py` | Surveyor agent, import graph, git velocity |

**PR checklist:**
- [ ] Graceful degradation: `is_safe_file()` called before reading any file
- [ ] No `eval()` or `exec()` or dynamic imports of analysed code
- [ ] tree-sitter failure falls back to regex (never raises)
- [ ] Private helper functions (`_private_xxx`) excluded from `exported_symbols`

---

## feature/04-hydrologist-agent

**Purpose:** Data lineage extraction from Python, SQL, YAML.

| File | Description |
|------|-------------|
| `src/analyzers/sql_lineage.py` | sqlglot-based SQL dependency extraction |
| `src/analyzers/dag_config_parser.py` | Airflow/dbt/Prefect YAML config parsing |
| `src/agents/hydrologist.py` | Hydrologist agent, DataLineageGraph |

**PR checklist:**
- [ ] Dynamic Python references (f-strings) are logged, not crashed on
- [ ] sqlglot failures try all dialects before giving up
- [ ] No execution of SQL queries — parse only
- [ ] YAML loaded with `yaml.safe_load()` (never `yaml.load()`)

---

## feature/05-semanticist-agent

**Purpose:** LLM-powered semantic analysis, doc drift, domain clustering.

| File | Description |
|------|-------------|
| `src/agents/semanticist.py` | Semanticist agent, purpose statements, clustering |

**PR checklist:**
- [ ] API keys never logged (check all logger calls)
- [ ] `ContextWindowBudget` enforced before every LLM call
- [ ] Hard cap raises `BudgetExceededError` cleanly
- [ ] Prompt does NOT include raw API keys or auth tokens
- [ ] `safe_parse_json` strips markdown fences before `json.loads`

---

## feature/06-archivist-agent

**Purpose:** Artifact generation — CODEBASE.md, onboarding brief, trace log.

| File | Description |
|------|-------------|
| `src/agents/archivist.py` | Archivist agent, all artifact writers |

**PR checklist:**
- [ ] Output directory created with `parents=True, exist_ok=True`
- [ ] `cartography_trace.jsonl` opened in append mode (not overwrite)
- [ ] Trace entries never include API keys or raw source code

---

## feature/07-navigator-agent

**Purpose:** Interactive query interface with four tools.

| File | Description |
|------|-------------|
| `src/agents/navigator.py` | Navigator, NavigatorTools, interactive REPL |

**PR checklist:**
- [ ] All four tools return `evidence_source` in their response
- [ ] Tool dispatch uses explicit allow-list (not `getattr` on arbitrary input)
- [ ] REPL handles `KeyboardInterrupt` gracefully

---

## feature/08-knowledge-graph

**Purpose:** NetworkX graph wrapper — core data store for all agents.

| File | Description |
|------|-------------|
| `src/graph/__init__.py` | Package init |
| `src/graph/knowledge_graph.py` | KnowledgeGraph — nodes, edges, analytics, serialisation |

**PR checklist:**
- [ ] JSON serialisation uses `default=str` for datetime safety
- [ ] `pagerank()` handles disconnected graphs (try/except)
- [ ] `blast_radius()` handles unknown node IDs gracefully

---

## feature/09-cli-orchestrator

**Purpose:** CLI entry point and pipeline orchestration.

| File | Description |
|------|-------------|
| `src/__init__.py` | Package init |
| `src/cli.py` | Typer CLI — analyze, query, version commands |
| `src/orchestrator.py` | Pipeline orchestration, GitHub cloning |

**PR checklist:**
- [ ] GitHub token NEVER appears in logs (use `validated_url`, not `clone_url`)
- [ ] `subprocess.run` always uses list-form (never `shell=True`)
- [ ] Temp dirs cleaned up on error paths (try/finally)
- [ ] `typer.Exit(code=1)` raised on failure (not `sys.exit`)

---

## feature/10-tests

**Purpose:** Test suite with ≥70% coverage target.

| File | Description |
|------|-------------|
| `tests/__init__.py` | Package init |
| `tests/test_surveyor.py` | Surveyor and tree-sitter tests |
| `tests/test_sql_lineage.py` | SQL lineage extraction tests |
| `tests/test_knowledge_graph.py` | KnowledgeGraph, security, token budget tests |

**PR checklist:**
- [ ] No real API calls in tests (mock LLM clients)
- [ ] All tests use `tmp_path` fixture (never write to project dir)
- [ ] Coverage ≥ 70% (`pytest --cov-fail-under=70`)
- [ ] No hardcoded secrets in test files
