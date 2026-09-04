"""Contracts for the observation that is correct but old.

This channel is unlike the others: it does not corrupt an observation, it
chooses *which* observation the rest of the pipeline sees. That is why it runs
first, and why its one non-negotiable property is causality. At 15 m/s, 0.4 s of
latency is six metres of closing distance the AEB cannot see; a selection that
reached even one frame into the future would hand the controller information the
real system could not have had, and the whole study would overstate what an AEB
can do.

So the target is a timestamp, the search never looks forward, a tie goes to the
older frame, and a history too short to satisfy the request is invalid rather
than clamped. Clamping would quietly return a fresher observation than the
configuration asked for and report the severity as if it had been applied.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

BASE_US = 1_600_000_000_000_000


def load_latency_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.errors import latency
    except ImportError:
        pytest.fail("aebrisk.errors.latency is missing", pytrace=False)
    return latency


def make_frame(index: int, timestamp_us: int | None = None, tracks: int = 2) -> Any:
    from aebrisk.observation.models import TrackState, WorldFrame

    stamp = BASE_US + index * 100_000 if timestamp_us is None else timestamp_us
    return WorldFrame(
        scenario_token="s-0001",
        timestamp_us=stamp,
        ego_center_xy_m=(float(index), 0.0),
        ego_yaw_rad=0.0,
        ego_speed_mps=8.0,
        tracks=tuple(
            TrackState(
                track_id=f"t-{number:04d}",
                category="vehicle",
                center_xy_m=(10.0 + number + index, 2.0),
                yaw_rad=0.0,
                size_lw_m=(4.5, 1.9),
                velocity_xy_mps=(5.0, 0.0),
                visible=True,
                source_timestamp_us=stamp,
                covariance_xy=(0.0, 0.0, 0.0, 0.0),
            )
            for number in range(tracks)
        ),
    )


def regular_history(count: int = 8) -> tuple[Any, ...]:
    return tuple(make_frame(index) for index in range(count))


def test_zero_latency_selects_the_current_frame() -> None:
    """Severity zero is the reference; it must observe the present."""

    latency = load_latency_module()
    history = regular_history()

    selection = latency.select_latency_frame(history, 5, 0.0)

    assert selection.selected_index == 5
    assert selection.realized_latency_s == 0.0
    assert selection.valid is True


@pytest.mark.parametrize(
    ("requested", "steps"),
    [(0.1, 1), (0.2, 2), (0.4, 4)],
)
def test_the_declared_latencies_select_whole_steps_at_ten_hertz(
    requested: float,
    steps: int,
) -> None:
    """The fixed matrix is stated in seconds; at 10 Hz each is a whole frame."""

    latency = load_latency_module()
    history = regular_history()

    selection = latency.select_latency_frame(history, 6, requested)

    assert selection.selected_index == 6 - steps
    assert selection.realized_latency_s == pytest.approx(requested)


def test_the_requested_and_realized_latencies_are_both_recorded() -> None:
    """They differ whenever the history is irregular, and a reader needs both."""

    latency = load_latency_module()
    history = regular_history()

    selection = latency.select_latency_frame(history, 4, 0.2)

    assert selection.requested_latency_s == 0.2
    assert selection.realized_latency_s == pytest.approx(0.2)


def test_a_history_too_short_for_the_request_is_invalid() -> None:
    """Clamping would return a fresher observation than the severity asked for.

    The run would then report the high-latency configuration while having
    applied a lower one, and no artifact would show it.
    """

    latency = load_latency_module()
    history = regular_history()

    selection = latency.select_latency_frame(history, 2, 0.4)

    assert selection.valid is False


def test_an_invalid_selection_never_points_into_the_future() -> None:
    """The one thing that must never happen, stated as its own test."""

    latency = load_latency_module()
    history = regular_history()

    selection = latency.select_latency_frame(history, 1, 0.4)

    assert selection.selected_index <= 1


def test_an_invalid_selection_carries_a_reason() -> None:
    """A frame nobody could observe must say why, or it looks like an empty road."""

    latency = load_latency_module()
    history = regular_history()

    selection = latency.select_latency_frame(history, 1, 0.4)

    assert latency.invalid_reason(selection)
    assert "latency" in latency.invalid_reason(selection)


def test_a_valid_selection_has_no_reason() -> None:
    """The pair with the test above, so the field cannot be constant."""

    latency = load_latency_module()

    selection = latency.select_latency_frame(regular_history(), 5, 0.2)

    assert latency.invalid_reason(selection) is None


def test_an_invalid_observation_shows_nothing() -> None:
    """A system that cannot yet observe reports no detections, not stale ones."""

    latency = load_latency_module()
    history = regular_history()

    tracks, selection = latency.apply_latency(history, 1, 0.4)

    assert selection.valid is False
    assert all(not track.visible for track in tracks)


def test_a_valid_observation_returns_the_older_frame_intact() -> None:
    """The observation is correct, only old; nothing else about it changes."""

    latency = load_latency_module()
    history = regular_history()

    tracks, selection = latency.apply_latency(history, 5, 0.2)

    assert selection.selected_index == 3
    assert tracks == history[3].tracks


def test_zero_latency_is_the_exact_identity() -> None:
    """Severity zero must return the current frame's tracks unchanged."""

    latency = load_latency_module()
    history = regular_history()

    tracks, _ = latency.apply_latency(history, 5, 0.0)

    assert tracks == history[5].tracks


def test_an_irregular_history_selects_the_nearest_frame_not_later_than_the_target() -> None:
    """Real logs are not perfectly regular, and rounding forward would leak the future."""

    latency = load_latency_module()
    history = (
        make_frame(0, BASE_US),
        make_frame(1, BASE_US + 90_000),
        make_frame(2, BASE_US + 210_000),
        make_frame(3, BASE_US + 300_000),
    )

    # Target is 300_000 - 100_000 = 200_000, which lies between frames 1 and 2.
    selection = latency.select_latency_frame(history, 3, 0.1)

    assert selection.selected_index == 1
    assert selection.realized_latency_s == pytest.approx(0.21)


def test_a_target_landing_exactly_on_a_frame_selects_that_frame() -> None:
    """Not later than the target includes the target itself."""

    latency = load_latency_module()
    history = regular_history()

    selection = latency.select_latency_frame(history, 5, 0.3)

    assert selection.selected_index == 2
    assert selection.realized_latency_s == pytest.approx(0.3)


def test_two_frames_sharing_a_timestamp_select_the_older() -> None:
    """A tie resolved forward would hand the controller the newer of the two."""

    latency = load_latency_module()
    history = (
        make_frame(0, BASE_US),
        make_frame(1, BASE_US + 100_000),
        make_frame(2, BASE_US + 100_000),
        make_frame(3, BASE_US + 200_000),
    )

    selection = latency.select_latency_frame(history, 3, 0.1)

    assert selection.selected_index == 1


def test_a_history_that_goes_backwards_in_time_is_refused() -> None:
    """Non-monotonic timestamps mean the history was assembled wrongly.

    Searching it would return a frame that is neither the newest past one nor
    reproducible, and the selection would depend on the assembly order.
    """

    latency = load_latency_module()
    history = (
        make_frame(0, BASE_US),
        make_frame(1, BASE_US + 200_000),
        make_frame(2, BASE_US + 100_000),
    )

    with pytest.raises(
        ValueError,
        match=r"^history\ timestamps\ must\ be\ monotonic;\ a\ history\ that\ goes\ backwards\ ",
    ):
        latency.select_latency_frame(history, 2, 0.1)


def test_an_empty_history_is_refused() -> None:
    """There is nothing to observe, so any selection would be invented."""

    latency = load_latency_module()

    with pytest.raises(ValueError, match="history"):
        latency.select_latency_frame((), 0, 0.0)


def test_an_index_outside_the_history_is_refused() -> None:
    """Reading past the end would silently observe the last frame forever."""

    latency = load_latency_module()

    with pytest.raises(IndexError, match=r"^current_index\ "):
        latency.select_latency_frame(regular_history(4), 4, 0.0)


@pytest.mark.parametrize("bad_value", [-0.1, float("nan"), float("inf")])
def test_an_impossible_latency_is_refused(bad_value: float) -> None:
    """A negative latency would be a request to observe the future."""

    latency = load_latency_module()

    with pytest.raises(ValueError, match="requested_latency_s"):
        latency.select_latency_frame(regular_history(), 3, bad_value)


@pytest.mark.parametrize("bad_value", [0.0, -10.0, float("nan")])
def test_an_impossible_frequency_is_refused(bad_value: float) -> None:
    """The sampling rate is what the request is expressed in steps of."""

    latency = load_latency_module()

    with pytest.raises(ValueError, match="frequency_hz"):
        latency.select_latency_frame(regular_history(), 3, 0.1, frequency_hz=bad_value)


@pytest.mark.parametrize("bad_value", ["0.1", True, None])
def test_a_latency_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` arithmetics to one second, and ``"0.1"`` to a type error deep inside.

    Both would be configuration mistakes that a comparison alone would either
    accept silently or fail far from their cause, so the type is checked here.
    """

    latency = load_latency_module()

    with pytest.raises(ValueError, match=r"^requested_latency_s\ must\ be\ a\ number"):
        latency.select_latency_frame(regular_history(), 3, bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", ["10", True, None])
def test_a_frequency_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """The same guard on the rate the request is expressed in steps of."""

    latency = load_latency_module()

    with pytest.raises(ValueError, match="frequency_hz must be a number"):
        latency.select_latency_frame(regular_history(), 3, 0.1, frequency_hz=bad_value)  # type: ignore[arg-type]


def test_the_selection_is_frozen() -> None:
    """The selection is provenance for what the controller was allowed to see."""

    import dataclasses

    latency = load_latency_module()
    selection = latency.select_latency_frame(regular_history(), 3, 0.1)

    with pytest.raises(dataclasses.FrozenInstanceError):
        selection.selected_index = 0  # type: ignore[misc]


def test_the_first_frame_can_always_be_observed_with_zero_latency() -> None:
    """A run must be able to start; zero latency at step zero is the base case."""

    latency = load_latency_module()

    selection = latency.select_latency_frame((make_frame(0),), 0, 0.0)

    assert selection.valid is True
    assert selection.selected_index == 0


def test_the_reason_names_how_many_frames_were_needed() -> None:
    """A reason a reader cannot act on is only slightly better than none."""

    latency = load_latency_module()

    selection = latency.select_latency_frame(regular_history(), 1, 0.4)
    reason = latency.invalid_reason(selection)

    assert reason is not None
    assert "0.4" in reason
