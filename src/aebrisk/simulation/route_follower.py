"""The nominal controller, which must not be able to see.

This module is the study's control. Every configuration — oracle, no AEB, and
each corrupted cell of the matrix — drives the same route at the same speed, and
the only thing that differs is what the AEB was told. If the nominal controller
could read the agents, a perception error would change the driving as well as
the braking, and no result could separate the two.

Blindness is therefore a requirement, not a property of this implementation, and
it is enforced by the signature: agents and the AEB are not arguments, so no
future edit inside this module can reach them. Nothing from `aebrisk.observation`,
`aebrisk.errors` or `aebrisk.aeb` is imported here, and a test asserts it.

The target speed is `min(initial, map limit, 13.9)`. The cap keeps a scenario
logged on a fast road from turning every configuration's outcome into a question
about high-speed braking; the initial speed keeps the ego from accelerating into
a scenario it was logged coasting through.

The lateral path is the expert route verbatim. Any smoothing would be a
controller parameter, and the study needs the nominal path to be identical
across configurations; verbatim is the strongest guarantee of that and costs
nothing the study wants.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import numpy.typing as npt

Float64Array = npt.NDArray[np.float64]

#: 50 km/h. The study's own cap on the nominal speed, applied on top of whatever
#: the map allows, so that no cell of the matrix becomes a question about
#: high-speed braking rather than about perception.
MAXIMUM_TARGET_SPEED_MPS = 13.9

#: The first-order time constant the longitudinal law closes the speed error
#: over. One second is slow enough that the nominal controller never competes
#: with the AEB for the actuator and fast enough to reach the target within a
#: scenario.
SPEED_TRACKING_TIME_CONSTANT_S = 1.0


@dataclass(frozen=True)
class NominalPlan:
    """What the perception-blind controller intends, at one step."""

    target_speed_mps: float
    lateral_path_xy: Float64Array
    nominal_acceleration_mps2: float


def _require_speed(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative, got {value!r}")
    return float(value)


def target_speed(initial_speed_mps: float, map_speed_limit_mps: Optional[float]) -> float:
    """The smallest of the logged speed, the map limit and the study's cap."""

    initial_speed_mps = _require_speed(initial_speed_mps, "initial_speed_mps")
    if map_speed_limit_mps is None:
        return min(initial_speed_mps, MAXIMUM_TARGET_SPEED_MPS)
    if not isinstance(map_speed_limit_mps, (int, float)) or isinstance(map_speed_limit_mps, bool):
        raise ValueError("map_speed_limit_mps must be a number")
    if not math.isfinite(map_speed_limit_mps) or map_speed_limit_mps <= 0.0:
        # Zero is a missing value dressed as a real one: it would stop every
        # scenario on that road and look like a deliberate speed limit.
        raise ValueError(
            f"map_speed_limit_mps must be finite and positive, got {map_speed_limit_mps!r}"
        )
    return min(initial_speed_mps, float(map_speed_limit_mps), MAXIMUM_TARGET_SPEED_MPS)


def _validated_route(expert_route_xy: Float64Array) -> Float64Array:
    route = np.asarray(expert_route_xy, dtype=np.float64)
    if route.ndim != 2 or route.shape[1] != 2:
        raise ValueError(
            f"expert_route_xy must be an (N, 2) array of planar waypoints, got shape {route.shape}"
        )
    if route.shape[0] < 2:
        raise ValueError(
            f"expert_route_xy needs at least two waypoints to have a direction, "
            f"got {route.shape[0]}"
        )
    if not np.isfinite(route).all():
        raise ValueError("expert_route_xy components must all be finite")
    return route


def build_nominal_plan(
    expert_route_xy: Float64Array,
    current_speed_mps: float,
    initial_speed_mps: float,
    map_speed_limit_mps: Optional[float],
) -> NominalPlan:
    """Build the plan the nominal controller follows, from route and map data only."""

    route = _validated_route(expert_route_xy)
    current_speed_mps = _require_speed(current_speed_mps, "current_speed_mps")
    target = target_speed(initial_speed_mps, map_speed_limit_mps)

    # Copied and locked: a plan that changed when the caller reused its input
    # array would not be a record of anything.
    path = route.copy()
    path.setflags(write=False)

    # Left unclamped on purpose. The route follower asks and the actuator
    # decides; clamping here as well would apply the bound twice and hide which
    # stage produced a saturated command.
    return NominalPlan(
        target_speed_mps=target,
        lateral_path_xy=path,
        nominal_acceleration_mps2=(target - current_speed_mps) / SPEED_TRACKING_TIME_CONSTANT_S,
    )


def plan_bytes(plan: NominalPlan) -> bytes:
    """A canonical signature of one plan, for comparing configurations.

    The study's control condition is that every configuration receives the same
    nominal plan for the same scenario, and this is what makes that checkable in
    a run record rather than only in a test. Floats are hashed from their IEEE
    754 bytes in a fixed byte order, so the signature does not depend on the
    platform that produced it.
    """

    digest = hashlib.sha256()
    digest.update(np.float64(plan.target_speed_mps).tobytes())
    digest.update(np.float64(plan.nominal_acceleration_mps2).tobytes())
    digest.update(np.ascontiguousarray(plan.lateral_path_xy, dtype="<f8").tobytes())
    return digest.digest()
