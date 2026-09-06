"""Read one nuPlan scenario out of a log database with raw SQL, and nothing else.

The devkit's own scenario builder is unreachable here, and that is the design
rather than an accident. `nuplan.planning.scenario_builder` imports the map
stack, which needs rasterio; the observation types import the devkit's image
utilities, which need OpenCV. The container has neither, so a study that reads
world state only would otherwise have to install an entire sensor pipeline in
order to name types it never uses. Depending on the query layer instead makes
the environment enforce the boundary: an edit that reaches for the sensor or map
API cannot import.

What this module does import is the raw-SQL query functions and, through them,
the `actor_state` types. Those carry exactly what `oracle_world_frame` reads, so
the adapter above needs no change: a `QueryScenario` answers `token`,
`get_number_of_iterations()`, `get_ego_state_at_iteration(i)` and
`get_tracked_objects_at_iteration(i)`, and nothing else.

Two decisions here would be wrong silently, so both raise instead.

THE ITERATION GRID IS THE PROTOCOL'S RATE, NOT THE LOG'S. nuPlan records lidar
at about 20 Hz and the protocol simulates at 10, so every second frame is taken.
The stride is computed from the log's MEASURED interval rather than assumed: a
log recorded at another rate would otherwise be simulated at that rate, and
every duration the study reports — brake onset delay, intervention duration —
would be scaled by a factor nobody chose. A rate that is not a whole multiple of
the protocol's is refused rather than resampled onto uneven instants.

A SCENARIO SHORTER THAN THE PROTOCOL'S DURATION IS REFUSED. Truncating one is
not the same experiment as running it to the end, and a cohort holding both
would compare configurations over different amounts of time.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from nuplan.database.nuplan_db.nuplan_db_utils import get_lidarpc_sensor_data
from nuplan.database.nuplan_db.nuplan_scenario_queries import (
    get_ego_state_for_lidarpc_token_from_db,
    get_sampled_lidarpcs_from_db,
    get_scenarios_from_db,
    get_tracked_objects_for_lidarpc_token_from_db,
)

#: The database calls, named here so a test can replace them without a database.
QUERY_SAMPLED_LIDARPCS = get_sampled_lidarpcs_from_db
QUERY_EGO_STATE = get_ego_state_for_lidarpc_token_from_db
QUERY_TRACKED_OBJECTS = get_tracked_objects_for_lidarpc_token_from_db
QUERY_SCENARIOS = get_scenarios_from_db

#: Which sensor table the iteration grid is read from. The study reads no point
#: cloud; the lidar_pc table is simply nuPlan's clock.
LIDAR_SOURCE = get_lidarpc_sensor_data()

#: How many frames to look at when measuring the log's own interval. Enough for
#: a median to be stable against the microsecond jitter real timestamps carry.
RATE_PROBE_FRAMES = 21

#: How far one protocol step may fall from the rate it claims, as a fraction of
#: that step: half a percent, which is 500 microseconds of the study's 0.1 s.
#: Wide enough for every real lidar clock, and narrower by two orders of
#: magnitude than the smallest wrong rate a nuPlan log could be recorded at.
STEP_TOLERANCE = 0.005


@dataclass(frozen=True)
class ScenarioReference:
    """One scenario as the cohort names it, before anything is simulated."""

    log_file: str
    token: str
    scenario_type: str
    timestamp_us: int


def _as_token(value: Any) -> str:
    """Return a scenario token as text, whether the driver handed back bytes or text."""

    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return str(value)


def native_period_us(frames: Sequence[Any]) -> int:
    """Return the log's own sampling interval, measured rather than assumed.

    The median of the intervals, not the mean: real timestamps jitter by a few
    microseconds and a single gap — a dropped frame, the end of a recording —
    would drag a mean far enough to change the stride. `median_low` rather than
    `median` so the answer is an interval the log actually exhibited; an even
    number of intervals would otherwise average two of them into a period that
    never occurred.
    """

    if len(frames) < 2:
        raise ValueError("a sampling interval needs at least two frames")
    intervals = [
        int(later.timestamp) - int(earlier.timestamp) for earlier, later in zip(frames, frames[1:])
    ]
    return int(statistics.median_low(intervals))


def stride_for(period_us: int, frequency_hz: float) -> int:
    """Return how many logged frames make one protocol step, or refuse.

    A log recorded at a rate the protocol's does not divide cannot be stepped
    onto evenly spaced instants, and simulating it anyway would put the study's
    steps at times that drift against the recording.

    The tolerance is RELATIVE, and it has to be. This check first ran over real
    recordings on 2026-09-06 with an absolute tolerance of one microsecond, and
    refused 2,671 of 2,675 candidates: a real 20 Hz lidar measures as 19.99 or
    20.01 Hz, so a stride of two lands a few microseconds off 0.1 s. Worse than
    the refusals were the four acceptances — the scenarios whose jitter happened
    to cancel, which is a cohort selected on a property of the clock. Microseconds
    are jitter; a rate the protocol genuinely does not divide misses by percent
    (12 Hz by 17, 15 Hz by 33, 25 Hz by 20).
    """

    wanted_us = 1_000_000.0 / frequency_hz
    stride = round(wanted_us / period_us)
    if stride < 1 or abs(stride * period_us - wanted_us) > wanted_us * STEP_TOLERANCE:
        raise ValueError(
            f"samples at {1_000_000.0 / period_us:.3f} Hz, which {frequency_hz:.3f} Hz "
            "does not divide; the study would step at instants the recording does not have"
        )
    return stride


class QueryScenario:
    """One scenario, answered from the log database a frame at a time."""

    def __init__(self, log_file: str, token: str, iteration_tokens: tuple[str, ...]) -> None:
        self._log_file = log_file
        self._token = token
        self._iteration_tokens = iteration_tokens

    @property
    def token(self) -> str:
        """The anchor lidar_pc token, which is how the cohort names this scenario."""

        return self._token

    def get_number_of_iterations(self) -> int:
        """How many protocol steps this scenario runs for."""

        return len(self._iteration_tokens)

    def _token_at(self, iteration: int) -> str:
        total = len(self._iteration_tokens)
        if iteration < 0 or iteration >= total:
            raise IndexError(f"iteration {iteration} is outside the scenario's 0..{total - 1}")
        return self._iteration_tokens[iteration]

    def get_ego_state_at_iteration(self, iteration: int) -> Any:
        """The logged ego state at one step."""

        return QUERY_EGO_STATE(self._log_file, self._token_at(iteration))

    def get_tracked_objects_at_iteration(self, iteration: int) -> Iterator[Any]:
        """Every tracked object the log recorded at one step."""

        return QUERY_TRACKED_OBJECTS(self._log_file, self._token_at(iteration))


def build_scenario(
    log_file: str, token: str, duration_s: float, frequency_hz: float
) -> QueryScenario:
    """Build one scenario's iteration grid at the protocol's rate, or refuse."""

    probe: list[Any] = list(
        QUERY_SAMPLED_LIDARPCS(log_file, token, LIDAR_SOURCE, list(range(RATE_PROBE_FRAMES)), True)
    )
    if len(probe) < 2:
        raise ValueError(
            f"{log_file} has too few frames after {token} to measure its sampling rate: "
            f"{len(probe)}"
        )
    try:
        stride = stride_for(native_period_us(probe), frequency_hz)
    except ValueError as error:
        raise ValueError(f"{log_file} {error}") from error

    steps = round(duration_s * frequency_hz)
    indexes = [step * stride for step in range(steps)]
    frames: list[Any] = list(QUERY_SAMPLED_LIDARPCS(log_file, token, LIDAR_SOURCE, indexes, True))
    if len(frames) < steps:
        raise ValueError(
            f"{log_file} has {len(frames)} frames after {token} at the protocol rate, and the "
            f"protocol needs {steps}; a truncated scenario is not the same experiment"
        )
    return QueryScenario(log_file, token, tuple(_as_token(frame.token) for frame in frames))


def scenarios_of_type(
    log_file: str, scenario_types: Sequence[str]
) -> tuple[ScenarioReference, ...]:
    """Every scenario in one log whose tag is in the given set, in a stable order.

    Sorted by token, not by the order the driver returned rows in: a cohort is a
    set, and two orderings of it must not produce two different cohorts.
    """

    references = [
        ScenarioReference(
            log_file=log_file,
            token=_as_token(row["token"]),
            scenario_type=str(row["scenario_type"]),
            timestamp_us=int(row["timestamp"]),
        )
        for row in QUERY_SCENARIOS(log_file, None, list(scenario_types), None)
    ]
    return tuple(sorted(references, key=lambda reference: reference.token))
