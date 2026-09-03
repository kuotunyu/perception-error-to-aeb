"""The `report` stage: build the static site from claims and artifacts.

A report is a set of claims about artifacts. Without the claims it is a page of
numbers nobody has taken responsibility for, so a missing claims file is a
refusal rather than an empty section.
"""

# This module deliberately does NOT use `from __future__ import annotations`.
# Typer reads the `Annotated` metadata that declares each option, and with
# postponed evaluation this Typer version resolves the annotation without
# extras, so every option silently becomes a positional argument instead.
# Every annotation here must therefore be valid at runtime on Python 3.9:
# use `Optional[X]`, never `X | None`.

from pathlib import Path
from typing import Annotated

import typer

from aebrisk.report.builder import build_site

app = typer.Typer(add_completion=False, help="Build the static report.")


@app.callback(invoke_without_command=True)
def report(
    claims: Annotated[Path, typer.Option("--claims", help="The claims this report makes.")],
    artifacts_dir: Annotated[Path, typer.Option("--artifacts-dir", help="Where results live.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Where the site is written.")
    ] = Path("site"),
) -> None:
    """Build the site, or refuse with the reason."""

    if not claims.is_file():
        typer.echo(f"report failed: the claims file {str(claims)!r} does not exist", err=True)
        raise typer.Exit(code=1)
    if not artifacts_dir.is_dir():
        typer.echo(
            f"report failed: the artifacts directory {str(artifacts_dir)!r} does not exist",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        written = build_site(claims, artifacts_dir, output_dir)
    except ValueError as error:
        # The registry decides what a valid claim is. The CLI's job is to say
        # what it refused and why, not to let the exception escape as a trace.
        typer.echo(f"report failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"report wrote {written}")
