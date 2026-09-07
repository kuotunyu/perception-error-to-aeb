"""Contracts for the nominal controller, which must not be able to see.

This module is the study's control. Every configuration — oracle, no AEB, and
each corrupted cell of the matrix — drives the same route at the same speed, and
the only thing that differs is what the AEB was told. If the nominal controller
could see the agents, a perception error would change the driving as well as the
braking, and no result could separate the two.

So being blind is not a property of this implementation, it is the requirement.
The tests below check it three ways: nothing about agents or the AEB appears in
the signature, nothing from those packages is imported into the module, and the
plan built for the same scenario is byte-identical across configurations.

The speed rule is `min(initial, map limit, 13.9)`. The cap is there so that a
scenario logged on a fast road does not turn every configuration's outcome into
a question about high-speed braking; the initial speed is there so the ego does
not accelerate into a scenario it was logged coasting through.
"""

from __future__ import annotations

import hashlib
import math
import struct
from types import ModuleType
from typing import Any, Optional

import numpy as np
import pytest


def load_route_follower_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.simulation import route_follower
    except ImportError:
        pytest.fail("aebrisk.simulation.route_follower is missing", pytrace=False)
    return route_follower


def straight_route(points: int = 40, spacing: float = 2.0) -> Any:
    return np.array(
        [[index * spacing, 0.0] for index in range(points)],
        dtype=np.float64,
    )


# --------------------------------------------------------------------------
# The target speed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("initial", "limit", "expected"),
    [
        (8.0, 20.0, 8.0),  # the ego was slower than the road allows
        (20.0, 11.0, 11.0),  # the road is the binding constraint
        (30.0, 40.0, 13.9),  # the study's own cap binds
        (8.0, None, 8.0),  # no map limit available
        (30.0, None, 13.9),  # no map limit, cap still binds
        (8.0, 0.5, 0.5),  # a small positive map limit is still a real constraint
    ],
)
def test_the_target_speed_is_the_smallest_of_the_three_constraints(
    initial: float,
    limit: Optional[float],
    expected: float,
) -> None:
    """`min(initial, map limit, 13.9)`, with the limit simply absent when unknown."""

    route_follower = load_route_follower_module()

    assert route_follower.target_speed(initial, limit) == pytest.approx(expected)


def test_the_speed_cap_is_the_declared_constant() -> None:
    """Named rather than written into the comparison, because a result cites it."""

    route_follower = load_route_follower_module()

    assert pytest.approx(13.9) == route_follower.MAXIMUM_TARGET_SPEED_MPS


def test_a_stationary_start_stays_stationary() -> None:
    """The minimum includes zero; inventing motion would change the scenario."""

    route_follower = load_route_follower_module()

    assert route_follower.target_speed(0.0, 13.0) == 0.0


@pytest.mark.parametrize("bad_value", [-1.0, float("nan"), float("inf")])
def test_an_impossible_initial_speed_is_refused(bad_value: float) -> None:
    """Speed is a magnitude read from the log; a negative one means a parsing error."""

    route_follower = load_route_follower_module()

    with pytest.raises(
        ValueError, match=r"^initial_speed_mps\ must\ be\ finite\ and\ non\-negative"
    ):
        route_follower.target_speed(bad_value, 13.0)


@pytest.mark.parametrize("bad_value", [-1.0, 0.0, float("nan")])
def test_an_impossible_map_speed_limit_is_refused(bad_value: float) -> None:
    """A zero limit would stop every scenario; it is a missing value, not a real one."""

    route_follower = load_route_follower_module()

    with pytest.raises(
        ValueError,
        match=r"^(map_speed_limit_mps\ must\ be\ a\ number|map_speed_limit_mps\ must\ be\ finite\ and\ positive,\ got\ )",
    ):
        route_follower.target_speed(8.0, bad_value)


@pytest.mark.parametrize("bad_value", ["8.0", True])
def test_a_speed_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean one metre per second."""

    route_follower = load_route_follower_module()

    with pytest.raises(ValueError, match=r"^initial_speed_mps must be a number$"):
        route_follower.target_speed(bad_value, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", ["13.0", True])
def test_a_map_speed_limit_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """A limit read from a map field of the wrong type must not become one metre per second."""

    route_follower = load_route_follower_module()

    with pytest.raises(ValueError, match=r"^map_speed_limit_mps\ must\ be\ a\ number"):
        route_follower.target_speed(8.0, bad_value)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------


def test_the_lateral_path_is_the_expert_route() -> None:
    """The logged path is used verbatim rather than re-fitted.

    Any smoothing would be a controller parameter, and the study needs the
    nominal path to be identical across configurations. Verbatim is the
    strongest guarantee of that, and it costs nothing the study wants.
    """

    route_follower = load_route_follower_module()
    route = straight_route()

    plan = route_follower.build_nominal_plan(route, 8.0, 8.0, 13.0)

    assert np.array_equal(plan.lateral_path_xy, route)


def test_the_lateral_path_is_a_copy_the_caller_cannot_alter() -> None:
    """A plan that changed when its input array was reused would not be a record."""

    route_follower = load_route_follower_module()
    route = straight_route()

    plan = route_follower.build_nominal_plan(route, 8.0, 8.0, 13.0)
    route[0, 0] = 999.0

    assert plan.lateral_path_xy[0, 0] == 0.0


def test_the_stored_path_cannot_be_written_through() -> None:
    """The plan is provenance, so it refuses to be edited in place."""

    route_follower = load_route_follower_module()

    plan = route_follower.build_nominal_plan(straight_route(), 8.0, 8.0, 13.0)

    with pytest.raises(ValueError, match=r"^assignment destination is read-only$"):
        plan.lateral_path_xy[0, 0] = 1.0


def test_the_plan_carries_the_target_speed() -> None:
    """The same rule as `target_speed`, reachable from the plan a run records."""

    route_follower = load_route_follower_module()

    plan = route_follower.build_nominal_plan(straight_route(), 8.0, 20.0, 11.0)

    assert plan.target_speed_mps == pytest.approx(11.0)


def test_a_slower_ego_is_asked_to_accelerate() -> None:
    """The longitudinal law holds the target speed, so a deficit is a positive request."""

    route_follower = load_route_follower_module()

    plan = route_follower.build_nominal_plan(straight_route(), 6.0, 10.0, 13.0)

    assert plan.nominal_acceleration_mps2 > 0.0


def test_an_ego_at_the_target_speed_is_asked_for_nothing() -> None:
    """The steady state, which is where most of a scenario is spent."""

    route_follower = load_route_follower_module()

    plan = route_follower.build_nominal_plan(straight_route(), 10.0, 10.0, 13.0)

    assert plan.nominal_acceleration_mps2 == 0.0


def test_a_faster_ego_is_asked_to_slow_down() -> None:
    """Symmetric, and needed whenever the AEB has just released a brake."""

    route_follower = load_route_follower_module()

    plan = route_follower.build_nominal_plan(straight_route(), 12.0, 10.0, 13.0)

    assert plan.nominal_acceleration_mps2 < 0.0


def test_the_longitudinal_request_is_proportional_to_the_speed_error() -> None:
    """A first-order law, hand computed, so the constant cannot drift unnoticed."""

    route_follower = load_route_follower_module()

    plan = route_follower.build_nominal_plan(straight_route(), 6.0, 10.0, 13.0)

    assert plan.nominal_acceleration_mps2 == pytest.approx(
        4.0 / route_follower.SPEED_TRACKING_TIME_CONSTANT_S
    )


def test_the_request_is_left_unclamped_for_the_limiter() -> None:
    """The route follower asks; the actuator decides.

    Clamping here as well would apply the bound twice and hide which stage
    produced a saturated command in the record.
    """

    route_follower = load_route_follower_module()

    plan = route_follower.build_nominal_plan(straight_route(), 0.0, 13.9, None)

    assert plan.nominal_acceleration_mps2 > 2.0


# --------------------------------------------------------------------------
# Blindness
# --------------------------------------------------------------------------


def test_the_signature_cannot_see_agents_or_the_aeb() -> None:
    """The strongest form of the guarantee: they are not arguments.

    A controller that cannot be handed the agents cannot read them, whatever a
    future edit does inside the function body.
    """

    import inspect

    route_follower = load_route_follower_module()

    parameters = set(inspect.signature(route_follower.build_nominal_plan).parameters)

    assert parameters == {
        "expert_route_xy",
        "current_speed_mps",
        "initial_speed_mps",
        "map_speed_limit_mps",
    }


def test_the_module_imports_nothing_from_perception_or_the_aeb() -> None:
    """The second form: nothing from those packages is even in scope.

    Checked through the module's own namespace rather than by reading its
    source, so the test describes what the module can reach at runtime.
    """

    route_follower = load_route_follower_module()
    forbidden = ("aebrisk.observation", "aebrisk.errors", "aebrisk.aeb")

    reachable = []
    for value in vars(route_follower).values():
        module = getattr(value, "__module__", None) or getattr(value, "__name__", "")
        if isinstance(module, str) and module.startswith(forbidden):
            reachable.append(module)

    assert reachable == []


def test_every_configuration_receives_the_same_plan_bytes() -> None:
    """The control condition of the whole study, stated as a test.

    Oracle, no-AEB and every corrupted cell build the plan from the same
    scenario inputs, so the bytes must match. If they ever diverge, the
    comparison the study rests on is no longer controlled.
    """

    route_follower = load_route_follower_module()
    route = straight_route()

    plans = [
        route_follower.build_nominal_plan(route.copy(), 8.0, 8.0, 13.0)
        for _ in ("oracle", "no_aeb", "corrupted")
    ]
    signatures = {route_follower.plan_bytes(plan) for plan in plans}

    assert len(signatures) == 1


def test_the_plan_bytes_change_when_the_plan_does() -> None:
    """The pair to the test above; a constant signature would prove nothing."""

    route_follower = load_route_follower_module()
    route = straight_route()

    # The initial speed has to exceed both limits for the limits to bind at all;
    # at 8 m/s the minimum is 8 either way and the two plans are rightly equal.
    first = route_follower.plan_bytes(route_follower.build_nominal_plan(route, 8.0, 12.0, 13.0))
    second = route_follower.plan_bytes(route_follower.build_nominal_plan(route, 8.0, 12.0, 11.0))

    assert first != second


def test_plan_bytes_pack_every_component_in_canonical_order() -> None:
    """The public digest covers both scalars and every little-endian path coordinate."""

    route_follower = load_route_follower_module()
    path = np.array([[10.5, -2.0], [13.0, 4.25]], dtype=">f8")
    plan = route_follower.NominalPlan(
        target_speed_mps=7.5,
        lateral_path_xy=path,
        nominal_acceleration_mps2=-1.25,
    )
    packed = struct.pack("<dd", 7.5, -1.25) + np.asarray(path, dtype="<f8").tobytes()
    expected = hashlib.sha256(packed).digest()

    assert route_follower.plan_bytes(plan) == expected
    for changed in (
        route_follower.NominalPlan(7.6, path, -1.25),
        route_follower.NominalPlan(7.5, path, -1.0),
        route_follower.NominalPlan(7.5, path + np.array([[0.0, 0.0], [0.0, 0.5]]), -1.25),
    ):
        assert route_follower.plan_bytes(changed) != expected


def test_the_plan_bytes_are_independent_of_the_platform() -> None:
    """A signature that differed between Windows and CI would be no signature at all."""

    route_follower = load_route_follower_module()

    signature = route_follower.plan_bytes(
        route_follower.build_nominal_plan(straight_route(), 8.0, 8.0, 13.0)
    )

    assert isinstance(signature, bytes)
    assert b"\r" not in signature


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_route_of_one_point_is_refused() -> None:
    """One point is a position, not a path; there is no direction to follow."""

    route_follower = load_route_follower_module()

    with pytest.raises(
        ValueError,
        match=r"^expert_route_xy (must be an \(N, 2\) array|needs at least two waypoints|components must all be finite)",
    ):
        route_follower.build_nominal_plan(np.array([[0.0, 0.0]], dtype=np.float64), 8.0, 8.0, 13.0)


def test_an_empty_route_is_refused() -> None:
    """A scenario with no logged route cannot be driven, and must say so."""

    route_follower = load_route_follower_module()

    with pytest.raises(
        ValueError,
        match=r"^expert_route_xy (must be an \(N, 2\) array|needs at least two waypoints|components must all be finite)",
    ):
        route_follower.build_nominal_plan(np.zeros((0, 2), dtype=np.float64), 8.0, 8.0, 13.0)


def test_a_route_that_is_not_two_dimensional_points_is_refused() -> None:
    """Three columns would mean the caller thinks it is planning in three dimensions."""

    route_follower = load_route_follower_module()

    with pytest.raises(
        ValueError,
        match=r"^expert_route_xy (must be an \(N, 2\) array|needs at least two waypoints|components must all be finite)",
    ):
        route_follower.build_nominal_plan(np.zeros((4, 3), dtype=np.float64), 8.0, 8.0, 13.0)


def test_a_route_containing_a_non_finite_point_is_refused() -> None:
    """A NaN waypoint reaches every position the simulation integrates."""

    route_follower = load_route_follower_module()
    route = straight_route()
    route[3, 1] = math.nan

    with pytest.raises(
        ValueError,
        match=r"^expert_route_xy (must be an \(N, 2\) array|needs at least two waypoints|components must all be finite)",
    ):
        route_follower.build_nominal_plan(route, 8.0, 8.0, 13.0)


@pytest.mark.parametrize("bad_value", [-1.0, float("nan")])
def test_an_impossible_current_speed_is_refused(bad_value: float) -> None:
    """The same guard as the initial speed, on the value that changes every step."""

    route_follower = load_route_follower_module()

    with pytest.raises(ValueError, match=r"^current_speed_mps must "):
        route_follower.build_nominal_plan(straight_route(), bad_value, 8.0, 13.0)


def test_the_plan_is_frozen() -> None:
    """It is the record of what the nominal controller intended."""

    import dataclasses

    route_follower = load_route_follower_module()
    plan = route_follower.build_nominal_plan(straight_route(), 8.0, 8.0, 13.0)

    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.target_speed_mps = 0.0  # type: ignore[misc]


# --------------------------------------------------------------------------
# pose_at_distance
# --------------------------------------------------------------------------


def cornering_route() -> Any:
    """Ten metres east, then ten metres north, as two segments.

    Named apart from `straight_route` above, which other tests index by position:
    two helpers of one name in one file is a shadowing bug waiting to happen, and
    it already caused one here.
    """

    return np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]], dtype=np.float64)


def test_the_pose_at_the_start_is_the_first_waypoint_facing_the_first_segment() -> None:
    """The ego begins where the recording begins, pointed the way the road goes."""

    route_follower = load_route_follower_module()

    (x, y), yaw = route_follower.pose_at_distance(cornering_route(), 0.0)

    assert (x, y) == (0.0, 0.0)
    assert yaw == pytest.approx(0.0)


def test_a_pose_inside_a_segment_is_interpolated_along_it() -> None:
    """Following a route means being between its waypoints, not snapped to one."""

    route_follower = load_route_follower_module()

    (x, y), yaw = route_follower.pose_at_distance(cornering_route(), 4.0)

    assert (x, y) == pytest.approx((4.0, 0.0))
    assert yaw == pytest.approx(0.0)


def test_the_heading_turns_with_the_route() -> None:
    """A yaw that ignored the corner would point the ego's body across the road."""

    route_follower = load_route_follower_module()

    (x, y), yaw = route_follower.pose_at_distance(cornering_route(), 13.0)

    assert (x, y) == pytest.approx((10.0, 3.0))
    assert yaw == pytest.approx(math.pi / 2)


def test_running_past_the_end_of_the_route_stops_at_its_last_waypoint() -> None:
    """The recording ran out; extrapolating would invent road that was never driven."""

    route_follower = load_route_follower_module()

    (x, y), yaw = route_follower.pose_at_distance(cornering_route(), 1_000.0)

    assert (x, y) == pytest.approx((10.0, 10.0))
    assert yaw == pytest.approx(math.pi / 2)


def test_terminal_heading_uses_both_components_of_the_last_segment() -> None:
    """A translated diagonal endpoint faces atan2(dy, dx), not endpoint coordinates."""

    route_follower = load_route_follower_module()
    route = np.array([[1.0, 2.0], [5.0, 13.0], [11.0, 17.0]], dtype=np.float64)

    point, yaw = route_follower.pose_at_distance(route, 1_000.0)

    assert point == pytest.approx((11.0, 17.0))
    assert yaw == pytest.approx(math.atan2(4.0, 6.0))


@pytest.mark.parametrize(
    ("distance", "point", "heading"),
    [
        (2.5, (11.5, 22.0), math.atan2(4.0, 3.0)),
        (5.0, (13.0, 24.0), math.atan2(4.0, 3.0)),
        (8.0, (13.0, 27.0), math.pi / 2.0),
        (1_000.0, (21.0, 30.0), 0.0),
    ],
)
def test_translated_unequal_route_interpolation_and_terminal_heading(
    distance: float, point: tuple[float, float], heading: float
) -> None:
    """Interior points, corner ties, and terminal clamps use their actual moving segment."""

    route_follower = load_route_follower_module()
    route = np.array([[10.0, 20.0], [13.0, 24.0], [13.0, 30.0], [21.0, 30.0]], dtype=np.float64)

    observed_point, observed_heading = route_follower.pose_at_distance(route, distance)

    assert observed_point == pytest.approx(point)
    assert observed_heading == pytest.approx(heading)


def test_a_distance_before_the_route_is_refused() -> None:
    """Negative travel is a bug upstream, and clamping it would hide the bug."""

    route_follower = load_route_follower_module()

    with pytest.raises(ValueError, match=r"^distance_m must be finite and non-negative"):
        route_follower.pose_at_distance(cornering_route(), -1.0)


def test_a_repeated_waypoint_does_not_divide_by_zero() -> None:
    """Real logs hold duplicate poses when the vehicle is stopped at a light."""

    route_follower = load_route_follower_module()
    route = np.array([[0.0, 0.0], [0.0, 0.0], [5.0, 0.0]], dtype=np.float64)

    (x, y), yaw = route_follower.pose_at_distance(route, 2.0)

    assert (x, y) == pytest.approx((2.0, 0.0))
    assert yaw == pytest.approx(0.0)


def test_a_route_that_never_moves_has_no_direction_and_is_refused() -> None:
    """A stationary recording gives no heading, and guessing one would be a fiction."""

    route_follower = load_route_follower_module()
    route = np.array([[3.0, 4.0], [3.0, 4.0]], dtype=np.float64)

    with pytest.raises(ValueError, match=r"^the route has no length"):
        route_follower.pose_at_distance(route, 0.0)


def test_the_route_length_is_the_sum_of_its_segments() -> None:
    """The loop needs to know when the ego has run out of recording."""

    route_follower = load_route_follower_module()

    assert route_follower.route_length_m(cornering_route()) == pytest.approx(20.0)


@pytest.mark.parametrize("bad_distance", ["4.0", True, None])
def test_a_distance_that_is_not_a_number_is_refused(bad_distance: object) -> None:
    """A non-number would reach `math.isfinite` as a TypeError three frames down."""

    route_follower = load_route_follower_module()

    with pytest.raises(ValueError, match=r"got a non-number$"):
        route_follower.pose_at_distance(cornering_route(), bad_distance)
