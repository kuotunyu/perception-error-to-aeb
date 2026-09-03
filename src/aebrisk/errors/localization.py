"""The object that is seen, but not quite where or how big it is.

This is where an observation stops being the recording. Everything this channel
touches reaches the threat assessment directly: a position error changes the
distance, a yaw error changes which way the box points, and a size error changes
whether two boxes overlap at all.

Three choices are deliberate.

Position and yaw errors are **absolute**, because a detector's localization error
does not scale with the object it is looking at. Size error is **relative**,
because a 25 cm error on a pedestrian and on a truck are not the same mistake.

The covariance **grows** with the position variance rather than being left alone.
A perception stack that is off by a metre and reports that it might be is a
different input to a planner than one that is off by a metre and claims
certainty; modelling the error without the uncertainty would be a third,
unrealistic system.

Every track is perturbed whether or not it is visible. Skipping hidden tracks
would make a track's geometry depend on whether dropout happened to hide it
earlier in the same frame, and the factorial attribution assumes the channels do
not couple like that.
"""

from __future__ import annotations

import math

from aebrisk.errors.pipeline import ErrorKey, track_field_generator
from aebrisk.observation.models import TrackState

#: No detector reports a box smaller than this, and one that did would be
#: undetectable by any overlap test: a zero-area box can never intersect.
MINIMUM_BOX_DIMENSION_M = 0.1

TWO_PI = 2.0 * math.pi


def wrap_to_pi(angle_rad: float) -> float:
    """Fold an angle into ``[-pi, pi)``.

    Half open on purpose. Leaving a heading unwrapped would let two orientations
    that are a degree apart compare as though they were a full turn apart, and
    every heading comparison downstream assumes one canonical representation.
    """

    return (angle_rad + math.pi) % TWO_PI - math.pi


def perturbation_draw(key: ErrorKey, track_id: str, step: int, field: str) -> float:
    """Return this track's standard normal draw for one field at one step."""

    return float(track_field_generator(key, track_id, step, field).standard_normal())


def _require_deviation(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be a finite, non-negative magnitude, got {value!r}")
    return float(value)


def _perturb_one(
    track: TrackState,
    *,
    position_std_m: float,
    yaw_std_rad: float,
    size_relative_std: float,
    key: ErrorKey,
    step: int,
) -> TrackState:
    """Perturb one observation.

    Split out of the loop rather than closed over it: a closure that captured
    the loop variable would read whichever track the loop had reached by the
    time it was called, which is correct here only by accident.
    """

    def draw(field: str) -> float:
        return perturbation_draw(key, track.track_id, step, field)

    position_variance = position_std_m * position_std_m
    return TrackState(
        track_id=track.track_id,
        category=track.category,
        center_xy_m=(
            track.center_xy_m[0] + position_std_m * draw("position_x"),
            track.center_xy_m[1] + position_std_m * draw("position_y"),
        ),
        yaw_rad=wrap_to_pi(track.yaw_rad + yaw_std_rad * draw("yaw")),
        size_lw_m=(
            max(
                MINIMUM_BOX_DIMENSION_M,
                track.size_lw_m[0] * (1.0 + size_relative_std * draw("size_length")),
            ),
            max(
                MINIMUM_BOX_DIMENSION_M,
                track.size_lw_m[1] * (1.0 + size_relative_std * draw("size_width")),
            ),
        ),
        velocity_xy_mps=track.velocity_xy_mps,
        visible=track.visible,
        source_timestamp_us=track.source_timestamp_us,
        covariance_xy=(
            track.covariance_xy[0] + position_variance,
            track.covariance_xy[1],
            track.covariance_xy[2],
            track.covariance_xy[3] + position_variance,
        ),
    )


def perturb_localization_shape(
    tracks: tuple[TrackState, ...],
    *,
    position_std_m: float,
    yaw_std_deg: float,
    size_relative_std: float,
    key: ErrorKey,
    step: int,
) -> tuple[TrackState, ...]:
    """Move, turn and resize each observation, deterministically and per track."""

    position_std_m = _require_deviation(position_std_m, "position_std_m")
    yaw_std_deg = _require_deviation(yaw_std_deg, "yaw_std_deg")
    size_relative_std = _require_deviation(size_relative_std, "size_relative_std")
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise ValueError("step must be a non-negative integer")

    yaw_std_rad = math.radians(yaw_std_deg)
    return tuple(
        _perturb_one(
            track,
            position_std_m=position_std_m,
            yaw_std_rad=yaw_std_rad,
            size_relative_std=size_relative_std,
            key=key,
            step=step,
        )
        for track in tracks
    )
