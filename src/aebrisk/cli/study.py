"""The `study` commands: run and check the pre-registered policy v2 study.

`configs/experiments/aeb_policy_v2_study.yaml` declares the study: its cells,
its five arms and the three factors each arm sets. `study simulate` runs one
arm. An arm is the released runner, through the same `execute_matrix` as
`aeb-risk simulate`, over the arm's cells with the arm's AEB policy, RNG scheme
and velocity estimator, so an arm that sets no factor writes the released bytes.

EVERY ARM NAMES WHAT IT RAN. Its `run_context.json` holds the released run
context and, beside it, the study file's SHA-256, the arm and its factors, the
SHA-256 of the policy and the error configuration it read, and the Python and
numpy versions inside the container. Its `run.log` stamps each event and each
finished token with the UTC time the line was written, so the first and last
lines give the run's start and end.

A FORMAL ARM RUNS ONLY ON THE STUDY'S COHORT, and only with the commit and the
image identifier set, because the integrity gate checks both. A pilot
(`--pilot`) runs on the smoke manifest into `artifacts/pilot/`, where nothing is
analysed. No arm writes under the released records,
`artifacts/formal/nuplan_aeb_v2/`, or under `docs/`.
"""

# Like `simulate.py`, this module deliberately does NOT use
# `from __future__ import annotations`: Typer reads each option from its runtime
# annotation, so every annotation here must be valid at runtime on Python 3.9.

import dataclasses
import hashlib
import os
import platform
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, NoReturn

import numpy
import typer
import yaml

from aebrisk.cli.simulate import (
    COMMIT_VAR,
    IMAGE_DIGEST_VAR,
    UNKNOWN_COMMIT,
    UNKNOWN_DIGEST,
    container_digest,
    execute_matrix,
    read_run_inputs,
)
from aebrisk.cohort.manifest import membership_sha256
from aebrisk.committed_config import read_committed_config
from aebrisk.observation.tracking import CV_KALMAN_PARAMETERS
from aebrisk.study.definition import (
    StudyRunContext,
    arm_configurations,
    load_study,
    study_sha256,
)

#: Where no arm may write: the released records and the published documentation.
FROZEN_OUTPUTS: tuple[str, ...] = ("artifacts/formal/nuplan_aeb_v2", "docs")

#: Where a pilot writes, one directory per arm. Nothing under it is analysed.
PILOT_ROOT = "artifacts/pilot"

#: The split a pilot's manifest declares.
PILOT_SPLIT = "smoke"

app = typer.Typer(
    add_completion=False,
    help="Run and check the pre-registered policy v2 study.",
    no_args_is_help=True,
)


def _refuse(message: str) -> NoReturn:
    """End the command with a diagnostic, not a traceback."""

    typer.echo(message)
    raise typer.Exit(code=1)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def refuse_frozen_output(output_dir: Path, repository_root: Path) -> None:
    """Refuse an output directory under the released records or the documentation.

    A relative `output_dir` is read from `repository_root`.
    """

    target = (repository_root / output_dir).resolve()
    for frozen in FROZEN_OUTPUTS:
        if target.is_relative_to((repository_root / frozen).resolve()):
            raise ValueError(
                f"writing to {output_dir} is refused: it lies under {frozen}/, which holds "
                "the released records or the published documentation"
            )


def append_run_log(path: Path, line: str, now: Callable[[], datetime] = _utc_now) -> None:
    """Append one line to an arm's run log, stamped with the UTC time it is written.

    A line break inside `line`, such as one in the exception an invalid token's
    reason quotes, becomes a space, so every line of the log carries a stamp.
    """

    stamp = now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{stamp} {' '.join(line.splitlines())}\n")


def _committed_sha256(*parts: str) -> str:
    """The SHA-256 of a committed config as the run reads it, hashed as `load_study` does."""

    return hashlib.sha256(read_committed_config(*parts).encode("utf-8")).hexdigest()


def _velocity_parameters(velocity_estimator: str) -> str:
    """The estimator's parameters as sorted `name=value` pairs; the finite difference has none."""

    if velocity_estimator == "finite-difference":
        return ""
    return ",".join(
        f"{name}={value!r}"
        for name, value in sorted(dataclasses.asdict(CV_KALMAN_PARAMETERS).items())
    )


@app.command("simulate")
def simulate_arm(
    study: Annotated[Path, typer.Option("--study", help="The study file.")],
    arm: Annotated[str, typer.Option("--arm", help="The id of the arm to run.")],
    protocol: Annotated[Path, typer.Option("--protocol", help="The frozen protocol file.")],
    manifest: Annotated[Path, typer.Option("--manifest", help="The frozen cohort manifest.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Where the arm is written.")],
    scenario_source: Annotated[
        str, typer.Option("--scenario-source", help="synthetic or nuplan.")
    ] = "nuplan",
    split: Annotated[
        str, typer.Option("--split", help="Which nuPlan split the scenarios come from.")
    ] = "val",
    workers: Annotated[
        int, typer.Option("--workers", min=1, help="Number of token worker processes.")
    ] = 1,
    resume: Annotated[
        bool, typer.Option("--resume", help="Skip tokens with every requested result file.")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Check the inputs and the mount, and run nothing."),
    ] = False,
    pilot: Annotated[
        bool,
        typer.Option("--pilot", help="Run on the smoke manifest into artifacts/pilot/."),
    ] = False,
) -> None:
    """Run one arm of the study and write its documents, run context and run log."""

    repository_root = Path.cwd()
    try:
        refuse_frozen_output(output_dir, repository_root)
    except ValueError as error:
        _refuse(str(error))
    commit = os.environ.get(COMMIT_VAR) or UNKNOWN_COMMIT
    digest = container_digest()
    if pilot:
        pilot_root = (repository_root / PILOT_ROOT).resolve()
        target = output_dir.resolve()
        if target == pilot_root or not target.is_relative_to(pilot_root):
            _refuse(
                f"a pilot writes one directory per arm under {PILOT_ROOT}/, and {output_dir} "
                "is not one"
            )
    elif commit == UNKNOWN_COMMIT:
        _refuse(
            f"{COMMIT_VAR} is not set; a formal arm records the commit it ran, which the "
            "integrity gate checks"
        )
    elif digest == UNKNOWN_DIGEST:
        _refuse(
            f"{IMAGE_DIGEST_VAR} is not set; a formal arm records the image it ran in, which "
            "the integrity gate checks"
        )

    try:
        definition = load_study(study)
        configurations = arm_configurations(definition, arm)
    except (OSError, ValueError, yaml.YAMLError) as error:
        _refuse(f"the study cannot run arm {arm!r} from {study}: {error}")

    cohort_manifest, protocol_sha256 = read_run_inputs(
        protocol, manifest, output_dir, scenario_source, split
    )
    cohort_sha256 = membership_sha256(cohort_manifest)
    if pilot:
        if cohort_manifest.split != PILOT_SPLIT:
            _refuse(
                f"a pilot runs on the {PILOT_SPLIT} manifest, and {manifest} declares the "
                f"split {cohort_manifest.split!r}"
            )
    elif cohort_sha256 != definition.cohort_membership_sha256:
        _refuse(
            f"the manifest's membership hash {cohort_sha256} is not the cohort membership "
            f"hash {definition.cohort_membership_sha256} the study file records; a formal "
            "arm runs only on the study's cohort"
        )

    factors = {candidate.id: candidate for candidate in definition.arms}[arm]
    context = StudyRunContext(
        configuration_id=f"study:{arm}",
        protocol_sha256=protocol_sha256,
        cohort_sha256=cohort_sha256,
        container_digest=digest,
        commit=commit,
        study_sha256=study_sha256(study),
        arm_id=arm,
        aeb_policy=factors.aeb_policy,
        policy_sha256=_committed_sha256("aeb", f"policy_{factors.aeb_policy}.yaml"),
        rng_scheme=factors.rng_scheme,
        velocity_estimator=factors.velocity_estimator,
        velocity_parameters=_velocity_parameters(factors.velocity_estimator),
        error_config_sha256=_committed_sha256("errors", "formal_v1.yaml"),
        python_version=platform.python_version(),
        numpy_version=numpy.__version__,
    )
    run_log = output_dir / "run.log"

    def report(line: str) -> None:
        typer.echo(line)
        if not dry_run:
            append_run_log(run_log, line.strip())

    execute_matrix(
        context,
        cohort_manifest,
        configurations,
        tuple(configuration.configuration_id for configuration in configurations),
        output_dir,
        scenario_source=scenario_source,
        split=split,
        workers=workers,
        resume=resume,
        dry_run=dry_run,
        report=report,
    )
