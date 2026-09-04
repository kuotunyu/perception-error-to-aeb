"""Contracts for the limiter between what the AEB asks for and what the car does.

A state machine that jumps from coasting to -6 m/s^2 in one step is not a
vehicle, and a study that let it would report comfort numbers no real system
could produce. The limiter is what makes the simulated actuator physical: it
bounds the acceleration and bounds how fast it may change.

Both limits are load-bearing in opposite directions. The bounds stop the
controller commanding more braking than the vehicle has. The jerk limit stops it
arriving there instantly, which is what the intervention-comfort metric measures
and what a passenger feels.
"""

from __future__ import annotations

import math
from types import ModuleType

import pytest


def load_controller_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.aeb import controller
    except ImportError:
        pytest.fail("aebrisk.aeb.controller is missing", pytrace=False)
    return controller


def test_a_reachable_target_is_returned_unchanged() -> None:
    """The limiter must be transparent when it has nothing to do."""

    controller = load_controller_module()

    assert controller.limit_acceleration(-1.0, -1.2) == pytest.approx(-1.2)


def test_the_jerk_limit_caps_one_step_at_half_a_metre_per_second_squared() -> None:
    """5 m/s^3 over a 0.1 s step is 0.5 m/s^2, and that is the whole budget.

    Full braking from a coast therefore takes twelve steps to arrive, which is
    1.2 s of the intervention the comfort metric is measuring.
    """

    controller = load_controller_module()

    assert controller.limit_acceleration(0.0, -6.0) == pytest.approx(-0.5)


def test_the_jerk_limit_applies_in_both_directions() -> None:
    """Releasing a brake abruptly is as unphysical as applying one."""

    controller = load_controller_module()

    assert controller.limit_acceleration(-6.0, 0.0) == pytest.approx(-5.5)


def test_full_braking_is_reached_by_repeated_steps() -> None:
    """The limiter must converge, not oscillate or stall short of the target."""

    controller = load_controller_module()

    acceleration = 0.0
    for _ in range(12):
        acceleration = controller.limit_acceleration(acceleration, -6.0)

    assert acceleration == pytest.approx(-6.0)


def test_the_lower_bound_clamps_a_command_the_vehicle_cannot_produce() -> None:
    """A policy asking for -9 m/s^2 gets the -6 the vehicle has."""

    controller = load_controller_module()

    assert controller.limit_acceleration(-5.8, -9.0) == pytest.approx(-6.0)


def test_the_upper_bound_clamps_acceleration_too() -> None:
    """The nominal controller is bounded by the same actuator."""

    controller = load_controller_module()

    assert controller.limit_acceleration(1.8, 5.0) == pytest.approx(2.0)


def test_the_bound_wins_over_the_jerk_allowance() -> None:
    """Clamping after the jerk step, not before, or the bound could be exceeded."""

    controller = load_controller_module()

    assert controller.limit_acceleration(-5.9, -6.5) == pytest.approx(-6.0)


def test_a_previous_value_outside_the_bounds_is_brought_back_inside() -> None:
    """A state restored from a record must not be able to stay illegal."""

    controller = load_controller_module()

    assert controller.limit_acceleration(-8.0, -6.0) == pytest.approx(-6.0)


def test_the_step_duration_scales_the_jerk_budget() -> None:
    """The limit is per second, so a longer step allows a larger change."""

    controller = load_controller_module()

    assert controller.limit_acceleration(0.0, -6.0, dt_s=0.2) == pytest.approx(-1.0)


def test_the_bounds_are_configurable_and_actually_used() -> None:
    """They come from the committed policy, so the defaults must not be hard wired."""

    controller = load_controller_module()

    assert controller.limit_acceleration(
        0.0, -6.0, min_mps2=-2.0, jerk_limit_mps3=100.0
    ) == pytest.approx(-2.0)


@pytest.mark.parametrize("bad_value", [0.0, -0.1, float("nan")])
def test_an_impossible_step_duration_is_refused(bad_value: float) -> None:
    """A zero step is an unlimited jerk allowance in disguise."""

    controller = load_controller_module()

    with pytest.raises(ValueError, match=r"^dt_s must "):
        controller.limit_acceleration(0.0, -6.0, dt_s=bad_value)


@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan")])
def test_an_impossible_jerk_limit_is_refused(bad_value: float) -> None:
    """A zero limit would freeze the acceleration at whatever it already was."""

    controller = load_controller_module()

    with pytest.raises(ValueError, match=r"^jerk_limit_mps3 must "):
        controller.limit_acceleration(0.0, -6.0, jerk_limit_mps3=bad_value)


def test_inverted_bounds_are_refused() -> None:
    """A minimum above the maximum has no correct clamp, only a silent one."""

    controller = load_controller_module()

    with pytest.raises(ValueError, match=r"^min_mps2\ "):
        controller.limit_acceleration(0.0, -1.0, min_mps2=2.0, max_mps2=-6.0)


@pytest.mark.parametrize("field", ["previous_mps2", "target_mps2"])
def test_a_non_finite_acceleration_is_refused(field: str) -> None:
    """A NaN command propagates into every position the simulation integrates."""

    controller = load_controller_module()
    arguments = {"previous_mps2": 0.0, "target_mps2": -1.0}
    arguments[field] = math.nan

    with pytest.raises(ValueError, match=field):
        controller.limit_acceleration(**arguments)


@pytest.mark.parametrize("field", ["previous_mps2", "target_mps2", "min_mps2", "max_mps2"])
@pytest.mark.parametrize("bad_value", ["-1.0", True, None])
def test_an_acceleration_that_is_not_a_number_is_refused(
    field: str,
    bad_value: object,
) -> None:
    """``True`` would silently mean one metre per second squared."""

    controller = load_controller_module()
    arguments: dict[str, object] = {"previous_mps2": 0.0, "target_mps2": -1.0}
    arguments[field] = bad_value

    with pytest.raises(ValueError, match=f"{field} must be a number"):
        controller.limit_acceleration(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["dt_s", "jerk_limit_mps3"])
@pytest.mark.parametrize("bad_value", ["0.1", True, None])
def test_a_positive_limit_that_is_not_a_number_is_refused(
    field: str,
    bad_value: object,
) -> None:
    """The same guard on the two values that must be strictly positive."""

    controller = load_controller_module()
    arguments: dict[str, object] = {"previous_mps2": 0.0, "target_mps2": -1.0}
    arguments[field] = bad_value

    with pytest.raises(ValueError, match=f"{field} must be a number"):
        controller.limit_acceleration(**arguments)  # type: ignore[arg-type]
