"""Contracts for the two ways a run's error draws can be keyed.

Every random channel derives its draws from one root key per run, plus the
track, the step and a field label. Under the released `dropout-keyed` scheme
that key names the dropout channel and carries the cell's dropout severity, so
two cells that differ only in dropout draw different noise on every channel.
Under `channel-independent` (the policy v2 study, section 4.3 of its analysis
plan) the key carries severity zero in every cell, so no channel's draws depend
on any channel's severity.

Four things must hold. The released scheme builds exactly the released key. The
new scheme is the released one wherever the dropout severity is zero, so those
cells are byte-identical under both. Under the new scheme the tracks dropout
hides at one severity are hidden at every higher one. And the step loop and the
replay build one key, from one rule, under the scheme the configuration names:
a replay keyed differently from the run it redraws would show observations the
run never had.
"""

from __future__ import annotations

import itertools
import re
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aebrisk.errors import pipeline
from aebrisk.errors.dropout import apply_dropout
from aebrisk.errors.pipeline import ERROR_CHANNELS, SEVERITIES, ErrorKey, keyed_seed
from aebrisk.observation.models import TrackState
from aebrisk.simulation.common_cohort import RNG_SCHEMES, ExperimentConfiguration

TOKEN = "keys-0001"
PROTOCOL_HASH = "d" * 64
FIRST_TIMESTAMP_US = 1_600_000_000_000_000
OTHER_CHANNELS = tuple(channel for channel in ERROR_CHANNELS if channel != "dropout")


def released_key(severity_by_channel: dict[str, str], *, token: str = TOKEN, **values: Any) -> Any:
    """The key the released step loop and replay built, written out by hand."""

    return ErrorKey(
        scenario_token=token,
        channel="dropout",
        severity=severity_by_channel["dropout"],
        replicate=values.get("replicate", 2),
        protocol_hash=values.get("protocol_hash", PROTOCOL_HASH),
    )


def key_for(severity_by_channel: dict[str, str], **values: Any) -> Any:
    arguments: dict[str, Any] = {
        "token": TOKEN,
        "channel": "dropout",
        "severity_by_channel": severity_by_channel,
        "replicate": 2,
        "protocol_hash": PROTOCOL_HASH,
    }
    arguments.update(values)
    return pipeline.error_key_for(**arguments)


def every_severity_map() -> list[dict[str, str]]:
    return [
        dict(zip(ERROR_CHANNELS, severities))
        for severities in itertools.product(SEVERITIES, repeat=len(ERROR_CHANNELS))
    ]


def test_dropout_keyed_is_the_released_key() -> None:
    """The released scheme is the default, and builds the key the released runs drew from.

    The seed is pinned to its value so that a change anywhere between the
    configuration and the generator shows here rather than as a quietly
    different experiment.
    """

    severities = {
        "dropout": "medium",
        "localization_shape": "high",
        "latency": "low",
        "track_instability": "zero",
    }

    default = key_for(severities)
    explicit = key_for(severities, rng_scheme="dropout-keyed")

    assert default == explicit == released_key(severities)
    assert default.severity == "medium"
    for severity_by_channel in every_severity_map():
        assert key_for(severity_by_channel) == released_key(severity_by_channel)
    keyed_seed.cache_clear()
    assert keyed_seed(explicit) == 14396229516860554977


@settings(deadline=None, derandomize=True)
@given(
    severities=st.fixed_dictionaries(
        {
            "dropout": st.just("zero"),
            **{channel: st.sampled_from(SEVERITIES) for channel in OTHER_CHANNELS},
        }
    ),
    token=st.text(min_size=1, max_size=24),
    replicate=st.integers(min_value=0, max_value=2**31),
    protocol_hash=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
)
def test_channel_independent_equals_the_released_key_when_dropout_is_zero(
    severities: dict[str, str], token: str, replicate: int, protocol_hash: str
) -> None:
    """Where dropout is zero the new scheme redraws nothing, whatever the other channels."""

    key = key_for(
        severities,
        token=token,
        replicate=replicate,
        protocol_hash=protocol_hash,
        rng_scheme="channel-independent",
    )
    released = released_key(
        severities, token=token, replicate=replicate, protocol_hash=protocol_hash
    )

    assert key == released
    assert keyed_seed(key) == keyed_seed(released)


def test_channel_independent_ignores_every_severity() -> None:
    """One key for every cell: no channel's draws move with any channel's severity."""

    keys = {
        key_for(severity_by_channel, rng_scheme="channel-independent")
        for severity_by_channel in every_severity_map()
    }

    assert len(every_severity_map()) == len(SEVERITIES) ** len(ERROR_CHANNELS)
    assert keys == {released_key({"dropout": "zero"})}


def synthetic_tracks(count: int) -> tuple[TrackState, ...]:
    return tuple(
        TrackState(
            track_id=f"t-{number:04d}",
            category="vehicle",
            center_xy_m=(10.0 + number, 2.0),
            yaw_rad=0.0,
            size_lw_m=(4.5, 1.9),
            velocity_xy_mps=(5.0, 0.0),
            visible=True,
            source_timestamp_us=FIRST_TIMESTAMP_US,
            covariance_xy=(0.0, 0.0, 0.0, 0.0),
        )
        for number in range(count)
    )


def hidden_by_severity(rng_scheme: str) -> dict[str, set[tuple[str, int]]]:
    """Which (track, step) pairs dropout hides at each committed severity."""

    tracks = synthetic_tracks(30)
    hidden: dict[str, set[tuple[str, int]]] = {}
    for severity in SEVERITIES:
        severity_by_channel = dict.fromkeys(ERROR_CHANNELS, "zero")
        severity_by_channel["dropout"] = severity
        key = key_for(severity_by_channel, rng_scheme=rng_scheme)
        probability = pipeline.parameter("dropout", "dropout_probability", severity)
        hidden[severity] = set()
        for step in range(40):
            observed, _ = apply_dropout(tracks, probability, key, step, None)
            hidden[severity].update(
                (track.track_id, step) for track in observed if not track.visible
            )
    return hidden


def test_dropout_hides_nested_sets_across_severities_under_channel_independent() -> None:
    """A track hidden at one severity is hidden at every higher one.

    Under the released key each severity draws afresh, so a higher severity can
    see a track a lower one hid; the contrast below shows the nesting is the
    scheme's doing rather than the synthetic scene's.
    """

    nested = hidden_by_severity("channel-independent")
    released = hidden_by_severity("dropout-keyed")

    assert nested["zero"] == set()
    assert nested["low"]
    assert nested["zero"] <= nested["low"] <= nested["medium"] <= nested["high"]
    assert nested["low"] < nested["high"]
    assert not released["low"] <= released["medium"] <= released["high"]


def replay_scenario() -> tuple[Any, Any]:
    from aebrisk.cohort.manifest import CohortManifestV1
    from aebrisk.simulation.runner import ScenarioSetup

    frames = tuple(
        SimpleNamespace(
            timestamp_us=FIRST_TIMESTAMP_US + step * 100_000,
            tracks=tuple(
                TrackState(
                    track_id=f"body-{number}",
                    category="vehicle",
                    center_xy_m=(40.0 + 6.0 * number, 1.5 * number),
                    yaw_rad=0.0,
                    size_lw_m=(4.5, 1.9),
                    velocity_xy_mps=(0.0, 0.0),
                    visible=True,
                    source_timestamp_us=FIRST_TIMESTAMP_US + step * 100_000,
                    covariance_xy=(0.0, 0.0, 0.0, 0.0),
                )
                for number in range(4)
            ),
        )
        for step in range(8)
    )
    recording = SimpleNamespace(
        frames=frames,
        route_xy=np.array([[0.0, 0.0], [400.0, 0.0]], dtype=np.float64),
        ego_size_lw_m=(4.8, 2.0),
        initial_speed_mps=10.0,
    )
    setup = ScenarioSetup(
        scenario_token=TOKEN,
        family="lead_or_stopping",
        initial_speed_mps=10.0,
        route_signature="route",
        planner_id="planner",
        controller_id="controller",
        frequency_hz=10.0,
        termination_s=15.0,
    )
    protocol = CohortManifestV1(
        schema_version="aeb-cohort-manifest/v1",
        split="evaluation",
        protocol_sha256=PROTOCOL_HASH,
        families={
            "lead_or_stopping": (TOKEN,),
            "cut_in_or_crossing": (),
            "pedestrian_or_crosswalk": (),
            "bicycle_or_vru": (),
        },
        log_names=("log.db",),
    )
    return SimpleNamespace(build_setup=lambda manifest: setup, _recording=recording), protocol


@pytest.mark.parametrize(
    ("rng_scheme", "key_severity"),
    [("dropout-keyed", "medium"), ("channel-independent", "zero")],
)
def test_step_loop_and_replay_build_the_same_key(
    monkeypatch: pytest.MonkeyPatch, rng_scheme: str, key_severity: str
) -> None:
    """The replay redraws a run's observations with the key the run itself used."""

    from aebrisk.cli import replay
    from aebrisk.simulation import step_loop

    loop_keys: list[Any] = []
    replay_keys: list[Any] = []
    original_loop = step_loop.APPLY_ERRORS
    original_replay = replay.apply_error_pipeline

    def loop_spy(history: Any, index: int, config: Any, key: Any, **keywords: Any) -> Any:
        loop_keys.append(key)
        return original_loop(history, index, config, key, **keywords)

    def replay_spy(history: Any, index: int, config: Any, key: Any, **keywords: Any) -> Any:
        replay_keys.append(key)
        return original_replay(history, index, config, key, **keywords)

    monkeypatch.setattr(step_loop, "APPLY_ERRORS", loop_spy)
    monkeypatch.setattr(replay, "apply_error_pipeline", replay_spy)
    severities = dict.fromkeys(ERROR_CHANNELS, "medium")
    configuration = ExperimentConfiguration(
        configuration_id="coalition-full",
        aeb_enabled=True,
        observation_mode="corrupted",
        severity_by_channel=severities,
        replicate_count=1,
        rng_scheme=rng_scheme,
    )
    scenario, protocol = replay_scenario()

    frames = replay._replay_frames(scenario, configuration, protocol)

    expected = ErrorKey(
        scenario_token=TOKEN,
        channel=step_loop.KEY_CHANNEL,
        severity=key_severity,
        replicate=0,
        protocol_hash=PROTOCOL_HASH,
    )
    assert len(frames) == 9
    assert len(loop_keys) == len(replay_keys) == 8
    assert set(loop_keys) == set(replay_keys) == {expected}


@pytest.mark.parametrize(
    "rng_scheme",
    ["", "dropout", "Dropout-Keyed", "channel_independent", "per-channel", " dropout-keyed"],
)
def test_an_unknown_scheme_is_refused(rng_scheme: str) -> None:
    """A misspelt scheme must stop the run rather than key it as the released one."""

    severities = dict.fromkeys(ERROR_CHANNELS, "low")
    message = f"rng_scheme must be one of {RNG_SCHEMES}, got {rng_scheme!r}"

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        key_for(severities, rng_scheme=rng_scheme)
    assert [key_for(severities, rng_scheme=scheme).severity for scheme in RNG_SCHEMES] == [
        "low",
        "zero",
    ]
