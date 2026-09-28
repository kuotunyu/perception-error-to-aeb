"""Contracts for the collision-course gate of AEB policy v2.

Policy v2 (`docs/studies/aeb-policy-v2/analysis-plan.md`, section 4.2) lets the
AEB select a visible observed body only when two conditions hold: the released
threat assessment predicts an overlap with the ego's corridor, and the body's
centre is not behind the ego's along the ego's heading. Everything else the
controller does is policy v1's.

The cases are those of the policy v1 known-limitation tests
(`tests/unit/aeb/test_policy_v1_known_limitations.py`), with the same ego
(4.8 x 2.0 m) and the same car (4.5 x 1.9 m), and the expectation inverted
where the gate is meant to change the outcome. The parked-beside and behind
cases run at 15 m/s as there. One case pins where the gate still admits a body
beside the path: a lateral velocity toward the path, which the analysis plan
documents as the gate's leak.

The gate is a pure function of one assessed frame. The assessment itself is
not touched: the cohort prefilter and the replay read it as released, so the
last test holds the known-limitation cases to their released values exactly.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Optional

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aebrisk.aeb import threat
from aebrisk.aeb.state_machine import policy_for
from aebrisk.aeb.threat import EgoKinematicState, ThreatAssessment
from aebrisk.observation.models import TrackState

EGO_SIZE_LW_M = (4.8, 2.0)
CAR_SIZE_LW_M = (4.5, 1.9)


def ego(
    speed_mps: float,
    center_xy_m: tuple[float, float] = (0.0, 0.0),
    yaw_rad: float = 0.0,
) -> EgoKinematicState:
    return EgoKinematicState(
        center_xy_m=center_xy_m,
        yaw_rad=yaw_rad,
        size_lw_m=EGO_SIZE_LW_M,
        speed_mps=speed_mps,
        velocity_xy_mps=(speed_mps * math.cos(yaw_rad), speed_mps * math.sin(yaw_rad)),
        acceleration_mps2=0.0,
    )


def car(
    track_id: str,
    center_xy_m: tuple[float, float],
    velocity_xy_mps: tuple[float, float] = (0.0, 0.0),
    yaw_rad: float = 0.0,
) -> TrackState:
    return TrackState(
        track_id=track_id,
        category="vehicle",
        center_xy_m=center_xy_m,
        yaw_rad=yaw_rad,
        size_lw_m=CAR_SIZE_LW_M,
        velocity_xy_mps=velocity_xy_mps,
        visible=True,
        source_timestamp_us=1_600_000_000_000_000,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )


def gate() -> Mapping[str, Any]:
    """The committed policy v2's target selection, as a run reads it."""

    selection: Mapping[str, Any] = policy_for("v2")["target_selection"]
    return selection


def assessed(
    ego_state: EgoKinematicState, tracks: tuple[TrackState, ...]
) -> tuple[ThreatAssessment, ...]:
    return tuple(threat.assess_threat(ego_state, track) for track in tracks)


def candidates(
    ego_state: EgoKinematicState, tracks: tuple[TrackState, ...]
) -> tuple[ThreatAssessment, ...]:
    """Assess one frame and gate it with the committed policy v2."""

    return threat.collision_course_candidates(
        ego_state, tracks, assessed(ego_state, tracks), gate()
    )


# --------------------------------------------------------------------------
# What the gate removes
# --------------------------------------------------------------------------


def test_a_parked_car_beside_the_path_is_not_a_candidate() -> None:
    """Policy v1 brakes fully for this car; it is ahead, but no overlap is predicted."""

    parked = car("parked-beside", (20.0, 3.5))
    [assessment] = assessed(ego(15.0), (parked,))

    assert assessment.predicted_overlap is False
    assert threat.signed_longitudinal_separation_m(ego(15.0), parked) == 20.0
    assert candidates(ego(15.0), (parked,)) == ()


def test_a_slower_car_behind_is_not_a_candidate() -> None:
    """Policy v1 brakes fully for this car, because its gap is an absolute value."""

    follower = car("behind", (-7.0, 0.0), velocity_xy_mps=(10.0, 0.0))
    [assessment] = assessed(ego(15.0), (follower,))
    separation = threat.signed_longitudinal_separation_m(ego(15.0), follower)

    assert assessment.predicted_overlap is False
    assert separation == -7.0
    # The released gap is this projection with its sign dropped.
    assert threat._longitudinal_gap(ego(15.0), follower) == abs(separation) - 4.8 / 2 - 4.5 / 2
    assert candidates(ego(15.0), (follower,)) == ()


def test_a_faster_car_closing_from_behind_is_not_a_candidate() -> None:
    """A predicted overlap alone does not admit a body: the rear is not the AEB's to guard."""

    closing = car("closing-from-behind", (-10.0, 0.0), velocity_xy_mps=(20.0, 0.0))
    [assessment] = assessed(ego(15.0), (closing,))

    assert assessment.predicted_overlap is True
    assert assessment.ttc_s == pytest.approx(1.0)
    assert threat.signed_longitudinal_separation_m(ego(15.0), closing) < 0.0
    assert candidates(ego(15.0), (closing,)) == ()


# --------------------------------------------------------------------------
# What the gate keeps
# --------------------------------------------------------------------------


def test_a_stopped_lead_in_the_path_is_a_candidate() -> None:
    """The in-path lead is kept, and the parked car that masked it under v1 is not."""

    lead = car("in-path-lead", (25.0, 0.0))
    parked = car("parked-beside", (18.0, 4.0))
    lead_assessment, parked_assessment = assessed(ego(10.0), (lead, parked))

    assert lead_assessment.predicted_overlap is True
    assert lead_assessment.ttc_s == pytest.approx(2.0)
    assert parked_assessment.predicted_overlap is False
    assert candidates(ego(10.0), (lead,)) == (lead_assessment,)
    assert candidates(ego(10.0), (lead, parked)) == (lead_assessment,)


def test_a_body_exactly_abreast_is_a_candidate() -> None:
    """Zero separation is in: the not-behind boundary is inclusive, as pre-registered."""

    abreast = car("abreast", (0.0, 3.0), velocity_xy_mps=(15.0, -2.0))
    [assessment] = assessed(ego(15.0), (abreast,))

    assert assessment.predicted_overlap is True
    assert assessment.ttc_s == pytest.approx(0.3)
    assert threat.signed_longitudinal_separation_m(ego(15.0), abreast) == 0.0
    assert candidates(ego(15.0), (abreast,)) == (assessment,)


def test_a_cut_in_ahead_is_a_candidate() -> None:
    """A slower car ahead moving into the path is predicted to overlap, and is ahead."""

    cutting_in = car("cut-in", (15.0, 3.5), velocity_xy_mps=(10.0, -1.5))
    [assessment] = assessed(ego(15.0), (cutting_in,))

    assert assessment.predicted_overlap is True
    assert assessment.ttc_s == pytest.approx(2.0)
    assert threat.signed_longitudinal_separation_m(ego(15.0), cutting_in) == 15.0
    assert candidates(ego(15.0), (cutting_in,)) == (assessment,)


def test_a_parked_car_beside_the_path_with_a_lateral_velocity_toward_it_is_a_candidate() -> None:
    """The documented leak: an observed velocity toward the path admits a parked car.

    The rollout is constant velocity, so a car that is not moving but is observed
    drifting toward the path at 2 m/s is predicted to reach the corridor 1.0 s
    ahead, which is below the full-braking time to collision.
    """

    drifting = car("parked-beside", (20.0, 3.5), velocity_xy_mps=(0.0, -2.0))
    [assessment] = assessed(ego(15.0), (drifting,))

    assert assessment.predicted_overlap is True
    assert assessment.ttc_s == pytest.approx(1.0)
    assert candidates(ego(15.0), (drifting,)) == (assessment,)


# --------------------------------------------------------------------------
# Policy v1, and inputs the gate refuses
# --------------------------------------------------------------------------


def test_no_target_selection_returns_the_same_tuple() -> None:
    """Policy v1 declares no gate, so every visible track stays a candidate, untouched."""

    tracks = (
        car("parked-beside", (20.0, 3.5)),
        car("behind", (-7.0, 0.0), velocity_xy_mps=(10.0, 0.0)),
        car("in-path-lead", (25.0, 0.0)),
    )
    threats = assessed(ego(15.0), tracks)
    selection: Optional[Mapping[str, Any]] = policy_for("v1").get("target_selection")

    assert selection is None
    assert threat.collision_course_candidates(ego(15.0), tracks, threats, selection) is threats


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize(
    ("track_ids", "threat_ids", "message"),
    [
        (("lead", "parked"), ("lead",), "one threat assessment per track"),
        (("lead",), ("lead", "parked"), "one threat assessment per track"),
        (("lead", "parked"), ("parked", "lead"), "describes track 'parked', not 'lead'"),
    ],
    ids=["a-track-without-its-assessment", "an-assessment-without-its-track", "swapped"],
)
def test_misaligned_tracks_and_threats_are_refused(
    version: str, track_ids: tuple[str, ...], threat_ids: tuple[str, ...], message: str
) -> None:
    """Threat i is judged by track i's position, so the two must describe the same body."""

    bodies = {"lead": car("lead", (25.0, 0.0)), "parked": car("parked", (18.0, 4.0))}
    tracks = tuple(bodies[track_id] for track_id in track_ids)
    threats = tuple(threat.assess_threat(ego(10.0), bodies[track_id]) for track_id in threat_ids)

    with pytest.raises(ValueError, match=message):
        threat.collision_course_candidates(
            ego(10.0), tracks, threats, policy_for(version).get("target_selection")
        )


# --------------------------------------------------------------------------
# Invariance
# --------------------------------------------------------------------------

#: A body of every kind the gate decides on, seen from an ego at 15 m/s. None is
#: within rounding of a boundary, so moving the scene cannot flip a decision.
FRAME_SCENE = (
    car("parked-beside", (20.0, 3.5)),
    car("drifting-toward-the-path", (20.0, 3.5), (0.0, -2.0)),
    car("slower-behind", (-7.0, 0.0), (10.0, 0.0)),
    car("closing-from-behind", (-10.0, 0.0), (20.0, 0.0)),
    car("stopped-lead", (25.0, 0.0)),
    car("cut-in", (15.0, 3.5), (10.0, -1.5)),
)


@settings(deadline=None, derandomize=True)
@given(
    rotation=st.floats(min_value=-math.pi, max_value=math.pi),
    offset_x=st.floats(min_value=-5.0e6, max_value=5.0e6),
    offset_y=st.floats(min_value=-5.0e6, max_value=5.0e6),
)
def test_the_gate_does_not_depend_on_the_world_frame(
    rotation: float, offset_x: float, offset_y: float
) -> None:
    """Rotating and translating the whole scene keeps the same candidates.

    nuPlan's map-frame coordinates run to millions of metres, so a gate that
    depended on the origin or on the heading's quadrant would decide differently
    in different parts of the same city.
    """

    cos, sin = math.cos(rotation), math.sin(rotation)

    def turn(vector: tuple[float, float]) -> tuple[float, float]:
        return (vector[0] * cos - vector[1] * sin, vector[0] * sin + vector[1] * cos)

    def move(point: tuple[float, float]) -> tuple[float, float]:
        turned = turn(point)
        return (turned[0] + offset_x, turned[1] + offset_y)

    plain_ego = ego(15.0)
    moved_ego = ego(15.0, center_xy_m=move((0.0, 0.0)), yaw_rad=rotation)
    moved_scene = tuple(
        car(
            track.track_id,
            move(track.center_xy_m),
            turn(track.velocity_xy_mps),
            yaw_rad=track.yaw_rad + rotation,
        )
        for track in FRAME_SCENE
    )

    plain = [assessment.track_id for assessment in candidates(plain_ego, FRAME_SCENE)]
    moved = [assessment.track_id for assessment in candidates(moved_ego, moved_scene)]

    assert plain == ["drifting-toward-the-path", "stopped-lead", "cut-in"]
    assert moved == plain
    for plain_track, moved_track in zip(FRAME_SCENE, moved_scene):
        assert threat.signed_longitudinal_separation_m(moved_ego, moved_track) == pytest.approx(
            threat.signed_longitudinal_separation_m(plain_ego, plain_track), abs=1e-6
        )


# --------------------------------------------------------------------------
# The released assessment
# --------------------------------------------------------------------------


def test_the_released_assessments_of_the_known_limitation_cases_are_unchanged() -> None:
    """Every case of the policy v1 known-limitation tests keeps its assessment to the bit.

    The gate reads the assessment and does not change it. The values are those
    the released controller acted on; the hand derivation of each deceleration
    is in the policy v1 known-limitation tests.
    """

    released = [
        (
            15.0,
            car("parked-beside", (20.0, 3.5)),
            ThreatAssessment("parked-beside", None, 7.328990228013029, False, 1.0499999999999998),
        ),
        (
            15.0,
            car("behind", (-7.0, 0.0), velocity_xy_mps=(10.0, 0.0)),
            ThreatAssessment("behind", None, 5.319148936170214, False, 1.85),
        ),
        (
            10.0,
            car("in-path-lead", (25.0, 0.0)),
            ThreatAssessment("in-path-lead", 2.0, 2.457002457002457, True, 0.0),
        ),
        (
            10.0,
            car("parked-beside", (18.0, 4.0)),
            ThreatAssessment("parked-beside", None, 3.7453183520599254, False, 1.5499999999999998),
        ),
        (
            5.0,
            car("in-path-lead", (13.0, 0.0)),
            ThreatAssessment("in-path-lead", 1.6, 1.4970059880239521, True, 0.0),
        ),
        (
            5.0,
            car("parked-beside", (11.5, 4.0)),
            ThreatAssessment("parked-beside", None, 1.8248175182481752, False, 1.5499999999999998),
        ),
    ]

    for speed_mps, track, expected in released:
        assert threat.assess_threat(ego(speed_mps), track) == expected
