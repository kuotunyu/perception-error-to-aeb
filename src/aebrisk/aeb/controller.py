"""The limiter between what the policy asks for and what the vehicle does.

A state machine that jumped from coasting to -6 m/s^2 in one step would not be a
vehicle, and a study that allowed it would report comfort numbers no real system
could produce. Two limits make the simulated actuator physical, and they act in
opposite directions: the bounds stop the controller commanding more braking than
the vehicle has, and the jerk limit stops it arriving there instantly.

The jerk limit is the one that shows up in results. At the committed 5 m/s^3
over a 0.1 s step the budget is half a metre per second squared, so full braking
from a coast takes twelve steps to arrive. That 1.2 s is part of every
intervention this study measures, and a limiter applied in the wrong order or
skipped would move every comfort and every avoided-collision number together.

The bound is applied after the jerk step, not before. Clamping first would let
the jerk allowance carry the command back outside the bound.
"""

from __future__ import annotations

import math


def _require_finite(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return float(value)


def _require_positive(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return float(value)


def limit_acceleration(
    previous_mps2: float,
    target_mps2: float,
    dt_s: float = 0.1,
    min_mps2: float = -6.0,
    max_mps2: float = 2.0,
    jerk_limit_mps3: float = 5.0,
) -> float:
    """Return the acceleration the vehicle actually applies this step."""

    previous_mps2 = _require_finite(previous_mps2, "previous_mps2")
    target_mps2 = _require_finite(target_mps2, "target_mps2")
    dt_s = _require_positive(dt_s, "dt_s")
    jerk_limit_mps3 = _require_positive(jerk_limit_mps3, "jerk_limit_mps3")
    min_mps2 = _require_finite(min_mps2, "min_mps2")
    max_mps2 = _require_finite(max_mps2, "max_mps2")
    if min_mps2 > max_mps2:
        raise ValueError(
            f"min_mps2 {min_mps2} exceeds max_mps2 {max_mps2}; there is no correct "
            "clamp for inverted bounds, only a silent one"
        )

    allowance = jerk_limit_mps3 * dt_s
    change = max(-allowance, min(allowance, target_mps2 - previous_mps2))
    return max(min_mps2, min(max_mps2, previous_mps2 + change))
