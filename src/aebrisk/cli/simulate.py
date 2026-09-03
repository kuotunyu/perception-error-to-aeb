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
synthetic source works today; the nuPlan source is refused until the portfolio
order gate opens, and refusing loudly is better than reading a database this
study is not yet allowed to read.
"""

# This module deliberately does NOT use `from __future__ import annotations`.
# Typer reads the `Annotated` metadata that declares each option, and with
# postponed evaluation this Typer version resolves the annotation without
# extras, so every option silently becomes a positional argument instead.
# Every annotation here must therefore be valid at runtime on Python 3.9:
# use `Optional[X]`, never `X | None`.

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer

from aebrisk.artifacts.envelope import canonical_json_bytes
from aebrisk.attribution.factorial import formal_configurations

#: Set by the container image so a run can name the environment it happened in.
IMAGE_DIGEST_VAR = "AEBRISK_IMAGE_DIGEST"
UNKNOWN_DIGEST = "unknown"

SCENARIO_SOURCES: tuple[str, ...] = ("synthetic", "nuplan")

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


def cohort_sha256(tokens: tuple[str, ...]) -> str:
    """Hash a cohort as the set it is.

    Sorted before hashing, because a cohort is a set and two orderings of it are
    the same cohort; a hash that depended on order would make one study look
    like two.
    """

    if not tokens:
        raise ValueError(
            "the cohort is empty; hashing it would give a stable value for 'nothing was run'"
        )
    payload = canonical_json_bytes(sorted(set(tokens)))
    return hashlib.sha256(payload).hexdigest()


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
    if configuration_id not in known:
        raise typer.BadParameter(
            f"{configuration_id!r} is not one of the {len(known)} configurations in the "
            "committed experiment matrix"
        )


@app.callback(invoke_without_command=True)
def simulate(
    protocol: Annotated[Path, typer.Option("--protocol", help="The frozen protocol file.")],
    manifest: Annotated[Path, typer.Option("--manifest", help="The frozen cohort manifest.")],
    config_id: Annotated[str, typer.Option("--config-id", help="One cell of the matrix.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Where results are written.")],
    scenario_source: Annotated[
        str, typer.Option("--scenario-source", help="synthetic or nuplan.")
    ] = "nuplan",
) -> None:
    """Run one configuration and write its results and run context."""

    _known_configuration(config_id)

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
        # Refusing loudly is better than reading a database this study is not
        # yet allowed to read. The gate opens when bev-calibration-lab releases.
        raise typer.BadParameter(
            "the nuplan scenario source is closed until the portfolio order gate "
            "opens; use --scenario-source synthetic to exercise the pipeline"
        )

    tokens = tuple(json.loads(manifest.read_text(encoding="utf-8"))["scenario_tokens"])
    context = RunContext(
        configuration_id=config_id,
        protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),
        cohort_sha256=cohort_sha256(tokens),
        container_digest=container_digest(),
        commit=os.environ.get("AEBRISK_COMMIT", "0" * 40),
    )
    write_run_context(context, output_dir / "run_context.json")
    typer.echo(f"wrote the run context for {config_id} to {output_dir}")
