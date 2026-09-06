"""One scenario's AEB outcome, and the vocabulary published claims may use.

Every number this project reports is an aggregate over these records, so this
model is where a physically impossible outcome is refused rather than averaged.
The constraints are not defensive programming: each one names a way a simulator
bug would otherwise reach a published figure looking like a measurement.
"""

from __future__ import annotations

import math
from typing import Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The four scenario families the protocol freezes. Adding one is a new study,
#: not a new option, because the cohort is stratified across exactly these.
#:
#: The type and the tuple are one declaration rather than two: written twice
#: they could disagree, and the disagreement would be a family the validator
#: accepted and the type system did not, or the reverse.
ScenarioFamily = Literal[
    "lead_or_stopping",
    "cut_in_or_crossing",
    "pedestrian_or_crosswalk",
    "bicycle_or_vru",
]
SCENARIO_FAMILIES: tuple[str, ...] = get_args(ScenarioFamily)

#: Shared with the other two repositories. Three projects labelling evidence
#: differently could not be read side by side.
ALLOWED_EVIDENCE_TYPES: tuple[str, ...] = ("observed", "derived", "synthetic", "illustrative")
ALLOWED_STATUSES: tuple[str, ...] = ("draft", "verified", "rejected", "superseded")

#: The protocol fixes the actuator envelope at [-6.0, 2.0] m/s2. A record that
#: reports harder braking than the actuator can produce did not come from this
#: AEB, so it is refused rather than quietly widening the published limit.
MAX_DECELERATION_MPS2 = 6.0


def _require_finite(value: Optional[float], name: str) -> Optional[float]:
    if value is not None and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


class AEBScenarioResultV1(BaseModel):
    """What one (scenario, configuration, replicate) simulation produced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aeb-scenario-result/v1"]
    scenario_token: str = Field(min_length=1)
    family: ScenarioFamily
    configuration_id: str = Field(min_length=1)
    replicate: int = Field(ge=0)

    valid: bool
    invalid_reason: Optional[str] = None

    #: Collisions the ego is at fault for. A contact it could not have avoided —
    #: it was stopped, or it was struck from behind by something faster — is not
    #: one of these. The agents replay the recording and cannot react to an ego
    #: that braked, so counting those would report braking as harmful; see
    #: `simulation/step_loop.py` for the measurement that showed it.
    collision_vru: int = Field(ge=0)
    collision_vehicle: int = Field(ge=0)
    collision_object: int = Field(ge=0)
    collision_energy: float = Field(ge=0.0)

    #: The contacts excluded above, counted rather than dropped.
    contacts_not_at_fault: int = Field(ge=0)

    #: ``None`` where no object was ever on a collision course. Zero would mean
    #: a collision was imminent, which is the opposite claim.
    min_ttc_s: Optional[float] = None
    min_clearance_m: float = Field(ge=0.0)

    missed_interventions: int = Field(ge=0)
    false_interventions: int = Field(ge=0)

    #: One SIGNED difference of brake onsets per matched intervention: this run's
    #: onset minus the oracle's. Negative means it braked EARLIER than the oracle
    #: did, which is not a failure and must not be folded away — an absolute
    #: value would report an early intervention as a late one, and a clamp would
    #: report it as simultaneous.
    matched_delay_s: tuple[float, ...] = ()

    #: How far the ego had travelled the FIRST time it came to rest, or ``None``
    #: where it never did — which is not a distance of zero. The first time
    #: rather than the last, because an AEB that stops the vehicle and then
    #: releases still stopped it, and a distance read off the final speed would
    #: report that run as never having stopped.
    stop_distance_m: Optional[float] = None
    max_deceleration_mps2: float = Field(ge=0.0, le=MAX_DECELERATION_MPS2)
    max_abs_jerk_mps3: float = Field(ge=0.0)
    intervention_duration_s: float = Field(ge=0.0)

    @field_validator(
        "collision_energy",
        "min_ttc_s",
        "min_clearance_m",
        "stop_distance_m",
        "max_deceleration_mps2",
        "max_abs_jerk_mps3",
        "intervention_duration_s",
    )
    @classmethod
    def validate_finite(cls, value: Optional[float]) -> Optional[float]:
        """Refuse NaN and infinity, which would poison every statistic downstream."""

        return _require_finite(value, "measurement")

    @field_validator("matched_delay_s")
    @classmethod
    def validate_delays(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        """A delay is finite. Its SIGN is the measurement and is left alone.

        This validator required non-negative delays until the runner began
        producing them, on the reasoning that a negative delay reverses cause
        and effect. It does not: the delay is the difference between two onsets,
        and a corrupted run that braked before the oracle did is early rather
        than impossible. Refusing it would have discarded exactly the runs where
        perception error made the AEB jumpy.
        """

        for delay in value:
            if not math.isfinite(delay):
                raise ValueError("matched_delay_s entries must be finite")
        return value

    @model_validator(mode="after")
    def validate_validity_is_explained(self) -> AEBScenarioResultV1:
        """Tie the validity flag to its reason in both directions.

        An invalid run without a reason is indistinguishable from one that was
        quietly dropped, and a valid run carrying a reason cannot be counted
        either way. Both are refused so the cohort's denominator stays honest.
        """

        if self.valid and self.invalid_reason is not None:
            raise ValueError("a valid result must not carry an invalid_reason")
        if not self.valid and not self.invalid_reason:
            raise ValueError("an invalid result must record its invalid_reason")
        return self
