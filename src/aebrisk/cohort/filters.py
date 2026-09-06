"""Decide which recorded scenarios are worth simulating, before any AEB exists.

Everything the prefilter looks at is a property of the recording: how fast the
ego was travelling, whether anything crossed into its path, and how close that
came. It never looks at what an AEB did, because a cohort chosen on the outcome
answers a question nobody asked — it would be selected for the very effect the
study is trying to measure.

The corridor the candidate reports on is the ego footprint swept along the
expert route over the four-second horizon and widened laterally. It exists only
to select scenarios. Nothing downstream scores with it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

#: The four strata the cohort is balanced across. Identical to the results
#: schema's, and a test pins that, because a cohort and a result set stratified
#: differently cannot be joined.
SCENARIO_FAMILIES: tuple[str, ...] = (
    "lead_or_stopping",
    "cut_in_or_crossing",
    "pedestrian_or_crosswalk",
    "bicycle_or_vru",
)

#: The nuPlan scenario types in each family, frozen by protocol v2.
#:
#: The pinned devkit ships no canonical list of scenario types: they live in
#: each log database's `scenario_tag` table rather than in the library. So this
#: mapping was checked against the data instead, over every mini database on
#: 2026-09-06, and every type below appears in
#: `configs/nuplan_scenario_vocabulary.yaml`. Protocol v1 pinned five names that
#: nuPlan tags nothing with; `tests/contract/test_scenario_families.py` now
#: refuses that, and also refuses this mapping drifting from the protocol file
#: whose bytes are the published hash.
#:
#: The tag decides which family a scenario is counted in. Whether a threat is
#: present is decided by the corridor and TTC rules below, on oracle world
#: state, never by the tag.
FAMILY_TYPES: dict[str, tuple[str, ...]] = {
    "lead_or_stopping": (
        "following_lane_with_lead",
        "following_lane_with_slow_lead",
        "stopping_with_lead",
        "stopping_at_stop_sign_with_lead",
        "stopping_at_traffic_light_with_lead",
    ),
    # nuPlan tags no "cut in" and no "crossed by vehicle"; it tags the lane
    # change itself, and the unprotected turns where crossing traffic conflicts.
    "cut_in_or_crossing": (
        "changing_lane",
        "changing_lane_to_left",
        "changing_lane_to_right",
        "starting_unprotected_cross_turn",
        "starting_unprotected_noncross_turn",
    ),
    "pedestrian_or_crosswalk": (
        "waiting_for_pedestrian_to_cross",
        "near_pedestrian_on_crosswalk",
        "near_pedestrian_on_crosswalk_with_ego",
        "behind_pedestrian_on_driveable",
    ),
    # `behind_bike` is the only bicycle tag nuPlan has. The family is kept with
    # one type rather than dropped: a thin family is reported with its count.
    "bicycle_or_vru": ("behind_bike",),
}

_TYPE_TO_FAMILY: dict[str, str] = {
    scenario_type: family
    for family, scenario_types in FAMILY_TYPES.items()
    for scenario_type in scenario_types
}

#: An ego slower than this cannot brake to avoid anything, so the scenario would
#: measure the error channels against a decision that was never available.
MINIMUM_INITIAL_EGO_SPEED_MPS = 2.0

#: Strictly less than. Six seconds of headway is not a near miss, and the plan
#: fixes the bound rather than leaving it to whoever writes the comparison.
MAXIMUM_ORACLE_MIN_TTC_S = 6.0

#: The official nuPlan splits a candidate can be recorded in. `mini` is here for
#: the smoke cohort only: it is neither of the two halves the study reports on,
#: and `OFFICIAL_SPLIT_FOR` keeps it out of both, so a mini recording can never
#: reach development or locked evaluation however the candidates are assembled.
OFFICIAL_SPLITS: tuple[str, ...] = ("train", "val", "mini")


@dataclass(frozen=True)
class CorridorCandidate:
    """One recorded scenario and the evidence that decides whether to simulate it.

    Every field is measured from the recording. There is deliberately nowhere to
    put an AEB outcome: by the time a field like ``collided`` exists here,
    somebody has already written the code that would select on it.
    """

    scenario_token: str
    log_name: str
    scenario_type: str
    family: str
    official_split: str
    initial_ego_speed_mps: float
    oracle_enters_corridor_within_4s: bool
    oracle_min_ttc_within_4s: Optional[float]

    def __post_init__(self) -> None:
        if not self.scenario_token:
            raise ValueError("scenario_token must not be empty")
        if not self.log_name:
            raise ValueError("log_name must not be empty")
        if self.official_split not in OFFICIAL_SPLITS:
            raise ValueError(f"official_split must be one of {OFFICIAL_SPLITS}")
        if not math.isfinite(self.initial_ego_speed_mps) or self.initial_ego_speed_mps < 0.0:
            raise ValueError("initial_ego_speed_mps must be a finite, non-negative speed")
        if self.oracle_min_ttc_within_4s is not None:
            value = self.oracle_min_ttc_within_4s
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    "oracle_min_ttc_within_4s must be a finite, non-negative time or None"
                )


def family_of(scenario_type: str) -> str:
    """Return the family a nuPlan scenario type belongs to, or raise.

    Failing closed matters more here than convenience. A devkit release that
    adds a type would otherwise have those scenarios silently grouped into
    whichever family the fallback picked, and every per-family number would
    quietly describe a different population than the one it names.
    """

    return _TYPE_TO_FAMILY[scenario_type]


def types_in_family(family: str) -> tuple[str, ...]:
    """Return the scenario types that make up one family."""

    return FAMILY_TYPES[family]


def prefilter_refusal(candidate: CorridorCandidate) -> str:
    """Say WHY one recorded scenario is not worth simulating, or "" if it is.

    A reason rather than a bool, because the eligibility record written beside a
    frozen cohort has to say what was rejected and on which rule. "Fewer
    scenarios than the cap" is a sentence a reader can check; a column of
    `false` is not.
    """

    if _TYPE_TO_FAMILY.get(candidate.scenario_type) != candidate.family:
        return (
            f"type {candidate.scenario_type!r} does not belong to the {candidate.family!r} family"
        )
    if candidate.initial_ego_speed_mps < MINIMUM_INITIAL_EGO_SPEED_MPS:
        return (
            f"initial ego speed {candidate.initial_ego_speed_mps:.3f} m/s is below "
            f"{MINIMUM_INITIAL_EGO_SPEED_MPS} m/s, so no braking decision was available"
        )
    if not candidate.oracle_enters_corridor_within_4s:
        return "no observed object enters the ego corridor within 4 s"
    if candidate.oracle_min_ttc_within_4s is None:
        return "no observed object is on a collision course within 4 s"
    if candidate.oracle_min_ttc_within_4s >= MAXIMUM_ORACLE_MIN_TTC_S:
        return (
            f"minimum time to collision {candidate.oracle_min_ttc_within_4s:.3f} s is not "
            f"below {MAXIMUM_ORACLE_MIN_TTC_S} s, so the scenario is not a near miss"
        )
    return ""


def passes_prefilter(candidate: CorridorCandidate) -> bool:
    """Say whether one recorded scenario is worth the cost of simulating it."""

    return not prefilter_refusal(candidate)
