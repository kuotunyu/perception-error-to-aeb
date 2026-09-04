"""Contracts for the velocity a real tracker would have had to estimate.

The oracle hands out an exact velocity for every object, and using it would
quietly exempt the study from the error it exists to measure. A real stack
differences successive positions, so a position error of half a metre at 10 Hz
becomes a velocity error of five metres per second, and it is that amplified
number the AEB's time-to-collision divides by.

So velocity is a finite difference of the *corrupted* positions, taken over the
real elapsed time rather than the nominal step, because a track that was missing
for three frames has moved three frames' worth. Where there is no previous
position the oracle value is used, and the fact that it was used is reported,
because a run that silently mixed oracle and estimated velocities would show an
error attribution that belongs to neither.
"""

from __future__ import annotations

from types import ModuleType

import pytest

BASE_US = 1_600_000_000_000_000


def load_tracking_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.observation import tracking
    except ImportError:
        pytest.fail("aebrisk.observation.tracking is missing", pytrace=False)
    return tracking


def make_track(
    center: tuple[float, float],
    *,
    track_id: str = "t-0001",
    velocity: tuple[float, float] = (99.0, 99.0),
    timestamp_us: int = BASE_US,
):
    from aebrisk.observation.models import TrackState

    return TrackState(
        track_id=track_id,
        category="vehicle",
        center_xy_m=center,
        yaw_rad=0.0,
        size_lw_m=(4.5, 1.9),
        velocity_xy_mps=velocity,
        visible=True,
        source_timestamp_us=timestamp_us,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )


def test_velocity_is_the_finite_difference_of_successive_positions() -> None:
    """The estimate must come from what was observed, not from the oracle."""

    tracking = load_tracking_module()
    previous = make_track((10.0, 2.0))
    current = make_track((11.0, 2.5))

    velocity = tracking.estimate_velocity(previous, current, 0.1)

    assert velocity == pytest.approx((10.0, 5.0))


def test_the_finite_difference_amplifies_a_position_error() -> None:
    """The reason this channel matters, stated as its own test.

    Half a metre of position error at 10 Hz is five metres per second of
    velocity error, and time-to-collision divides by that number.
    """

    tracking = load_tracking_module()
    truth_previous = make_track((10.0, 0.0))
    truth_current = make_track((11.0, 0.0))
    perturbed_current = make_track((11.5, 0.0))

    clean = tracking.estimate_velocity(truth_previous, truth_current, 0.1)
    noisy = tracking.estimate_velocity(truth_previous, perturbed_current, 0.1)

    assert noisy[0] - clean[0] == pytest.approx(5.0)


def test_no_previous_observation_falls_back_to_the_oracle_velocity() -> None:
    """A track seen for the first time has nothing to difference against."""

    tracking = load_tracking_module()
    current = make_track((11.0, 2.5), velocity=(7.0, -1.0))

    assert tracking.estimate_velocity(None, current, 0.1) == (7.0, -1.0)


def test_the_oracle_fallback_is_reported() -> None:
    """Silently mixing oracle and estimated velocities would misattribute the error."""

    tracking = load_tracking_module()

    assert tracking.velocity_source(None) == tracking.VELOCITY_FROM_ORACLE
    assert tracking.velocity_source((10.0, 2.0)) == tracking.VELOCITY_FROM_FINITE_DIFFERENCE


def test_the_two_velocity_sources_are_distinct_names() -> None:
    """A constant pair that collapsed to one value would make the flag useless."""

    tracking = load_tracking_module()

    assert tracking.VELOCITY_FROM_ORACLE != tracking.VELOCITY_FROM_FINITE_DIFFERENCE


def test_a_centre_difference_uses_the_elapsed_time_it_is_given() -> None:
    """A track missing for three frames has moved three frames' worth.

    Dividing that displacement by the nominal step would report three times the
    speed, so the caller passes the real elapsed time.
    """

    tracking = load_tracking_module()

    velocity = tracking.finite_difference_velocity((10.0, 0.0), (13.0, 0.0), 0.3)

    assert velocity == pytest.approx((10.0, 0.0))


def test_a_stationary_object_estimates_zero_velocity() -> None:
    """The base case, and the one the AEB must not turn into a threat."""

    tracking = load_tracking_module()

    assert tracking.finite_difference_velocity((5.0, 5.0), (5.0, 5.0), 0.1) == (0.0, 0.0)


def test_the_estimate_is_signed() -> None:
    """A receding object must not read as an approaching one."""

    tracking = load_tracking_module()

    velocity = tracking.finite_difference_velocity((10.0, 0.0), (9.0, -0.5), 0.1)

    assert velocity[0] == pytest.approx(-10.0)
    assert velocity[1] == pytest.approx(-5.0)


@pytest.mark.parametrize("bad_value", [0.0, -0.1, float("nan"), float("inf")])
def test_an_impossible_elapsed_time_is_refused(bad_value: float) -> None:
    """Zero elapsed time is a division by zero, and a negative one is time running back."""

    tracking = load_tracking_module()

    with pytest.raises(ValueError, match=r"^dt_s must "):
        tracking.finite_difference_velocity((0.0, 0.0), (1.0, 0.0), bad_value)


@pytest.mark.parametrize("bad_value", ["0.1", True, None])
def test_an_elapsed_time_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean one second."""

    tracking = load_tracking_module()

    with pytest.raises(ValueError, match=r"^dt_s\ must\ be\ a\ number"):
        tracking.finite_difference_velocity((0.0, 0.0), (1.0, 0.0), bad_value)  # type: ignore[arg-type]


def test_estimate_velocity_refuses_an_impossible_elapsed_time_too() -> None:
    """The wrapper must not become a way around the guard."""

    tracking = load_tracking_module()

    with pytest.raises(ValueError, match=r"^dt_s must "):
        tracking.estimate_velocity(make_track((0.0, 0.0)), make_track((1.0, 0.0)), 0.0)


def test_the_oracle_fallback_is_reached_without_touching_the_elapsed_time() -> None:
    """With no previous state there is nothing to divide, so no guard applies.

    Refusing here would make a track's first frame unrepresentable.
    """

    tracking = load_tracking_module()

    assert tracking.estimate_velocity(None, make_track((0.0, 0.0), velocity=(3.0, 0.0)), 0.0) == (
        3.0,
        0.0,
    )
