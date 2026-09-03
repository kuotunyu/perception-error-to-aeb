"""The `evaluate` stage: turn a directory of run results into reportable numbers.

It reads a run index rather than scanning a directory, so that what was
evaluated is a decision recorded in a file rather than whatever happened to be
on disk when the command ran.
"""

# This module deliberately does NOT use `from __future__ import annotations`.
# Typer reads the `Annotated` metadata that declares each option, and with
# postponed evaluation this Typer version resolves the annotation without
# extras, so every option silently becomes a positional argument instead.
# Every annotation here must therefore be valid at runtime on Python 3.9:
# use `Optional[X]`, never `X | None`.

import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(add_completion=False, help="Aggregate simulation results.")


@app.callback(invoke_without_command=True)
def evaluate(
    run_index: Annotated[Path, typer.Option("--run-index", help="The runs to evaluate.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Where to write metrics.")],
) -> None:
    """Aggregate the named runs, or refuse with the reason."""

    if not run_index.is_file():
        typer.echo(f"evaluate failed: the run index {str(run_index)!r} does not exist", err=True)
        raise typer.Exit(code=1)

    index = json.loads(run_index.read_text(encoding="utf-8"))
    runs = index.get("runs", [])
    if not runs:
        typer.echo("evaluate failed: the run index names no runs", err=True)
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"evaluated_runs": sorted(runs)}
    with (output_dir / "evaluation.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    typer.echo(f"evaluated {len(runs)} runs into {output_dir}")
