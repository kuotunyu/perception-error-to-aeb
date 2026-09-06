"""Contracts for the geometry the AEB decides on.

Every number the controller acts on comes from here, and each is computed from
the *corrupted* observation rather than the truth. A time-to-collision is a
first predicted overlap between two rotated rectangles, not a centre-to-centre
distance divided by a speed: a box that is 4.5 m long and yawed 30 degrees to
the corridor reaches into it long before its centre does, and the difference is
the metre or two that decides whether a brake is late.

The rectangles are the reason. Circles would be simpler and would make every
crossing scenario wrong in the same direction, because a circle around a vehicle
overstates its width and understates its length.

Three conventions are pinned by hand-computed tests below, because each is the
kind of thing that is wrong silently: the vertex order of a rotated box, that
two boxes exactly touching count as overlapping, and that a receding object
requires no deceleration rather than a negative one.
"""

from __future__ import annotations

import math
from types import ModuleType
from typing import Any

import pytest


def load_threat_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.aeb import threat
    except ImportError:
        pytest.fail("aebrisk.aeb.threat is missing", pytrace=False)
    return threat


def make_ego(
    *,
    center: tuple[float, float] = (0.0, 0.0),
    yaw: float = 0.0,
    speed: float = 10.0,
    velocity: tuple[float, float] = (10.0, 0.0),
    acceleration: float = 0.0,
) -> Any:
    from aebrisk.aeb.threat import EgoKinematicState

    return EgoKinematicState(
        center_xy_m=center,
        yaw_rad=yaw,
        size_lw_m=(4.0, 2.0),
        speed_mps=speed,
        velocity_xy_mps=velocity,
        acceleration_mps2=acceleration,
    )


def make_track(
    *,
    center: tuple[float, float] = (20.0, 0.0),
    yaw: float = 0.0,
    velocity: tuple[float, float] = (0.0, 0.0),
    track_id: str = "t-0001",
) -> Any:
    from aebrisk.observation.models import TrackState

    return TrackState(
        track_id=track_id,
        category="vehicle",
        center_xy_m=center,
        yaw_rad=yaw,
        size_lw_m=(4.0, 2.0),
        velocity_xy_mps=velocity,
        visible=True,
        source_timestamp_us=1_600_000_000_000_000,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )


def corners(polygon: Any) -> list[tuple[float, float]]:
    return [(round(float(x), 9), round(float(y), 9)) for x, y in polygon]


# --------------------------------------------------------------------------
# Oriented boxes
# --------------------------------------------------------------------------


def test_an_unrotated_box_has_the_hand_computed_vertices() -> None:
    """Length runs along the heading and width across it, not the other way round.

    Swapping them would make every vehicle 2 m long and 4 m wide, which changes
    every overlap in the study and nothing else about the run.
    """

    threat = load_threat_module()

    polygon = threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0))

    assert corners(polygon) == [(-2.0, -1.0), (2.0, -1.0), (2.0, 1.0), (-2.0, 1.0)]


def test_the_vertices_run_counter_clockwise_from_the_rear_right() -> None:
    """A fixed order, because the overlap test walks edges in order."""

    threat = load_threat_module()

    polygon = threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0))
    area = 0.0
    for index in range(4):
        x0, y0 = polygon[index]
        x1, y1 = polygon[(index + 1) % 4]
        area += float(x0) * float(y1) - float(x1) * float(y0)

    assert area > 0.0


def test_a_quarter_turn_rotates_the_vertices_by_hand() -> None:
    """Yaw is measured counter-clockwise from the x axis, like every other angle here."""

    threat = load_threat_module()

    polygon = threat.oriented_box_polygon((0.0, 0.0), math.pi / 2, (4.0, 2.0))

    assert corners(polygon) == [(1.0, -2.0), (1.0, 2.0), (-1.0, 2.0), (-1.0, -2.0)]


def test_the_box_is_translated_to_its_centre() -> None:
    """Rotation happens about the box's own centre, then it moves."""

    threat = load_threat_module()

    polygon = threat.oriented_box_polygon((10.0, -3.0), 0.0, (4.0, 2.0))

    assert corners(polygon) == [(8.0, -4.0), (12.0, -4.0), (12.0, -2.0), (8.0, -2.0)]


def test_the_margin_grows_the_box_on_every_side() -> None:
    """The corridor is the ego footprint plus a margin, so the margin is a half extent."""

    threat = load_threat_module()

    polygon = threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0), margin_m=0.5)

    assert corners(polygon) == [(-2.5, -1.5), (2.5, -1.5), (2.5, 1.5), (-2.5, 1.5)]


@pytest.mark.parametrize("bad_value", [-0.1, float("nan")])
def test_an_impossible_margin_is_refused(bad_value: float) -> None:
    """A negative margin would shrink the corridor and hide real overlaps."""

    threat = load_threat_module()

    with pytest.raises(
        ValueError,
        match=r"^(corridor_margin_m\ must\ be\ a\ number|margin_m\ must\ be\ a\ number|margin_m\ must\ be\ finite\ and\ non\-negative,\ got\ )",
    ):
        threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0), margin_m=bad_value)


@pytest.mark.parametrize("bad_size", [(0.0, 2.0), (4.0, -1.0), (float("nan"), 2.0)])
def test_an_impossible_size_is_refused(bad_size: tuple[float, float]) -> None:
    """A zero-area box can never overlap, so a threat carrying one is undetectable."""

    threat = load_threat_module()

    with pytest.raises(ValueError, match=r"^size_lw(_m)? components must be (positive|finite)$"):
        threat.oriented_box_polygon((0.0, 0.0), 0.0, bad_size)


# --------------------------------------------------------------------------
# Overlap
# --------------------------------------------------------------------------


def test_two_boxes_exactly_touching_count_as_overlapping() -> None:
    """The convention, chosen deliberately and pinned here.

    Contact is a collision. Treating a shared edge as clear would let the study
    report a grazing impact as an avoided one.
    """

    threat = load_threat_module()
    first = threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0))
    second = threat.oriented_box_polygon((4.0, 0.0), 0.0, (4.0, 2.0))

    assert threat.polygons_overlap(first, second) is True


def test_boxes_a_hand_apart_do_not_overlap() -> None:
    """The other side of the same boundary."""

    threat = load_threat_module()
    first = threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0))
    second = threat.oriented_box_polygon((4.2, 0.0), 0.0, (4.0, 2.0))

    assert threat.polygons_overlap(first, second) is False


def test_a_box_overlaps_itself() -> None:
    """The trivial case that a separating-axis sign error breaks first."""

    threat = load_threat_module()
    polygon = threat.oriented_box_polygon((3.0, -2.0), 0.7, (4.0, 2.0))

    assert threat.polygons_overlap(polygon, polygon) is True


def test_a_rotated_box_reaches_into_a_corridor_its_centre_never_enters() -> None:
    """The whole reason rectangles are used instead of circles or centres.

    A 4.5 m vehicle yawed into the lane crosses the corridor boundary well
    before its centre does, and that difference is the metre that decides
    whether the brake was late.
    """

    threat = load_threat_module()
    corridor = threat.oriented_box_polygon((0.0, 0.0), 0.0, (20.0, 2.0), margin_m=0.5)
    # The corridor reaches to y = 1.5. Square on, the body's near edge is at
    # 3.0 - 0.95 = 2.05 and it is clear. Yawed 45 degrees its half extent across
    # the corridor becomes (2.25 + 0.95) / sqrt(2) = 2.26, so its corner reaches
    # to 0.74 and it is inside.
    yawed = threat.oriented_box_polygon((0.0, 3.0), math.pi / 4, (4.5, 1.9))
    square_on = threat.oriented_box_polygon((0.0, 3.0), 0.0, (4.5, 1.9))

    assert threat.polygons_overlap(corridor, yawed) is True
    assert threat.polygons_overlap(corridor, square_on) is False


def test_overlap_is_symmetric() -> None:
    """A separating axis found from one side must be found from the other."""

    threat = load_threat_module()
    first = threat.oriented_box_polygon((0.0, 0.0), 0.3, (4.0, 2.0))
    second = threat.oriented_box_polygon((3.5, 1.0), -0.9, (4.0, 2.0))

    assert threat.polygons_overlap(first, second) == threat.polygons_overlap(second, first)


# --------------------------------------------------------------------------
# Clearance
# --------------------------------------------------------------------------


def test_clearance_between_two_boxes_is_the_gap_between_their_edges() -> None:
    """Edge to edge, not centre to centre."""

    threat = load_threat_module()
    first = threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0))
    second = threat.oriented_box_polygon((5.0, 0.0), 0.0, (4.0, 2.0))

    assert threat.polygon_clearance(first, second) == pytest.approx(1.0)


def test_clearance_is_measured_corner_to_corner_when_that_is_nearest() -> None:
    """A face-normal-only distance would report the wrong number here.

    The nearest points are the corners (2, 1) and (3, 3), which are sqrt(5)
    apart along a direction that is not either box's face normal.
    """

    threat = load_threat_module()
    first = threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0))
    second = threat.oriented_box_polygon((5.0, 4.0), 0.0, (4.0, 2.0))

    assert threat.polygon_clearance(first, second) == pytest.approx(math.sqrt(5.0))


def test_overlapping_boxes_have_no_clearance() -> None:
    """Zero rather than a negative depth: this number is a distance."""

    threat = load_threat_module()
    first = threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0))
    second = threat.oriented_box_polygon((1.0, 0.0), 0.0, (4.0, 2.0))

    assert threat.polygon_clearance(first, second) == 0.0


# --------------------------------------------------------------------------
# Threat assessment
# --------------------------------------------------------------------------


def test_a_stationary_obstacle_ahead_gives_the_hand_computed_time_to_collision() -> None:
    """Ego front at 2.5 + 10t, obstacle rear at 18; they meet at t = 1.55.

    The rollout samples every 0.1 s, so the first sampled overlap is 1.6.
    """

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(), make_track())

    assert assessment.predicted_overlap is True
    assert assessment.ttc_s == pytest.approx(1.6)


def test_an_immediate_overlap_gives_a_time_to_collision_of_zero() -> None:
    """Already touching is not one step away from touching."""

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(), make_track(center=(3.0, 0.0)))

    assert assessment.ttc_s == 0.0
    assert assessment.predicted_overlap is True


def test_an_object_receding_faster_than_the_ego_never_collides() -> None:
    """No overlap within the horizon is ``None``, not a large number."""

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(), make_track(velocity=(20.0, 0.0)))

    assert assessment.ttc_s is None
    assert assessment.predicted_overlap is False


def test_a_stationary_ego_and_a_stationary_obstacle_never_collide() -> None:
    """Nothing moves, so nothing is a threat, however close it is."""

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(speed=0.0, velocity=(0.0, 0.0)), make_track())

    assert assessment.ttc_s is None


def test_a_crossing_object_is_caught() -> None:
    """The case a longitudinal-only model misses entirely.

    The ego travels along x at 10 m/s and the object drops down y at 10 m/s.
    Their centres would coincide at t = 2, but they never get that far: the
    corridor reaches to x = 10t + 2.5 and the yawed body starts at x = 19, so
    their corners touch at t = 1.65 and the first sampled overlap is 1.7. That
    0.3 s is exactly what a centre-to-centre model would throw away.
    """

    threat = load_threat_module()

    assessment = threat.assess_threat(
        make_ego(),
        make_track(center=(20.0, 20.0), yaw=math.pi / 2, velocity=(0.0, -10.0)),
    )

    assert assessment.predicted_overlap is True
    assert assessment.ttc_s == pytest.approx(1.7)


def test_a_missed_object_reports_how_close_it_came() -> None:
    """A near miss and a comfortable pass are different results."""

    threat = load_threat_module()

    near = threat.assess_threat(make_ego(), make_track(center=(20.0, 3.2)))
    far = threat.assess_threat(make_ego(), make_track(center=(20.0, 12.0)))

    assert near.predicted_overlap is False
    assert 0.0 < near.min_clearance_m < far.min_clearance_m


def test_a_predicted_overlap_reports_no_clearance() -> None:
    """The two fields must agree; a threat cannot both hit and keep its distance."""

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(), make_track())

    assert assessment.min_clearance_m == 0.0


def test_the_assessment_carries_the_track_it_describes() -> None:
    """The controller selects by identity, and the record explains by it."""

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(), make_track(track_id="t-0042"))

    assert assessment.track_id == "t-0042"


def test_the_corridor_margin_widens_what_counts_as_a_threat() -> None:
    """It is the study's stated conservatism, so it must actually do something."""

    threat = load_threat_module()
    passing = make_track(center=(20.0, 2.6))

    tight = threat.assess_threat(make_ego(), passing, corridor_margin_m=0.0)
    wide = threat.assess_threat(make_ego(), passing, corridor_margin_m=1.0)

    assert tight.predicted_overlap is False
    assert wide.predicted_overlap is True


def test_the_horizon_bounds_the_rollout() -> None:
    """A collision beyond the horizon is not this controller's to see."""

    threat = load_threat_module()
    far = make_track(center=(60.0, 0.0))

    assert threat.assess_threat(make_ego(), far, horizon_s=4.0).ttc_s is None
    assert threat.assess_threat(make_ego(), far, horizon_s=8.0).ttc_s is not None


def test_the_horizon_is_inclusive() -> None:
    """An overlap exactly at the horizon is inside it, not one step outside."""

    threat = load_threat_module()
    # Ego front 2.5 + 10t, obstacle rear at 18: first overlap at t = 1.6.
    obstacle = make_track()

    assert threat.assess_threat(make_ego(), obstacle, horizon_s=1.6).ttc_s == pytest.approx(1.6)
    assert threat.assess_threat(make_ego(), obstacle, horizon_s=1.5).ttc_s is None


# --------------------------------------------------------------------------
# Required deceleration
# --------------------------------------------------------------------------


def test_the_required_deceleration_is_the_hand_computed_value() -> None:
    """``v^2 / (2 d)`` on the longitudinal gap between the two bodies.

    Ego at the origin closing at 10 m/s on an obstacle 20 m away: the gap
    between the bodies is 20 - 2 - 2 = 16 m, so 100 / 32 = 3.125.
    """

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(), make_track())

    assert assessment.required_deceleration_mps2 == pytest.approx(3.125)


def test_a_receding_object_requires_no_deceleration() -> None:
    """Not a negative one: this number is the magnitude of a brake."""

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(), make_track(velocity=(20.0, 0.0)))

    assert assessment.required_deceleration_mps2 == 0.0


def test_a_stationary_ego_requires_no_deceleration() -> None:
    """There is no speed to shed."""

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(speed=0.0, velocity=(0.0, 0.0)), make_track())

    assert assessment.required_deceleration_mps2 == 0.0


def test_the_required_deceleration_is_finite_when_the_gap_has_closed() -> None:
    """A zero gap is a division by zero, and the artifact must still be writable.

    The value saturates instead of becoming infinite. Anything above the
    vehicle's braking capability means the same thing to the controller, so the
    exact saturated number never changes a decision.
    """

    threat = load_threat_module()

    assessment = threat.assess_threat(make_ego(), make_track(center=(4.0, 0.0)))

    assert math.isfinite(assessment.required_deceleration_mps2)
    assert assessment.required_deceleration_mps2 > 0.0


def test_a_closer_obstacle_requires_harder_braking() -> None:
    """Monotonic in the gap, which is the property the controller ranks on."""

    threat = load_threat_module()

    near = threat.assess_threat(make_ego(), make_track(center=(15.0, 0.0)))
    far = threat.assess_threat(make_ego(), make_track(center=(30.0, 0.0)))

    assert near.required_deceleration_mps2 > far.required_deceleration_mps2


def test_a_faster_ego_requires_harder_braking() -> None:
    """Quadratic in speed, which is why a small speed error matters so much."""

    threat = load_threat_module()

    slow = threat.assess_threat(make_ego(speed=10.0, velocity=(10.0, 0.0)), make_track())
    fast = threat.assess_threat(make_ego(speed=20.0, velocity=(20.0, 0.0)), make_track())

    assert fast.required_deceleration_mps2 == pytest.approx(4.0 * slow.required_deceleration_mps2)


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def test_no_threats_select_nothing() -> None:
    """An empty road is not a threat with zero urgency."""

    threat = load_threat_module()

    assert threat.select_highest_required_deceleration(()) is None


def test_the_most_urgent_threat_is_selected() -> None:
    """The AEB brakes for one object, and it must be the worst one."""

    threat = load_threat_module()
    assessments = (
        threat.assess_threat(make_ego(), make_track(center=(30.0, 0.0), track_id="t-far")),
        threat.assess_threat(make_ego(), make_track(center=(12.0, 0.0), track_id="t-near")),
    )

    selected = threat.select_highest_required_deceleration(assessments)

    assert selected is not None
    assert selected.track_id == "t-near"


def test_a_tie_is_broken_by_track_identity() -> None:
    """Input order is not a contract, so a tie must resolve on something that is."""

    threat = load_threat_module()
    first = threat.assess_threat(make_ego(), make_track(track_id="t-0002"))
    second = threat.assess_threat(make_ego(), make_track(track_id="t-0001"))

    assert threat.select_highest_required_deceleration((first, second)).track_id == "t-0001"
    assert threat.select_highest_required_deceleration((second, first)).track_id == "t-0001"


# --------------------------------------------------------------------------
# Invariance
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "rotation"),
    [
        ((0.0, 0.0), 0.0),
        ((100.0, -250.0), 0.0),
        ((0.0, 0.0), 1.1),
        ((-40.0, 7.5), -2.3),
        ((631.0, 512.0), math.pi),
    ],
)
def test_the_assessment_does_not_depend_on_the_world_frame(
    offset: tuple[float, float],
    rotation: float,
) -> None:
    """Moving the whole scene must not change what the AEB decides.

    nuPlan's world coordinates are large map-frame numbers, so a formulation
    that quietly depended on the origin would give different answers in
    different parts of the same city and nothing would say so.
    """

    threat = load_threat_module()

    def move(point: tuple[float, float]) -> tuple[float, float]:
        cos, sin = math.cos(rotation), math.sin(rotation)
        return (
            point[0] * cos - point[1] * sin + offset[0],
            point[0] * sin + point[1] * cos + offset[1],
        )

    def turn(vector: tuple[float, float]) -> tuple[float, float]:
        cos, sin = math.cos(rotation), math.sin(rotation)
        return (vector[0] * cos - vector[1] * sin, vector[0] * sin + vector[1] * cos)

    plain = threat.assess_threat(make_ego(), make_track(center=(20.0, 1.0), velocity=(2.0, -1.0)))
    moved = threat.assess_threat(
        make_ego(center=move((0.0, 0.0)), yaw=rotation, velocity=turn((10.0, 0.0))),
        make_track(center=move((20.0, 1.0)), yaw=rotation, velocity=turn((2.0, -1.0))),
    )

    assert moved.ttc_s == pytest.approx(plain.ttc_s)
    assert moved.required_deceleration_mps2 == pytest.approx(plain.required_deceleration_mps2)
    assert moved.min_clearance_m == pytest.approx(plain.min_clearance_m, abs=1e-9)


# --------------------------------------------------------------------------
# The ego state
# --------------------------------------------------------------------------


def test_the_ego_state_is_frozen() -> None:
    """It is the simulation's state at one step, and the record of it."""

    import dataclasses

    load_threat_module()
    ego = make_ego()

    with pytest.raises(dataclasses.FrozenInstanceError):
        ego.speed_mps = 0.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "field",
    ["center_xy_m", "velocity_xy_mps", "size_lw_m"],
)
def test_a_non_finite_ego_pair_is_refused(field: str) -> None:
    """A NaN coordinate propagates into every distance and time-to-collision."""

    threat = load_threat_module()

    with pytest.raises(
        ValueError, match=rf"^{field} (components must be finite|must have exactly two components)$"
    ):
        threat.EgoKinematicState(
            **{
                **{
                    "center_xy_m": (0.0, 0.0),
                    "yaw_rad": 0.0,
                    "size_lw_m": (4.0, 2.0),
                    "speed_mps": 10.0,
                    "velocity_xy_mps": (10.0, 0.0),
                    "acceleration_mps2": 0.0,
                },
                field: (float("nan"), 0.0),
            }
        )


def test_a_zero_area_ego_is_refused() -> None:
    """A corridor with no width can never overlap anything."""

    threat = load_threat_module()

    with pytest.raises(ValueError, match=r"^size_lw_m\ components\ must\ be\ positive"):
        threat.EgoKinematicState(
            center_xy_m=(0.0, 0.0),
            yaw_rad=0.0,
            size_lw_m=(4.0, 0.0),
            speed_mps=10.0,
            velocity_xy_mps=(10.0, 0.0),
            acceleration_mps2=0.0,
        )


def test_a_negative_ego_speed_is_refused() -> None:
    """Speed is a magnitude; direction lives in the velocity and the yaw."""

    threat = load_threat_module()

    with pytest.raises(ValueError, match=r"^speed_mps\ must\ be\ finite\ and\ non\-negative$"):
        threat.EgoKinematicState(
            center_xy_m=(0.0, 0.0),
            yaw_rad=0.0,
            size_lw_m=(4.0, 2.0),
            speed_mps=-1.0,
            velocity_xy_mps=(10.0, 0.0),
            acceleration_mps2=0.0,
        )


@pytest.mark.parametrize("field", ["yaw_rad", "acceleration_mps2"])
def test_a_non_finite_ego_scalar_is_refused(field: str) -> None:
    """The same guard on the scalars."""

    threat = load_threat_module()

    with pytest.raises(ValueError, match=rf"^{field} must be finite$"):
        threat.EgoKinematicState(
            **{
                **{
                    "center_xy_m": (0.0, 0.0),
                    "yaw_rad": 0.0,
                    "size_lw_m": (4.0, 2.0),
                    "speed_mps": 10.0,
                    "velocity_xy_mps": (10.0, 0.0),
                    "acceleration_mps2": 0.0,
                },
                field: float("inf"),
            }
        )


@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan")])
def test_an_impossible_horizon_is_refused(bad_value: float) -> None:
    """A zero horizon would report every scenario as safe."""

    threat = load_threat_module()

    with pytest.raises(
        ValueError, match=r"^horizon_s must (be a number|be finite and positive, got )"
    ):
        threat.assess_threat(make_ego(), make_track(), horizon_s=bad_value)


@pytest.mark.parametrize("bad_value", [0.0, -0.1, float("nan")])
def test_an_impossible_rollout_step_is_refused(bad_value: float) -> None:
    """A zero step is an infinite rollout."""

    threat = load_threat_module()

    with pytest.raises(ValueError, match=r"^step_s must "):
        threat.assess_threat(make_ego(), make_track(), step_s=bad_value)


def test_a_step_longer_than_the_horizon_is_refused() -> None:
    """It would sample only t = 0 while claiming a four second horizon."""

    threat = load_threat_module()

    with pytest.raises(ValueError, match=r"^step_s 2\.0 exceeds horizon_s 1\.0;"):
        threat.assess_threat(make_ego(), make_track(), horizon_s=1.0, step_s=2.0)


def test_a_point_that_is_not_a_pair_is_refused() -> None:
    """A three component centre would silently take only the first two.

    Every coordinate here is planar; a third component means the caller thinks
    it is working in three dimensions, and it must find out here rather than by
    reading a plausible wrong answer.
    """

    threat = load_threat_module()

    with pytest.raises(ValueError, match=r"^center_xy must "):
        threat.oriented_box_polygon((0.0, 0.0, 0.0), 0.0, (4.0, 2.0))  # type: ignore[arg-type]


def test_a_non_finite_yaw_is_refused() -> None:
    """A NaN heading rotates a box into nothing, and every overlap then reads false."""

    threat = load_threat_module()

    with pytest.raises(ValueError, match=r"^yaw_rad\ must\ be\ finite$"):
        threat.oriented_box_polygon((0.0, 0.0), float("nan"), (4.0, 2.0))


@pytest.mark.parametrize("bad_value", ["0.5", True, None])
def test_a_margin_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean a one metre corridor margin."""

    threat = load_threat_module()

    with pytest.raises(
        ValueError,
        match=r"^(corridor_margin_m\ must\ be\ a\ number|margin_m\ must\ be\ a\ number)$",
    ):
        threat.oriented_box_polygon((0.0, 0.0), 0.0, (4.0, 2.0), margin_m=bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", ["4.0", True, None])
def test_a_horizon_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean a one second horizon."""

    threat = load_threat_module()

    with pytest.raises(ValueError, match=r"^horizon_s must be a number$"):
        threat.assess_threat(make_ego(), make_track(), horizon_s=bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", ["0.5", True, None])
def test_a_corridor_margin_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """The margin is the study's stated conservatism, so its type is checked too."""

    threat = load_threat_module()

    with pytest.raises(ValueError, match=r"^corridor_margin_m\ must\ be\ a\ number"):
        threat.assess_threat(
            make_ego(),
            make_track(),
            corridor_margin_m=bad_value,  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------
# The bound that skips a rollout it cannot change
# --------------------------------------------------------------------------


def test_a_body_that_cannot_close_the_gap_skips_the_rollout() -> None:
    """A real urban frame holds over a hundred tracks, and almost none of them matter.

    Measured on nuPlan mini on 2026-09-06: 134 tracked objects in one frame,
    6.9 ms of rollout each, 37 seconds to decide one cohort candidate. The
    skip is a bound rather than a heuristic — both bodies translate at constant
    velocity, so their separation cannot fall faster than the relative speed —
    and the clearance it reports is that bound.
    """

    threat = load_threat_module()

    # Settled by arithmetic alone: 100 m between the centres, less how far each
    # box's corner can reach (2.9155 m for the corridor, 2.2361 m for the body),
    # less the 40 m the two can close in four seconds at 10 m/s.
    assessment = threat.assess_threat(make_ego(), make_track(center=(100.0, 0.0)))

    assert assessment.ttc_s is None
    assert assessment.predicted_overlap is False
    assert assessment.min_clearance_m == pytest.approx(
        100.0 - threat.half_diagonal_m((4.0, 2.0), 0.5) - threat.half_diagonal_m((4.0, 2.0)) - 40.0,
        abs=1e-9,
    )


def test_a_body_inside_the_bound_is_still_rolled_forward() -> None:
    """The pair to the test above: the bound must not refuse a threat that is real."""

    threat = load_threat_module()

    # The same closing speed, a gap it can close: this must brake, not skip.
    assessment = threat.assess_threat(make_ego(), make_track(center=(30.0, 0.0)))

    assert assessment.predicted_overlap is True
    assert assessment.ttc_s is not None


def test_a_body_that_is_already_touching_is_never_skipped() -> None:
    """Zero separation cannot exceed any reach, however slowly the two are closing."""

    threat = load_threat_module()

    assessment = threat.assess_threat(
        make_ego(speed=0.0, velocity=(0.0, 0.0)),
        make_track(center=(4.0, 0.0)),
    )

    assert assessment.predicted_overlap is True
    assert assessment.ttc_s == 0.0


def test_two_bodies_that_never_approach_are_skipped_on_the_first_step() -> None:
    """With no relative motion the rollout is forty-one copies of one measurement."""

    threat = load_threat_module()

    assessment = threat.assess_threat(
        make_ego(speed=6.0, velocity=(6.0, 0.0)),
        make_track(center=(0.0, 30.0), velocity=(6.0, 0.0)),
    )

    assert assessment.ttc_s is None
    assert assessment.min_clearance_m > 0.0
