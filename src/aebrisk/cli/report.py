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
import yaml

from aebrisk.analysis.claims import (
    audit_claims,
    evidence_provenance,
    generate_claims,
)
from aebrisk.report.builder import build_site

app = typer.Typer(add_completion=False, help="Build the static report.")


def audit_claims_command(
    claims: Annotated[Path, typer.Option("--claims", help="The claim registry to audit.")],
) -> None:
    """Audit every stated number against its exact committed artifact."""

    violations = audit_claims(claims, Path.cwd())
    if violations:
        for violation in violations:
            typer.echo(violation, err=True)
        raise typer.Exit(code=1)
    typer.echo("audit-claims: 0 violations")


def generate_claims_command(
    evidence_dir: Annotated[
        Path, typer.Option("--evidence-dir", help="Directory of published analysis evidence.")
    ],
    output: Annotated[Path, typer.Option("--output", help="Claim registry to write.")],
) -> None:
    """Generate exact observed claims from the registered analysis documents."""

    try:
        protocol_sha256, cohort_manifest_sha256 = evidence_provenance(evidence_dir)
        registry = generate_claims(evidence_dir, protocol_sha256, cohort_manifest_sha256)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        typer.echo(f"generate-claims failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(
            registry.model_dump(mode="json"),
            handle,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        )
    typer.echo(f"generated {len(registry.claims)} claims in {output}")


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
