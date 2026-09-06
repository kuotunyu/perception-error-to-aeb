"""Aggregate one complete locked matrix into four validated evidence documents."""

# Typer on Python 3.9 needs eager Annotated evaluation in command modules.

from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal, Optional, cast

import typer
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aebrisk.analysis.aggregate import (
    common_cohort,
    configuration_intervals,
    configuration_rows,
    exclusion_rows,
    load_formal_results,
    validate_formal_set,
)
from aebrisk.analysis.attribution import attribution_by_metric
from aebrisk.artifacts.documents import (
    AEBEvaluationV1,
    AEBExclusionsV1,
    AEBIntervalsV1,
    AEBShapleyV1,
    write_document,
)
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import load_manifest

app = typer.Typer(add_completion=False, help="Aggregate simulation results.")

_COHORT_FILES = (
    "smoke.json",
    "development.json",
    "evaluation.json",
    "development-eligibility.json",
    "evaluation-eligibility.json",
)


class _EligibilityRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    family: str
    initial_ego_speed_mps: Optional[Annotated[float, Field(allow_inf_nan=False)]]
    log_name: str
    official_split: str
    oracle_enters_corridor_within_4s: Optional[bool]
    oracle_min_ttc_within_4s: Optional[Annotated[float, Field(allow_inf_nan=False)]]
    reason: str
    scenario_token: str
    scenario_type: str


class _EligibilityDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aeb-cohort-eligibility/v1"]
    examined: tuple[_EligibilityRow, ...]
    scenarios_in_split_by_family: dict[str, Annotated[int, Field(ge=0, strict=True)]]


def _validate_eligibility(path: Path) -> bytes:
    try:
        _EligibilityDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise ValueError(f"invalid cohort eligibility document {str(path)!r}") from error
    return path.read_bytes()


def _read_cohort_metadata(manifest_path: Path) -> dict[str, bytes]:
    metadata: dict[str, bytes] = {}
    for name in _COHORT_FILES:
        source = manifest_path.parent / name
        if not source.is_file():
            raise ValueError(f"required cohort metadata {str(source)!r} does not exist")
        payload = (
            source.read_bytes() if "-eligibility" not in name else _validate_eligibility(source)
        )
        if "-eligibility" not in name:
            load_manifest(source)
        metadata[name] = payload
    return metadata


def _write_cohort_metadata(metadata: dict[str, bytes], output_dir: Path) -> None:
    destination = output_dir / "cohort"
    destination.mkdir(parents=True, exist_ok=True)
    for name, payload in metadata.items():
        (destination / name).write_bytes(payload)


@app.callback(invoke_without_command=True)
def evaluate(
    results_dir: Annotated[
        Path, typer.Option("--results-dir", help="The complete formal result directory.")
    ],
    manifest: Annotated[
        Path, typer.Option("--manifest", help="The frozen cohort manifest for these results.")
    ],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Where to write published evidence.")
    ],
) -> None:
    """Validate the complete formal set before publishing any aggregate."""

    try:
        cohort_manifest = load_manifest(manifest)
        cohort_metadata = _read_cohort_metadata(manifest)
        tokens = tuple(
            token for family_tokens in cohort_manifest.families.values() for token in family_tokens
        )
        loaded = load_formal_results(results_dir)
        configurations = formal_configurations()
        validate_formal_set(loaded, configurations, tokens, manifest=cohort_manifest)
        common = common_cohort(loaded)
        rows = configuration_rows(loaded, common)
        families = {
            token: family
            for family, family_tokens in cohort_manifest.families.items()
            for token in family_tokens
        }
        computed_intervals = configuration_intervals(loaded, common, families)
        common_fields = {
            "protocol_sha256": cohort_manifest.protocol_sha256,
            "cohort_manifest_sha256": loaded.cohort_manifest_sha256,
            "cohort_size": len(tokens),
            "common_valid_tokens": len(common),
        }
        evaluation_document = AEBEvaluationV1.model_validate(
            {
                "schema_version": "aeb-evaluation/v1",
                **common_fields,
                "simulated_seconds": sum(cast(float, row["simulated_seconds"]) for row in rows),
                "configurations": rows,
            }
        )
        intervals_document = AEBIntervalsV1.model_validate(
            {
                "schema_version": "aeb-intervals/v1",
                **common_fields,
                "intervals": {
                    configuration: {
                        metric: asdict(interval) for metric, interval in metrics.items()
                    }
                    for configuration, metrics in computed_intervals.items()
                },
            }
        )
        shapley_document = AEBShapleyV1.model_validate(
            {
                "schema_version": "aeb-shapley/v1",
                **common_fields,
                "metrics": attribution_by_metric(loaded, common),
            }
        )
        exclusions_document = AEBExclusionsV1.model_validate(
            {
                "schema_version": "aeb-exclusions/v1",
                **common_fields,
                "excluded": exclusion_rows(loaded, common),
            }
        )
    except (OSError, ValueError) as error:
        typer.echo(f"evaluate failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    for name, document in (
        ("evaluation.json", evaluation_document),
        ("intervals.json", intervals_document),
        ("shapley.json", shapley_document),
        ("exclusions.json", exclusions_document),
    ):
        write_document(document, output_dir / name)
    _write_cohort_metadata(cohort_metadata, output_dir)
    typer.echo(
        f"evaluated {len(configurations)} configurations over {len(common)} common-valid "
        f"tokens into {output_dir}"
    )
