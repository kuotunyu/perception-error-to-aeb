"""Strict contracts for the four analysis documents published under ``docs``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aebrisk.attribution.shapley import ATTRIBUTED_METRICS, CHANNELS

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(ge=1, strict=True)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]

EVALUATION_SCHEMA_VERSION = "aeb-evaluation/v1"
INTERVALS_SCHEMA_VERSION = "aeb-intervals/v1"
SHAPLEY_SCHEMA_VERSION = "aeb-shapley/v1"
EXCLUSIONS_SCHEMA_VERSION = "aeb-exclusions/v1"
INTERVAL_METRICS: tuple[str, ...] = (
    "collision_indicator",
    "intervention_duration_s",
    "missed_interventions",
    "false_interventions",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AEBConfigurationEvaluationV1(_Strict):
    configuration_id: str = Field(min_length=1)
    group: Literal["baseline", "single_channel", "coalition", "imported"]
    scenarios: NonNegativeInt
    simulated_seconds: NonNegativeFloat
    collisions: NonNegativeInt
    collisions_vru: NonNegativeInt
    collisions_vehicle: NonNegativeInt
    collisions_object: NonNegativeInt
    contacts_not_at_fault: NonNegativeInt
    collision_energy_total: NonNegativeFloat
    missed_interventions: NonNegativeInt
    false_interventions: NonNegativeInt
    mean_intervention_duration_s: Optional[NonNegativeFloat]
    max_deceleration_mps2: Optional[NonNegativeFloat]
    max_abs_jerk_mps3: Optional[NonNegativeFloat]
    min_ttc_s: Optional[NonNegativeFloat]
    min_clearance_m: Optional[NonNegativeFloat]
    collisions_per_1000_scenarios: Optional[NonNegativeFloat]
    collisions_per_hour: Optional[NonNegativeFloat]
    collisions_per_100km: Optional[NonNegativeFloat]

    @model_validator(mode="after")
    def validate_collision_total(self) -> AEBConfigurationEvaluationV1:
        total = self.collisions_vru + self.collisions_vehicle + self.collisions_object
        if self.collisions != total:
            raise ValueError("collision total must equal the three category counts")
        return self


class AEBEvaluationV1(_Strict):
    schema_version: Literal["aeb-evaluation/v1"]
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    cohort_size: NonNegativeInt
    common_valid_tokens: NonNegativeInt
    simulated_seconds: NonNegativeFloat
    configurations: tuple[AEBConfigurationEvaluationV1, ...]

    @model_validator(mode="after")
    def validate_totals(self) -> AEBEvaluationV1:
        if self.common_valid_tokens > self.cohort_size:
            raise ValueError("common-valid count cannot exceed cohort_size")
        if self.simulated_seconds != sum(
            configuration.simulated_seconds for configuration in self.configurations
        ):
            raise ValueError("simulated_seconds must equal the sum of configuration exposures")
        return self


class BootstrapIntervalV1(_Strict):
    estimate: FiniteFloat
    low: FiniteFloat
    high: FiniteFloat
    confidence: Annotated[float, Field(gt=0.0, lt=1.0, allow_inf_nan=False)]
    resamples: PositiveInt
    seed: int

    @model_validator(mode="after")
    def validate_order(self) -> BootstrapIntervalV1:
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        return self


class AEBIntervalsV1(_Strict):
    schema_version: Literal["aeb-intervals/v1"]
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    cohort_size: NonNegativeInt
    common_valid_tokens: NonNegativeInt
    intervals: dict[str, dict[str, BootstrapIntervalV1]]

    @model_validator(mode="after")
    def validate_metrics(self) -> AEBIntervalsV1:
        expected = set(INTERVAL_METRICS)
        if any(set(metrics) != expected for metrics in self.intervals.values()):
            raise ValueError(f"interval metrics must be exactly {list(INTERVAL_METRICS)}")
        return self


class ShapleyMetricV1(_Strict):
    values: dict[str, FiniteFloat]
    efficiency_max_abs_residual: NonNegativeFloat
    scenarios_attributed: NonNegativeInt

    @model_validator(mode="after")
    def validate_channels(self) -> ShapleyMetricV1:
        if set(self.values) != set(CHANNELS):
            raise ValueError(f"attributed channels must be exactly {list(CHANNELS)}")
        return self


class AEBShapleyV1(_Strict):
    schema_version: Literal["aeb-shapley/v1"]
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    cohort_size: NonNegativeInt
    common_valid_tokens: NonNegativeInt
    metrics: dict[str, ShapleyMetricV1]

    @model_validator(mode="after")
    def validate_metrics(self) -> AEBShapleyV1:
        if set(self.metrics) != set(ATTRIBUTED_METRICS):
            raise ValueError(f"attributed metrics must be exactly {list(ATTRIBUTED_METRICS)}")
        if any(
            metric.scenarios_attributed != self.common_valid_tokens
            for metric in self.metrics.values()
        ):
            raise ValueError("scenarios_attributed must equal common_valid_tokens")
        return self


class AEBExclusionV1(_Strict):
    scenario_token: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    exception_type: Optional[str]


class AEBExclusionsV1(_Strict):
    schema_version: Literal["aeb-exclusions/v1"]
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    cohort_size: NonNegativeInt
    common_valid_tokens: NonNegativeInt
    excluded: tuple[AEBExclusionV1, ...]

    @model_validator(mode="after")
    def validate_exclusion_count(self) -> AEBExclusionsV1:
        if len(self.excluded) != self.cohort_size - self.common_valid_tokens:
            raise ValueError(
                "excluded token count must equal cohort_size minus common_valid_tokens"
            )
        return self


SCHEMA_VERSIONS: tuple[str, ...] = (
    EVALUATION_SCHEMA_VERSION,
    INTERVALS_SCHEMA_VERSION,
    SHAPLEY_SCHEMA_VERSION,
    EXCLUSIONS_SCHEMA_VERSION,
)

DOCUMENT_MODELS: dict[str, type[BaseModel]] = {
    EVALUATION_SCHEMA_VERSION: AEBEvaluationV1,
    INTERVALS_SCHEMA_VERSION: AEBIntervalsV1,
    SHAPLEY_SCHEMA_VERSION: AEBShapleyV1,
    EXCLUSIONS_SCHEMA_VERSION: AEBExclusionsV1,
}


def write_document(document: BaseModel, path: Path) -> None:
    """Revalidate and write one registered document as stable UTF-8/LF JSON."""

    model = DOCUMENT_MODELS.get(str(getattr(document, "schema_version", "")))
    if model is None or type(document) is not model:
        raise ValueError(f"unsupported published document model {type(document).__name__!r}")
    payload = model.model_validate(document.model_dump(mode="json")).model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
