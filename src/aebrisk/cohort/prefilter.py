"""Measuring recorded scenarios against the eligibility rules, without reading them all.

The pinned families are enormous. In the mini split alone, 64 logs carry about
seventy-five thousand `pedestrian_or_crosswalk` scenarios; the validation split
has 1,381 logs, and the cohort needs a hundred per family. Measuring every
candidate would read millions of recordings in order to throw almost all of them
away.

So candidates are examined IN THE ORDER THE FREEZE WOULD TAKE THEM — the hash of
the protocol and the token, fixed before any scenario is looked at — and the walk
stops once the cap is full. That gives exactly the cohort that measuring
everything and then capping would give, because the freeze sorts by the same key
and keeps the same prefix. The equivalence is what makes the bounded walk
legitimate rather than merely convenient, and it holds only while the order is
decided without looking at the data.

WHAT IS MEASURED IS THE RECORDING, NOT A SIMULATION. The ego is where the log
put it, moving at the speed the log recorded, and its acceleration is taken as
zero: the question is whether the recording contains a threat, and using the
logged driver's braking would let the expert's avoidance — the very thing this
study withholds from its own controller — decide which scenarios are eligible.

A recording too short for the protocol's full duration is refused HERE, with the
reason the builder gave, because eligibility is a property of the recording and
a scenario simulated for less time is not the same experiment.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Optional

from aebrisk.aeb.threat import EgoKinematicState, assess_threat
from aebrisk.cohort.filters import CorridorCandidate, prefilter_refusal
from aebrisk.cohort.splits import selection_key
from aebrisk.nuplan_adapter.query_scenario import ScenarioReference, build_scenario
from aebrisk.nuplan_adapter.scenario import oracle_world_frame
from aebrisk.nuplan_adapter.simulation import (
    PROTOCOL_FREQUENCY_HZ,
    PROTOCOL_SCENARIO_DURATION_S,
)
from aebrisk.observation.models import WorldFrame

#: Named so a test can examine candidates without a log database.
BUILD_SCENARIO = build_scenario
ORACLE_FRAME = oracle_world_frame

#: How much of a recording decides eligibility. The rules are written about the
#: first four seconds because a threat that only appears at second twelve leaves
#: too little room for a difference between configurations to show.
CANDIDATE_HORIZON_S = 4.0

#: The ego's size when the recording does not carry a footprint. nuPlan puts one
#: on every ego state, so this is reached only by a fake in a test.
DEFAULT_EGO_SIZE_LW_M = (5.176, 2.297)


@dataclass(frozen=True)
class Eligibility:
    """One examined scenario, and the rule that accepted or refused it."""

    scenario_token: str
    log_name: str
    scenario_type: str
    family: str
    official_split: str
    accepted: bool
    reason: str
    initial_ego_speed_mps: Optional[float]
    oracle_enters_corridor_within_4s: Optional[bool]
    oracle_min_ttc_within_4s: Optional[float]


def _log_name(log_file: str) -> str:
    """The database's file name, which is how a manifest names a log."""

    return log_file.replace("\\", "/").rsplit("/", 1)[-1]


def _unreadable(
    reference: ScenarioReference, family: str, official_split: str, reason: str
) -> Eligibility:
    """A scenario the recording could not supply, named rather than skipped."""

    return Eligibility(
        scenario_token=reference.token,
        log_name=_log_name(reference.log_file),
        scenario_type=reference.scenario_type,
        family=family,
        official_split=official_split,
        accepted=False,
        reason=reason,
        initial_ego_speed_mps=None,
        oracle_enters_corridor_within_4s=None,
        oracle_min_ttc_within_4s=None,
    )


def _ego_state(frame: WorldFrame, size_lw_m: tuple[float, float]) -> EgoKinematicState:
    """The logged ego, as the threat model wants it.

    The acceleration is zero rather than the logged one, for the reason in the
    module docstring. The velocity is the logged speed along the logged heading,
    which is what a recording of a vehicle going forwards means by both.
    """

    return EgoKinematicState(
        center_xy_m=frame.ego_center_xy_m,
        yaw_rad=frame.ego_yaw_rad,
        size_lw_m=size_lw_m,
        speed_mps=frame.ego_speed_mps,
        velocity_xy_mps=(
            frame.ego_speed_mps * math.cos(frame.ego_yaw_rad),
            frame.ego_speed_mps * math.sin(frame.ego_yaw_rad),
        ),
        acceleration_mps2=0.0,
    )


def _ego_size(scenario: object) -> tuple[float, float]:
    footprint = getattr(scenario.get_ego_state_at_iteration(0), "car_footprint", None)  # type: ignore[attr-defined]
    if footprint is None:
        return DEFAULT_EGO_SIZE_LW_M
    return (float(footprint.length), float(footprint.width))


def measure(
    reference: ScenarioReference,
    family: str,
    official_split: str,
    *,
    horizon_s: float = CANDIDATE_HORIZON_S,
) -> Eligibility:
    """Read one recording far enough to decide whether it is worth simulating."""

    try:
        scenario = BUILD_SCENARIO(
            reference.log_file,
            reference.token,
            duration_s=PROTOCOL_SCENARIO_DURATION_S,
            frequency_hz=PROTOCOL_FREQUENCY_HZ,
        )
    except Exception as error:
        # A recording too short for the protocol, a rate the protocol does not
        # divide, or a database that will not open. All three are eligibility
        # facts about the recording, and each is named rather than counted.
        return _unreadable(reference, family, official_split, str(error))

    steps = min(
        int(scenario.get_number_of_iterations()),
        round(horizon_s * PROTOCOL_FREQUENCY_HZ),
    )
    size_lw_m = _ego_size(scenario)

    enters = False
    min_ttc: Optional[float] = None
    initial_speed = 0.0
    for iteration in range(steps):
        frame = ORACLE_FRAME(scenario, iteration)
        if iteration == 0:
            initial_speed = frame.ego_speed_mps
        ego = _ego_state(frame, size_lw_m)
        for track in frame.tracks:
            threat = assess_threat(ego, track)
            enters = enters or threat.predicted_overlap
            if threat.ttc_s is not None:
                min_ttc = threat.ttc_s if min_ttc is None else min(min_ttc, threat.ttc_s)

    candidate = CorridorCandidate(
        scenario_token=reference.token,
        log_name=_log_name(reference.log_file),
        scenario_type=reference.scenario_type,
        family=family,
        official_split=official_split,
        initial_ego_speed_mps=initial_speed,
        oracle_enters_corridor_within_4s=enters,
        oracle_min_ttc_within_4s=min_ttc,
    )
    refusal = prefilter_refusal(candidate)
    return Eligibility(
        scenario_token=candidate.scenario_token,
        log_name=candidate.log_name,
        scenario_type=candidate.scenario_type,
        family=candidate.family,
        official_split=candidate.official_split,
        accepted=not refusal,
        reason=refusal,
        initial_ego_speed_mps=candidate.initial_ego_speed_mps,
        oracle_enters_corridor_within_4s=candidate.oracle_enters_corridor_within_4s,
        oracle_min_ttc_within_4s=candidate.oracle_min_ttc_within_4s,
    )


def candidate_of(eligibility: Eligibility) -> CorridorCandidate:
    """Rebuild the candidate an accepted eligibility record describes."""

    if not eligibility.accepted:
        raise ValueError(
            f"{eligibility.scenario_token!r} was refused ({eligibility.reason}); only an "
            "accepted scenario is a candidate, and freezing a refused one would put an "
            "unmeasurable recording in the cohort"
        )
    return CorridorCandidate(
        scenario_token=eligibility.scenario_token,
        log_name=eligibility.log_name,
        scenario_type=eligibility.scenario_type,
        family=eligibility.family,
        official_split=eligibility.official_split,
        initial_ego_speed_mps=float(eligibility.initial_ego_speed_mps or 0.0),
        oracle_enters_corridor_within_4s=bool(eligibility.oracle_enters_corridor_within_4s),
        oracle_min_ttc_within_4s=eligibility.oracle_min_ttc_within_4s,
    )


def examine_until_capped(
    references: Iterable[ScenarioReference],
    family: str,
    official_split: str,
    *,
    protocol_hash: str,
    cap: int,
) -> tuple[Eligibility, ...]:
    """Examine one family's scenarios in freeze order, stopping once the cap is full.

    Every scenario examined comes back, accepted or refused, in the order it was
    examined. That order is the freeze's own, so the accepted ones are exactly
    the cohort a full sweep would have produced, and the refused ones are the
    evidence for why the accepted ones are the ones they are.
    """

    if cap < 1:
        raise ValueError(f"cap must be at least one, got {cap}")

    ordered: Sequence[ScenarioReference] = sorted(
        references, key=lambda reference: selection_key(protocol_hash, reference.token)
    )
    examined: list[Eligibility] = []
    accepted = 0
    index = 0
    # A `while` rather than a `for` with a `break`: CPython 3.9 attributes no
    # line number to a bare `break`, so the arc out of the loop is untraceable
    # and this project's 100 percent branch gate could never pass. The early
    # exit is the whole point of the walk, so it cannot be dropped instead.
    while accepted < cap and index < len(ordered):
        eligibility = measure(ordered[index], family, official_split)
        examined.append(eligibility)
        accepted += 1 if eligibility.accepted else 0
        index += 1
    return tuple(examined)
