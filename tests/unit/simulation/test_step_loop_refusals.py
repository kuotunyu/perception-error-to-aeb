"""The step loop's own refusals, stated in full.

A refusal is the only record a caller gets of why a run did not happen, so its
wording is part of the contract rather than decoration. And the loop checks its
step length itself: a binding that happened to refuse the same value would hide
a loop that no longer does.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pytest

from aebrisk.observation.models import TrackState
from aebrisk.simulation import step_loop
from aebrisk.simulation.common_cohort import ExperimentConfiguration

CHANNELS = ("dropout", "localization_shape", "latency", "track_instability")


def configuration() -> ExperimentConfiguration:
    return ExperimentConfiguration(
        configuration_id="oracle_aeb",
        aeb_enabled=True,
        observation_mode="oracle",
        severity_by_channel=dict.fromkeys(CHANNELS, "zero"),
        replicate_count=1,
    )


def run(*, source: Any, steps: int, dt_s: float = 0.1) -> Any:
    return step_loop.run_steps(
        token="loop-0001",
        route_xy=np.array([[0.0, 0.0], [400.0, 0.0]], dtype=np.float64),
        frame_at_step=source,
        steps=steps,
        initial_speed_mps=10.0,
        ego_size_lw_m=(4.0, 2.0),
        configuration=configuration(),
        replicate=0,
        protocol_hash="c" * 64,
        dt_s=dt_s,
        map_speed_limit_mps=None,
    )


def empty_frames(step: int) -> tuple[int, tuple[TrackState, ...]]:
    return 1_600_000_000_000_000 + step * 100_000, ()


@pytest.mark.parametrize("bad_dt", [0.0, -0.1, math.nan, math.inf])
def test_an_impossible_step_length_is_refused_before_any_channel_is_bound(
    monkeypatch: pytest.MonkeyPatch, bad_dt: float
) -> None:
    """The loop's own check refuses it, not the channel binding that would follow."""

    def binding_reached(*arguments: Any, **keywords: Any) -> Any:
        pytest.fail(f"channels were bound for dt_s={bad_dt!r}")

    monkeypatch.setattr(step_loop, "BIND_CHANNELS", binding_reached)

    with pytest.raises(
        ValueError,
        match=rf"^{re.escape(f'dt_s must be finite and positive, got {bad_dt!r}')}$",
    ):
        run(source=empty_frames, steps=1, dt_s=bad_dt)


def test_a_world_source_that_goes_backwards_is_refused_in_full() -> None:
    """The refusal names the step, both timestamps, and why the order matters."""

    stamps = (7_000_000, 7_100_000, 7_050_000)
    message = (
        "the world source went backwards in time at step 2: 7050000 is before 7100000; "
        "every latency selection out of a history assembled in that order depends on "
        "the order rather than on the recording"
    )

    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        run(source=lambda step: (stamps[step], ()), steps=3)
