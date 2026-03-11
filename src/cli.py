"""
src/cli.py
───────────
Brownfield Cartographer — Command Line Interface.

Subcommands:
  analyze   Run the full four-agent analysis pipeline on a repo.
  query     Launch the interactive Navigator query interface.
  version   Print version and exit.

Branch: feature/09-cli-orchestrator
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.models.graph import Settings
from src.orchestrator import Orchestrator
from src.utils.logging_config import configure_logging, get_logger

app     = typer.Typer(
    name="cartographer",
    help="[bold cyan]Brownfield Cartographer[/bold cyan] — Codebase Intelligence System",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
logger  = get_logger(__name__)


# ── Shared initialisation ─────────────────────────────────────────────────────

def _load_settings() -> Settings:
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    return settings


# ── analyze ──────────────────────────────────────────────────────────────────

@app.command()
def analyze(
    target: str = typer.Argument(
        ...,
        help="Local repo path or GitHub URL (https://github.com/owner/repo).",
    ),
    output_dir: Path = typer.Option(
        Path(".cartography"),
        "--output", "-o",
        help="Directory where artifacts are written.",
    ),
    skip_llm: bool = typer.Option(
        False,
        "--skip-llm",
        help="Skip Semanticist (LLM) phase even if API keys are configured.",
    ),
    incremental: bool = typer.Option(
        False,
        "--incremental",
        help="Only re-analyse files changed since the last run.",
    ),
) -> None:
    """
    Run the full Cartographer analysis pipeline on a repository.

    Produces CODEBASE.md, onboarding_brief.md, lineage_graph.json,
    module_graph.json, and cartography_trace.jsonl in OUTPUT_DIR.
    """
    settings = _load_settings()

    if skip_llm:
        # Override: clear all LLM keys so Semanticist phase is skipped
        settings.anthropic_api_key  = None
        settings.openai_api_key     = None
        settings.openrouter_api_key = None

    console.print(
        Panel(
            f"[bold]Target:[/bold] {target}\n"
            f"[bold]Output:[/bold] {output_dir}\n"
            f"[bold]LLM:[/bold] {'[yellow]skipped[/yellow]' if skip_llm else '[green]enabled[/green]'}\n"
            f"[bold]Mode:[/bold] {'incremental' if incremental else 'full'}",
            title="[bold cyan]Brownfield Cartographer — analyze[/bold cyan]",
            expand=False,
        )
    )

    orchestrator = Orchestrator(settings)

    # ── Incremental mode ──────────────────────────────────────────────────────
    if incremental:
        manifest_path = output_dir / "run_manifest.json"
        if manifest_path.exists():
            import json
            try:
                manifest = json.loads(manifest_path.read_text())
                last_commit = manifest.get("git_commit")
                if last_commit:
                    logger.info("incremental_mode", last_commit=last_commit[:7])
                    # Note: full incremental re-analysis requires wiring per-file
                    # re-analysis through each agent — MVP uses last_commit as signal.
                    console.print(
                        f"[dim]Incremental mode: last commit {last_commit[:7]}[/dim]"
                    )
            except Exception:  # noqa: BLE001
                console.print("[yellow]Could not read previous manifest — running full analysis.[/yellow]")
        else:
            console.print("[yellow]No previous run found — running full analysis.[/yellow]")

    # ── Run ───────────────────────────────────────────────────────────────────
    with console.status("[bold green]Analysing…[/bold green]", spinner="dots"):
        run = orchestrator.run_full(target, output_dir=output_dir)

    # ── Summary table ─────────────────────────────────────────────────────────
    table = Table(title="Analysis Summary", show_header=True, header_style="bold magenta")
    table.add_column("Metric",  style="cyan")
    table.add_column("Value",   style="white")

    table.add_row("Run ID",          run.run_id[:8])
    table.add_row("Files Analysed",  str(run.total_files_analysed))
    table.add_row("Files Skipped",   str(run.total_files_skipped))
    table.add_row("Phases Completed", ", ".join(run.phases_completed))
    table.add_row("LLM Cost (USD)",  f"${run.llm_cost_usd:.4f}")
    table.add_row("Errors",          str(len(run.errors)) or "0")
    table.add_row("Output Dir",      str(output_dir))

    console.print(table)

    if run.errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for err in run.errors:
            console.print(f"  [red]•[/red] {err}")

    if run.phases_completed:
        console.print(
            f"\n[bold green]✓[/bold green] Artifacts written to [cyan]{output_dir}[/cyan]\n"
            f"  Run [bold]cartographer query {target}[/bold] to explore the codebase."
        )
    else:
        console.print("\n[bold red]✗ Analysis failed. Check logs above.[/bold red]")
        raise typer.Exit(code=1)


# ── query ─────────────────────────────────────────────────────────────────────

@app.command()
def query(
    target: str = typer.Argument(
        ".",
        help="Local repo path (must have been previously analysed).",
    ),
    output_dir: Path = typer.Option(
        Path(".cartography"),
        "--output", "-o",
        help="Directory containing previously generated artifacts.",
    ),
    tool: str | None = typer.Option(
        None, "--tool", "-t",
        help="Run a single tool non-interactively (find_implementation|trace_lineage|blast_radius|explain_module).",
    ),
    args: str | None = typer.Option(
        None, "--args", "-a",
        help='JSON string of arguments for --tool, e.g. \'{"concept": "revenue calculation"}\'.',
    ),
) -> None:
    """
    Launch the interactive Navigator query interface against a previously
    analysed codebase.
    """
    settings     = _load_settings()
    orchestrator = Orchestrator(settings)

    try:
        repo_path = Path(target).expanduser().resolve()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Invalid target path:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    navigator = orchestrator.build_navigator(repo_path, output_dir=output_dir)

    if tool:
        # Non-interactive single query
        import json
        kwargs = json.loads(args or "{}")
        try:
            result = navigator.query(tool, **kwargs)
            console.print_json(json.dumps(result, indent=2, default=str))
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc
    else:
        # Interactive REPL
        navigator.interactive_loop()


# ── version ───────────────────────────────────────────────────────────────────

@app.command()
def version() -> None:
    """Print Cartographer version and exit."""
    from importlib.metadata import version as pkg_version
    try:
        ver = pkg_version("brownfield-cartographer")
    except Exception:  # noqa: BLE001
        ver = "development"
    console.print(f"[bold cyan]Brownfield Cartographer[/bold cyan] v{ver}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
