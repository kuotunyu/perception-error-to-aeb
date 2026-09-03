"""The detection the perception stack never reported.

Each track's fate is drawn from its own key rather than from a shared sequence,
and that choice is the whole design. A sequential stream would make one track's
visibility depend on how many tracks happened to precede it, so adding a parked
car at the edge of the scene would change whether the pedestrian in front was
seen. That is a simulator artefact rather than a perception error, and it would
not appear anywhere in a result.

Drawing per `(key, track_id, step)` buys three properties at once: the outcome
does not depend on the order tracks arrive in, recomputing a step gives the same
answer, and the next step draws again.

A hidden track is kept with ``visible=False`` rather than removed. The AEB
filters it out, but the record of what was hidden is what makes a missed
intervention explainable after the fact.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

from aebrisk.errors.pipeline import ErrorKey, track_field_generator
from aebrisk.observation.models import TrackState


@dataclass(frozen=True)
class DropoutState:
    """What this channel decided at one step, kept for audit and for replay."""

    last_step: int
    visible_by_track: Mapping[str, bool]


def dropout_draw(key: ErrorKey, track_id: str, step: int) -> float:
    """Return this track's uniform draw for this step.

    Derived rather than sequential: the same three inputs always give the same
    number, and no other track's presence can change it.
    """

    return float(track_field_generator(key, track_id, step, "dropout").random())


def apply_dropout(
    tracks: tuple[TrackState, ...],
    probability: float,
    key: ErrorKey,
    step: int,
    state: Optional[DropoutState],
) -> tuple[tuple[TrackState, ...], DropoutState]:
    """Hide each track with the configured probability, deterministically."""

    if not isinstance(probability, (int, float)) or isinstance(probability, bool):
        raise ValueError("probability must be a number")
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError(f"probability must lie in [0, 1], got {probability!r}")
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise ValueError("step must be a non-negative integer")
    if state is not None and step < state.last_step:
        raise ValueError(
            f"step {step} precedes the recorded step {state.last_step}; time running "
            "backwards means a frame that has already been observed is being redrawn"
        )

    recorded = dict(state.visible_by_track) if state is not None and state.last_step == step else {}

    updated: list[TrackState] = []
    visibility: dict[str, bool] = {}
    for track in tracks:
        if track.track_id in recorded:
            visible = recorded[track.track_id]
        else:
            # Dropout is a loss: a track another stage already hid stays hidden,
            # because reviving it here would silently undo that channel.
            visible = track.visible and dropout_draw(key, track.track_id, step) >= probability
        visibility[track.track_id] = visible
        updated.append(track if visible == track.visible else _with_visibility(track, visible))

    return tuple(updated), DropoutState(last_step=step, visible_by_track=visibility)


def _with_visibility(track: TrackState, visible: bool) -> TrackState:
    """Return the same observation with only its visibility changed."""

    return TrackState(
        track_id=track.track_id,
        category=track.category,
        center_xy_m=track.center_xy_m,
        yaw_rad=track.yaw_rad,
        size_lw_m=track.size_lw_m,
        velocity_xy_mps=track.velocity_xy_mps,
        visible=visible,
        source_timestamp_us=track.source_timestamp_us,
        covariance_xy=track.covariance_xy,
    )
