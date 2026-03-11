"""
tests/test_sql_lineage.py
──────────────────────────
Unit tests for the SQL lineage analyzer.

Branch: feature/10-tests
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.analyzers.sql_lineage import SQLLineageAnalyzer


@pytest.fixture
def analyzer() -> SQLLineageAnalyzer:
    return SQLLineageAnalyzer()


# ── Basic SQL parsing ─────────────────────────────────────────────────────────

def test_simple_select(tmp_path: Path, analyzer: SQLLineageAnalyzer):
    sql = "SELECT * FROM orders WHERE status = 'active';"
    f = tmp_path / "orders.sql"
    f.write_text(sql)

    deps = analyzer.analyse_file(f, "orders_model")
    table_names = [d.source_table for d in deps]
    assert "orders" in table_names


def test_join_extracts_both_tables(tmp_path: Path, analyzer: SQLLineageAnalyzer):
    sql = """
    SELECT o.id, c.name
    FROM orders o
    JOIN customers c ON o.customer_id = c.id;
    """
    f = tmp_path / "model.sql"
    f.write_text(sql)

    deps = analyzer.analyse_file(f, "model")
    source_tables = {d.source_table for d in deps}
    assert "orders" in source_tables
    assert "customers" in source_tables


def test_cte_not_treated_as_external_table(tmp_path: Path, analyzer: SQLLineageAnalyzer):
    sql = """
    WITH cte_orders AS (
        SELECT * FROM raw_orders
    )
    SELECT * FROM cte_orders;
    """
    f = tmp_path / "cte_model.sql"
    f.write_text(sql)

    deps = analyzer.analyse_file(f, "cte_model")
    source_tables = {d.source_table for d in deps}
    # raw_orders should be extracted, cte_orders should NOT
    assert "raw_orders" in source_tables
    assert "cte_orders" not in source_tables


def test_dbt_ref_extracted(tmp_path: Path, analyzer: SQLLineageAnalyzer):
    sql = """
    SELECT * FROM {{ ref('stg_orders') }}
    JOIN {{ ref('stg_customers') }} ON ...
    """
    f = tmp_path / "orders_model.sql"
    f.write_text(sql)

    deps = analyzer.analyse_file(f, "orders_model")
    source_tables = {d.source_table for d in deps}
    assert "stg_orders" in source_tables
    assert "stg_customers" in source_tables


def test_dbt_source_extracted(tmp_path: Path, analyzer: SQLLineageAnalyzer):
    sql = "SELECT * FROM {{ source('jaffle_shop', 'orders') }}"
    f = tmp_path / "model.sql"
    f.write_text(sql)

    deps = analyzer.analyse_file(f, "model")
    source_tables = {d.source_table for d in deps}
    assert "jaffle_shop.orders" in source_tables


def test_nonexistent_file_returns_empty(analyzer: SQLLineageAnalyzer):
    deps = analyzer.analyse_file(Path("/nonexistent/file.sql"))
    assert deps == []


def test_target_defaults_to_file_stem(tmp_path: Path, analyzer: SQLLineageAnalyzer):
    sql = "SELECT * FROM source_table;"
    f = tmp_path / "my_model.sql"
    f.write_text(sql)

    deps = analyzer.analyse_file(f)   # no model_name arg
    assert all(d.target_table == "my_model" for d in deps)
