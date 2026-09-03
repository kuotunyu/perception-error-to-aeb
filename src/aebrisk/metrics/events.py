"""Turning a brake trace into events, and comparing two traces.

A missed intervention and a false one are the study's two headline failures, and
both are defined by COMPARISON: the same scenario, the same controller, once
with perfect observation and once with corrupted observation. Neither can be
read off a single run.

Three definitions carry the weight, and each is a judgement rather than a fact,
so each is stated here and tested on both sides.

AN INTERVENTION IS A CONTIGUOUS RUN OF BRAKING. Warning is not part of it: the
vehicle does not decelerate in warning, so counting it would make corrupted runs
look like they intervened when nothing happened to the passengers.

A LATE INTERVENTION IS A MISS. Braking 0.4 s after the oracle did is not the
same protective action arriving slightly later; at 15 m/s it is six metres of
closing distance that were not braked away. Only lateness counts — an
intervention that came EARLY is not a failure, so the comparison is signed.

AN UNMATCHED CORRUPTED INTERVENTION IS FALSE. It braked for something the oracle
never braked for, which is the cost side of the trade being measured.

THE MATCHING IS DETERMINISTIC AND ONE TO ONE. Candidate pairs are sorted by
absolute delay and then by position, and taken greedily. Ties are not exotic:
the simulation runs on a 0.1 s grid, so two counterparts equally far away on
either side are common, and without the positional tie-break the pairing would
depend on iteration order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aebrisk.aeb.state_machine import AEBCommand, AEBState

#: The states in which the vehicle actually decelerates.
BRAKING_STATES: tuple[AEBState, ...] = (AEBState.PARTIAL, AEBState.FULL)

#: Event times are ``step_index * dt_s``, so a difference that is exactly 0.3 s
#: on the simulation's grid arrives as 0.30000000000000004. Without this the
#: +0.30 s threshold would silently behave as +0.29999999 for every grid-aligned
#: comparison, which is precisely the boundary error the strict thresholds exist
#: to avoid. A nanosecond is far below any rate this study runs at, so it can
#: never absorb a real difference.
GRID_EPSILON_S = 1e-9


@dataclass(frozen=True)
class InterventionEvent:
    """One contiguous period of braking."""

    onset_s: float
    offset_s: float
    max_state: AEBState

    def __post_init__(self) -> None:
        if not math.isfinite(self.onset_s) or self.onset_s < 0.0:
            raise ValueError("onset_s must be finite and non-negative")
        if not math.isfinite(self.offset_s) or self.offset_s <= self.onset_s:
            raise ValueError(
                f"offset_s {self.offset_s} must be after onset_s {self.onset_s}; time "
                "running backwards inside one event means the extraction was wrong"
            )
        if self.max_state not in BRAKING_STATES:
            raise ValueError(
                f"max_state must be one of {[state.value for state in BRAKING_STATES]}, "
                f"got {self.max_state.value!r}; a monitoring or warning state is not an "
                "intervention"
            )


@dataclass(frozen=True)
class EventMatchSummary:
    """What comparing two traces of the same scenario found."""

    missed: int
    false: int
    matched_delays_s: tuple[float, ...]


def _require_positive(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return float(value)


def _require_non_negative(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative, got {value!r}")
    return float(value)


def extract_interventions(
    commands: tuple[AEBCommand, ...],
    dt_s: float = 0.1,
) -> tuple[InterventionEvent, ...]:
    """Collapse a step-by-step trace into the interventions a reader counts."""

    dt_s = _require_positive(dt_s, "dt_s")

    events: list[InterventionEvent] = []
    start: int | None = None
    severest = AEBState.PARTIAL

    # Written as one if/elif chain rather than with a `continue`: CPython 3.9
    # attributes no line number to a bare `continue`, so the arc into it is
    # untraceable and the project's 100% branch gate can never pass. This is
    # the third place in this repository where that has come up.
    for index, command in enumerate(commands):
        if command.state in BRAKING_STATES:
            if start is None:
                start, severest = index, command.state
            elif command.state is AEBState.FULL:
                severest = AEBState.FULL
        elif start is not None:
            events.append(
                InterventionEvent(
                    onset_s=start * dt_s,
                    offset_s=index * dt_s,
                    max_state=severest,
                )
            )
            start = None

    if start is not None:
        # A scenario can end mid-brake, and that event still has a duration.
        events.append(
            InterventionEvent(
                onset_s=start * dt_s,
                offset_s=len(commands) * dt_s,
                max_state=severest,
            )
        )

    return tuple(events)


def match_interventions(
    oracle: tuple[InterventionEvent, ...],
    corrupted: tuple[InterventionEvent, ...],
    tolerance_s: float = 1.0,
    missed_delay_s: float = 0.30,
) -> EventMatchSummary:
    """Pair the two traces' interventions and count what failed."""

    tolerance_s = _require_non_negative(tolerance_s, "tolerance_s")
    missed_delay_s = _require_non_negative(missed_delay_s, "missed_delay_s")
    if missed_delay_s > tolerance_s:
        raise ValueError(
            f"missed_delay_s {missed_delay_s} exceeds tolerance_s {tolerance_s}; it "
            "could never fire, because nothing that far apart is matched at all"
        )

    candidates = sorted(
        (
            (abs(right.onset_s - left.onset_s), left_index, right_index)
            for left_index, left in enumerate(oracle)
            for right_index, right in enumerate(corrupted)
            if abs(right.onset_s - left.onset_s) <= tolerance_s + GRID_EPSILON_S
        )
    )

    matched_oracle: set[int] = set()
    matched_corrupted: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, left_index, right_index in candidates:
        if left_index in matched_oracle or right_index in matched_corrupted:
            continue
        matched_oracle.add(left_index)
        matched_corrupted.add(right_index)
        pairs.append((left_index, right_index))

    # Reported in the oracle's onset order, so the tuple does not depend on the
    # order the matching happened to find the pairs in.
    pairs.sort(key=lambda pair: oracle[pair[0]].onset_s)
    delays = tuple(
        corrupted[right_index].onset_s - oracle[left_index].onset_s
        for left_index, right_index in pairs
    )

    late = sum(1 for delay in delays if delay > missed_delay_s + GRID_EPSILON_S)
    return EventMatchSummary(
        missed=len(oracle) - len(matched_oracle) + late,
        false=len(corrupted) - len(matched_corrupted),
        matched_delays_s=delays,
    )
