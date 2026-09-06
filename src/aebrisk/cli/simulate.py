"""The `simulate` stage: run one configuration over the cohort and say what ran.

The command is thin. It parses arguments, names the inputs, calls the common
cohort runner, and turns a refusal into an exit code. Everything it decides is
recorded, because a result whose inputs cannot be named is not reproducible and
this is where the naming happens.

Four hashes identify a run: the configuration, the cohort, the container image
and the commit. They are what a reader compares when two runs disagree. The
container digest is READ from the environment rather than guessed, because a
wrong digest is worse than an absent one — it claims a reproducibility that was
never checked — and "unknown" is a truthful answer for a run outside the image.

The scenario source is named on the command line rather than inferred. The
synthetic source exercises the pipeline without a licensed dataset; the nuPlan
source reads the real split, and refuses with the path it looked for when none
is mounted. Naming the missing root beats a driver error three layers down.

ONE CELL IS NEVER RUN ALONE. `--config-id` names the cell under test and the
oracle is added to the run, because a missed intervention is defined by the
comparison with it. `--config-id all` runs the whole matrix in one pass, which
is the cheaper path: the reference is then computed once per token instead of
once per cell. Only the cell that was asked for is written, so no cell's results
are produced twice.

`--dry-run` stops after the run context. It is what an operator uses to check
the arguments, the mount and the manifest in a second, rather than by starting a
job that takes hours to reach its first refusal.
"""

# This module deliberately does NOT use `from __future__ import annotations`.
# Typer reads the `Annotated` metadata that declares each option, and with
# postponed evaluation this Typer version resolves the annotation without
# extras, so every option silently becomes a positional argument instead.
# Every annotation here must therefore be valid at runtime on Python 3.9:
# use `Optional[X]`, never `X | None`.

import hashlib
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer

from aebrisk.artifacts.envelope import canonical_json_bytes
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import load_manifest, membership_sha256
from aebrisk.nuplan_adapter.database import resolve_installation
from aebrisk.simulation.orchestrate import (
    TokenRun,
    configurations_for,
    finished_tokens,
    resolve_cohort,
    run_cohort,
    summarize,
    write_run_complete,
    write_token_run,
)
from aebrisk.simulation.synthetic import synthetic_cohort

#: Where the mounted nuPlan split is named. Read from the environment rather than
#: taken as an option so one operator decision cannot disagree with another.
DATA_ROOT_VAR = "NUPLAN_DATA_ROOT"

#: Set by the container image so a run can name the environment it happened in.
IMAGE_DIGEST_VAR = "AEBRISK_IMAGE_DIGEST"
UNKNOWN_DIGEST = "unknown"

SCENARIO_SOURCES: tuple[str, ...] = ("synthetic", "nuplan")

#: Asks for every cell of the committed matrix in one pass.
ALL_CONFIGURATIONS = "all"

app = typer.Typer(add_completion=False, help="Run one configuration over the cohort.")


@dataclass(frozen=True)
class RunContext:
    """Everything that identifies one simulation run."""

    configuration_id: str
    protocol_sha256: str
    cohort_sha256: str
    container_digest: str
    commit: str

    def __post_init__(self) -> None:
        for name in ("configuration_id", "protocol_sha256", "cohort_sha256", "commit"):
            if not getattr(self, name):
                raise ValueError(
                    f"{name} must not be empty; an unnamed input reads as recorded "
                    "and identifies nothing"
                )
        if not self.container_digest:
            raise ValueError("container_digest must not be empty; use 'unknown' instead")


def container_digest(environment: Optional[Mapping[str, str]] = None) -> str:
    """The image this run happened in, or an honest admission that it is unknown."""

    source = os.environ if environment is None else environment
    return source.get(IMAGE_DIGEST_VAR) or UNKNOWN_DIGEST


def write_run_context(context: RunContext, path: Path) -> None:
    """Write the run context beside its results, in canonical bytes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json_bytes(asdict(context)).decode("utf-8"))
        handle.write("\n")


def _known_configuration(configuration_id: str) -> None:
    known = {config.configuration_id for config in formal_configurations()}
    if configuration_id == ALL_CONFIGURATIONS:
        return
    if configuration_id not in known:
        raise typer.BadParameter(
            f"{configuration_id!r} is not one of the {len(known)} configurations in the "
            f"committed experiment matrix, and is not {ALL_CONFIGURATIONS!r}"
        )


def _refuse(message: str) -> None:
    """End the command with a diagnostic, not a traceback."""

    typer.echo(message)
    raise typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def simulate(
    protocol: Annotated[Path, typer.Option("--protocol", help="The frozen protocol file.")],
    manifest: Annotated[Path, typer.Option("--manifest", help="The frozen cohort manifest.")],
    config_id: Annotated[str, typer.Option("--config-id", help="One cell of the matrix.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Where results are written.")],
    scenario_source: Annotated[
        str, typer.Option("--scenario-source", help="synthetic or nuplan.")
    ] = "nuplan",
    split: Annotated[
        str, typer.Option("--split", help="Which nuPlan split the scenarios come from.")
    ] = "val",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Check the inputs and the mount, and run nothing."),
    ] = False,
    workers: Annotated[
        int, typer.Option("--workers", min=1, help="Number of token worker processes.")
    ] = 1,
    resume: Annotated[
        bool, typer.Option("--resume", help="Skip tokens with every requested result file.")
    ] = False,
) -> None:
    """Run one configuration and write its results and run context."""

    _known_configuration(config_id)
    if resume and (output_dir / "run_complete.json").exists():
        _refuse(
            "this run is already complete; a second run into the same directory would overwrite evidence"
        )

    if scenario_source not in SCENARIO_SOURCES:
        raise typer.BadParameter(
            f"{scenario_source!r} is not a scenario source; expected one of "
            f"{list(SCENARIO_SOURCES)}"
        )
    if not protocol.exists():
        raise typer.BadParameter(f"the protocol file {str(protocol)!r} does not exist")
    if not manifest.exists():
        raise typer.BadParameter(f"the cohort manifest {str(manifest)!r} does not exist")

    if scenario_source == "nuplan":
        # An operational check, not a gate: the command needs a mounted split and
        # the operator who forgot to mount one should be told which path was
        # looked for, rather than meeting a driver error three layers down.
        try:
            resolve_installation(Path(os.environ.get(DATA_ROOT_VAR, "")), split=split)
        except (ValueError, FileNotFoundError) as error:
            raise typer.BadParameter(
                f"the nuplan scenario source needs a mounted split: {error}. Set "
                f"{DATA_ROOT_VAR} to the nuPlan data root, or use "
                "--scenario-source synthetic to exercise the pipeline without one"
            ) from error

    try:
        cohort_manifest = load_manifest(manifest)
    except Exception as error:
        raise typer.BadParameter(
            f"{str(manifest)!r} is not a cohort manifest this project can read: {error}"
        ) from error

    protocol_sha256 = hashlib.sha256(protocol.read_bytes()).hexdigest()
    cohort_manifest_sha256 = membership_sha256(cohort_manifest)
    context = RunContext(
        configuration_id=config_id,
        protocol_sha256=protocol_sha256,
        cohort_sha256=cohort_manifest_sha256,
        container_digest=container_digest(),
        commit=os.environ.get("AEBRISK_COMMIT", "0" * 40),
    )
    if not resume or not (output_dir / "run_context.json").exists():
        write_run_context(context, output_dir / "run_context.json")
    typer.echo(f"wrote the run context for {config_id} to {output_dir}")

    if dry_run:
        typer.echo(f"nothing was simulated ({scenario_source}, dry-run={dry_run})")
        return

    chosen = configurations_for(
        formal_configurations(),
        None if config_id == ALL_CONFIGURATIONS else config_id,
    )
    written_configurations = (
        tuple(configuration.configuration_id for configuration in formal_configurations())
        if config_id == ALL_CONFIGURATIONS
        else (config_id,)
    )
    try:
        cohort = (
            synthetic_cohort()
            if scenario_source == "synthetic"
            else resolve_cohort(
                cohort_manifest,
                resolve_installation(Path(os.environ.get(DATA_ROOT_VAR, "")), split=split),
            )
        )
        typer.echo(f"resolved {len(cohort)} tokens from {len(cohort_manifest.log_names)} logs")
        finished = (
            finished_tokens(output_dir, written_configurations, (s.reference.token for s in cohort))
            if resume
            else frozenset()
        )
        pending = tuple(s for s in cohort if s.reference.token not in finished)
        paths: list[Path] = []

        def persist(run: TokenRun) -> None:
            paths.extend(
                write_token_run(
                    run,
                    cohort_manifest,
                    output_dir,
                    written_configurations=written_configurations,
                    protocol_sha256=protocol_sha256,
                    cohort_manifest_sha256=cohort_manifest_sha256,
                )
            )
            typer.echo(
                f"  {run.token} {run.family} "
                f"{'ok' if run.invalid is None else 'INVALID ' + run.invalid.reason}"
            )

        runs = (
            run_cohort(
                pending,
                chosen,
                protocol_hash=protocol_sha256,
                protocol=cohort_manifest,
                on_token=persist,
                workers=workers,
            )
            if pending
            else ()
        )
        # These entries account only for membership; persisted results are not
        # rewritten or counted as simulations performed by this invocation.
        earlier = tuple(
            TokenRun(s.reference.token, s.family, (), None)
            for s in cohort
            if s.reference.token in finished
        )
        write_run_complete(
            (*earlier, *runs),
            cohort_manifest,
            output_dir,
            cohort_manifest_sha256=cohort_manifest_sha256,
        )
    except (ValueError, FileNotFoundError) as error:
        _refuse(str(error))
        return

    counts = summarize(runs)
    typer.echo(
        f"wrote {len(paths)} result documents: {counts['tokens']} tokens, "
        f"{counts['valid']} valid, {counts['invalid']} invalid, {counts['records']} records"
    )
