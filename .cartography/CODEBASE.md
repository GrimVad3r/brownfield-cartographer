# CODEBASE.md — Cartographer Living Context
> Generated: 2026-03-13T15:38:35.331396+00:00 | Repo: C:\Users\henokt\AppData\Local\Temp\cartographer_wl2qpp1h
> Git commit: 19f03ea69f72f58f84a7a625b8bf759250b85127

---

## Architecture Overview

This codebase contains **1107 analysed source files** across 1 inferred domains. The static analysis identified **0 circular dependency group(s)**, **10 dead code candidate(s)**, and **0 documentation drift instance(s)**.

---

## Critical Path (Top 5 Modules by PageRank)

These modules are imported most frequently — changes here have the highest blast radius.

- `dg_deployments\local\dagster.yaml` (score: 0.0078)
- `dataset:source` (score: 0.0074)
- `dataset:cleaned` (score: 0.0067)
- `dataset:renamed` (score: 0.0042)
- `dataset:most_recent_source` (score: 0.0039)

---

## Domain Architecture Map

---

## Data Sources & Sinks

### Sources (in-degree = 0 in lineage graph)

- `.pre-commit-config.yaml`
- `build.yaml`
- `docker-compose.yaml`
- `bin\dbt-create-staging-models.py`
- `bin\dbt-create-staging-models.py:extract_domain_from_prefix:20`
- `bin\dbt-create-staging-models.py:run_dbt_command:36`
- `bin\dbt-create-staging-models.py:generate_sources:103`
- `bin\dbt-create-staging-models.py:merge_sources_content:215`
- `bin\dbt-create-staging-models.py:adjust_source_schema_pattern:366`
- `bin\dbt-create-staging-models.py:generate_staging_models:434`

### Sinks (out-degree = 0 in lineage graph)

- `.pre-commit-config.yaml`
- `build.yaml`
- `docker-compose.yaml`
- `bin\dbt-create-staging-models.py`
- `bin\dbt-create-staging-models.py:extract_domain_from_prefix:20`
- `bin\dbt-create-staging-models.py:run_dbt_command:36`
- `bin\dbt-create-staging-models.py:generate_sources:103`
- `bin\dbt-create-staging-models.py:merge_sources_content:215`
- `bin\dbt-create-staging-models.py:adjust_source_schema_pattern:366`
- `bin\dbt-create-staging-models.py:generate_staging_models:434`

---

## Known Debt

### Circular Dependencies

_None detected._

### Documentation Drift

_None detected._

---

## High-Velocity Files (Last 90 Days)

Files with the most commits are the most active pain points.

- `.pre-commit-config.yaml` — 1 commits (30d: 1)
- `build.yaml` — 1 commits (30d: 1)
- `docker-compose.yaml` — 1 commits (30d: 1)
- `bin\dbt-create-staging-models.py` — 0 commits (30d: 0)
- `bin\dbt-local-dev.py` — 0 commits (30d: 0)
- `bin\uv-operations.py` — 0 commits (30d: 0)
- `bin\utils\chunk_tracking_logs_by_day.py` — 0 commits (30d: 0)
- `dg_deployments\reconcile_edxorg_partitions.py` — 0 commits (30d: 0)
- `dg_deployments\local\dagster.yaml` — 0 commits (30d: 0)
- `dg_deployments\local\workspace.yaml` — 0 commits (30d: 0)

---

## Module Purpose Index
