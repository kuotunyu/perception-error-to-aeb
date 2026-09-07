"""Contracts for binding the four error channels into the pipeline.

Until this module existed the channels were implemented, individually tested,
and NEVER CONNECTED: `ChannelStages` was only ever constructed with its identity
defaults, so `apply_error_pipeline` corrupted nothing and every configuration
was the oracle wearing a different name. Each channel's own tests passed
throughout. That is the failure this file exists to make impossible, so the
first test here asserts that a bound pipeline actually changes something.

The severity-zero case is subtler than it looks, and the tests below state it
carefully. At severity zero the channels introduce NO CORRUPTION: positions,
sizes, headings and visibility come through untouched. But velocity is still a
FINITE DIFFERENCE of observed positions rather than the oracle's exact value,
because differencing is how a real tracker obtains velocity — it is the
estimator, not an error. So the all-zero corrupted configuration is NOT the
oracle, and that difference is a measurable quantity rather than a bug. It is
precisely why the experiment matrix carries both `oracle_aeb` and
`coalition-none`, and why the Shapley baseline is the latter.
"""

from __future__ import annotations

import copy
from types import ModuleType
from typing import Any

import pytest


def load_channels_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.errors import channels
    except ImportError:
        pytest.fail("aebrisk.errors.channels is missing", pytrace=False)
    return channels


def key() -> Any:
    from aebrisk.errors.pipeline import ErrorKey

    return ErrorKey(
        scenario_token="s-0001",
        channel="dropout",
        severity="high",
        replicate=0,
        protocol_hash="a" * 64,
    )


def configuration(severity: str = "zero", **overrides: str) -> Any:
    from aebrisk.errors.pipeline import ErrorConfiguration

    severities = dict.fromkeys(
        ("dropout", "localization_shape", "latency", "track_instability"), severity
    )
    severities.update(overrides)
    name = "coalition-none" if set(severities.values()) == {"zero"} else "mixed"
    return ErrorConfiguration(configuration_id=name, severity_by_channel=severities)


def frame(index: int, *, tracks: int = 3) -> Any:
    from aebrisk.observation.models import TrackState, WorldFrame

    stamp = 1_600_000_000_000_000 + index * 100_000
    return WorldFrame(
        scenario_token="s-0001",
        timestamp_us=stamp,
        ego_center_xy_m=(index * 1.0, 0.0),
        ego_yaw_rad=0.0,
        ego_speed_mps=10.0,
        tracks=tuple(
            TrackState(
                track_id=f"t-{number:04d}",
                category="vehicle",
                center_xy_m=(30.0 + number * 6.0 + index * 0.5, number * 1.5),
                yaw_rad=0.1 * number,
                size_lw_m=(4.5, 1.9),
                velocity_xy_mps=(5.0, 0.0),
                visible=True,
                source_timestamp_us=stamp,
                covariance_xy=(0.0, 0.0, 0.0, 0.0),
            )
            for number in range(tracks)
        ),
    )


def history(count: int = 12) -> list:
    return [frame(index) for index in range(count)]


def observe(config: Any, steps: int = 12) -> list:
    """Run the bound pipeline over a whole history and collect each observation."""

    from aebrisk.errors.pipeline import apply_error_pipeline

    channels = load_channels_module()
    frames = history(steps)
    bound = channels.ScenarioChannels(config)
    return [
        apply_error_pipeline(frames, index, config, key(), stages=bound.stages())
        for index in range(steps)
    ]


# --------------------------------------------------------------------------
# The channels are actually connected
# --------------------------------------------------------------------------


def test_a_real_severity_changes_the_observation() -> None:
    """The test that would have caught four channels wired to nothing.

    Every channel had passing tests while `ChannelStages` was only ever built
    with its identity defaults, so the whole pipeline was the oracle.
    """

    frames = history()
    corrupted = observe(configuration("high"))

    assert corrupted[6] != frames[6].tracks


@pytest.mark.parametrize(
    "channel",
    ["dropout", "localization_shape", "latency", "track_instability"],
)
def test_each_channel_on_its_own_changes_something(channel: str) -> None:
    """One identity stage left in place would hide that channel's whole effect."""

    frames = history()
    alone = observe(configuration("zero", **{channel: "high"}))

    assert any(alone[index] != frames[index].tracks for index in range(len(frames)))


def test_the_stages_run_in_the_fixed_order() -> None:
    """Latency chooses WHICH frame is observed, so it must run first.

    The same parameters in another order produce a different corruption, and no
    published number would say which order made it.
    """

    channels = load_channels_module()
    bound = channels.ScenarioChannels(configuration("medium"))

    assert channels.STAGE_ORDER == ("latency", "visibility", "geometry", "tracking")
    stages = bound.stages()
    for name in channels.STAGE_ORDER:
        assert getattr(stages, name) is not None


# --------------------------------------------------------------------------
# Severity zero
# --------------------------------------------------------------------------


def test_severity_zero_corrupts_nothing() -> None:
    """Position, size, heading and visibility come through untouched."""

    frames = history()
    observed = observe(configuration("zero"))

    for index, tracks in enumerate(observed):
        for track, truth in zip(tracks, frames[index].tracks):
            assert track.center_xy_m == truth.center_xy_m
            assert track.size_lw_m == pytest.approx(truth.size_lw_m)
            assert track.yaw_rad == pytest.approx(truth.yaw_rad)
            assert track.visible == truth.visible


def test_severity_zero_keeps_every_identity() -> None:
    """No track fragments, so no track is renamed."""

    frames = history()
    observed = observe(configuration("zero"))

    for index, tracks in enumerate(observed):
        assert [track.track_id for track in tracks] == [
            truth.track_id for truth in frames[index].tracks
        ]


def test_severity_zero_reports_a_differenced_velocity_not_the_oracle_value() -> None:
    """Even at zero, velocity is ESTIMATED rather than known.

    Differencing is how a real tracker obtains velocity: it is the estimator,
    not an error, so the all-zero configuration is not the oracle. On this
    synthetic history the two happen to agree, because the tracks move at
    constant velocity and differencing exact positions recovers it exactly.
    The paired test below shows them diverging as soon as the positions are
    corrupted, which is what makes the gap a measurable quantity rather than a
    bug, and why the Shapley baseline is `coalition-none` and not the oracle.
    """

    frames = history()
    observed = observe(configuration("zero"))

    # The first frame has no previous position, so it reports the oracle value.
    assert observed[0][0].velocity_xy_mps == frames[0].tracks[0].velocity_xy_mps
    # Later frames report the difference: 0.5 m over 0.1 s in x.
    assert observed[5][0].velocity_xy_mps == pytest.approx((5.0, 0.0))


def test_the_estimate_and_the_oracle_diverge_once_positions_are_corrupted() -> None:
    """The paired test: the agreement above is a property of this history, not of the model.

    Without this, the test above could be read as saying the estimator always
    recovers the truth, which is the opposite of what the study measures.
    """

    frames = history()
    noisy = observe(configuration("zero", localization_shape="high"))

    assert noisy[5][0].velocity_xy_mps != pytest.approx(frames[5].tracks[0].velocity_xy_mps)


def test_the_estimated_velocity_follows_the_corrupted_positions() -> None:
    """The amplification the study exists to measure, end to end.

    Position error at 10 Hz becomes ten times as much velocity error, and this
    is where that reaches the controller.
    """

    clean = observe(configuration("zero"))
    noisy = observe(configuration("zero", localization_shape="high"))

    spread_clean = max(abs(track.velocity_xy_mps[0]) for track in clean[6])
    spread_noisy = max(abs(track.velocity_xy_mps[0]) for track in noisy[6])

    assert spread_noisy > spread_clean


# --------------------------------------------------------------------------
# State across steps
# --------------------------------------------------------------------------


def test_the_channels_carry_state_across_steps() -> None:
    """Dropout redraws each step and fragmentation remembers; both need state.

    A pipeline rebuilt per step would redraw fragmentation from scratch and no
    track would ever stay lost for its reacquisition delay.
    """

    observed = observe(configuration("high"), steps=20)
    hidden = [sum(1 for track in tracks if not track.visible) for tracks in observed]

    assert len(set(hidden)) > 1


def test_two_runs_of_the_same_configuration_agree() -> None:
    """Determinism end to end, with every channel connected."""

    assert observe(configuration("medium")) == observe(configuration("medium"))


def test_a_different_severity_gives_a_different_observation() -> None:
    """The pair to the test above; identical output would satisfy it alone."""

    assert observe(configuration("low")) != observe(configuration("high"))


def test_each_scenario_run_gets_its_own_state() -> None:
    """State leaking between scenarios would make one run depend on the last."""

    channels = load_channels_module()
    config = configuration("high")

    first = channels.ScenarioChannels(config)
    second = channels.ScenarioChannels(config)

    assert first.dropout_state is None
    assert second.track_memories == {}


def test_an_injected_error_document_controls_the_bound_geometry() -> None:
    """A validated caller-supplied calibration must not be discarded for the default file."""

    from aebrisk.errors.pipeline import validate_error_config

    channels = load_channels_module()
    document = copy.deepcopy(channels.load_error_config())
    document["channels"]["localization_shape"]["position_std_m"] = [0.0, 0.1, 2.0, 3.0]
    validate_error_config(document)
    bound = channels.ScenarioChannels(
        configuration("zero", localization_shape="medium"), error_config=document
    )
    source = frame(0, tracks=1).tracks

    [observed] = bound.geometry(source, key=key(), current_index=0)

    assert observed.covariance_xy == pytest.approx((4.0, 0.0, 0.0, 4.0))


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_an_imported_configuration_is_refused() -> None:
    """Its severities are measured, not chosen from this study's grid.

    Binding it here would silently read a `medium` that means something else.
    """

    from aebrisk.errors.pipeline import ErrorConfiguration

    channels = load_channels_module()
    imported = ErrorConfiguration(
        configuration_id="calibration_imported_0123456789abcdef",
        severity_by_channel=dict.fromkeys(
            ("dropout", "localization_shape", "latency", "track_instability"), "zero"
        ),
        imported=True,
    )

    with pytest.raises(
        ValueError, match=r"^'calibration_imported_\w+' is an imported configuration; "
    ):
        channels.ScenarioChannels(imported)


@pytest.mark.parametrize("bad_value", [0.0, -0.1, float("nan")])
def test_an_impossible_step_duration_is_refused(bad_value: float) -> None:
    """The fragmentation hazard conversion divides the rate over it."""

    channels = load_channels_module()

    with pytest.raises(ValueError, match=r"^dt_s must "):
        channels.ScenarioChannels(configuration("zero"), dt_s=bad_value)


@pytest.mark.parametrize("bad_value", ["0.1", True, None])
def test_a_step_duration_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean a one second step, which changes every rate."""

    channels = load_channels_module()

    with pytest.raises(ValueError, match=r"^dt_s\ must\ be\ a\ number"):
        channels.ScenarioChannels(configuration("zero"), dt_s=bad_value)  # type: ignore[arg-type]


def test_default_and_nondefault_step_lengths_reach_real_fragmentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draw between the 0.1 s and 0.2 s hazards exposes both bound durations."""

    from aebrisk.errors import fragmentation

    channels = load_channels_module()
    document = copy.deepcopy(channels.load_error_config())
    document["channels"]["track_instability"]["fragmentation_rate_per_s"][2] = 1.0
    monkeypatch.setattr(fragmentation, "fragmentation_draw", lambda *args: 0.15)
    config = configuration("zero", track_instability="medium")
    default = channels.ScenarioChannels(config, error_config=document)
    slower = channels.ScenarioChannels(config, error_config=document, dt_s=0.2)
    source = frame(0, tracks=1).tracks

    default_track = default.tracking(source, key=key(), current_index=0)[0]
    slower_track = slower.tracking(source, key=key(), current_index=0)[0]

    assert default_track.visible is True
    assert slower_track.visible is False


def test_latency_and_dropout_stages_each_change_the_composed_pipeline_alone() -> None:
    """The bound stage table must expose each actual channel, not an identity placeholder."""

    from aebrisk.errors.pipeline import apply_error_pipeline

    channels = load_channels_module()
    document = copy.deepcopy(channels.load_error_config())
    document["channels"]["latency"]["latency_s"][2] = 0.1
    document["channels"]["dropout"]["dropout_probability"][2] = 1.0
    frames = history(3)
    latency_config = configuration("zero", latency="medium")
    dropout_config = configuration("zero", dropout="medium")
    latency_bound = channels.ScenarioChannels(latency_config, error_config=document)
    dropout_bound = channels.ScenarioChannels(dropout_config, error_config=document)

    delayed = apply_error_pipeline(
        frames,
        2,
        latency_config,
        key(),
        stages=latency_bound.stages(),
    )
    hidden = apply_error_pipeline(
        frames,
        2,
        dropout_config,
        key(),
        stages=dropout_bound.stages(),
    )

    assert delayed[0].source_timestamp_us == frames[1].timestamp_us
    assert all(track.visible is False for track in hidden)


def test_visibility_carries_state_and_refuses_a_backward_step() -> None:
    """Dropping state would silently restart the causal dropout stream."""

    channels = load_channels_module()
    bound = channels.ScenarioChannels(configuration("zero"))
    tracks = frame(2, tracks=1).tracks

    bound.visibility(tracks, key=key(), current_index=2)

    with pytest.raises(ValueError, match=r"^step 1 precedes the recorded step 2;"):
        bound.visibility(tracks, key=key(), current_index=1)


def test_latency_selection_is_optional_until_the_first_observation() -> None:
    """There is no selected frame before latency runs; afterwards the actual choice is kept."""

    channels = load_channels_module()
    bound = channels.ScenarioChannels(configuration("zero"))
    frames = history(2)

    assert bound.latency_selection is None
    bound.latency(frames[1].tracks, history=frames, current_index=1)
    assert bound.latency_selection is not None
    assert bound.latency_selection.selected_index == 1
