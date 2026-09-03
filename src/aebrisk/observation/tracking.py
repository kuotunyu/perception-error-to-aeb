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
from typing import Optional

from aebrisk.observation.models import TrackState

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
