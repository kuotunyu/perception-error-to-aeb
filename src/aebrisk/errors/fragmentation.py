"""The track that breaks and comes back as somebody else.

A real tracker loses association and re-initialises. To the AEB that is not a
brief gap, it is a new object with no history: the accumulated velocity estimate
is gone, so the first frame after reacquisition carries the oracle velocity and
a larger position covariance. A controller that had been braking for a closing
vehicle sees the threat vanish and then reappear as something it knows nothing
about, and the intervention restarts from the beginning. That difference is why
the new identity is modelled rather than treated as bookkeeping: hiding a track
and giving it back under the same id would measure a short occlusion, which is a
different failure with a different cost.

The rate is per second and becomes a per-step probability as
``1 - exp(-rate * dt)``. Multiplying instead would exceed one for any rate above
ten at 10 Hz and would silently collapse the two highest severities into one.

Two rules keep this channel from smuggling in another one. Every track draws
whether or not it is currently visible, so a fragmentation outcome never depends
on whether dropout happened to hide it first; and a reacquired track that
another channel has hidden stays hidden, because this channel can lose a track
but must never give one back.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

from aebrisk.errors.pipeline import ErrorKey, track_field_generator
from aebrisk.observation.models import TrackState
from aebrisk.observation.tracking import finite_difference_velocity

MICROSECONDS_PER_SECOND = 1_000_000

#: Separates a source identity from its generation, so ``t-0001#2`` says both
#: which real object this is and that it has been re-initialised twice. An
#: opaque identity would be just as deterministic and would make every run
#: record unreadable.
FRAGMENT_SEPARATOR = "#"

#: Added to the position variance of a reacquired track. A tracker that has just
#: re-initialised has one observation and no history; reporting the same
#: confidence as a track it has followed for a second would be a claim it cannot
#: make, and a planner reads that confidence.
REACQUISITION_VARIANCE_M2 = 1.0


@dataclass(frozen=True)
class TrackMemory:
    """What this channel remembers about one real object between frames."""

    public_track_id: str
    source_track_id: str
    last_seen_timestamp_us: int
    reacquire_after_us: int
    previous_center_xy_m: Optional[tuple[float, float]]


def fragmentation_probability(rate_per_s: float, dt_s: float) -> float:
    """Convert a rate per second into the probability of at least one event in a step."""

    return 1.0 - math.exp(-rate_per_s * dt_s)


def fragmentation_draw(key: ErrorKey, track_id: str, step: int) -> float:
    """Return this track's uniform draw for this step.

    Derived rather than sequential, for the same reason dropout draws this way:
    no other track's presence may change this one's outcome.
    """

    return float(track_field_generator(key, track_id, step, "fragmentation").random())


def next_public_id(public_track_id: str) -> str:
    """Advance an identity by one generation.

    The generation is read back out of the identity rather than kept in its own
    field, because the identity is what reaches the artifacts and it should say
    on its own how many times the object has been re-initialised.
    """

    base, separator, generation = public_track_id.rpartition(FRAGMENT_SEPARATOR)
    if separator and generation.isdigit():
        return f"{base}{FRAGMENT_SEPARATOR}{int(generation) + 1}"
    return f"{public_track_id}{FRAGMENT_SEPARATOR}1"


def _require_non_negative(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative, got {value!r}")
    return float(value)


def _require_positive(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return float(value)


def _emit(
    track: TrackState,
    *,
    track_id: str,
    visible: bool,
    velocity_xy_mps: tuple[float, float],
    covariance_xy: tuple[float, float, float, float],
) -> TrackState:
    """Rebuild one observation with only what this channel is allowed to change."""

    return TrackState(
        track_id=track_id,
        category=track.category,
        center_xy_m=track.center_xy_m,
        yaw_rad=track.yaw_rad,
        size_lw_m=track.size_lw_m,
        velocity_xy_mps=velocity_xy_mps,
        visible=visible,
        source_timestamp_us=track.source_timestamp_us,
        covariance_xy=covariance_xy,
    )


def _estimated_velocity(track: TrackState, memory: Optional[TrackMemory]) -> tuple[float, float]:
    """Difference against the remembered position, over the time actually elapsed."""

    if memory is None or memory.previous_center_xy_m is None:
        return track.velocity_xy_mps
    elapsed_us = track.source_timestamp_us - memory.last_seen_timestamp_us
    if elapsed_us <= 0:
        # A repeated or out-of-order timestamp gives nothing to divide by. The
        # oracle value is the only honest answer, and it is flagged as such.
        return track.velocity_xy_mps
    return finite_difference_velocity(
        memory.previous_center_xy_m,
        track.center_xy_m,
        elapsed_us / MICROSECONDS_PER_SECOND,
    )


def update_fragmentation(
    tracks: tuple[TrackState, ...],
    memories: Mapping[str, TrackMemory],
    *,
    rate_per_s: float,
    reacquisition_delay_s: float,
    key: ErrorKey,
    step: int,
    dt_s: float = 0.1,
) -> tuple[tuple[TrackState, ...], dict[str, TrackMemory]]:
    """Break tracks, hold them for the reacquisition delay, and return them renamed."""

    rate_per_s = _require_non_negative(rate_per_s, "rate_per_s")
    reacquisition_delay_s = _require_non_negative(reacquisition_delay_s, "reacquisition_delay_s")
    dt_s = _require_positive(dt_s, "dt_s")
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise ValueError("step must be a non-negative integer")

    probability = fragmentation_probability(rate_per_s, dt_s)
    delay_us = round(reacquisition_delay_s * MICROSECONDS_PER_SECOND)

    # Memories for objects absent from this frame are carried forward untouched.
    # Dropping them would release a fragmented track the moment it flickered out
    # of the frame, which is the opposite of what the delay models.
    updated_memories: dict[str, TrackMemory] = dict(memories)
    updated: list[TrackState] = []

    for track in tracks:
        memory = updated_memories.get(track.track_id)
        public_id = track.track_id if memory is None else memory.public_track_id
        lost_until = 0 if memory is None else memory.reacquire_after_us

        if track.source_timestamp_us < lost_until:
            # Still inside the outage. Nothing is reported and the memory does
            # not move, so the release step depends only on when it broke.
            updated.append(
                _emit(
                    track,
                    track_id=public_id,
                    visible=False,
                    velocity_xy_mps=track.velocity_xy_mps,
                    covariance_xy=track.covariance_xy,
                )
            )
            continue

        if lost_until > 0:
            # The release frame. A new identity, no history to difference
            # against, and a covariance that admits it.
            reacquired = next_public_id(public_id)
            updated.append(
                _emit(
                    track,
                    track_id=reacquired,
                    visible=track.visible,
                    velocity_xy_mps=track.velocity_xy_mps,
                    covariance_xy=(
                        track.covariance_xy[0] + REACQUISITION_VARIANCE_M2,
                        track.covariance_xy[1],
                        track.covariance_xy[2],
                        track.covariance_xy[3] + REACQUISITION_VARIANCE_M2,
                    ),
                )
            )
            updated_memories[track.track_id] = TrackMemory(
                public_track_id=reacquired,
                source_track_id=track.track_id,
                last_seen_timestamp_us=track.source_timestamp_us,
                reacquire_after_us=0,
                previous_center_xy_m=track.center_xy_m,
            )
            continue

        if fragmentation_draw(key, track.track_id, step) < probability:
            # It breaks now. The draw happens for hidden tracks too, so this
            # channel's effect never depends on whether dropout ran first.
            updated.append(
                _emit(
                    track,
                    track_id=public_id,
                    visible=False,
                    velocity_xy_mps=track.velocity_xy_mps,
                    covariance_xy=track.covariance_xy,
                )
            )
            updated_memories[track.track_id] = TrackMemory(
                public_track_id=public_id,
                source_track_id=track.track_id,
                last_seen_timestamp_us=track.source_timestamp_us,
                reacquire_after_us=track.source_timestamp_us + delay_us,
                previous_center_xy_m=None,
            )
            continue

        updated.append(
            _emit(
                track,
                track_id=public_id,
                visible=track.visible,
                velocity_xy_mps=_estimated_velocity(track, memory),
                covariance_xy=track.covariance_xy,
            )
        )
        updated_memories[track.track_id] = TrackMemory(
            public_track_id=public_id,
            source_track_id=track.track_id,
            last_seen_timestamp_us=track.source_timestamp_us,
            reacquire_after_us=0,
            previous_center_xy_m=track.center_xy_m,
        )

    return tuple(updated), updated_memories
