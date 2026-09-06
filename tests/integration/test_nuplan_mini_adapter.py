"""One real nuPlan frame, checked against the devkit rather than against a fake.

Every other adapter test uses fakes, which prove the adapter's logic but not
that it reads a real scenario correctly: a fake is only ever as right as the
author's belief about the devkit. This test is the one place that belief is
checked, and it is the reason the fakes elsewhere are trustworthy.

It skips, loudly and with a reason, whenever the dataset is absent, which is
what happens in CI and on any machine without a licensed copy. The portfolio's
order gate opened when `driving-risk-metrics` released, so where the data is
mounted this test runs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

DATA_ROOT_VAR = "NUPLAN_DATA_ROOT"


def dataset_root() -> Path | None:
    """Return the configured data root, or ``None`` when there is nothing to read."""

    raw_value = os.environ.get(DATA_ROOT_VAR, "")
    if not raw_value:
        return None
    root = Path(raw_value)
    split = root / "nuplan-v1.1" / "splits" / "mini"
    if not split.is_dir() or not any(split.glob("*.db")):
        return None
    return root


requires_dataset = pytest.mark.skipif(
    dataset_root() is None,
    reason=(
        f"{DATA_ROOT_VAR} does not point at a nuPlan mini split with log databases, "
        "so there is no real scenario to read. Mount one to run this test."
    ),
)


@requires_dataset
def test_one_real_frame_matches_direct_devkit_extraction() -> None:
    """The adapter's frame must equal what the devkit reports for the same iteration.

    Comparing against the devkit rather than against recorded numbers is what
    makes this a check on the adapter instead of a check on a fixture somebody
    generated with the adapter.
    """

    from nuplan.common.actor_state.tracked_objects_types import TrackedObjectType

    from aebrisk.nuplan_adapter.database import resolve_installation
    from aebrisk.nuplan_adapter.scenario import category_to_group, oracle_world_frame

    root = dataset_root()
    assert root is not None
    layout = resolve_installation(root)
    scenario = _first_scenario(layout)
    iteration = 0

    frame = oracle_world_frame(scenario, iteration)

    ego = scenario.get_ego_state_at_iteration(iteration)
    expected = {
        entry.track_token: entry
        for entry in scenario.get_tracked_objects_at_iteration(iteration)
        if entry.tracked_object_type is not TrackedObjectType.EGO
    }

    assert frame.timestamp_us == ego.time_us
    assert frame.ego_center_xy_m == (ego.center.x, ego.center.y)
    assert {track.track_id for track in frame.tracks} == set(expected)
    for track in frame.tracks:
        source = expected[track.track_id]
        assert track.category == category_to_group(source.tracked_object_type.name)
        assert track.center_xy_m == (source.box.center.x, source.box.center.y)
        assert track.size_lw_m == (source.box.length, source.box.width)


@requires_dataset
def test_no_sensor_root_is_configured_for_the_real_dataset() -> None:
    """The boundary must hold on the machine that has the data, not only in fakes."""

    assert not os.environ.get("NUPLAN_SENSOR_ROOT")


def _first_scenario(layout: Any) -> Any:
    """Build one real scenario from the first log that has a pinned scenario type.

    Through the query module rather than the devkit's scenario builder, which
    cannot be imported here: it pulls in the map stack and the observation types,
    which need rasterio and OpenCV, and this container has neither. That is the
    boundary working, so this test exercises the path the study actually uses.
    """

    from aebrisk.cohort.filters import FAMILY_TYPES
    from aebrisk.nuplan_adapter.query_scenario import build_scenario, scenarios_of_type
    from aebrisk.nuplan_adapter.simulation import (
        PROTOCOL_FREQUENCY_HZ,
        PROTOCOL_SCENARIO_DURATION_S,
    )

    wanted = sorted(
        scenario_type
        for scenario_types in FAMILY_TYPES.values()
        for scenario_type in scenario_types
    )
    for database in sorted(layout.log_databases):
        references = scenarios_of_type(str(database), wanted)
        for reference in references:
            try:
                return build_scenario(
                    reference.log_file,
                    reference.token,
                    duration_s=PROTOCOL_SCENARIO_DURATION_S,
                    frequency_hz=PROTOCOL_FREQUENCY_HZ,
                )
            except ValueError:
                # Too close to the end of its recording to run the full duration.
                # That is an eligibility fact, not a failure; try the next one.
                continue
    raise pytest.skip(
        "no log in the mounted split holds a scenario of a pinned type with enough "
        "frames after it to run the protocol's duration"
    )


@requires_dataset
def test_the_iteration_grid_runs_at_the_protocol_rate_over_real_timestamps() -> None:
    """The log records at about 20 Hz; the study steps at 10, and must prove it does.

    Reading the grid straight from the log would double every duration this study
    reports, and nothing downstream would look wrong: the numbers would simply be
    twice what they should be.
    """

    from aebrisk.nuplan_adapter.database import resolve_installation
    from aebrisk.nuplan_adapter.simulation import (
        PROTOCOL_FREQUENCY_HZ,
        PROTOCOL_SCENARIO_DURATION_S,
    )

    root = dataset_root()
    assert root is not None
    scenario = _first_scenario(resolve_installation(root))

    expected_steps = round(PROTOCOL_SCENARIO_DURATION_S * PROTOCOL_FREQUENCY_HZ)
    assert scenario.get_number_of_iterations() == expected_steps

    step_us = 1_000_000.0 / PROTOCOL_FREQUENCY_HZ
    stamps = [
        scenario.get_ego_state_at_iteration(index).time_us
        for index in range(min(11, expected_steps))
    ]
    gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    # Real timestamps jitter by a few microseconds; a whole step out is a bug.
    assert all(abs(gap - step_us) < step_us * 0.05 for gap in gaps), gaps


@requires_dataset
def test_a_real_scenario_reads_only_the_members_the_adapter_is_allowed_to_touch() -> None:
    """The fakes elsewhere are only as right as this: a real object, the same members."""

    from aebrisk.nuplan_adapter.database import resolve_installation
    from aebrisk.nuplan_adapter.scenario import oracle_world_frame

    root = dataset_root()
    assert root is not None
    scenario = _first_scenario(resolve_installation(root))

    frame = oracle_world_frame(scenario, 0)

    assert frame.scenario_token == scenario.token
    assert frame.timestamp_us > 0
    assert len({track.track_id for track in frame.tracks}) == len(frame.tracks)
    assert all(track.visible for track in frame.tracks)
    assert all(track.covariance_xy == (0.0, 0.0, 0.0, 0.0) for track in frame.tracks)
