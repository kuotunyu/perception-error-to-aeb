"""Aggregating one configuration's results into numbers a reader may quote.

Two rules run through all of this, and both are about what a reader is allowed
to conclude from a single figure.

EVERY RATE CARRIES ITS NUMERATOR, ITS DENOMINATOR AND THE COHORT IT WAS TAKEN
OVER. "Three collisions per thousand scenarios" says nothing on its own: over
forty scenarios it is one collision and noise, over four thousand it is a
finding. A rate that hid its denominator would let the same number mean either,
and the number is the part that gets quoted.

AN UNDEFINED VALUE IS ``None``, NEVER ZERO. A cohort with no stops has no mean
stopping distance, and reporting zero would say the vehicle stopped instantly.
A minimum time to collision of ``None`` means nothing was ever on a collision
course, which is the opposite of a minimum of zero.

The per-distance rate is refused below 100 km of simulated driving. Collisions
per 100 km computed from 4 km is a number with two significant figures of noise
and an authoritative unit, which is worse than no number, because a reader
cannot tell it apart from a measurement.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from aebrisk.artifacts.results import AEBScenarioResultV1

SECONDS_PER_HOUR = 3600.0
METRES_PER_100KM = 100_000.0

#: Below this, a per-distance rate is not reported at all.
MINIMUM_DISTANCE_FOR_RATE_M = 100_000.0


@dataclass(frozen=True)
class Rate:
    """A rate that cannot be quoted without the cohort it came from."""

    numerator: float
    denominator: float
    per: float
    value: Optional[float]


def _rate(numerator: float, denominator: float, per: float) -> Rate:
    return Rate(
        numerator=numerator,
        denominator=denominator,
        per=per,
        value=None if denominator <= 0.0 else per * numerator / denominator,
    )


@dataclass(frozen=True)
class SafetySummary:
    """What one configuration produced over the common cohort."""

    configuration_id: str
    cohort_size: int
    scenarios: int

    collisions_vru: int
    collisions_vehicle: int
    collisions_object: int
    collision_energy_total: float

    min_ttc_s: Optional[float]
    min_clearance_m: Optional[float]
    mean_stop_distance_m: Optional[float]

    max_deceleration_mps2: Optional[float]
    max_abs_jerk_mps3: Optional[float]
    mean_intervention_duration_s: Optional[float]

    missed_interventions: int
    false_interventions: int

    collisions_per_1000_scenarios: Rate
    collisions_per_hour: Rate
    collisions_per_100km: Optional[Rate]


def _require_positive(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return float(value)


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def summarize_configuration(
    results: tuple[AEBScenarioResultV1, ...],
    *,
    cohort: tuple[str, ...],
    simulated_seconds: float,
    simulated_metres: Optional[float] = None,
) -> SafetySummary:
    """Aggregate one configuration's valid results over the common cohort."""

    simulated_seconds = _require_positive(simulated_seconds, "simulated_seconds")
    if simulated_metres is not None:
        if not isinstance(simulated_metres, (int, float)) or isinstance(simulated_metres, bool):
            raise ValueError("simulated_metres must be a number")
        if not math.isfinite(simulated_metres) or simulated_metres < 0.0:
            raise ValueError(
                f"simulated_metres must be finite and non-negative, got {simulated_metres!r}"
            )

    identifiers = {record.configuration_id for record in results}
    if len(identifiers) > 1:
        raise ValueError(
            f"results span several configuration_id values {sorted(identifiers)}; a "
            "summary describes one cell, and mixing two would report neither"
        )

    included = tuple(
        record for record in results if record.scenario_token in set(cohort) and record.valid
    )

    collisions = sum(
        record.collision_vru + record.collision_vehicle + record.collision_object
        for record in included
    )
    times_to_collision = [record.min_ttc_s for record in included if record.min_ttc_s is not None]
    stops = [record.stop_distance_m for record in included if record.stop_distance_m is not None]

    per_distance: Optional[Rate] = None
    if simulated_metres is not None and simulated_metres >= MINIMUM_DISTANCE_FOR_RATE_M:
        per_distance = _rate(collisions, simulated_metres, METRES_PER_100KM)

    return SafetySummary(
        configuration_id=next(iter(identifiers), ""),
        cohort_size=len(cohort),
        scenarios=len(included),
        collisions_vru=sum(record.collision_vru for record in included),
        collisions_vehicle=sum(record.collision_vehicle for record in included),
        collisions_object=sum(record.collision_object for record in included),
        collision_energy_total=sum(record.collision_energy for record in included),
        min_ttc_s=min(times_to_collision) if times_to_collision else None,
        min_clearance_m=min((record.min_clearance_m for record in included), default=None),
        mean_stop_distance_m=_mean(stops),
        max_deceleration_mps2=max(
            (record.max_deceleration_mps2 for record in included), default=None
        ),
        max_abs_jerk_mps3=max((record.max_abs_jerk_mps3 for record in included), default=None),
        mean_intervention_duration_s=_mean([record.intervention_duration_s for record in included]),
        missed_interventions=sum(record.missed_interventions for record in included),
        false_interventions=sum(record.false_interventions for record in included),
        collisions_per_1000_scenarios=_rate(collisions, len(included), 1000.0),
        collisions_per_hour=_rate(collisions, simulated_seconds, SECONDS_PER_HOUR),
        collisions_per_100km=per_distance,
    )
