"""Contracts for the track that breaks and comes back as somebody else.

A real tracker loses association and re-initialises. To the AEB that is not a
brief gap, it is a *new object with no history*: the accumulated velocity
estimate is gone, so the first frames after reacquisition carry the oracle
velocity and a larger position covariance. An AEB that had been braking for a
closing vehicle sees the threat vanish and then reappear as something it knows
nothing about, and the intervention restarts from the beginning.

That is why the new identity is part of the model rather than an implementation
detail. A channel that hid a track and gave it back under the same id would
measure a short occlusion, which is a different failure with a different cost.

Two rules keep this channel from smuggling in another one. The draw happens
whether or not the track is currently visible, so a fragmentation outcome never
depends on whether dropout happened to hide the track first; and a reacquired
track that dropout has hidden stays hidden, so this channel can lose a track but
never resurrect one.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

BASE_US = 1_600_000_000_000_000
STEP_US = 100_000

#: A rate this large makes ``1 - exp(-rate * dt)`` indistinguishable from one, so
#: every track fragments and the identity and timing rules can be tested without
#: reaching inside the generator to force a draw.
CERTAIN_RATE = 1.0e6


def load_fragmentation_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.errors import fragmentation
    except ImportError:
        pytest.fail("aebrisk.errors.fragmentation is missing", pytrace=False)
    return fragmentation


def make_key() -> Any:
    from aebrisk.errors.pipeline import ErrorKey

    return ErrorKey(
        scenario_token="s-0001",
        channel="track_instability",
        severity="high",
        replicate=0,
        protocol_hash="a" * 64,
    )


def make_track(
    track_id: str = "t-0001",
    *,
    center: tuple[float, float] = (10.0, 2.0),
    visible: bool = True,
    timestamp_us: int = BASE_US,
    velocity: tuple[float, float] = (5.0, 0.0),
    covariance: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
) -> Any:
    from aebrisk.observation.models import TrackState

    return TrackState(
        track_id=track_id,
        category="vehicle",
        center_xy_m=center,
        yaw_rad=0.0,
        size_lw_m=(4.5, 1.9),
        velocity_xy_mps=velocity,
        visible=visible,
        source_timestamp_us=timestamp_us,
        covariance_xy=covariance,
    )


def test_the_per_step_probability_is_the_hazard_rate_conversion() -> None:
    """A rate per second becomes a per-step probability as ``1 - exp(-rate * dt)``.

    Multiplying the rate by the step instead would exceed one for any rate above
    ten at 10 Hz, and would silently make the two highest severities identical.
    """

    import math

    fragmentation = load_fragmentation_module()

    assert fragmentation.fragmentation_probability(0.5, 0.1) == pytest.approx(1.0 - math.exp(-0.05))
    assert fragmentation.fragmentation_probability(0.0, 0.1) == 0.0
    assert 0.0 < fragmentation.fragmentation_probability(1000.0, 0.1) <= 1.0


def test_zero_rate_leaves_every_identity_alone() -> None:
    """Severity zero is the reference; it must be the exact identity on ids."""

    fragmentation = load_fragmentation_module()
    tracks = (make_track("t-0001"), make_track("t-0002", center=(20.0, -3.0)))

    updated, memories = fragmentation.update_fragmentation(
        tracks,
        {},
        rate_per_s=0.0,
        reacquisition_delay_s=1.0,
        key=make_key(),
        step=0,
    )

    assert [track.track_id for track in updated] == ["t-0001", "t-0002"]
    assert all(track.visible for track in updated)
    assert sorted(memories) == ["t-0001", "t-0002"]


def test_zero_rate_keeps_the_observation_otherwise_untouched() -> None:
    """Only velocity is re-derived; position, size and covariance are not this channel's."""

    fragmentation = load_fragmentation_module()
    track = make_track("t-0001", covariance=(0.25, 0.0, 0.0, 0.25))

    (updated,), _ = fragmentation.update_fragmentation(
        (track,),
        {},
        rate_per_s=0.0,
        reacquisition_delay_s=1.0,
        key=make_key(),
        step=0,
    )

    assert updated.center_xy_m == track.center_xy_m
    assert updated.size_lw_m == track.size_lw_m
    assert updated.covariance_xy == track.covariance_xy


def test_a_fragmented_track_is_hidden_for_the_configured_delay() -> None:
    """The gap is what a tracker's re-initialisation costs, in frames."""

    fragmentation = load_fragmentation_module()
    key = make_key()

    updated, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US),),
        {},
        rate_per_s=CERTAIN_RATE,
        reacquisition_delay_s=0.3,
        key=key,
        step=0,
    )

    assert updated[0].visible is False
    assert memories["t-0001"].reacquire_after_us == BASE_US + 300_000


def test_the_release_step_is_exact() -> None:
    """One frame early would shorten the modelled outage; one late would lengthen it."""

    fragmentation = load_fragmentation_module()
    key = make_key()
    memories: Any = {}

    seen: list[bool] = []
    for step in range(5):
        stamp = BASE_US + step * STEP_US
        # Only the first step may fragment; afterwards the rate is zero so the
        # test observes the delay alone rather than repeated fragmentation.
        rate = CERTAIN_RATE if step == 0 else 0.0
        updated, memories = fragmentation.update_fragmentation(
            (make_track("t-0001", timestamp_us=stamp),),
            memories,
            rate_per_s=rate,
            reacquisition_delay_s=0.3,
            key=key,
            step=step,
        )
        seen.append(updated[0].visible)

    # Fragmented at BASE_US, released when the stamp reaches BASE_US + 300_000,
    # which is step 3.
    assert seen == [False, False, False, True, True]


def test_reacquisition_returns_a_new_public_identity() -> None:
    """To the AEB the object that comes back is not the one that left."""

    fragmentation = load_fragmentation_module()
    key = make_key()

    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US),),
        {},
        rate_per_s=CERTAIN_RATE,
        reacquisition_delay_s=0.1,
        key=key,
        step=0,
    )
    updated, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US + STEP_US),),
        memories,
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=1,
    )

    assert updated[0].track_id != "t-0001"
    assert updated[0].visible is True


def test_the_new_identity_is_derived_from_the_source_and_a_generation() -> None:
    """Deterministic, not a random UUID: two runs of the same cell must agree.

    A random identity would also make a run record unreadable, because nothing
    in it would connect the reacquired track to the object it came from.
    """

    fragmentation = load_fragmentation_module()
    key = make_key()

    def fragment_once(memories: Any, step: int) -> Any:
        stamp = BASE_US + step * STEP_US
        _, memories = fragmentation.update_fragmentation(
            (make_track("t-0001", timestamp_us=stamp),),
            memories,
            rate_per_s=CERTAIN_RATE,
            reacquisition_delay_s=0.1,
            key=key,
            step=step,
        )
        updated, memories = fragmentation.update_fragmentation(
            (make_track("t-0001", timestamp_us=stamp + STEP_US),),
            memories,
            rate_per_s=0.0,
            reacquisition_delay_s=0.1,
            key=key,
            step=step + 1,
        )
        return updated[0].track_id, memories

    first, memories = fragment_once({}, 0)
    second, _ = fragment_once(memories, 2)

    assert first == "t-0001#1"
    assert second == "t-0001#2"


def test_the_reacquired_track_carries_a_larger_position_covariance() -> None:
    """A freshly initialised track has no history, and the record must say so."""

    fragmentation = load_fragmentation_module()
    key = make_key()

    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US, covariance=(0.25, 0.0, 0.0, 0.25)),),
        {},
        rate_per_s=CERTAIN_RATE,
        reacquisition_delay_s=0.1,
        key=key,
        step=0,
    )
    updated, _ = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US + STEP_US, covariance=(0.25, 0.0, 0.0, 0.25)),),
        memories,
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=1,
    )

    assert updated[0].covariance_xy[0] > 0.25
    assert updated[0].covariance_xy[3] > 0.25
    assert updated[0].covariance_xy[1] == 0.0


def test_the_reacquired_track_reports_the_oracle_velocity() -> None:
    """There is no previous position to difference against, by construction."""

    fragmentation = load_fragmentation_module()
    key = make_key()

    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US),),
        {},
        rate_per_s=CERTAIN_RATE,
        reacquisition_delay_s=0.1,
        key=key,
        step=0,
    )
    updated, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US + STEP_US, velocity=(7.0, -2.0)),),
        memories,
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=1,
    )

    assert updated[0].velocity_xy_mps == (7.0, -2.0)
    assert memories[updated[0].track_id.split("#")[0]].previous_center_xy_m is not None


def test_a_continuously_tracked_object_gets_a_differenced_velocity() -> None:
    """The whole point: the AEB divides by an estimate, not by the truth."""

    fragmentation = load_fragmentation_module()
    key = make_key()

    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", center=(10.0, 0.0), timestamp_us=BASE_US),),
        {},
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=0,
    )
    updated, _ = fragmentation.update_fragmentation(
        (
            make_track(
                "t-0001",
                center=(11.0, 0.5),
                timestamp_us=BASE_US + STEP_US,
                velocity=(99.0, 99.0),
            ),
        ),
        memories,
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=1,
    )

    assert updated[0].velocity_xy_mps == pytest.approx((10.0, 5.0))


def test_the_difference_uses_the_time_actually_elapsed() -> None:
    """A track absent from two frames has moved two frames' worth.

    Dividing by the nominal step would report three times the speed and turn a
    parked car into a threat.
    """

    fragmentation = load_fragmentation_module()
    key = make_key()

    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", center=(10.0, 0.0), timestamp_us=BASE_US),),
        {},
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=0,
    )
    # The track is absent for two frames, then returns three steps later.
    updated, _ = fragmentation.update_fragmentation(
        (make_track("t-0001", center=(13.0, 0.0), timestamp_us=BASE_US + 3 * STEP_US),),
        memories,
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=3,
    )

    assert updated[0].velocity_xy_mps == pytest.approx((10.0, 0.0))


def test_a_repeated_timestamp_falls_back_to_the_oracle_velocity() -> None:
    """Zero elapsed time is a division by zero, and a log can repeat a stamp.

    Refusing outright would abort a scenario over one duplicated frame, and
    differencing anyway would produce an infinite speed that reaches the AEB's
    time-to-collision. The oracle value is the only honest answer here, and
    ``velocity_source`` is what records that it was used.
    """

    fragmentation = load_fragmentation_module()
    key = make_key()

    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", center=(10.0, 0.0), timestamp_us=BASE_US),),
        {},
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=0,
    )
    updated, _ = fragmentation.update_fragmentation(
        (
            make_track(
                "t-0001",
                center=(11.0, 0.0),
                timestamp_us=BASE_US,
                velocity=(4.0, -1.0),
            ),
        ),
        memories,
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=1,
    )

    assert updated[0].velocity_xy_mps == (4.0, -1.0)


def test_a_memory_for_an_absent_track_is_carried_forward() -> None:
    """Dropping it would release a fragmented track early, the moment it flickered."""

    fragmentation = load_fragmentation_module()
    key = make_key()

    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US),),
        {},
        rate_per_s=CERTAIN_RATE,
        reacquisition_delay_s=1.0,
        key=key,
        step=0,
    )
    _, memories = fragmentation.update_fragmentation(
        (),
        memories,
        rate_per_s=0.0,
        reacquisition_delay_s=1.0,
        key=key,
        step=1,
    )

    assert memories["t-0001"].reacquire_after_us == BASE_US + 1_000_000


def test_reacquisition_does_not_resurrect_a_track_another_channel_hid() -> None:
    """This channel can lose a track. It must never give one back."""

    fragmentation = load_fragmentation_module()
    key = make_key()

    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US),),
        {},
        rate_per_s=CERTAIN_RATE,
        reacquisition_delay_s=0.1,
        key=key,
        step=0,
    )
    updated, _ = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US + STEP_US, visible=False),),
        memories,
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=key,
        step=1,
    )

    assert updated[0].visible is False


def test_an_already_hidden_track_still_draws() -> None:
    """The outcome must not depend on whether dropout ran first.

    If a hidden track were exempt, the tracking channel's effect would change
    when the dropout channel changed, and the factorial attribution assumes the
    channels do not couple like that.
    """

    fragmentation = load_fragmentation_module()
    key = make_key()

    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001", timestamp_us=BASE_US, visible=False),),
        {},
        rate_per_s=CERTAIN_RATE,
        reacquisition_delay_s=0.1,
        key=key,
        step=0,
    )

    assert memories["t-0001"].reacquire_after_us == BASE_US + 100_000


def test_the_hidden_track_is_kept_rather_than_removed() -> None:
    """The record of what was lost is what makes a missed intervention explainable."""

    fragmentation = load_fragmentation_module()

    updated, _ = fragmentation.update_fragmentation(
        (make_track("t-0001"), make_track("t-0002", center=(20.0, 1.0))),
        {},
        rate_per_s=CERTAIN_RATE,
        reacquisition_delay_s=0.1,
        key=make_key(),
        step=0,
    )

    assert len(updated) == 2


def test_the_output_order_follows_the_input() -> None:
    """Iteration order reaches the AEB's tie-breaking between equally urgent threats."""

    fragmentation = load_fragmentation_module()
    tracks = tuple(
        make_track(f"t-{number:04d}", center=(10.0 + number, 0.0)) for number in range(5)
    )

    updated, _ = fragmentation.update_fragmentation(
        tracks,
        {},
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=make_key(),
        step=0,
    )

    assert [track.track_id for track in updated] == [track.track_id for track in tracks]


def test_the_channel_is_deterministic() -> None:
    """Same key, same step, same tracks: the same outcome, every time."""

    fragmentation = load_fragmentation_module()
    tracks = tuple(
        make_track(f"t-{number:04d}", center=(10.0 + number, 0.0)) for number in range(8)
    )

    first, _ = fragmentation.update_fragmentation(
        tracks, {}, rate_per_s=2.0, reacquisition_delay_s=0.5, key=make_key(), step=4
    )
    second, _ = fragmentation.update_fragmentation(
        tracks, {}, rate_per_s=2.0, reacquisition_delay_s=0.5, key=make_key(), step=4
    )

    assert first == second


def test_one_tracks_outcome_does_not_depend_on_the_others() -> None:
    """Adding a parked car at the edge of the scene must not change the car in front."""

    fragmentation = load_fragmentation_module()
    subject = make_track("t-0001")
    crowd = tuple(
        make_track(f"t-{number:04d}", center=(30.0 + number, 8.0)) for number in range(2, 9)
    )

    alone, _ = fragmentation.update_fragmentation(
        (subject,), {}, rate_per_s=2.0, reacquisition_delay_s=0.5, key=make_key(), step=3
    )
    crowded, _ = fragmentation.update_fragmentation(
        (subject, *crowd), {}, rate_per_s=2.0, reacquisition_delay_s=0.5, key=make_key(), step=3
    )

    assert alone[0] == crowded[0]


def test_the_memory_is_frozen() -> None:
    """It is provenance for which observed object came from which real one."""

    import dataclasses

    fragmentation = load_fragmentation_module()
    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001"),),
        {},
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=make_key(),
        step=0,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        memories["t-0001"].public_track_id = "other"  # type: ignore[misc]


def test_the_memory_records_the_source_it_came_from() -> None:
    """Without it, no artifact can connect a reacquired track to the real object."""

    fragmentation = load_fragmentation_module()
    _, memories = fragmentation.update_fragmentation(
        (make_track("t-0001"),),
        {},
        rate_per_s=0.0,
        reacquisition_delay_s=0.1,
        key=make_key(),
        step=0,
    )

    assert memories["t-0001"].source_track_id == "t-0001"
    assert memories["t-0001"].last_seen_timestamp_us == BASE_US


@pytest.mark.parametrize("bad_value", [-0.1, float("nan"), float("inf")])
def test_an_impossible_rate_is_refused(bad_value: float) -> None:
    """A negative rate is a negative probability."""

    fragmentation = load_fragmentation_module()

    with pytest.raises(ValueError, match=r"^rate_per_s must "):
        fragmentation.update_fragmentation(
            (make_track(),),
            {},
            rate_per_s=bad_value,
            reacquisition_delay_s=0.1,
            key=make_key(),
            step=0,
        )


@pytest.mark.parametrize("bad_value", ["1", True, None])
def test_a_rate_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean one fragmentation per second."""

    fragmentation = load_fragmentation_module()

    with pytest.raises(ValueError, match=r"^rate_per_s must be a number$"):
        fragmentation.update_fragmentation(
            (make_track(),),
            {},
            rate_per_s=bad_value,  # type: ignore[arg-type]
            reacquisition_delay_s=0.1,
            key=make_key(),
            step=0,
        )


@pytest.mark.parametrize("bad_value", [-0.1, float("nan"), float("inf")])
def test_an_impossible_reacquisition_delay_is_refused(bad_value: float) -> None:
    """A negative delay would release a track before it was lost."""

    fragmentation = load_fragmentation_module()

    with pytest.raises(ValueError, match=r"^reacquisition_delay_s must "):
        fragmentation.update_fragmentation(
            (make_track(),),
            {},
            rate_per_s=0.5,
            reacquisition_delay_s=bad_value,
            key=make_key(),
            step=0,
        )


@pytest.mark.parametrize("bad_value", ["1", True, None])
def test_a_reacquisition_delay_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """The same guard on the second parameter of the pair."""

    fragmentation = load_fragmentation_module()

    with pytest.raises(ValueError, match=r"^reacquisition_delay_s must be a number$"):
        fragmentation.update_fragmentation(
            (make_track(),),
            {},
            rate_per_s=0.5,
            reacquisition_delay_s=bad_value,  # type: ignore[arg-type]
            key=make_key(),
            step=0,
        )


@pytest.mark.parametrize("bad_value", [0.0, -0.1, float("nan")])
def test_an_impossible_step_duration_is_refused(bad_value: float) -> None:
    """The hazard conversion divides the rate over it; zero makes every rate harmless."""

    fragmentation = load_fragmentation_module()

    with pytest.raises(ValueError, match=r"^dt_s must "):
        fragmentation.update_fragmentation(
            (make_track(),),
            {},
            rate_per_s=0.5,
            reacquisition_delay_s=0.1,
            key=make_key(),
            step=0,
            dt_s=bad_value,
        )


@pytest.mark.parametrize("bad_value", ["0.1", True, None])
def test_a_step_duration_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean a one second step."""

    fragmentation = load_fragmentation_module()

    with pytest.raises(ValueError, match=r"^dt_s\ must\ be\ a\ number"):
        fragmentation.update_fragmentation(
            (make_track(),),
            {},
            rate_per_s=0.5,
            reacquisition_delay_s=0.1,
            key=make_key(),
            step=0,
            dt_s=bad_value,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("bad_value", [-1, True, 0.5])
def test_an_impossible_step_index_is_refused(bad_value: object) -> None:
    """The step is what makes each frame draw again; a wrong type silences that."""

    fragmentation = load_fragmentation_module()

    with pytest.raises(ValueError, match=r"^step\ must\ be\ a\ non\-negative\ integer$"):
        fragmentation.update_fragmentation(
            (make_track(),),
            {},
            rate_per_s=0.5,
            reacquisition_delay_s=0.1,
            key=make_key(),
            step=bad_value,  # type: ignore[arg-type]
        )
