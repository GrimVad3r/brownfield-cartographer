# 🗺️ Brownfield Cartographer

> **Multi-agent Codebase Intelligence System for Rapid FDE Onboarding**

The Brownfield Cartographer ingests any GitHub repository or local path and produces a living, queryable knowledge graph of the system's architecture, data flows, and semantic structure — answering the five questions every Forward Deployed Engineer needs answered in the first 72 hours.

---

## Table of Contents

- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Output Artifacts](#output-artifacts)
- [Branch Structure](#branch-structure)
- [Development](#development)
- [Security](#security)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    CLI (cli.py)                          │
│              analyze │ query │ version                   │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  Orchestrator                            │
│   Surveyor → Hydrologist → Semanticist → Archivist       │
└──┬───────────┬──────────────┬────────────┬──────────────┘
   │           │              │            │
   ▼           ▼              ▼            ▼
Surveyor  Hydrologist    Semanticist   Archivist
tree-sitter  sqlglot +      LLM          CODEBASE.md
AST parsing  pandas/spark   purpose      onboarding_brief
PageRank     lineage DAG    extraction   lineage_graph.json
git velocity blast_radius   doc drift    cartography_trace
dead code    sources/sinks  clustering   run_manifest
   │           │              │            │
   └───────────┴──────────────┴────────────┘
                       │
            ┌──────────▼──────────┐
            │   KnowledgeGraph    │
            │  NetworkX DiGraph   │
            │  + Pydantic models  │
            └──────────┬──────────┘
                       │
            ┌──────────▼──────────┐
            │     Navigator       │
            │  LangGraph agent    │
            │  4 query tools      │
            └─────────────────────┘
```

### The Four Agents

| Agent | Role | Key Technologies |
|-------|------|-----------------|
| **Surveyor** | Static structure analysis | tree-sitter, NetworkX, git |
| **Hydrologist** | Data lineage extraction | sqlglot, pandas regex, DAG config |
| **Semanticist** | LLM-powered purpose analysis | Anthropic/OpenAI, sentence-transformers |
| **Archivist** | Artifact generation | Pydantic, Markdown, JSON |

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/your-org/brownfield-cartographer
cd brownfield-cartographer
pip install -e ".[dev]"

# 2. Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# 3. Analyse a repository
cartographer analyze https://github.com/dbt-labs/jaffle_shop

# 4. Query the knowledge graph
cartographer query .
```

---

## Installation

### Prerequisites

- Python 3.11+
- git (for velocity analysis and repo cloning)
- [uv](https://github.com/astral-sh/uv) (recommended for locked installs)

### With uv (recommended)

```bash
uv sync
uv run cartographer --help
```

### With pip

```bash
pip install -e ".[dev]"
```

---

## Usage

### `analyze` — Full Pipeline

```bash
# Analyse a GitHub repository
cartographer analyze https://github.com/dbt-labs/jaffle_shop

# Analyse a local path
cartographer analyze /path/to/your/repo

# Skip LLM phase (no API key required)
cartographer analyze /path/to/repo --skip-llm

# Custom output directory
cartographer analyze /path/to/repo --output ./my_analysis

# Incremental mode (re-analyse only changed files)
cartographer analyze /path/to/repo --incremental
```

### `query` — Interactive Navigator

```bash
# Start interactive REPL
cartographer query /path/to/repo

# Single non-interactive query
cartographer query . --tool blast_radius --args '{"module_path": "src/ingestion.py"}'
cartographer query . --tool trace_lineage --args '{"dataset": "orders", "direction": "upstream"}'
cartographer query . --tool find_implementation --args '{"concept": "revenue calculation"}'
cartographer query . --tool explain_module --args '{"path": "src/transform.py"}'
```

### Navigator Tool Reference

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `find_implementation` | `concept: str` | Semantic search over Purpose Statements |
| `trace_lineage` | `dataset: str, direction: str` | Upstream/downstream graph traversal |
| `blast_radius` | `module_path: str` | Downstream dependency enumeration |
| `explain_module` | `path: str` | LLM-generated module explanation |

---

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Recommended | Enables Semanticist LLM analysis |
| `OPENAI_API_KEY` | Alternative | OpenAI fallback for Semanticist |
| `LMSTUDIO_BASE_URL` | Optional | LM Studio OpenAI-compatible base URL (e.g. http://localhost:1234/v1) |
| `LMSTUDIO_API_KEY` | Optional | LM Studio API key (can be `lm-studio`) |
| `BULK_LLM_MODEL` | Optional | Model ID for bulk purpose extraction |
| `SYNTHESIS_LLM_MODEL` | Optional | Model ID for Day-One synthesis |
| `GITHUB_TOKEN` | For private repos | GitHub PAT for private repo cloning |
| `MAX_REPO_SIZE_MB` | Optional | Max repo size (default: 500 MB) |
| `LOG_LEVEL` | Optional | DEBUG/INFO/WARNING (default: INFO) |

> ⚠️ **Never commit your `.env` file.** It is git-ignored by default.

---

### LM Studio (Local LLM)

If LM Studio is running with an OpenAI-compatible server:

```bash
export LMSTUDIO_BASE_URL=http://localhost:1234/v1
export LMSTUDIO_API_KEY=lm-studio
export BULK_LLM_MODEL=mistralai/ministral-3-14b-reasoning
export SYNTHESIS_LLM_MODEL=mistralai/ministral-3-14b-reasoning
```

Then run `cartographer analyze ...` as usual.

---


---

## Output Artifacts

All artifacts are written to `.cartography/` (git-ignored):

| File | Description |
|------|-------------|
| `CODEBASE.md` | Living context file — inject into AI coding agents |
| `onboarding_brief.md` | Day-One Brief: Five FDE questions with evidence |
| `module_graph.json` | Serialised module import graph (NetworkX) |
| `lineage_graph.json` | Data lineage DAG (NetworkX) |
| `semantic_index/` | Vector index of module/function purpose statements |
| `cartography_trace.jsonl` | Audit log of every analysis action |
| `run_manifest.json` | Run metadata, stats, errors |

> ⚠️ **Artifacts contain extracted business logic and schema data.** Never commit them to a public repository.

---

## Branch Structure

| Branch | Contents |
|--------|----------|
| `main` | Stable releases |
| `feature/01-project-setup` | `pyproject.toml`, `.gitignore`, `.env.example`, `logging_config.py`, `security.py`, `token_budget.py` |
| `feature/02-data-models` | `src/models/` — Pydantic node, edge, graph schemas |
| `feature/03-surveyor-agent` | `src/analyzers/tree_sitter_analyzer.py`, `src/agents/surveyor.py` |
| `feature/04-hydrologist-agent` | `src/analyzers/sql_lineage.py`, `src/analyzers/dag_config_parser.py`, `src/agents/hydrologist.py` |
| `feature/05-semanticist-agent` | `src/agents/semanticist.py` |
| `feature/06-archivist-agent` | `src/agents/archivist.py` |
| `feature/07-navigator-agent` | `src/agents/navigator.py` |
| `feature/08-knowledge-graph` | `src/graph/knowledge_graph.py` |
| `feature/09-cli-orchestrator` | `src/cli.py`, `src/orchestrator.py` |
| `feature/10-tests` | `tests/` |

---

## Development

```bash
# Run tests with coverage
pytest

# Lint
ruff check src/ tests/

# Type check
mypy src/

# Format
ruff format src/ tests/
```

### Running Against the Required Target Codebases

```bash
# Primary: dbt jaffle_shop
cartographer analyze https://github.com/dbt-labs/jaffle_shop --output ./jaffle_shop_analysis

# Primary: Apache Airflow examples
cartographer analyze https://github.com/apache/airflow --output ./airflow_analysis --skip-llm

# Self-audit: run on this repo
cartographer analyze . --output ./self_analysis
```

---

## Security

The Cartographer implements defence-in-depth security at every input boundary:

- **Path traversal prevention** — all local paths are canonicalised and validated
- **GitHub URL allow-listing** — only `https://github.com/<owner>/<repo>` URLs accepted
- **Resource limits** — max repo size (500 MB), max file size (500 KB), max depth (20)
- **Blocked extensions** — executables, archives, media files are never read into memory
- **No eval/exec** — analysed code is never executed; tree-sitter is a pure parser
- **Secret isolation** — API keys loaded from environment only; never logged or committed
- **Shell injection prevention** — all `subprocess` calls use list-form arguments (no `shell=True`)
- **Artifact isolation** — `.cartography/` is git-ignored to prevent accidental secret commits

---

## The Five FDE Day-One Questions

The system automatically answers these questions for every analysed codebase:

1. **What is the primary data ingestion path?**
2. **What are the 3–5 most critical output datasets/endpoints?**
3. **What is the blast radius if the most critical module fails?**
4. **Where is the business logic concentrated vs. distributed?**
5. **What has changed most frequently in the last 90 days?**

Answers include `file:line` evidence citations generated by the Semanticist agent.
