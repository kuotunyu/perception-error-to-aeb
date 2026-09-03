"""Contracts for the object that is seen, but not quite where or how big it is.

This channel is where an observation stops being the recording. Everything it
touches reaches the threat assessment directly: a position error changes the
distance, a yaw error changes which way the box points, and a size error changes
whether two boxes overlap at all.

Three choices are worth stating. Position and yaw errors are absolute because a
detector's localization error does not scale with the object; size error is
relative because a 25 cm error on a pedestrian and on a truck are not the same
mistake. And the covariance grows with the position variance, because a
perception stack that is wrong by a metre and says so is a different input to a
planner than one that is wrong by a metre and claims certainty.
"""

from __future__ import annotations

import math
from types import ModuleType
from typing import Any

import pytest

PROTOCOL_HASH = "a" * 64


def load_localization_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.errors import localization
    except ImportError:
        pytest.fail("aebrisk.errors.localization is missing", pytrace=False)
    return localization


def make_key(**overrides: Any) -> Any:
    from aebrisk.errors.pipeline import ErrorKey

    values: dict[str, Any] = {
        "scenario_token": "s-0001",
        "channel": "localization_shape",
        "severity": "medium",
        "replicate": 0,
        "protocol_hash": PROTOCOL_HASH,
    }
    values.update(overrides)
    return ErrorKey(**values)


def make_tracks(count: int = 4, **overrides: Any) -> tuple[Any, ...]:
    from aebrisk.observation.models import TrackState

    def build(number: int) -> Any:
        values: dict[str, Any] = {
            "track_id": f"t-{number:04d}",
            "category": "vehicle",
            "center_xy_m": (10.0 + number, 2.0),
            "yaw_rad": 0.25,
            "size_lw_m": (4.5, 1.9),
            "velocity_xy_mps": (5.0, -1.0),
            "visible": True,
            "source_timestamp_us": 1_600_000_000_000_000,
            "covariance_xy": (0.0, 0.0, 0.0, 0.0),
        }
        values.update(overrides)
        return TrackState(**values)

    return tuple(build(number) for number in range(count))


def perturb(localization: ModuleType, tracks: Any, **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "position_std_m": 0.5,
        "yaw_std_deg": 2.0,
        "size_relative_std": 0.05,
        "key": make_key(),
        "step": 0,
    }
    settings.update(overrides)
    return localization.perturb_localization_shape(tracks, **settings)


def test_all_zero_deviations_are_the_exact_identity() -> None:
    """Severity zero is the reference; it must return the tracks unchanged."""

    localization = load_localization_module()
    tracks = make_tracks()

    result = perturb(
        localization, tracks, position_std_m=0.0, yaw_std_deg=0.0, size_relative_std=0.0
    )

    assert result == tracks


def test_the_draw_is_the_documented_function_of_the_key_track_step_and_field() -> None:
    """Pinning the derivation stops it drifting while still looking deterministic."""

    from aebrisk.errors.pipeline import track_field_generator

    localization = load_localization_module()
    key = make_key()
    expected = track_field_generator(key, "t-0000", 3, "position_x").standard_normal()

    assert localization.perturbation_draw(key, "t-0000", 3, "position_x") == expected


def test_position_x_and_y_are_drawn_independently() -> None:
    """One draw applied to both axes would move every object along one diagonal."""

    localization = load_localization_module()
    tracks = make_tracks(1)

    [track] = perturb(localization, tracks, yaw_std_deg=0.0, size_relative_std=0.0)

    dx = track.center_xy_m[0] - tracks[0].center_xy_m[0]
    dy = track.center_xy_m[1] - tracks[0].center_xy_m[1]

    assert dx != 0.0
    assert dy != 0.0
    assert dx != dy


def test_the_offset_scales_with_the_configured_deviation() -> None:
    """Doubling the severity must double the error, or the axis is not what it says."""

    localization = load_localization_module()
    tracks = make_tracks(1)

    single = perturb(
        localization, tracks, position_std_m=1.0, yaw_std_deg=0.0, size_relative_std=0.0
    )[0]
    double = perturb(
        localization, tracks, position_std_m=2.0, yaw_std_deg=0.0, size_relative_std=0.0
    )[0]

    single_dx = single.center_xy_m[0] - tracks[0].center_xy_m[0]
    double_dx = double.center_xy_m[0] - tracks[0].center_xy_m[0]

    assert double_dx == pytest.approx(2.0 * single_dx)


def test_the_yaw_stays_inside_one_turn() -> None:
    """An unwrapped yaw would compare as far from a heading it is actually beside."""

    localization = load_localization_module()
    tracks = make_tracks(40, yaw_rad=3.14)

    result = perturb(localization, tracks, yaw_std_deg=120.0)

    for track in result:
        assert -math.pi <= track.yaw_rad < math.pi


def test_a_yaw_just_past_pi_wraps_to_the_negative_side() -> None:
    """The interval is half open, so exactly +pi belongs at -pi."""

    localization = load_localization_module()

    assert localization.wrap_to_pi(math.pi) == pytest.approx(-math.pi)
    assert localization.wrap_to_pi(-math.pi) == pytest.approx(-math.pi)
    assert localization.wrap_to_pi(0.0) == 0.0


def test_the_size_error_is_relative_to_the_object() -> None:
    """A 25 cm error on a pedestrian and on a truck are not the same mistake."""

    localization = load_localization_module()
    small = make_tracks(1, size_lw_m=(1.0, 1.0))
    large = make_tracks(1, size_lw_m=(10.0, 10.0))

    small_result = perturb(
        localization, small, position_std_m=0.0, yaw_std_deg=0.0, size_relative_std=0.1
    )[0]
    large_result = perturb(
        localization, large, position_std_m=0.0, yaw_std_deg=0.0, size_relative_std=0.1
    )[0]

    small_ratio = small_result.size_lw_m[0] / small[0].size_lw_m[0]
    large_ratio = large_result.size_lw_m[0] / large[0].size_lw_m[0]

    assert small_ratio == pytest.approx(large_ratio)


def test_a_box_can_never_be_shrunk_out_of_existence() -> None:
    """A zero-area box can never overlap, so it is a threat that cannot be detected."""

    localization = load_localization_module()
    tracks = make_tracks(40, size_lw_m=(0.3, 0.3))

    result = perturb(
        localization, tracks, position_std_m=0.0, yaw_std_deg=0.0, size_relative_std=5.0
    )

    for track in result:
        assert track.size_lw_m[0] >= 0.1
        assert track.size_lw_m[1] >= 0.1


def test_length_and_width_are_drawn_independently() -> None:
    """One draw for both would keep every box's aspect ratio exactly right."""

    localization = load_localization_module()
    tracks = make_tracks(1)

    [track] = perturb(
        localization, tracks, position_std_m=0.0, yaw_std_deg=0.0, size_relative_std=0.2
    )

    length_ratio = track.size_lw_m[0] / tracks[0].size_lw_m[0]
    width_ratio = track.size_lw_m[1] / tracks[0].size_lw_m[1]

    assert length_ratio != width_ratio


def test_the_covariance_grows_with_the_position_variance() -> None:
    """A stack that is wrong by a metre and says so is a different input to a planner.

    Reporting the error without reporting the uncertainty would model a detector
    that is confidently wrong, which is a different experiment.
    """

    localization = load_localization_module()
    tracks = make_tracks(1)

    [track] = perturb(localization, tracks, position_std_m=0.5)

    assert track.covariance_xy == pytest.approx((0.25, 0.0, 0.0, 0.25))


def test_the_covariance_adds_to_what_was_already_there() -> None:
    """Overwriting would discard uncertainty another stage had already recorded."""

    localization = load_localization_module()
    tracks = make_tracks(1, covariance_xy=(1.0, 0.5, 0.5, 1.0))

    [track] = perturb(localization, tracks, position_std_m=0.5)

    assert track.covariance_xy == pytest.approx((1.25, 0.5, 0.5, 1.25))


def test_identity_and_timestamp_and_velocity_survive_untouched() -> None:
    """This channel corrupts where and how big, not who or when or how fast."""

    localization = load_localization_module()
    tracks = make_tracks(3)

    result = perturb(localization, tracks)

    for before, after in zip(tracks, result):
        assert after.track_id == before.track_id
        assert after.category == before.category
        assert after.source_timestamp_us == before.source_timestamp_us
        assert after.velocity_xy_mps == before.velocity_xy_mps


def test_adding_an_unrelated_track_does_not_change_the_others() -> None:
    """Each track's error comes from its own key, not from its place in a list."""

    localization = load_localization_module()
    tracks = make_tracks(4)

    full = perturb(localization, tracks)
    subset = perturb(localization, tracks[2:])

    by_id = {track.track_id: track for track in full}
    for track in subset:
        assert track == by_id[track.track_id]


def test_the_result_does_not_depend_on_input_order() -> None:
    """The devkit's ordering is not part of any contract and must not reach a result."""

    localization = load_localization_module()
    tracks = make_tracks(4)

    forward = perturb(localization, tracks)
    backward = perturb(localization, tuple(reversed(tracks)))

    assert {track.track_id: track for track in forward} == {
        track.track_id: track for track in backward
    }


def test_the_next_step_perturbs_differently() -> None:
    """Localization error is per observation; freezing it would model a fixed offset."""

    localization = load_localization_module()
    tracks = make_tracks(1)

    first = perturb(localization, tracks, step=0)[0]
    second = perturb(localization, tracks, step=1)[0]

    assert first.center_xy_m != second.center_xy_m


def test_an_invisible_track_is_perturbed_like_any_other() -> None:
    """A track hidden at one step must not carry a different error when it returns.

    Skipping invisible tracks would make a track's geometry depend on whether
    dropout happened to hide it earlier in the same frame, which is a coupling
    between two channels that the factorial attribution assumes does not exist.
    """

    localization = load_localization_module()
    visible = make_tracks(2, visible=True)
    hidden = make_tracks(2, visible=False)

    from_visible = perturb(localization, visible)
    from_hidden = perturb(localization, hidden)

    for shown, concealed in zip(from_visible, from_hidden):
        assert shown.center_xy_m == concealed.center_xy_m
        assert shown.size_lw_m == concealed.size_lw_m
        assert concealed.visible is False


@pytest.mark.parametrize("field", ["position_std_m", "yaw_std_deg", "size_relative_std"])
@pytest.mark.parametrize("bad_value", [-0.1, float("nan"), float("inf")])
def test_an_impossible_deviation_is_refused(field: str, bad_value: float) -> None:
    """Every deviation here is a magnitude; a negative or NaN one is a typo."""

    localization = load_localization_module()

    with pytest.raises(ValueError, match=field):
        perturb(localization, make_tracks(1), **{field: bad_value})


def test_a_negative_step_is_refused() -> None:
    """Steps are counted from zero; a negative one indexes nothing in the simulation."""

    localization = load_localization_module()

    with pytest.raises(ValueError, match="step"):
        perturb(localization, make_tracks(1), step=-1)


def test_an_empty_scene_is_handled() -> None:
    """Dropout can empty a scene before this channel runs."""

    localization = load_localization_module()

    assert perturb(localization, ()) == ()


def test_the_observed_spread_matches_the_configured_deviation() -> None:
    """The parameter has to mean what it says, not merely be deterministic.

    A channel that applied a tenth of the error it was asked to would pass every
    determinism test above while reporting a severity it never applied.
    """

    import statistics

    localization = load_localization_module()
    key = make_key()
    offsets: list[float] = []
    for step in range(200):
        tracks = make_tracks(10)
        result = localization.perturb_localization_shape(
            tracks,
            position_std_m=1.0,
            yaw_std_deg=0.0,
            size_relative_std=0.0,
            key=key,
            step=step,
        )
        offsets.extend(
            after.center_xy_m[0] - before.center_xy_m[0] for before, after in zip(tracks, result)
        )

    assert abs(statistics.fmean(offsets)) < 0.1
    assert 0.9 < statistics.stdev(offsets) < 1.1


@pytest.mark.parametrize("field", ["position_std_m", "yaw_std_deg", "size_relative_std"])
def test_a_non_numeric_deviation_is_refused(field: str) -> None:
    """A string from a config file would scale in ways nobody intended."""

    localization = load_localization_module()

    with pytest.raises(ValueError, match=field):
        perturb(localization, make_tracks(1), **{field: "0.5"})
