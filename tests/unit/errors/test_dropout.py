"""Contracts for the detection the perception stack never reported.

The property that matters most here is that each track's fate is drawn from its
own key rather than from a shared sequence. A sequential stream would make one
track's visibility depend on how many tracks happened to precede it, so adding a
parked car at the edge of the scene would change whether the pedestrian in front
was seen. That is not a perception error; it is an artefact of the simulator,
and it would be invisible in every result.

Dropped tracks are kept with ``visible=False`` rather than removed. The AEB
filters them, but the record of what was hidden is what makes a missed
intervention explainable afterwards.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

PROTOCOL_HASH = "a" * 64


def load_dropout_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        import aebrisk.errors.dropout as dropout
    except ImportError:
        pytest.fail("aebrisk.errors.dropout is missing", pytrace=False)
    return dropout


def make_key(**overrides: Any) -> Any:
    from aebrisk.errors.pipeline import ErrorKey

    values: dict[str, Any] = {
        "scenario_token": "s-0001",
        "channel": "dropout",
        "severity": "medium",
        "replicate": 0,
        "protocol_hash": PROTOCOL_HASH,
    }
    values.update(overrides)
    return ErrorKey(**values)


def make_tracks(count: int = 6, *, visible: bool = True) -> tuple[Any, ...]:
    from aebrisk.observation.models import TrackState

    return tuple(
        TrackState(
            track_id=f"t-{number:04d}",
            category="vehicle",
            center_xy_m=(10.0 + number, 2.0),
            yaw_rad=0.0,
            size_lw_m=(4.5, 1.9),
            velocity_xy_mps=(5.0, 0.0),
            visible=visible,
            source_timestamp_us=1_600_000_000_000_000,
            covariance_xy=(0.0, 0.0, 0.0, 0.0),
        )
        for number in range(count)
    )


def test_zero_probability_is_the_exact_identity() -> None:
    """Severity zero is the reference; it must return the tracks unchanged."""

    dropout = load_dropout_module()
    tracks = make_tracks()

    result, _ = dropout.apply_dropout(tracks, 0.0, make_key(), 0, None)

    assert result == tracks


def test_certain_dropout_hides_every_track() -> None:
    """The other end of the range, so the comparison is pinned from both sides."""

    dropout = load_dropout_module()

    result, _ = dropout.apply_dropout(make_tracks(), 1.0, make_key(), 0, None)

    assert all(not track.visible for track in result)


def test_a_hidden_track_is_kept_rather_than_removed() -> None:
    """The record of what was hidden is what makes a missed intervention explainable."""

    dropout = load_dropout_module()
    tracks = make_tracks(4)

    result, _ = dropout.apply_dropout(tracks, 1.0, make_key(), 0, None)

    assert len(result) == len(tracks)
    assert [track.track_id for track in result] == [track.track_id for track in tracks]


def test_only_visibility_changes() -> None:
    """Dropout hides an object; it does not move it or resize it."""

    dropout = load_dropout_module()
    tracks = make_tracks(4)

    result, _ = dropout.apply_dropout(tracks, 1.0, make_key(), 0, None)

    for before, after in zip(tracks, result):
        assert after.center_xy_m == before.center_xy_m
        assert after.size_lw_m == before.size_lw_m
        assert after.velocity_xy_mps == before.velocity_xy_mps
        assert after.covariance_xy == before.covariance_xy


def test_dropout_never_revives_a_track_another_stage_hid() -> None:
    """Dropout is a loss. Turning a hidden track visible would undo another channel."""

    dropout = load_dropout_module()
    tracks = make_tracks(4, visible=False)

    result, _ = dropout.apply_dropout(tracks, 0.0, make_key(), 0, None)

    assert all(not track.visible for track in result)


def test_the_draw_is_the_documented_function_of_the_key_track_and_step() -> None:
    """Pinning the derivation stops it drifting while still looking deterministic."""

    import hashlib
    import json

    import numpy as np

    from aebrisk.errors.pipeline import keyed_seed

    dropout = load_dropout_module()
    key = make_key()
    payload = json.dumps(
        {"seed": keyed_seed(key), "track_id": "t-0000", "step": 3, "field": "dropout"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    seed = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    expected_draw = np.random.Generator(np.random.PCG64(seed)).random()

    assert dropout.dropout_draw(key, "t-0000", 3) == expected_draw


def test_each_track_is_drawn_independently_of_the_others() -> None:
    """A track's fate must not depend on how many tracks preceded it in the list.

    Otherwise adding a parked car at the edge of the scene changes whether the
    pedestrian in front was seen, which is a simulator artefact rather than a
    perception error and would be invisible in every result.
    """

    dropout = load_dropout_module()
    key = make_key()
    tracks = make_tracks(6)

    full, _ = dropout.apply_dropout(tracks, 0.5, key, 0, None)
    subset, _ = dropout.apply_dropout(tracks[3:], 0.5, key, 0, None)

    by_id = {track.track_id: track.visible for track in full}
    assert [track.visible for track in subset] == [by_id[track.track_id] for track in tracks[3:]]


def test_the_result_does_not_depend_on_input_order() -> None:
    """The devkit's ordering is not part of any contract and must not reach a result."""

    dropout = load_dropout_module()
    key = make_key()
    tracks = make_tracks(6)

    forward, _ = dropout.apply_dropout(tracks, 0.5, key, 0, None)
    backward, _ = dropout.apply_dropout(tuple(reversed(tracks)), 0.5, key, 0, None)

    assert {track.track_id: track.visible for track in forward} == {
        track.track_id: track.visible for track in backward
    }


def test_each_track_identity_reaches_its_own_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct identities select distinct deterministic fates in one frame."""

    dropout = load_dropout_module()
    tracks = make_tracks(2)
    draws = {tracks[0].track_id: 0.2, tracks[1].track_id: 0.8}
    monkeypatch.setattr(
        dropout,
        "dropout_draw",
        lambda _key, track_id, _step: draws[track_id],
    )

    result, _ = dropout.apply_dropout(tracks, 0.5, make_key(), 0, None)

    assert [track.visible for track in result] == [False, True]


def test_a_draw_equal_to_probability_remains_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dropout is the half-open event draw < probability."""

    dropout = load_dropout_module()
    monkeypatch.setattr(dropout, "dropout_draw", lambda *_args: 0.5)

    result, _ = dropout.apply_dropout(make_tracks(1), 0.5, make_key(), 0, None)

    assert result[0].visible is True


def test_repeating_one_step_gives_the_same_answer() -> None:
    """A step recomputed during a retry must not re-roll what was already observed."""

    dropout = load_dropout_module()
    key = make_key()
    tracks = make_tracks(8)

    first, state = dropout.apply_dropout(tracks, 0.4, key, 5, None)
    second, _ = dropout.apply_dropout(tracks, 0.4, key, 5, state)

    assert first == second


def test_the_next_step_draws_again() -> None:
    """Dropout is per frame; freezing it would model an occlusion, not a detector."""

    dropout = load_dropout_module()
    key = make_key()
    tracks = make_tracks(20)

    first, state = dropout.apply_dropout(tracks, 0.5, key, 0, None)
    second, _ = dropout.apply_dropout(tracks, 0.5, key, 1, state)

    assert [track.visible for track in first] != [track.visible for track in second]


def test_the_state_records_the_step_and_what_was_visible() -> None:
    """The state is the audit trail for a frame nobody can re-derive by eye."""

    dropout = load_dropout_module()
    tracks = make_tracks(3)

    result, state = dropout.apply_dropout(tracks, 0.5, make_key(), 7, None)

    assert state.last_step == 7
    assert state.visible_by_track == {track.track_id: track.visible for track in result}


def test_the_state_is_frozen() -> None:
    """An audit record that can be edited afterwards is not a record."""

    import dataclasses

    dropout = load_dropout_module()
    _, state = dropout.apply_dropout(make_tracks(2), 0.5, make_key(), 0, None)

    with pytest.raises(dataclasses.FrozenInstanceError):
        state.last_step = 99  # type: ignore[misc]


def test_a_step_that_goes_backwards_is_refused() -> None:
    """Time running backwards means the simulation is being replayed wrongly.

    Silently redrawing would corrupt a frame that has already been observed and
    acted on, and no result would show that it happened.
    """

    dropout = load_dropout_module()
    key = make_key()
    _, state = dropout.apply_dropout(make_tracks(), 0.5, key, 5, None)

    with pytest.raises(ValueError, match=r"^(step\ |step\ must\ be\ a\ non\-negative\ integer)"):
        dropout.apply_dropout(make_tracks(), 0.5, key, 4, state)


def test_a_track_absent_from_the_recorded_step_is_drawn_fresh() -> None:
    """A track that appears mid-step has no recorded fate, so it needs one."""

    dropout = load_dropout_module()
    key = make_key()
    _, state = dropout.apply_dropout(make_tracks(2), 0.5, key, 3, None)

    result, updated = dropout.apply_dropout(make_tracks(4), 0.5, key, 3, state)

    assert len(result) == 4
    assert set(updated.visible_by_track) == {track.track_id for track in result}


def test_an_empty_scene_is_handled() -> None:
    """Dropout can empty a scene, and the next step must still be able to run."""

    dropout = load_dropout_module()

    result, state = dropout.apply_dropout((), 0.5, make_key(), 0, None)

    assert result == ()
    assert state.visible_by_track == {}
    assert state.last_step == 0


@pytest.mark.parametrize("probability", [-0.01, 1.01, float("nan")])
def test_a_probability_outside_the_unit_interval_is_refused(probability: float) -> None:
    """A probability above one is a typo that would silently mean certainty."""

    dropout = load_dropout_module()

    with pytest.raises(
        ValueError,
        match=r"^(probability\ must\ be\ a\ number|probability\ must\ lie\ in\ \[0,\ 1\],\ got\ )",
    ):
        dropout.apply_dropout(make_tracks(), probability, make_key(), 0, None)


def test_a_negative_step_is_refused() -> None:
    """Steps are counted from zero; a negative one indexes nothing in the simulation."""

    dropout = load_dropout_module()

    with pytest.raises(ValueError, match=r"^(step\ |step\ must\ be\ a\ non\-negative\ integer)"):
        dropout.apply_dropout(make_tracks(), 0.5, make_key(), -1, None)


@pytest.mark.parametrize("step", [True, 1.5])
def test_a_boolean_or_noninteger_step_is_refused(step: object) -> None:
    """A frame index is an integer count, never a truth value or fraction."""

    dropout = load_dropout_module()

    with pytest.raises(ValueError, match=r"^step must be a non-negative integer$"):
        dropout.apply_dropout(make_tracks(), 0.5, make_key(), step, None)  # type: ignore[arg-type]


def test_the_observed_rate_is_close_to_the_configured_probability() -> None:
    """The parameter has to mean what it says, not merely be deterministic.

    A channel that hid a tenth of what it was asked to would still pass every
    determinism test above while reporting a severity it never applied.
    """

    dropout = load_dropout_module()
    key = make_key()
    hidden = 0
    total = 0
    for step in range(200):
        result, _ = dropout.apply_dropout(make_tracks(20), 0.2, key, step, None)
        hidden += sum(1 for track in result if not track.visible)
        total += len(result)

    assert 0.18 < hidden / total < 0.22


def test_a_non_numeric_probability_is_refused() -> None:
    """A string from a config file would compare in ways nobody intended."""

    dropout = load_dropout_module()

    with pytest.raises(
        ValueError,
        match=r"^(probability\ must\ be\ a\ number|probability\ must\ lie\ in\ \[0,\ 1\],\ got\ )",
    ):
        dropout.apply_dropout(make_tracks(), "0.5", make_key(), 0, None)


def test_a_boolean_probability_is_refused() -> None:
    """`True` is a number in Python, and it would silently mean certain dropout."""

    dropout = load_dropout_module()

    with pytest.raises(
        ValueError,
        match=r"^(probability\ must\ be\ a\ number|probability\ must\ lie\ in\ \[0,\ 1\],\ got\ )",
    ):
        dropout.apply_dropout(make_tracks(), True, make_key(), 0, None)
