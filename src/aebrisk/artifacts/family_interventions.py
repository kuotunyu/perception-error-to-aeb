"""Strict contract for observed intervention events stratified by scenario family."""

from __future__ import annotations

from typing import Literal, Optional, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aebrisk.artifacts.results import SCENARIO_FAMILIES, ScenarioFamily
from aebrisk.attribution.factorial import coalition_configuration_id
from aebrisk.attribution.shapley import CHANNELS

FamilyInterventionConfigurationId = Literal[
    "oracle_aeb",
    "coalition-none",
    "coalition-dropout+localization_shape+latency+track_instability",
]
FAMILY_INTERVENTIONS_SCHEMA_VERSION = "aeb-family-interventions/v1"
FULL_MEDIUM_COALITION_ID = cast(
    FamilyInterventionConfigurationId, coalition_configuration_id(frozenset(CHANNELS))
)
FAMILY_INTERVENTION_CONFIGURATION_IDS: tuple[FamilyInterventionConfigurationId, ...] = (
    "oracle_aeb",
    "coalition-none",
    FULL_MEDIUM_COALITION_ID,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FamilyInterventionRowV1(_Strict):
    """Observed intervention event counts for one family and configuration."""

    family: ScenarioFamily
    configuration_id: FamilyInterventionConfigurationId
    valid_tokens: int = Field(ge=0, strict=True)
    replicate_count: int = Field(ge=1, strict=True)
    scenario_replicates: int = Field(ge=0, strict=True)
    missed_interventions: int = Field(ge=0, strict=True)
    false_interventions: int = Field(ge=0, strict=True)
    missed_per_1000_scenario_replicates: Optional[float] = Field(
        default=None, ge=0.0, allow_inf_nan=False
    )
    false_per_1000_scenario_replicates: Optional[float] = Field(
        default=None, ge=0.0, allow_inf_nan=False
    )

    @model_validator(mode="after")
    def validate_denominator_and_rates(self) -> FamilyInterventionRowV1:
        expected_denominator = self.valid_tokens * self.replicate_count
        if self.scenario_replicates != expected_denominator:
            raise ValueError("scenario_replicates must equal valid_tokens times replicate_count")
        rates = (
            (self.missed_interventions, self.missed_per_1000_scenario_replicates),
            (self.false_interventions, self.false_per_1000_scenario_replicates),
        )
        if expected_denominator == 0:
            if any(count != 0 or rate is not None for count, rate in rates):
                raise ValueError("an empty family requires zero events and null rates")
            return self
        for count, rate in rates:
            expected_rate = 1000.0 * count / expected_denominator
            if rate != expected_rate:
                raise ValueError("intervention rate does not match its event count and denominator")
        return self


class FamilyInterventionsV1(_Strict):
    """The complete four-family by three-configuration observed summary."""

    schema_version: Literal["aeb-family-interventions/v1"]
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cohort_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    common_valid_tokens: int = Field(ge=0, strict=True)
    evaluation_per_family: int = Field(ge=1, strict=True)
    rows: tuple[FamilyInterventionRowV1, ...]

    @model_validator(mode="after")
    def validate_complete_inventory(self) -> FamilyInterventionsV1:
        identities = [(row.family, row.configuration_id) for row in self.rows]
        if len(set(identities)) != len(identities):
            raise ValueError("family intervention rows contain a duplicate identity")
        expected = [
            (family, configuration)
            for family in SCENARIO_FAMILIES
            for configuration in FAMILY_INTERVENTION_CONFIGURATION_IDS
        ]
        if identities != expected:
            raise ValueError("family intervention rows must be exactly the fixed ordered inventory")
        valid_by_family: dict[str, int] = {}
        for row in self.rows:
            prior = valid_by_family.setdefault(row.family, row.valid_tokens)
            if row.valid_tokens != prior:
                raise ValueError("valid token count must be constant across a family")
        if sum(valid_by_family.values()) != self.common_valid_tokens:
            raise ValueError("family valid token counts must sum to common_valid_tokens")
        return self
