"""Generate deterministic SVG figures from committed evidence."""

# Typer on the pinned Python 3.9 runtime needs evaluated Annotated metadata.

from pathlib import Path
from typing import Annotated

import typer

from aebrisk.report.svg import write_figures


def figures(
    evidence_dir: Annotated[Path, typer.Option("--evidence-dir")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
) -> None:
    """Write the three release figures, or explain why evidence was refused."""

    try:
        written = write_figures(evidence_dir, output_dir)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        typer.echo(f"figures failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    for path in written:
        typer.echo(f"figures wrote {path}")
