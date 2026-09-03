"""What the planner this study installs into the devkit is, and is not.

Nothing here builds a devkit object. The purpose is to state the planner's
identity, which every run record cites, and to make the one scope error that
would quietly turn this into a different study impossible to configure: a
planner that reads sensor data.

This project reads world state, logs and maps. A camera or lidar channel in the
planner's requirements would mean somebody has changed what it reads, and that
should fail here rather than at hour three of a simulation.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Cited in every run record. Two runs naming the same planner must have used
#: the same one, so this changes only when the planner's behaviour does.
PLANNER_ID = "aebrisk-closed-loop/v1"

#: World state only. Named so the scope is a value a test can assert rather
#: than a sentence in a document.
OBSERVATION_KIND = "world_state"

#: Deliberately empty, and checked. This study never replays a sensor.
REQUIRED_SENSOR_CHANNELS: tuple[str, ...] = ()


def planner_identity() -> str:
    """The name a run record files this planner's results under."""

    return PLANNER_ID


def validate_planner_scope(sensor_channels: Sequence[str]) -> None:
    """Refuse a planner configured to read sensor data."""

    requested = tuple(sensor_channels)
    if requested:
        raise ValueError(
            f"this planner reads {OBSERVATION_KIND} only, but sensor channels "
            f"{list(requested)} were requested; a sensor channel means the study's "
            "scope has changed, not that an option was set"
        )
