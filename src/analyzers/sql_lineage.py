"""
src/analyzers/sql_lineage.py
──────────────────────────────
SQL data lineage extraction using sqlglot.

Supports PostgreSQL, BigQuery, Snowflake, DuckDB, and generic SQL.
Extracts table-level dependencies from SELECT/FROM/JOIN/WITH (CTE) chains.
Also handles dbt model files (models/*.sql with ref() macro calls).

Branch: feature/04-hydrologist-agent
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import sqlglot
import sqlglot.expressions as exp

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# Supported dialects for auto-detection fallback order
_DIALECTS = ["duckdb", "bigquery", "snowflake", "postgres", "spark", ""]

# dbt ref() pattern: {{ ref('model_name') }}
_DBT_REF_RE  = re.compile(r"\{\{\s*ref\s*\(\s*['\"](\w+)['\"]\s*\)\s*\}\}")
# dbt source() pattern: {{ source('source_name', 'table_name') }}
_DBT_SRC_RE  = re.compile(r"\{\{\s*source\s*\(\s*['\"](\w+)['\"],\s*['\"](\w+)['\"]\s*\)\s*\}\}")


class TableDependency(NamedTuple):
    """A resolved upstream/downstream table reference from a SQL file."""
    source_table: str
    target_table: str   # the model / CTE that consumes source_table
    source_file:  str
    line_number:  int
    dialect:      str


class SQLLineageAnalyzer:
    """
    Parse SQL files and dbt models to extract table-level lineage.

    Usage
    -----
    analyzer = SQLLineageAnalyzer()
    deps = analyzer.analyse_file(Path("models/orders.sql"), "orders", "duckdb")
    """

    def analyse_file(
        self,
        path: Path,
        model_name: str | None = None,
        dialect: str = "",
    ) -> list[TableDependency]:
        """
        Parse *path* and return a list of TableDependency objects.

        Parameters
        ----------
        path:
            Path to the SQL or dbt model file.
        model_name:
            Name to use as the target/consumer of the extracted tables.
            Defaults to the file stem.
        dialect:
            sqlglot dialect. If empty, tries a list of common dialects.
        """
        target = model_name or path.stem
        deps: list[TableDependency] = []

        try:
            raw_sql = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("sql_file_read_error", path=str(path), error=str(exc))
            return deps

        # ── dbt macro substitution ──────────────────────────────────────────
        dbt_refs    = _DBT_REF_RE.findall(raw_sql)
        dbt_sources = [f"{s}.{t}" for s, t in _DBT_SRC_RE.findall(raw_sql)]

        # Replace dbt macros with plain table names so sqlglot can parse them
        clean_sql = _DBT_REF_RE.sub(lambda m: m.group(1), raw_sql)
        clean_sql = _DBT_SRC_RE.sub(lambda m: f"{m.group(1)}__{m.group(2)}", clean_sql)

        # Add dbt-resolved dependencies directly
        for ref_name in dbt_refs:
            deps.append(TableDependency(
                source_table=ref_name,
                target_table=target,
                source_file=str(path),
                line_number=0,
                dialect="dbt",
            ))
        for src in dbt_sources:
            deps.append(TableDependency(
                source_table=src,
                target_table=target,
                source_file=str(path),
                line_number=0,
                dialect="dbt_source",
            ))

        # ── sqlglot AST analysis ────────────────────────────────────────────
        parsed_tables = self._extract_with_sqlglot(clean_sql, dialect, str(path))
        for tbl, lineno in parsed_tables:
            # Don't double-count dbt refs already captured
            if tbl not in dbt_refs:
                deps.append(TableDependency(
                    source_table=tbl,
                    target_table=target,
                    source_file=str(path),
                    line_number=lineno,
                    dialect=dialect or "generic",
                ))

        logger.debug(
            "sql_lineage_extracted",
            path=str(path),
            target=target,
            upstream_count=len(deps),
        )
        return deps

    def analyse_directory(
        self,
        directory: Path,
        dialect: str = "",
        recursive: bool = True,
    ) -> list[TableDependency]:
        """Walk *directory* and analyse all .sql files."""
        all_deps: list[TableDependency] = []
        pattern = "**/*.sql" if recursive else "*.sql"
        for sql_file in sorted(directory.glob(pattern)):
            all_deps.extend(self.analyse_file(sql_file, dialect=dialect))
        return all_deps

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _extract_with_sqlglot(
        self,
        sql: str,
        dialect: str,
        source_file: str,
    ) -> list[tuple[str, int]]:
        """
        Use sqlglot to parse *sql* and return (table_name, line_number) pairs.
        Tries multiple dialects if the first attempt fails.
        """
        dialects_to_try = [dialect] if dialect else _DIALECTS

        for d in dialects_to_try:
            try:
                statements = sqlglot.parse(sql, dialect=d or None, error_level=sqlglot.ErrorLevel.WARN)
                results: list[tuple[str, int]] = []

                for stmt in statements:
                    if stmt is None:
                        continue
                    for table_expr in stmt.find_all(exp.Table):
                        # Skip CTEs — they are internal aliases, not real tables
                        if self._is_cte_alias(table_expr, stmt):
                            continue
                        tbl_name = self._qualified_name(table_expr)
                        if tbl_name:
                            lineno = getattr(table_expr, "line", 0) or 0
                            results.append((tbl_name, lineno))

                return results

            except Exception as exc:  # noqa: BLE001
                logger.debug("sqlglot_parse_attempt_failed", dialect=d, error=str(exc))
                continue

        logger.warning("sqlglot_all_dialects_failed", source_file=source_file)
        return []

    @staticmethod
    def _qualified_name(table: exp.Table) -> str | None:
        """Build a fully qualified name from a sqlglot Table expression."""
        parts = [
            p
            for p in [
                table.args.get("db"),
                table.args.get("this"),
            ]
            if p is not None
        ]
        if not parts:
            return None
        return ".".join(str(p) for p in parts)

    @staticmethod
    def _is_cte_alias(table: exp.Table, stmt: exp.Expression) -> bool:
        """Return True if *table* refers to a CTE alias defined in *stmt*."""
        cte_names: set[str] = set()
        for cte in stmt.find_all(exp.CTE):
            alias = cte.args.get("alias")
            if alias:
                cte_names.add(str(alias).lower())
        tbl_name = str(table.args.get("this", "")).lower()
        return tbl_name in cte_names
