"""Turn one nuPlan iteration into one `WorldFrame`, and nothing else.

This module is the only place in the project that knows what nuPlan calls
things. It is deliberately structural: it reads attributes off whatever scenario
object it is handed rather than importing `AbstractScenario`.

That is not a style preference. `AbstractScenario` imports the devkit's
observation types, which import its image utilities, which import OpenCV. A
study that reads world state only would otherwise have to install the entire
sensor pipeline in order to name a type it never uses, and the boundary this
project claims — world state, database and maps; no sensor replay — would exist
only as a promise. Depending on the shape instead makes the environment enforce
it: the container has no OpenCV, so a future edit that reaches for the sensor
API cannot even import.
"""

from __future__ import annotations

from typing import Any

from aebrisk.observation.models import TrackState, WorldFrame

#: The devkit's categories collapsed into the four groups this study weights.
#: Everything that is neither a vehicle, a person, nor a cyclist is inert for
#: the purposes of the AEB, but it is still an obstacle, so it is kept rather
#: than dropped. `EGO` is absent on purpose: the ego is not one of its own
#: observations, and silently mapping it would put the car in its own track list.
CATEGORY_GROUPS: dict[str, str] = {
    "VEHICLE": "vehicle",
    "PEDESTRIAN": "pedestrian",
    "BICYCLE": "bicycle",
    "TRAFFIC_CONE": "object",
    "BARRIER": "object",
    "CZONE_SIGN": "object",
    "GENERIC_OBJECT": "object",
}

#: The oracle observes perfectly, so its covariance is exactly zero. Every error
#: channel is defined as a departure from this frame.
ORACLE_COVARIANCE = (0.0, 0.0, 0.0, 0.0)


def category_to_group(nuplan_category: str) -> str:
    """Map one devkit category onto this study's four observation groups.

    An unrecognised category raises rather than falling back to ``object``. A
    silent fallback is the failure mode that matters here: a devkit release that
    adds, say, a motorcycle category would have every motorcyclist scored as an
    inert obstacle, and the risk weighting would understate exactly the class
    the study exists to protect.
    """

    if nuplan_category == "EGO":
        raise ValueError("EGO is the observer, not one of its own observations")
    try:
        return CATEGORY_GROUPS[nuplan_category]
    except KeyError:
        raise ValueError(f"unknown nuPlan category: {nuplan_category!r}") from None


def _velocity_of(tracked_object: Any) -> tuple[float, float]:
    """Return the object's velocity, or zero for the static objects that have none.

    The devkit's `StaticObject` carries no velocity attribute at all. Reading it
    defensively and defaulting to zero is correct rather than lenient: a barrier
    genuinely is not moving, and inventing a velocity would put motion into the
    oracle that the recording never contained.
    """

    velocity = getattr(tracked_object, "velocity", None)
    if velocity is None:
        return (0.0, 0.0)
    return (float(velocity.x), float(velocity.y))


def oracle_world_frame(scenario: Any, iteration: int) -> WorldFrame:
    """Read one iteration's ego state and tracked objects as a perfect observation.

    This is the zero-error reference: everything is visible, every measurement
    is exact, and the covariance is zero. The error channels take this frame and
    degrade it, so any difference in a result is attributable to the channel
    rather than to two different readings of the recording.
    """

    total = int(scenario.get_number_of_iterations())
    if iteration < 0 or iteration >= total:
        raise IndexError(f"iteration {iteration} is outside the scenario's 0..{total - 1}")

    ego = scenario.get_ego_state_at_iteration(iteration)
    timestamp_us = int(ego.time_us)

    tracks: list[TrackState] = []
    seen: set[str] = set()
    for tracked_object in scenario.get_tracked_objects_at_iteration(iteration):
        category = tracked_object.tracked_object_type.name
        # Written as a positive condition rather than an early `continue` because
        # CPython 3.9 attributes no line number to a bare `continue`, so the arc
        # into it is untraceable and the 100% branch gate cannot pass.
        if category != "EGO":
            track_id = str(tracked_object.track_token)
            if track_id in seen:
                raise ValueError(f"duplicate track token in scenario: {track_id!r}")
            seen.add(track_id)
            box = tracked_object.box
            tracks.append(
                TrackState(
                    track_id=track_id,
                    category=category_to_group(category),
                    center_xy_m=(float(box.center.x), float(box.center.y)),
                    yaw_rad=float(box.center.heading),
                    size_lw_m=(float(box.length), float(box.width)),
                    velocity_xy_mps=_velocity_of(tracked_object),
                    visible=True,
                    source_timestamp_us=timestamp_us,
                    covariance_xy=ORACLE_COVARIANCE,
                )
            )

    return WorldFrame(
        scenario_token=str(scenario.token),
        timestamp_us=timestamp_us,
        ego_center_xy_m=(float(ego.center.x), float(ego.center.y)),
        ego_yaw_rad=float(ego.center.heading),
        ego_speed_mps=float(ego.dynamic_car_state.speed),
        tracks=tuple(tracks),
    )
