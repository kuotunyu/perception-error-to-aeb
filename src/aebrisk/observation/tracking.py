"""The velocity a real tracker would have had to estimate.

The oracle knows every object's exact velocity, and using it would quietly
exempt the study from the error it exists to measure. A real stack differences
successive positions, which amplifies position error by the sampling rate: half
a metre of error at 10 Hz is five metres per second, and time-to-collision
divides by that number. The amplification is the point, not a side effect.

Two details keep the estimate honest. It divides by the time that actually
elapsed rather than the nominal step, because a track missing for three frames
has moved three frames' worth and the nominal step would report three times the
speed. And where there is no previous position the oracle value is used and said
to have been used, because a run that silently mixed estimated and oracle
velocities would show an error attribution that belongs to neither.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from aebrisk.observation.models import TrackState

if TYPE_CHECKING:
    # Only for annotations: the fragmentation channel imports this module, so a
    # runtime import back would be circular.
    from aebrisk.errors.fragmentation import TrackMemory

#: Named rather than written as bare strings at each call site: these two values
#: reach the run record, where a typo would look like a third source.
VELOCITY_FROM_ORACLE = "oracle"
VELOCITY_FROM_FINITE_DIFFERENCE = "finite_difference"


def velocity_source(previous_center_xy_m: Optional[tuple[float, float]]) -> str:
    """Name where this step's velocity came from."""

    if previous_center_xy_m is None:
        return VELOCITY_FROM_ORACLE
    return VELOCITY_FROM_FINITE_DIFFERENCE


def _require_elapsed(dt_s: float) -> float:
    if not isinstance(dt_s, (int, float)) or isinstance(dt_s, bool):
        raise ValueError("dt_s must be a number")
    if not math.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError(f"dt_s must be finite and positive, got {dt_s!r}")
    return float(dt_s)


def finite_difference_velocity(
    previous_center_xy_m: tuple[float, float],
    current_center_xy_m: tuple[float, float],
    dt_s: float,
) -> tuple[float, float]:
    """Estimate velocity from two observed positions and the time between them."""

    elapsed = _require_elapsed(dt_s)
    return (
        (current_center_xy_m[0] - previous_center_xy_m[0]) / elapsed,
        (current_center_xy_m[1] - previous_center_xy_m[1]) / elapsed,
    )


def estimate_velocity(
    previous: Optional[TrackState],
    current: TrackState,
    dt_s: float,
) -> tuple[float, float]:
    """Difference two observations, or fall back to the oracle on the first frame.

    The elapsed time is not checked in the fallback branch: with no previous
    state there is nothing to divide, and refusing there would make a track's
    first frame unrepresentable.
    """

    if previous is None:
        return current.velocity_xy_mps
    return finite_difference_velocity(previous.center_xy_m, current.center_xy_m, dt_s)


# --------------------------------------------------------------------------
# A constant-velocity Kalman filter, the policy v2 study's alternative estimate
# --------------------------------------------------------------------------

_MICROSECONDS_PER_SECOND = 1_000_000


def _require_standard_deviation(value: float, name: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0.0
    ):
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")


@dataclass(frozen=True)
class CVKalmanParameters:
    """The filter's three standard deviations, fixed before any run.

    The process noise is a white acceleration of standard deviation
    ``process_accel_std_mps2``. The measurement noise is ``measurement_std_m``
    in every cell, because a deployed tracker has one sensor model rather than
    one per error severity. A new track starts with velocity variance
    ``initial_velocity_std_mps`` squared around the reported velocity.
    """

    process_accel_std_mps2: float = 3.0
    measurement_std_m: float = 0.5
    initial_velocity_std_mps: float = 1.0

    def __post_init__(self) -> None:
        for name in ("process_accel_std_mps2", "measurement_std_m", "initial_velocity_std_mps"):
            _require_standard_deviation(getattr(self, name), name)


#: The one parameter set the policy v2 study runs (section 4.4 of its analysis
#: plan). No other values are run or reported.
CV_KALMAN_PARAMETERS = CVKalmanParameters()


@dataclass(frozen=True)
class KalmanAxis:
    """One axis of one track: the state [p, v] and its symmetric covariance."""

    position_m: float
    velocity_mps: float
    p_pp: float
    p_pv: float
    p_vv: float


def kalman_initialise(
    position_m: float, velocity_mps: float, parameters: CVKalmanParameters
) -> KalmanAxis:
    """Start an axis at the observed position and the reported velocity.

    The covariance is diag(sigma_R^2, sigma_v0^2): the position is as uncertain
    as one measurement, and the velocity as the initial velocity allowance.
    """

    return KalmanAxis(
        position_m=position_m,
        velocity_mps=velocity_mps,
        p_pp=parameters.measurement_std_m**2,
        p_pv=0.0,
        p_vv=parameters.initial_velocity_std_mps**2,
    )


def kalman_predict_update(
    axis: KalmanAxis,
    measurement_m: float,
    dt_s: float,
    parameters: CVKalmanParameters,
) -> KalmanAxis:
    """Predict one axis over ``dt_s`` and fuse one position measurement.

    The 2 x 2 equations written out in float64: F = [[1, dt], [0, 1]],
    Q = sigma_a^2 [[dt^4/4, dt^3/2], [dt^3/2, dt^2]], H = [1, 0] and
    R = sigma_R^2, with the covariance updated as (I - K H) P.
    """

    elapsed = _require_elapsed(dt_s)
    accel_variance = parameters.process_accel_std_mps2**2

    position = axis.position_m + elapsed * axis.velocity_mps
    velocity = axis.velocity_mps
    p_pp = (
        axis.p_pp
        + 2.0 * elapsed * axis.p_pv
        + elapsed**2 * axis.p_vv
        + accel_variance * elapsed**4 / 4.0
    )
    p_pv = axis.p_pv + elapsed * axis.p_vv + accel_variance * elapsed**3 / 2.0
    p_vv = axis.p_vv + accel_variance * elapsed**2

    innovation_variance = p_pp + parameters.measurement_std_m**2
    gain_p = p_pp / innovation_variance
    gain_v = p_pv / innovation_variance
    innovation = measurement_m - position
    return KalmanAxis(
        position_m=position + gain_p * innovation,
        velocity_mps=velocity + gain_v * innovation,
        p_pp=(1.0 - gain_p) * p_pp,
        p_pv=(1.0 - gain_p) * p_pv,
        p_vv=p_vv - gain_v * p_pv,
    )


class CVKalmanVelocity:
    """One constant-velocity Kalman filter per source track and per axis, for one run.

    Only the velocity it emits changes; the position, heading and size passed on
    stay the observed ones. The fragmentation channel decides when it is called,
    under the released memory rules: a new or reacquired track starts the filter
    from the reported velocity, every detection outside an outage is fused
    whether or not dropout hid it, and a detection with no elapsed time since the
    last fused one is not fused and reports its velocity, as the finite
    difference does.
    """

    def __init__(self, parameters: CVKalmanParameters = CV_KALMAN_PARAMETERS) -> None:
        self.parameters = parameters
        #: Each source track's two axes and the source time of the last fused
        #: measurement.
        self.state: dict[str, tuple[KalmanAxis, KalmanAxis, int]] = {}

    def initialise(self, track: TrackState) -> tuple[float, float]:
        """Start the track's filter from this observation, and emit the reported velocity."""

        self.state[track.track_id] = (
            kalman_initialise(track.center_xy_m[0], track.velocity_xy_mps[0], self.parameters),
            kalman_initialise(track.center_xy_m[1], track.velocity_xy_mps[1], self.parameters),
            track.source_timestamp_us,
        )
        return track.velocity_xy_mps

    def update(self, track: TrackState, memory: TrackMemory) -> tuple[float, float]:
        """Fuse this observation into the track's filter, and emit the filtered velocity."""

        filtered = self.state.get(track.track_id)
        if filtered is None or memory.previous_center_xy_m is None:
            # No history to predict from: where the finite difference reports
            # the velocity, the filter starts again from it.
            return self.initialise(track)
        x_axis, y_axis, fused_at_us = filtered
        elapsed_us = track.source_timestamp_us - fused_at_us
        if elapsed_us <= 0:
            # A repeated or out-of-order timestamp gives nothing to predict
            # over. The reported velocity is emitted and nothing is fused.
            return track.velocity_xy_mps
        elapsed_s = elapsed_us / _MICROSECONDS_PER_SECOND
        x_axis = kalman_predict_update(x_axis, track.center_xy_m[0], elapsed_s, self.parameters)
        y_axis = kalman_predict_update(y_axis, track.center_xy_m[1], elapsed_s, self.parameters)
        self.state[track.track_id] = (x_axis, y_axis, track.source_timestamp_us)
        return (x_axis.velocity_mps, y_axis.velocity_mps)
