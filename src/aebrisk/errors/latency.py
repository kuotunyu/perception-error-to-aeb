"""The observation that is correct, but old.

This channel is unlike the others: it does not corrupt an observation, it
chooses *which* observation the rest of the pipeline sees. That is why it runs
first, and why its one non-negotiable property is causality. At 15 m/s, 0.4 s of
latency is six metres of closing distance the controller cannot see, and a
selection that reached even one frame into the future would hand it information
the real system could not have had. The whole study would then overstate what an
AEB can do, and no artifact would say so.

Three rules follow, and each has its own test.

The target is a **timestamp**, not an index, because real logs are not perfectly
regular and counting frames would silently mean different amounts of time in
different scenarios.

The search never looks **forward**, and a tie goes to the **older** frame. Both
are the same rule seen twice: whenever the answer is ambiguous, take the
staler observation, because the alternative is leaking the future.

A history too short for the request is **invalid**, not clamped. Clamping would
return a fresher observation than the severity asked for, and the run would
report the high-latency configuration while having applied a lower one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from aebrisk.observation.models import TrackState, WorldFrame

MICROSECONDS_PER_SECOND = 1_000_000


@dataclass(frozen=True)
class LatencySelection:
    """Which frame the controller was allowed to see, and how stale it was."""

    requested_latency_s: float
    selected_index: int
    realized_latency_s: float
    valid: bool


def invalid_reason(selection: LatencySelection) -> Optional[str]:
    """Explain an unobservable frame, or return ``None`` when there is nothing to explain.

    Without a reason, a run that could not yet observe anything is
    indistinguishable from one that observed an empty road, and those are very
    different facts about a scenario.
    """

    if selection.valid:
        return None
    return (
        f"the history does not reach {selection.requested_latency_s} s into the past, "
        "so no observation was available at the configured latency"
    )


def _require_positive(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return float(value)


def select_latency_frame(
    history: Sequence[WorldFrame],
    current_index: int,
    requested_latency_s: float,
    frequency_hz: float = 10.0,
) -> LatencySelection:
    """Choose the newest frame that is no later than the requested delay."""

    if not history:
        raise ValueError("history must contain at least one frame")
    if current_index < 0 or current_index >= len(history):
        raise IndexError(f"current_index {current_index} is outside the history")
    if not isinstance(requested_latency_s, (int, float)) or isinstance(requested_latency_s, bool):
        raise ValueError("requested_latency_s must be a number")
    if not math.isfinite(requested_latency_s) or requested_latency_s < 0.0:
        raise ValueError(
            f"requested_latency_s must be finite and non-negative, got {requested_latency_s!r}"
        )
    _require_positive(frequency_hz, "frequency_hz")

    stamps = [frame.timestamp_us for frame in history[: current_index + 1]]
    for earlier, later in zip(stamps, stamps[1:]):
        if later < earlier:
            raise ValueError(
                "history timestamps must be monotonic; a history that goes backwards "
                "was assembled wrongly and any selection from it depends on that order"
            )

    now_us = stamps[current_index]
    target_us = now_us - round(requested_latency_s * MICROSECONDS_PER_SECOND)

    selected = None
    for index in range(current_index, -1, -1):
        if stamps[index] <= target_us:
            selected = index
            break

    if selected is None:
        # The whole history is newer than the target. Returning the oldest frame
        # would be a clamp, so the observation is reported as unavailable and the
        # index stays at the oldest frame, which is never in the future.
        return LatencySelection(
            requested_latency_s=float(requested_latency_s),
            selected_index=0,
            realized_latency_s=(now_us - stamps[0]) / MICROSECONDS_PER_SECOND,
            valid=False,
        )

    # A tie goes to the older frame: several frames may share a timestamp, and
    # the scan above stops at the newest of them, so walk back over the equals.
    while selected > 0 and stamps[selected - 1] == stamps[selected]:
        selected -= 1

    return LatencySelection(
        requested_latency_s=float(requested_latency_s),
        selected_index=selected,
        realized_latency_s=(now_us - stamps[selected]) / MICROSECONDS_PER_SECOND,
        valid=True,
    )


def apply_latency(
    history: Sequence[WorldFrame],
    current_index: int,
    requested_latency_s: float,
    frequency_hz: float = 10.0,
) -> tuple[tuple[TrackState, ...], LatencySelection]:
    """Return the observation the controller is allowed to see at this step.

    An unavailable observation shows nothing rather than something stale: a
    system whose pipeline has not filled yet reports no detections, and reporting
    the oldest frame instead would be the clamp this channel exists to avoid.
    """

    selection = select_latency_frame(history, current_index, requested_latency_s, frequency_hz)
    tracks = history[selection.selected_index].tracks
    if selection.valid:
        return tracks, selection
    return tuple(_hidden(track) for track in tracks), selection


def _hidden(track: TrackState) -> TrackState:
    """Return the same observation, reported as not seen."""

    return TrackState(
        track_id=track.track_id,
        category=track.category,
        center_xy_m=track.center_xy_m,
        yaw_rad=track.yaw_rad,
        size_lw_m=track.size_lw_m,
        velocity_xy_mps=track.velocity_xy_mps,
        visible=False,
        source_timestamp_us=track.source_timestamp_us,
        covariance_xy=track.covariance_xy,
    )
