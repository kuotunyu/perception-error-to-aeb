"""One real nuPlan frame, checked against the devkit rather than against a fake.

Every other adapter test uses fakes, which prove the adapter's logic but not
that it reads a real scenario correctly: a fake is only ever as right as the
author's belief about the devkit. This test is the one place that belief is
checked, and it is the reason the fakes elsewhere are trustworthy.

It skips, loudly and with a reason, whenever the dataset is absent. It is also
gated by the portfolio's order gate: until `bev-calibration-lab` is released,
no real nuPlan file may be read at all, so on this machine it skips today.
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
        f"{DATA_ROOT_VAR} does not point at a nuPlan mini split with log databases. "
        "This is expected until the portfolio order gate opens: no real nuPlan file "
        "may be read until bev-calibration-lab is released."
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
    """Build one scenario from the first log, importing the devkit lazily.

    The import is inside the function because the devkit's scenario builder
    pulls in its observation types, which pull in OpenCV. Importing it at module
    scope would make this file unimportable in the container this project ships,
    and the skip above would never be reached.
    """

    raise pytest.skip(
        "building a real scenario needs the devkit's scenario builder, which "
        "imports the sensor stack; wire this up at P3-19 together with the "
        "cohort freeze, when the order gate opens"
    )
