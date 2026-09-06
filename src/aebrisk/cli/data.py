"""The `data` stage: everything that happens to a split before a simulation does.

Three commands, in the order an operator runs them.

`preflight` fails in a second with a message naming what is missing, rather than
at hour three of a simulation. It opens no database: it checks that the things
this study reads are where they are said to be, and that nothing configures a
sensor root.

`census` counts what a split actually holds, by scenario type and by family. It
is the gate before any freeze, because whether a family has two logs to divide
between development and locked evaluation is a fact about the recording that no
amount of reading the protocol can settle.

`freeze` cuts one cohort out of one official split and writes it once, beside
the eligibility record for every scenario it examined. This is the long one: it
reads recordings, and what it writes is the document every published number is
checked against.
"""

# This module deliberately does NOT use `from __future__ import annotations`.
# Typer reads the `Annotated` metadata that declares each option, and with
# postponed evaluation this Typer version resolves the annotation without
# extras, so every option silently becomes a positional argument instead.
# Every annotation here must therefore be valid at runtime on Python 3.9:
# use `Optional[X]`, never `X | None`.

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
import yaml

from aebrisk.cohort.census import census_json_bytes, census_split
from aebrisk.cohort.freeze import freeze_split, write_eligibility
from aebrisk.cohort.manifest import save_manifest
from aebrisk.cohort.splits import OFFICIAL_SPLIT_FOR
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


@app.command()
def freeze(
    db_root: Annotated[Path, typer.Option("--db-root", help="Where the log databases live.")],
    protocol: Annotated[Path, typer.Option("--protocol", help="The frozen protocol file.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Where the manifest and its evidence go.")
    ],
    split: Annotated[
        str, typer.Option("--split", help="development or evaluation.")
    ] = "evaluation",
) -> None:
    """Freeze one cohort out of the official split it is drawn from, once.

    The official split is not an option: development comes from nuPlan's
    training logs and locked evaluation from its validation logs, and letting an
    operator pair them differently is how held-out recordings end up in the half
    that thresholds were chosen on.

    This reads recordings and takes a while. It writes two documents: the
    manifest, which `simulate` reads and every published number is checked
    against, and the eligibility record, which says what was examined and which
    rule refused each scenario that did not make it.
    """

    if split not in OFFICIAL_SPLIT_FOR:
        raise typer.BadParameter(
            f"{split!r} is not a cohort; expected one of {sorted(OFFICIAL_SPLIT_FOR)}"
        )
    official = OFFICIAL_SPLIT_FOR[split]

    try:
        installation = resolve_installation(db_root, split=official)
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"freeze failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    document = yaml.safe_load(protocol.read_text(encoding="utf-8"))
    families = {family: tuple(types) for family, types in document["scenario_families"].items()}
    protocol_sha256 = hashlib.sha256(protocol.read_bytes()).hexdigest()

    typer.echo(f"freezing {split} from {len(installation.log_databases)} {official!r} databases")
    try:
        frozen = freeze_split(
            installation,
            families,
            split=cast(Literal["development", "evaluation"], split),
            protocol_sha256=protocol_sha256,
        )
        save_manifest(frozen.manifest, output_dir / f"{split}.json")
    except (ValueError, FileExistsError) as error:
        typer.echo(f"freeze failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    write_eligibility(
        frozen.eligibility,
        output_dir / f"{split}-eligibility.json",
        frozen.scenarios_in_split_by_family,
    )
    for family, tokens in sorted(frozen.manifest.families.items()):
        typer.echo(
            f"  {family}: {len(tokens)} frozen of "
            f"{frozen.scenarios_in_split_by_family[family]} in the split"
        )
    typer.echo(f"froze {split} into {output_dir} over {len(frozen.manifest.log_names)} logs")
