"""The geometry the AEB decides on.

Every number here is computed from the corrupted observation rather than the
truth, which is the whole point: a position error moves a box, and a moved box
crosses the corridor boundary at a different time.

Bodies are oriented rectangles, not circles or points. A circle around a vehicle
overstates its width and understates its length, so it would make every crossing
scenario wrong in the same direction; a point ignores that a 4.5 m box yawed
into the lane reaches the corridor long before its centre does, and that reach is
the metre or two that decides whether a brake was late.

Time to collision is the first predicted overlap under a constant-velocity
rollout, sampled every ``step_s`` out to ``horizon_s`` inclusive. Constant
velocity is a deliberate under-model: a real AEB does not know what the other
driver is about to do either, and giving the simulated one a better predictor
than the real one has would flatter every result.

Required deceleration is ``v^2 / (2 d)`` on the longitudinal gap between the two
bodies. It is a magnitude, so a receding object requires zero rather than a
negative amount, and it saturates rather than diverging when the gap has closed.

A body that cannot reach the corridor before the horizon ends skips the rollout
entirely. That is a BOUND rather than a heuristic — both bodies translate at
constant velocity, so their separation cannot fall faster than the relative
speed, and the separation now is measured rather than guessed — and it changes
neither the time to collision nor the overlap flag. It exists because a real
urban frame carries over a hundred tracked objects and almost all of them are
parked cars and pedestrians tens of metres away: measured on nuPlan mini on
2026-09-06, one frame held 134 tracks and a full rollout cost 6.9 ms each, which
made a single cohort candidate cost 37 seconds and the freeze cost days.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import numpy.typing as npt

from aebrisk.observation.models import TrackState

Float64Array = npt.NDArray[np.float64]

#: The gap is clamped to this before dividing. A closed gap is a division by
#: zero, and an infinite required deceleration cannot be written to an artifact.
#: The value saturates instead: anything above the vehicle's braking capability
#: means the same thing to the controller, so the exact number never decides
#: anything.
MINIMUM_LONGITUDINAL_GAP_M = 0.05


@dataclass(frozen=True)
class EgoKinematicState:
    """The ego as the simulation knows it at one step."""

    center_xy_m: tuple[float, float]
    yaw_rad: float
    size_lw_m: tuple[float, float]
    speed_mps: float
    velocity_xy_mps: tuple[float, float]
    acceleration_mps2: float

    def __post_init__(self) -> None:
        _require_finite_pair(self.center_xy_m, "center_xy_m")
        _require_finite_pair(self.velocity_xy_mps, "velocity_xy_mps")
        _require_finite_pair(self.size_lw_m, "size_lw_m")
        if any(dimension <= 0.0 for dimension in self.size_lw_m):
            raise ValueError("size_lw_m components must be positive")
        if not math.isfinite(self.yaw_rad):
            raise ValueError("yaw_rad must be finite")
        if not math.isfinite(self.acceleration_mps2):
            raise ValueError("acceleration_mps2 must be finite")
        if not math.isfinite(self.speed_mps) or self.speed_mps < 0.0:
            raise ValueError("speed_mps must be finite and non-negative")


@dataclass(frozen=True)
class ThreatAssessment:
    """What one observed object means for the ego, at one step."""

    track_id: str
    ttc_s: Optional[float]
    required_deceleration_mps2: float
    predicted_overlap: bool
    #: A LOWER BOUND on how close the rollout came. It is the exact sampled
    #: minimum whenever the rollout was walked, and the provable bound when the
    #: body was far enough away that walking it could not have changed anything.
    #: Nothing in this study consumes it — the clearance a run reports is
    #: measured on the true geometry by the step loop — so it is diagnostic.
    min_clearance_m: float


def _require_finite_pair(value: tuple[float, float], name: str) -> None:
    if len(value) != 2:
        raise ValueError(f"{name} must have exactly two components")
    for component in value:
        if not math.isfinite(component):
            raise ValueError(f"{name} components must be finite")


def _require_positive(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return float(value)


def oriented_box_polygon(
    center_xy: tuple[float, float],
    yaw_rad: float,
    size_lw: tuple[float, float],
    margin_m: float = 0.0,
) -> Float64Array:
    """Return one body's four corners, counter-clockwise from the rear right.

    Length runs along the heading and width across it. Swapping them would make
    every vehicle 2 m long and 4 m wide, which changes every overlap in the
    study and nothing else about the run.
    """

    _require_finite_pair(center_xy, "center_xy")
    _require_finite_pair(size_lw, "size_lw")
    if any(dimension <= 0.0 for dimension in size_lw):
        raise ValueError("size_lw components must be positive")
    if not math.isfinite(yaw_rad):
        raise ValueError("yaw_rad must be finite")
    if not isinstance(margin_m, (int, float)) or isinstance(margin_m, bool):
        raise ValueError("margin_m must be a number")
    if not math.isfinite(margin_m) or margin_m < 0.0:
        raise ValueError(f"margin_m must be finite and non-negative, got {margin_m!r}")

    half_length = size_lw[0] / 2.0 + margin_m
    half_width = size_lw[1] / 2.0 + margin_m
    body = np.array(
        [
            [-half_length, -half_width],
            [half_length, -half_width],
            [half_length, half_width],
            [-half_length, half_width],
        ],
        dtype=np.float64,
    )
    cos, sin = math.cos(yaw_rad), math.sin(yaw_rad)
    rotation = np.array([[cos, -sin], [sin, cos]], dtype=np.float64)
    return (body @ rotation.T) + np.asarray(center_xy, dtype=np.float64)


def _axes(polygon: Float64Array) -> Float64Array:
    """The outward normals of a polygon's edges, which are its separating axes."""

    edges = np.roll(polygon, -1, axis=0) - polygon
    return np.stack([-edges[:, 1], edges[:, 0]], axis=1)


def polygons_overlap(first_n2: Float64Array, second_n2: Float64Array) -> bool:
    """Separating axis test for two convex polygons.

    Bodies that exactly touch count as overlapping. Contact is a collision, and
    treating a shared edge as clear would let the study report a grazing impact
    as an avoided one.
    """

    for axis in np.concatenate([_axes(first_n2), _axes(second_n2)]):
        first = first_n2 @ axis
        second = second_n2 @ axis
        if first.max() < second.min() or second.max() < first.min():
            return False
    return True


def _point_segment_distance(point: Float64Array, start: Float64Array, end: Float64Array) -> float:
    edge = end - start
    length_squared = float(edge @ edge)
    # A degenerate edge cannot occur here: every polygon comes from
    # ``oriented_box_polygon``, which refuses a non-positive dimension.
    position = float((point - start) @ edge) / length_squared
    clamped = min(1.0, max(0.0, position))
    nearest = start + clamped * edge
    return float(np.hypot(*(point - nearest)))


def polygon_clearance(first_n2: Float64Array, second_n2: Float64Array) -> float:
    """The distance between two convex polygons, or zero where they overlap.

    Measured over every vertex-to-edge pair in both directions, which is exact:
    for disjoint convex polygons the nearest points are always a vertex against
    an edge, and the vertex-to-vertex case is that edge's endpoint. A test over
    face normals alone would report the wrong number whenever two corners are
    the nearest features, which is most of a crossing scenario.
    """

    if polygons_overlap(first_n2, second_n2):
        return 0.0

    best = math.inf
    for source, target in ((first_n2, second_n2), (second_n2, first_n2)):
        for point in source:
            for index in range(len(target)):
                best = min(
                    best,
                    _point_segment_distance(
                        point, target[index], target[(index + 1) % len(target)]
                    ),
                )
    return best


def _longitudinal_gap(ego_state: EgoKinematicState, track: TrackState) -> float:
    """The clear distance along the ego's heading between the two bodies."""

    cos, sin = math.cos(ego_state.yaw_rad), math.sin(ego_state.yaw_rad)
    separation = (track.center_xy_m[0] - ego_state.center_xy_m[0]) * cos + (
        track.center_xy_m[1] - ego_state.center_xy_m[1]
    ) * sin
    relative_yaw = track.yaw_rad - ego_state.yaw_rad
    # How far the other body reaches along the ego's heading, which is not half
    # its length unless the two are aligned.
    track_reach = abs(track.size_lw_m[0] / 2.0 * math.cos(relative_yaw)) + abs(
        track.size_lw_m[1] / 2.0 * math.sin(relative_yaw)
    )
    return abs(separation) - ego_state.size_lw_m[0] / 2.0 - track_reach


def _required_deceleration(ego_state: EgoKinematicState, track: TrackState) -> float:
    """``v^2 / (2 d)`` on the longitudinal closing speed and gap."""

    cos, sin = math.cos(ego_state.yaw_rad), math.sin(ego_state.yaw_rad)
    closing_speed = (ego_state.velocity_xy_mps[0] - track.velocity_xy_mps[0]) * cos + (
        ego_state.velocity_xy_mps[1] - track.velocity_xy_mps[1]
    ) * sin
    if closing_speed <= 0.0:
        # Not closing. The magnitude of a brake that is not needed is zero, not
        # a negative number that would sort above a real threat.
        return 0.0
    gap = max(MINIMUM_LONGITUDINAL_GAP_M, _longitudinal_gap(ego_state, track))
    return closing_speed * closing_speed / (2.0 * gap)


def assess_threat(
    ego_state: EgoKinematicState,
    track: TrackState,
    horizon_s: float = 4.0,
    step_s: float = 0.1,
    corridor_margin_m: float = 0.5,
) -> ThreatAssessment:
    """Roll both bodies forward at constant velocity and report the first overlap.

    Visibility is the caller's filter, not this function's: a track that no
    channel reported never reaches the controller, and deciding that here would
    put the observation model inside the geometry.
    """

    horizon_s = _require_positive(horizon_s, "horizon_s")
    step_s = _require_positive(step_s, "step_s")
    if step_s > horizon_s:
        raise ValueError(
            f"step_s {step_s} exceeds horizon_s {horizon_s}; the rollout would sample "
            "only the present while claiming to look ahead"
        )
    if not isinstance(corridor_margin_m, (int, float)) or isinstance(corridor_margin_m, bool):
        raise ValueError("corridor_margin_m must be a number")

    # The bound. `polygon_clearance` is exact and costs one rollout step; the
    # rollout costs forty-one. A body that cannot close the measured gap within
    # the horizon cannot overlap the corridor at any step of it.
    separation = polygon_clearance(
        oriented_box_polygon(
            ego_state.center_xy_m,
            ego_state.yaw_rad,
            ego_state.size_lw_m,
            margin_m=corridor_margin_m,
        ),
        oriented_box_polygon(track.center_xy_m, track.yaw_rad, track.size_lw_m),
    )
    reach = (
        math.hypot(
            ego_state.velocity_xy_mps[0] - track.velocity_xy_mps[0],
            ego_state.velocity_xy_mps[1] - track.velocity_xy_mps[1],
        )
        * horizon_s
    )
    if separation > reach:
        return ThreatAssessment(
            track_id=track.track_id,
            ttc_s=None,
            required_deceleration_mps2=_required_deceleration(ego_state, track),
            predicted_overlap=False,
            min_clearance_m=separation - reach,
        )

    steps = math.floor(horizon_s / step_s + 1e-9)
    closest = math.inf
    for index in range(steps + 1):
        elapsed = index * step_s
        corridor = oriented_box_polygon(
            (
                ego_state.center_xy_m[0] + ego_state.velocity_xy_mps[0] * elapsed,
                ego_state.center_xy_m[1] + ego_state.velocity_xy_mps[1] * elapsed,
            ),
            ego_state.yaw_rad,
            ego_state.size_lw_m,
            margin_m=corridor_margin_m,
        )
        body = oriented_box_polygon(
            (
                track.center_xy_m[0] + track.velocity_xy_mps[0] * elapsed,
                track.center_xy_m[1] + track.velocity_xy_mps[1] * elapsed,
            ),
            track.yaw_rad,
            track.size_lw_m,
        )
        clearance = polygon_clearance(corridor, body)
        if clearance == 0.0:
            return ThreatAssessment(
                track_id=track.track_id,
                ttc_s=elapsed,
                required_deceleration_mps2=_required_deceleration(ego_state, track),
                predicted_overlap=True,
                min_clearance_m=0.0,
            )
        closest = min(closest, clearance)

    return ThreatAssessment(
        track_id=track.track_id,
        ttc_s=None,
        required_deceleration_mps2=_required_deceleration(ego_state, track),
        predicted_overlap=False,
        min_clearance_m=closest,
    )


def select_highest_required_deceleration(
    threats: tuple[ThreatAssessment, ...],
) -> Optional[ThreatAssessment]:
    """Pick the one object the AEB brakes for.

    Ties break on the track identity rather than on input order, because the
    order tracks arrive in is not part of any contract and two equally urgent
    threats must resolve the same way on every run.
    """

    if not threats:
        return None
    return min(
        threats,
        key=lambda threat: (-threat.required_deceleration_mps2, threat.track_id),
    )
