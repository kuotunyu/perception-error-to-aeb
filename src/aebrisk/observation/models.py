"""The world-state types every error channel and the AEB operate on.

These are the study's own types rather than the devkit's, and that separation is
what makes the perception pipeline testable without a database: a channel takes
a ``WorldFrame`` and returns a ``WorldFrame``, so dropout, latency, localization
error and track instability can all be exercised from frames built by hand.

Every constraint below names a way a corrupted frame would otherwise reach the
AEB looking plausible. A zero-area box can never overlap, so a threat carrying
one is undetectable; a NaN coordinate propagates into every distance and
time-to-collision; two tracks sharing an identity get matched to each other
across frames and produce a reacquisition that never happened.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The groups risk weighting and AEB priority key on. nuPlan's finer categories
#: are collapsed into these by the adapter, in one place, on purpose.
OBSERVATION_CATEGORIES: tuple[str, ...] = ("vehicle", "pedestrian", "bicycle", "object")


def _require_pair(value: tuple[float, ...], name: str) -> None:
    if len(value) != 2:
        raise ValueError(f"{name} must have exactly two components")
    for component in value:
        if not math.isfinite(component):
            raise ValueError(f"{name} components must be finite")


@dataclass(frozen=True)
class TrackState:
    """One observed object at one instant, as this study represents it."""

    track_id: str
    category: str
    center_xy_m: tuple[float, float]
    yaw_rad: float
    size_lw_m: tuple[float, float]
    velocity_xy_mps: tuple[float, float]
    visible: bool
    source_timestamp_us: int
    covariance_xy: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if not self.track_id:
            raise ValueError("track_id must not be empty")
        if self.category not in OBSERVATION_CATEGORIES:
            raise ValueError(
                f"category must be one of {OBSERVATION_CATEGORIES}, got {self.category!r}"
            )
        _require_pair(self.center_xy_m, "center_xy_m")
        _require_pair(self.velocity_xy_mps, "velocity_xy_mps")
        _require_pair(self.size_lw_m, "size_lw_m")
        if any(dimension <= 0.0 for dimension in self.size_lw_m):
            raise ValueError("size_lw_m components must be positive")
        if not math.isfinite(self.yaw_rad):
            raise ValueError("yaw_rad must be finite")
        if len(self.covariance_xy) != 4:
            raise ValueError("covariance_xy must have exactly four components")
        if any(not math.isfinite(component) for component in self.covariance_xy):
            raise ValueError("covariance_xy components must be finite")
        if self.source_timestamp_us < 0:
            raise ValueError("source_timestamp_us must not be negative")


@dataclass(frozen=True)
class WorldFrame:
    """Everything the ego observes at one simulation step.

    Tracks are sorted by identity on construction rather than trusted in the
    order they arrive. Iteration order reaches the AEB's tie-breaking when two
    threats are equally urgent, and the devkit's ordering is not part of any
    contract, so leaving it alone would make a result depend on it.
    """

    scenario_token: str
    timestamp_us: int
    ego_center_xy_m: tuple[float, float]
    ego_yaw_rad: float
    ego_speed_mps: float
    tracks: tuple[TrackState, ...]

    def __post_init__(self) -> None:
        if not self.scenario_token:
            raise ValueError("scenario_token must not be empty")
        if self.timestamp_us < 0:
            raise ValueError("timestamp_us must not be negative")
        _require_pair(self.ego_center_xy_m, "ego_center_xy_m")
        if not math.isfinite(self.ego_yaw_rad):
            raise ValueError("ego_yaw_rad must be finite")
        if not math.isfinite(self.ego_speed_mps) or self.ego_speed_mps < 0.0:
            raise ValueError("ego_speed_mps must be a finite, non-negative magnitude")

        identities = [track.track_id for track in self.tracks]
        if len(set(identities)) != len(identities):
            raise ValueError("tracks contain a duplicate track_id")
        object.__setattr__(self, "tracks", tuple(sorted(self.tracks, key=lambda t: t.track_id)))
