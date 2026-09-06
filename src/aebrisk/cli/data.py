"""The `data` stage: check the mounts before anything long-running starts.

The whole purpose is to fail in a second with a message naming what is missing,
rather than at hour three of a simulation. It opens no database and reads no
log; it checks that the three things this study reads are where they are said to
be, and that nothing configures a sensor root.
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
import yaml

from aebrisk.cohort.census import census_json_bytes, census_split
from aebrisk.nuplan_adapter.database import resolve_installation

app = typer.Typer(add_completion=False, help="Check a nuPlan installation.")


@app.command()
def preflight(
    db_root: Annotated[Path, typer.Option("--db-root", help="Where the log databases live.")],
    map_root: Annotated[Path, typer.Option("--map-root", help="Where the maps live.")],
    output: Annotated[Path, typer.Option("--output", help="Where to write the report.")],
    split: Annotated[str, typer.Option("--split", help="Which split to check.")] = "mini",
) -> None:
    """Report what is present, or refuse with the reason."""

    try:
        installation = resolve_installation(db_root, split=split)
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"preflight failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    # The adapter derives the maps root from the data root, so `--map-root` is a
    # CHECK rather than a second source of truth: it asserts that what the
    # operator believes was mounted is what the study will actually read. An
    # operator who mounted maps somewhere else finds out here, in a second,
    # rather than at hour three of a simulation.
    if map_root.resolve() != installation.maps_root.resolve():
        typer.echo(
            f"preflight failed: the map root {str(map_root)!r} is not the one this "
            f"installation will read, which is {str(installation.maps_root)!r}",
            err=True,
        )
        raise typer.Exit(code=1)

    report = {
        "split": installation.split,
        "data_root": str(installation.data_root),
        "maps_root": str(installation.maps_root),
        "log_database_count": len(installation.log_databases),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    typer.echo(f"preflight wrote {output}")


@app.command()
def census(
    db_root: Annotated[Path, typer.Option("--db-root", help="Where the log databases live.")],
    protocol: Annotated[Path, typer.Option("--protocol", help="The frozen protocol file.")],
    output: Annotated[Path, typer.Option("--output", help="Where to write the census.")],
    split: Annotated[str, typer.Option("--split", help="Which split to count.")] = "mini",
) -> None:
    """Count what a split holds, by scenario type and by family, before any freeze.

    Whether each family has two logs to divide between development and locked
    evaluation is the fact that decides whether a cohort can be frozen at all,
    and no amount of reading the protocol can settle it.
    """

    try:
        installation = resolve_installation(db_root, split=split)
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"census failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    document = yaml.safe_load(protocol.read_text(encoding="utf-8"))
    families = {family: tuple(types) for family, types in document["scenario_families"].items()}

    report = census_split(
        split=installation.split,
        log_databases=installation.log_databases,
        family_types=families,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(census_json_bytes(report))
    typer.echo(
        f"census wrote {output}: {report.databases_read} databases, "
        f"{len(report.types)} types, {len(report.pinned_absent)} pinned types absent"
    )
