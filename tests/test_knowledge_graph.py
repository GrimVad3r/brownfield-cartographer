"""
tests/test_knowledge_graph.py
──────────────────────────────
Unit tests for KnowledgeGraph, security utilities, and token budget.

Branch: feature/10-tests
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.graph.knowledge_graph import KnowledgeGraph
from src.models.edges import ImportsEdge, ProducesEdge
from src.models.nodes import DatasetNode, ModuleNode, Language
from src.utils.security import (
    SecurityError,
    is_safe_file,
    sanitise_for_shell,
    validate_github_url,
    validate_local_path,
)
from src.utils.token_budget import BudgetExceededError, ContextWindowBudget


# ── KnowledgeGraph ────────────────────────────────────────────────────────────

@pytest.fixture
def populated_graph() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    m1 = ModuleNode(node_id="src/a.py", path="src/a.py", language=Language.PYTHON)
    m2 = ModuleNode(node_id="src/b.py", path="src/b.py", language=Language.PYTHON)
    d1 = DatasetNode(node_id="dataset:orders", name="orders")

    kg.add_node(m1)
    kg.add_node(m2)
    kg.add_node(d1)

    kg.add_edge(ImportsEdge(source_id="src/b.py", target_id="src/a.py"))
    kg.add_edge(ProducesEdge(source_id="src/a.py", target_id="dataset:orders"))
    return kg


def test_graph_node_retrieval(populated_graph: KnowledgeGraph):
    node = populated_graph.get_node("src/a.py")
    assert isinstance(node, ModuleNode)
    assert node.path == "src/a.py"


def test_graph_node_not_found(populated_graph: KnowledgeGraph):
    assert populated_graph.get_node("nonexistent") is None


def test_blast_radius(populated_graph: KnowledgeGraph):
    # a.py produces orders; b.py imports a.py
    # blast_radius of a.py = {orders (direct) + b.py is upstream, not downstream}
    # Actually: blast_radius = descendants = nodes reachable BY FOLLOWING EDGES FROM
    affected = populated_graph.blast_radius("src/a.py")
    assert "dataset:orders" in affected


def test_find_sources(populated_graph: KnowledgeGraph):
    sources = populated_graph.find_sources()
    # b.py has in-degree=0 (nothing imports it)
    assert "src/b.py" in sources


def test_find_sinks(populated_graph: KnowledgeGraph):
    sinks = populated_graph.find_sinks()
    assert "dataset:orders" in sinks


def test_pagerank_returns_scores(populated_graph: KnowledgeGraph):
    scores = populated_graph.pagerank()
    assert isinstance(scores, dict)
    assert len(scores) > 0


def test_graph_serialisation_roundtrip(populated_graph: KnowledgeGraph, tmp_path: Path):
    out = tmp_path / "graph.json"
    populated_graph.save(out)

    assert out.exists()
    data = json.loads(out.read_text())
    assert "nodes" in data
    assert len(data["nodes"]) >= 3


# ── Security ──────────────────────────────────────────────────────────────────

def test_validate_local_path_valid(tmp_path: Path):
    resolved = validate_local_path(str(tmp_path))
    assert resolved == tmp_path


def test_validate_local_path_not_directory(tmp_path: Path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(SecurityError):
        validate_local_path(str(f))


def test_validate_github_url_valid():
    url = validate_github_url("https://github.com/dbt-labs/jaffle_shop")
    assert url == "https://github.com/dbt-labs/jaffle_shop"


def test_validate_github_url_rejects_http():
    with pytest.raises(SecurityError):
        validate_github_url("http://github.com/owner/repo")


def test_validate_github_url_rejects_arbitrary():
    with pytest.raises(SecurityError):
        validate_github_url("https://evil.com/malware")


def test_is_safe_file_blocked_extension(tmp_path: Path):
    exe = tmp_path / "payload.exe"
    exe.write_bytes(b"\x4d\x5a")
    assert is_safe_file(exe) is False


def test_is_safe_file_safe(tmp_path: Path):
    py = tmp_path / "main.py"
    py.write_text("print('hello')")
    assert is_safe_file(py) is True


def test_sanitise_for_shell_removes_special_chars():
    dangerous = "rm -rf /; echo 'pwned'"
    safe = sanitise_for_shell(dangerous)
    assert ";" not in safe
    assert "'" not in safe


# ── Token budget ──────────────────────────────────────────────────────────────

def test_budget_records_usage():
    budget = ContextWindowBudget(hard_cap_usd=10.0)
    budget.record_usage("claude-haiku-4-5-20251001", 1000, 200)
    summary = budget.summary
    assert summary["input_tokens"] == 1000
    assert summary["output_tokens"] == 200
    assert summary["total_cost_usd"] > 0


def test_budget_hard_cap_raises():
    budget = ContextWindowBudget(hard_cap_usd=0.0001)
    with pytest.raises(BudgetExceededError):
        budget.record_usage("claude-sonnet-4-6", 10000, 5000)


def test_budget_select_model():
    budget = ContextWindowBudget(
        bulk_model="cheap-model",
        synthesis_model="expensive-model",
    )
    assert budget.select_model("bulk") == "cheap-model"
    assert budget.select_model("synthesis") == "expensive-model"


def test_budget_token_counting():
    budget = ContextWindowBudget()
    count = budget.count_tokens("Hello, world!")
    assert count > 0


# ── tests/__init__.py
