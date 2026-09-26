"""Contracts for the constant-velocity Kalman velocity estimate.

The released tracker differences successive observed positions, which turns
half a metre of position noise into about seven metres per second of velocity
noise at 10 Hz. Two arms of the policy v2 study (section 4.4 of its analysis
plan) replace that estimate with one constant-velocity Kalman filter per source
track and per axis, with parameters fixed before any run.

The filter is small enough to check against its own definition. Its closed-form
update must equal the matrix equations; a noise-free track moving at constant
velocity must keep its velocity; its covariance must settle at the Riccati fixed
point the analysis plan states; and its lag behind a braking target must be the
amount the analysis plan declares as the filter's cost. The released finite
difference stays the default everywhere, and a run that names it binds exactly
what the released runs bound.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aebrisk.errors.pipeline import ERROR_CHANNELS
from aebrisk.observation import tracking
from aebrisk.observation.models import TrackState
from aebrisk.simulation.common_cohort import ExperimentConfiguration

DT_S = 0.1
FIRST_TIMESTAMP_US = 1_600_000_000_000_000


def matrix_form(axis: Any, measurement_m: float, dt_s: float, parameters: Any) -> tuple:
    """One predict and update step written as the textbook matrix equations."""

    transition = np.array([[1.0, dt_s], [0.0, 1.0]])
    process = parameters.process_accel_std_mps2**2 * np.array(
        [[dt_s**4 / 4.0, dt_s**3 / 2.0], [dt_s**3 / 2.0, dt_s**2]]
    )
    observation = np.array([[1.0, 0.0]])
    noise = np.array([[parameters.measurement_std_m**2]])

    state = transition @ np.array([axis.position_m, axis.velocity_mps])
    covariance = (
        transition @ np.array([[axis.p_pp, axis.p_pv], [axis.p_pv, axis.p_vv]]) @ transition.T
        + process
    )
    innovation_covariance = observation @ covariance @ observation.T + noise
    gain = covariance @ observation.T @ np.linalg.inv(innovation_covariance)
    state = state + gain @ (np.array([measurement_m]) - observation @ state)
    covariance = (np.eye(2) - gain @ observation) @ covariance
    return state, covariance


def settled(velocity_mps: float = 0.0, steps: int = 400) -> Any:
    """A filter that has followed a noise-free constant-velocity target to steady state."""

    parameters = tracking.CV_KALMAN_PARAMETERS
    axis = tracking.kalman_initialise(0.0, velocity_mps, parameters)
    for step in range(1, steps + 1):
        axis = tracking.kalman_predict_update(axis, velocity_mps * step * DT_S, DT_S, parameters)
    return axis


@settings(deadline=None, derandomize=True)
@given(
    position_m=st.floats(min_value=-1.0e4, max_value=1.0e4),
    velocity_mps=st.floats(min_value=-50.0, max_value=50.0),
    p_pp=st.floats(min_value=1.0e-4, max_value=10.0),
    p_vv=st.floats(min_value=1.0e-4, max_value=10.0),
    correlation=st.floats(min_value=-0.99, max_value=0.99),
    measurement_m=st.floats(min_value=-1.0e4, max_value=1.0e4),
    dt_s=st.floats(min_value=1.0e-3, max_value=2.0),
    process_accel_std_mps2=st.floats(min_value=0.1, max_value=10.0),
    measurement_std_m=st.floats(min_value=0.01, max_value=5.0),
)
def test_predict_update_matches_the_matrix_form(
    position_m: float,
    velocity_mps: float,
    p_pp: float,
    p_vv: float,
    correlation: float,
    measurement_m: float,
    dt_s: float,
    process_accel_std_mps2: float,
    measurement_std_m: float,
) -> None:
    """The closed-form 2 x 2 update is the Kalman filter, not an approximation of it."""

    parameters = tracking.CVKalmanParameters(
        process_accel_std_mps2=process_accel_std_mps2,
        measurement_std_m=measurement_std_m,
    )
    axis = tracking.KalmanAxis(
        position_m=position_m,
        velocity_mps=velocity_mps,
        p_pp=p_pp,
        p_pv=correlation * math.sqrt(p_pp * p_vv),
        p_vv=p_vv,
    )

    updated = tracking.kalman_predict_update(axis, measurement_m, dt_s, parameters)
    state, covariance = matrix_form(axis, measurement_m, dt_s, parameters)

    np.testing.assert_allclose(
        [updated.position_m, updated.velocity_mps], state, rtol=1.0e-9, atol=1.0e-9
    )
    np.testing.assert_allclose(
        [[updated.p_pp, updated.p_pv], [updated.p_pv, updated.p_vv]],
        covariance,
        rtol=1.0e-9,
        atol=1.0e-12,
    )


@pytest.mark.parametrize(
    ("start_m", "velocity_mps"),
    [(0.0, 0.0), (35.0, 12.5), (-120.0, -7.25), (60.0, 0.4)],
)
def test_a_noise_free_constant_velocity_track_keeps_its_velocity(
    start_m: float, velocity_mps: float
) -> None:
    """With every innovation zero there is nothing to move the estimate."""

    parameters = tracking.CV_KALMAN_PARAMETERS
    axis = tracking.kalman_initialise(start_m, velocity_mps, parameters)

    for step in range(1, 201):
        axis = tracking.kalman_predict_update(
            axis, start_m + velocity_mps * step * DT_S, DT_S, parameters
        )
        assert abs(axis.velocity_mps - velocity_mps) <= 1.0e-12


def test_the_steady_state_velocity_variance_is_the_riccati_fixed_point() -> None:
    """The analysis plan's 0.69 m/s and gain [0.292, 0.505] are this filter's steady state."""

    parameters = tracking.CV_KALMAN_PARAMETERS
    steady = settled()

    again = tracking.kalman_predict_update(steady, 0.0, DT_S, parameters)
    assert (again.p_pp, again.p_pv, again.p_vv) == pytest.approx(
        (steady.p_pp, steady.p_pv, steady.p_vv), rel=1.0e-12
    )
    assert math.sqrt(steady.p_vv) == pytest.approx(0.6903, abs=5.0e-5)

    # From a settled estimate of a target at rest at the origin, a measurement
    # one metre away is an innovation of one metre, so the estimate moves by
    # exactly the gain.
    kicked = tracking.kalman_predict_update(steady, 1.0, DT_S, parameters)
    assert (kicked.position_m, kicked.velocity_mps) == pytest.approx((0.2925, 0.5047), abs=5.0e-5)


@pytest.mark.parametrize(
    ("deceleration_mps2", "lag_after_half_a_second", "lag_after_one_second"),
    [(6.0, 2.400, 3.317), (3.0, 1.200, 1.659)],
)
def test_the_filter_lags_a_constant_deceleration_by_the_documented_amount(
    deceleration_mps2: float,
    lag_after_half_a_second: float,
    lag_after_one_second: float,
) -> None:
    """The cost the analysis plan declares: the estimate trails a braking target."""

    parameters = tracking.CV_KALMAN_PARAMETERS
    speed_mps = 20.0
    steps = 400
    axis = settled(speed_mps, steps)
    start_m = speed_mps * steps * DT_S

    lags = {}
    for step in range(1, 11):
        elapsed_s = step * DT_S
        position_m = start_m + speed_mps * elapsed_s - 0.5 * deceleration_mps2 * elapsed_s**2
        axis = tracking.kalman_predict_update(axis, position_m, DT_S, parameters)
        lags[step] = axis.velocity_mps - (speed_mps - deceleration_mps2 * elapsed_s)

    assert lags[5] == pytest.approx(lag_after_half_a_second, abs=5.0e-4)
    assert lags[10] == pytest.approx(lag_after_one_second, abs=5.0e-4)


def test_the_filter_parameters_are_the_pre_registered_ones() -> None:
    """Sigma_a 3.0 m/s^2, sigma_R 0.50 m and sigma_v0 1.0 m/s, and no other set."""

    parameters = tracking.CVKalmanParameters()

    assert parameters.process_accel_std_mps2 == 3.0
    assert parameters.measurement_std_m == 0.5
    assert parameters.initial_velocity_std_mps == 1.0
    assert parameters == tracking.CV_KALMAN_PARAMETERS
    assert tracking.CVKalmanVelocity().parameters is tracking.CV_KALMAN_PARAMETERS
    with pytest.raises(AttributeError):
        parameters.measurement_std_m = 1.0  # type: ignore[misc]


def test_initialisation_is_the_documented_prior() -> None:
    """p = z, v = the reported velocity, and P0 = diag(sigma_R^2, sigma_v0^2)."""

    assert tracking.kalman_initialise(12.5, -3.0, tracking.CV_KALMAN_PARAMETERS) == (
        tracking.KalmanAxis(position_m=12.5, velocity_mps=-3.0, p_pp=0.25, p_pv=0.0, p_vv=1.0)
    )
    wider = tracking.CVKalmanParameters(measurement_std_m=2.0, initial_velocity_std_mps=3.0)
    assert tracking.kalman_initialise(1.0, 2.0, wider) == tracking.KalmanAxis(
        position_m=1.0, velocity_mps=2.0, p_pp=4.0, p_pv=0.0, p_vv=9.0
    )


@pytest.mark.parametrize("dt_s", [0.0, -0.1, float("nan"), float("inf")])
def test_a_step_without_positive_elapsed_time_is_refused(dt_s: float) -> None:
    """Predicting over no time, or backwards, has no meaning for the filter."""

    axis = tracking.kalman_initialise(0.0, 0.0, tracking.CV_KALMAN_PARAMETERS)

    with pytest.raises(ValueError, match=r"^dt_s must be finite and positive"):
        tracking.kalman_predict_update(axis, 0.0, dt_s, tracking.CV_KALMAN_PARAMETERS)


@pytest.mark.parametrize(
    "field", ["process_accel_std_mps2", "measurement_std_m", "initial_velocity_std_mps"]
)
@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan"), float("inf"), True, "1"])
def test_an_impossible_filter_parameter_is_refused(field: str, bad_value: object) -> None:
    """A zero or negative standard deviation is not a sensor model."""

    with pytest.raises(ValueError, match=rf"^{field} must be a finite positive number, got "):
        tracking.CVKalmanParameters(**{field: bad_value})  # type: ignore[arg-type]


def lead_ahead(step: int, timestamp_us: int) -> tuple[TrackState, ...]:
    """A body 60 m ahead moving at 5 m/s, stamped by the source."""

    return (
        TrackState(
            track_id="lead-1",
            category="vehicle",
            center_xy_m=(60.0 + 5.0 * step * DT_S, 0.0),
            yaw_rad=0.0,
            size_lw_m=(4.5, 1.9),
            velocity_xy_mps=(5.0, 0.0),
            visible=True,
            source_timestamp_us=timestamp_us,
            covariance_xy=(0.0, 0.0, 0.0, 0.0),
        ),
    )


def run_loop(**factors: str) -> Any:
    from aebrisk.simulation import step_loop

    def frame_at_step(step: int) -> tuple[int, tuple[TrackState, ...]]:
        stamp = FIRST_TIMESTAMP_US + step * round(DT_S * 1_000_000)
        return stamp, lead_ahead(step, stamp)

    severities = dict.fromkeys(ERROR_CHANNELS, "zero")
    severities["localization_shape"] = "medium"
    return step_loop.run_steps(
        token="kalman-0001",
        route_xy=np.array([[0.0, 0.0], [400.0, 0.0]], dtype=np.float64),
        frame_at_step=frame_at_step,
        steps=30,
        initial_speed_mps=10.0,
        ego_size_lw_m=(4.8, 2.0),
        configuration=ExperimentConfiguration(
            configuration_id="localization_shape-medium",
            aeb_enabled=True,
            observation_mode="corrupted",
            severity_by_channel=severities,
            replicate_count=1,
            **factors,
        ),
        replicate=0,
        protocol_hash="e" * 64,
        dt_s=DT_S,
        map_speed_limit_mps=None,
    )


def test_the_step_loop_binds_the_estimator_the_configuration_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The released binding is unchanged, and a Kalman run fuses its tracks."""

    from aebrisk.simulation import step_loop

    bindings: list[tuple[tuple[Any, ...], dict[str, Any], Any]] = []
    original = step_loop.BIND_CHANNELS

    def recording(*arguments: Any, **keywords: Any) -> Any:
        bound = original(*arguments, **keywords)
        bindings.append((arguments, keywords, bound))
        return bound

    monkeypatch.setattr(step_loop, "BIND_CHANNELS", recording)

    released = run_loop()
    named = run_loop(velocity_estimator="finite-difference")
    kalman = run_loop(velocity_estimator="cv-kalman")
    kalman_again = run_loop(velocity_estimator="cv-kalman")

    assert [keywords for _, keywords, _ in bindings] == [
        {"dt_s": DT_S},
        {"dt_s": DT_S},
        {"dt_s": DT_S, "velocity_estimator": "cv-kalman"},
        {"dt_s": DT_S, "velocity_estimator": "cv-kalman"},
    ]
    assert all(len(arguments) == 1 for arguments, _, _ in bindings)
    assert named == released
    assert bindings[0][2].velocity_filter.state == {}
    assert bindings[1][2].velocity_filter.state == {}
    assert sorted(bindings[2][2].velocity_filter.state) == ["lead-1"]
    assert bindings[2][2].velocity_filter is not bindings[3][2].velocity_filter
    assert kalman_again == kalman
