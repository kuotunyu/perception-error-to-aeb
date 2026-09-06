"""Strict formal-set loading, common-cohort aggregation, and uncertainty."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, Optional, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aebrisk.artifacts.documents import INTERVAL_METRICS
from aebrisk.artifacts.results import AEBScenarioResult
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import CohortManifestV1, membership_sha256
from aebrisk.metrics.bootstrap import BootstrapInterval, paired_scenario_bootstrap
from aebrisk.metrics.safety import measured_simulated_seconds, summarize_configuration
from aebrisk.simulation.common_cohort import ExperimentConfiguration, common_valid_scenarios
from aebrisk.simulation.orchestrate import TokenResultsV1


class _RunCompleteV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aeb-run-complete/v1"]
    cohort_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tokens: tuple[str, ...]

    @model_validator(mode="after")
    def validate_unique_tokens(self) -> _RunCompleteV1:
        if len(set(self.tokens)) != len(self.tokens):
            raise ValueError("completion marker repeats a token")
        return self


class FormalResults(dict[str, dict[str, TokenResultsV1]]):
    """The mapping API plus the completion marker identity it was loaded under."""

    def __init__(self, *, marker_tokens: Sequence[str], cohort_manifest_sha256: str) -> None:
        super().__init__()
        self.marker_tokens = tuple(marker_tokens)
        self.cohort_manifest_sha256 = cohort_manifest_sha256


def load_formal_results(root: Path) -> FormalResults:
    """Parse every configuration/token document after requiring completion."""

    marker_path = root / "run_complete.json"
    if not marker_path.is_file():
        raise ValueError(f"formal result set {str(root)!r} has no run_complete.json")
    marker = _RunCompleteV1.model_validate_json(marker_path.read_text(encoding="utf-8"))
    loaded = FormalResults(
        marker_tokens=marker.tokens,
        cohort_manifest_sha256=marker.cohort_manifest_sha256,
    )
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        documents: dict[str, TokenResultsV1] = {}
        paths = sorted(directory.iterdir())
        unexpected = [path for path in paths if not path.is_file() or path.suffix != ".json"]
        if unexpected:
            raise ValueError(f"unexpected formal artifact {str(unexpected[0])!r}")
        for path in paths:
            document = TokenResultsV1.model_validate_json(path.read_text(encoding="utf-8"))
            if document.configuration_id != directory.name:
                raise ValueError(
                    f"document {path.name!r} is filed under {directory.name!r} but names "
                    f"configuration_id {document.configuration_id!r}"
                )
            if document.scenario_token != path.stem:
                raise ValueError(
                    f"document {path.name!r} names scenario_token {document.scenario_token!r}"
                )
            documents[path.stem] = document
        loaded[directory.name] = documents
    return loaded


def _validate_document_records(
    document: TokenResultsV1, configuration: ExperimentConfiguration
) -> None:
    if document.valid and (
        document.invalid_reason is not None or document.invalid_phase is not None
    ):
        raise ValueError("valid token document carries invalid diagnostics")
    if not document.valid and (not document.invalid_reason or not document.invalid_phase):
        raise ValueError("invalid token document requires a reason and phase")

    for record in document.results:
        if record.scenario_token != document.scenario_token:
            raise ValueError(f"record token differs in {document.configuration_id!r}")
        if record.configuration_id != document.configuration_id:
            raise ValueError(f"record configuration differs in {document.configuration_id!r}")
        if record.family != document.family:
            raise ValueError(f"record family differs in {document.configuration_id!r}")
        if record.valid != document.valid:
            raise ValueError(f"record validity differs in {document.configuration_id!r}")

    if document.valid:
        replicates = sorted(record.replicate for record in document.results)
        expected = list(range(configuration.replicate_count))
        if replicates != expected:
            raise ValueError(
                f"{document.configuration_id}/{document.scenario_token} must have replicates "
                f"{expected}"
            )
    elif len({record.replicate for record in document.results}) != len(document.results):
        raise ValueError(
            f"{document.configuration_id}/{document.scenario_token} repeats an invalid replicate"
        )


def validate_formal_set(
    loaded: FormalResults,
    configurations: Sequence[ExperimentConfiguration],
    tokens: Sequence[str],
    *,
    manifest: Optional[CohortManifestV1] = None,
) -> None:
    """Refuse identity, provenance, inventory, replicate, or validity drift."""

    expected_tokens = set(tokens)
    if set(loaded.marker_tokens) != expected_tokens:
        raise ValueError("completion marker covers a different cohort")

    expected_configurations = {item.configuration_id: item for item in configurations}
    extra = sorted(set(loaded) - set(expected_configurations))
    if extra:
        raise ValueError(f"unexpected configuration {extra[0]!r}")

    invalid_sets: list[set[str]] = []
    protocol_hashes: set[str] = set()
    cohort_hashes: set[str] = set()
    splits: set[str] = set()
    family_by_token: dict[str, str] = {}
    invalid_diagnostics: dict[str, set[tuple[str, str]]] = {}
    for identifier, configuration in expected_configurations.items():
        if identifier not in loaded:
            raise ValueError(f"missing configuration {identifier!r}")
        if set(loaded[identifier]) != expected_tokens:
            raise ValueError(f"configuration {identifier!r} covers a different cohort")

        invalid: set[str] = set()
        for token, document in loaded[identifier].items():
            protocol_hashes.add(document.protocol_sha256)
            cohort_hashes.add(document.cohort_manifest_sha256)
            splits.add(document.split)
            prior_family = family_by_token.setdefault(token, document.family)
            if document.family != prior_family:
                raise ValueError(f"token {token!r} changes family across configurations")
            if not document.valid:
                invalid.add(token)
                invalid_diagnostics.setdefault(token, set()).add(
                    (document.invalid_phase or "", document.invalid_reason or "")
                )
            _validate_document_records(document, configuration)
        invalid_sets.append(invalid)

    if any(invalid != invalid_sets[0] for invalid in invalid_sets[1:]):
        raise ValueError("invalid scenarios are not excluded globally")
    if any(len(diagnostics) != 1 for diagnostics in invalid_diagnostics.values()):
        raise ValueError("invalid diagnostics differ across configurations")
    if len(protocol_hashes) != 1:
        raise ValueError("formal documents do not share one protocol hash")
    if cohort_hashes != {loaded.cohort_manifest_sha256}:
        raise ValueError("formal documents do not match the completion marker cohort hash")
    if len(splits) != 1:
        raise ValueError("formal documents do not share one split")

    if manifest is not None:
        manifest_tokens = {
            token for family_tokens in manifest.families.values() for token in family_tokens
        }
        if manifest_tokens != expected_tokens:
            raise ValueError("manifest covers a different cohort")
        if membership_sha256(manifest) != loaded.cohort_manifest_sha256:
            raise ValueError("completion marker cohort hash does not match the manifest")
        if protocol_hashes != {manifest.protocol_sha256}:
            raise ValueError("formal documents do not match the manifest protocol hash")
        if splits != {manifest.split}:
            raise ValueError("formal documents do not match the manifest split")
        expected_families = {
            token: family
            for family, family_tokens in manifest.families.items()
            for token in family_tokens
        }
        if family_by_token != expected_families:
            raise ValueError("formal document families do not match the manifest")


def common_cohort(loaded: Mapping[str, Mapping[str, TokenResultsV1]]) -> tuple[str, ...]:
    """Return the tokens with valid replicate records in every configuration."""

    records_by_configuration = {
        configuration: tuple(
            record for document in documents.values() for record in document.results
        )
        for configuration, documents in loaded.items()
    }
    return common_valid_scenarios(records_by_configuration)


def _configuration_group(identifier: str) -> str:
    if identifier in ("no_aeb", "oracle_aeb"):
        return "baseline"
    if identifier.startswith("calibration_imported_"):
        return "imported"
    if identifier.startswith("coalition-"):
        return "coalition"
    return "single_channel"


def _records(documents: Mapping[str, TokenResultsV1]) -> tuple[AEBScenarioResult, ...]:
    return tuple(record for document in documents.values() for record in document.results)


def configuration_rows(
    loaded: Mapping[str, Mapping[str, TokenResultsV1]], cohort: tuple[str, ...]
) -> list[dict[str, object]]:
    """Build one report row per cell using that cell's measured exposure."""

    preferred = {
        configuration.configuration_id: index
        for index, configuration in enumerate(formal_configurations())
    }
    identifiers = sorted(loaded, key=lambda name: (preferred.get(name, len(preferred)), name))
    rows: list[dict[str, object]] = []
    for identifier in identifiers:
        records = _records(loaded[identifier])
        exposure = measured_simulated_seconds(records, cohort=cohort)
        summary = summarize_configuration(records, cohort=cohort, simulated_seconds=exposure)
        collisions = summary.collisions_vru + summary.collisions_vehicle + summary.collisions_object
        rows.append(
            {
                "configuration_id": identifier,
                "group": _configuration_group(identifier),
                "scenarios": summary.scenarios,
                "simulated_seconds": exposure,
                "collisions": collisions,
                "collisions_vru": summary.collisions_vru,
                "collisions_vehicle": summary.collisions_vehicle,
                "collisions_object": summary.collisions_object,
                "contacts_not_at_fault": summary.contacts_not_at_fault,
                "collision_energy_total": summary.collision_energy_total,
                "missed_interventions": summary.missed_interventions,
                "false_interventions": summary.false_interventions,
                "mean_intervention_duration_s": summary.mean_intervention_duration_s,
                "max_deceleration_mps2": summary.max_deceleration_mps2,
                "max_abs_jerk_mps3": summary.max_abs_jerk_mps3,
                "min_ttc_s": summary.min_ttc_s,
                "min_clearance_m": summary.min_clearance_m,
                "collisions_per_1000_scenarios": summary.collisions_per_1000_scenarios.value,
                "collisions_per_hour": summary.collisions_per_hour.value,
                "collisions_per_100km": None,
            }
        )
    return rows


def _replicate_metric_mean(document: TokenResultsV1, metric: str) -> float:
    replicates = sorted(record.replicate for record in document.results)
    if replicates != [0, 1, 2]:
        raise ValueError(
            f"{document.configuration_id}/{document.scenario_token} must have replicates [0, 1, 2]"
        )
    if metric == "collision_indicator":
        values = [
            float(bool(record.collision_vru + record.collision_vehicle + record.collision_object))
            for record in document.results
        ]
    else:
        values = [float(getattr(record, metric)) for record in document.results]
    return sum(values) / len(values)


def configuration_intervals(
    loaded: Mapping[str, Mapping[str, TokenResultsV1]],
    cohort: tuple[str, ...],
    family_by_scenario: Mapping[str, str],
) -> dict[str, dict[str, BootstrapInterval]]:
    """Paired family-stratified intervals over per-token replicate means."""

    output: dict[str, dict[str, BootstrapInterval]] = {name: {} for name in loaded}
    for metric in INTERVAL_METRICS:
        values = {
            token: {
                configuration: _replicate_metric_mean(documents[token], metric)
                for configuration, documents in loaded.items()
            }
            for token in cohort
        }
        by_configuration = paired_scenario_bootstrap(values, family_by_scenario=family_by_scenario)
        for configuration, interval in by_configuration.items():
            output[configuration][metric] = interval
    return output


def exclusion_rows(
    loaded: Mapping[str, Mapping[str, TokenResultsV1]], cohort: tuple[str, ...]
) -> list[dict[str, Optional[str]]]:
    """Describe each globally excluded token once; old exception types stay unknown."""

    if not loaded:
        return []
    first = next(iter(loaded.values()))
    included = set(cohort)
    return [
        {
            "scenario_token": token,
            "phase": cast(str, document.invalid_phase),
            "reason": cast(str, document.invalid_reason),
            "exception_type": None,
        }
        for token, document in sorted(first.items())
        if token not in included
    ]
