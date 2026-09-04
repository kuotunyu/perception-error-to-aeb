"""Contracts for turning a brake trace into events, and comparing two traces.

A missed intervention and a false one are the study's two headline failures, and
both are defined by COMPARISON: the same scenario, the same controller, once
with perfect observation and once with corrupted observation. Neither can be
read off a single run.

Three definitions carry the weight, and each is a judgement rather than a fact.

AN INTERVENTION IS A CONTIGUOUS RUN OF BRAKING. Warning is not part of it: the
vehicle does not decelerate in warning, so counting it would make the corrupted
runs look like they intervened when nothing happened to the passengers.

A LATE INTERVENTION IS A MISS. Braking 0.4 s after the oracle did is not the
same protective action arriving slightly later; at 15 m/s it is six metres of
closing distance that were not braked away. The threshold is +0.30 s, and only
lateness counts — an intervention that came EARLY is not a failure.

AN UNMATCHED CORRUPTED INTERVENTION IS FALSE. It braked for something the oracle
never braked for, which is the cost side of the trade the study is measuring.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest


def load_events_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.metrics import events
    except ImportError:
        pytest.fail("aebrisk.metrics.events is missing", pytrace=False)
    return events


def command(state_name: str) -> Any:
    from aebrisk.aeb.state_machine import AEBCommand, AEBState

    state = AEBState(state_name)
    targets = {"partial_brake": -3.0, "full_brake": -6.0}
    return AEBCommand(
        state=state,
        target_acceleration_mps2=targets.get(state_name, 0.0),
        selected_track_id="t-0001" if state_name != "monitor" else None,
    )


def trace(*state_names: str) -> tuple:
    return tuple(command(name) for name in state_names)


def event(onset: float, offset: float, state_name: str = "full_brake") -> Any:
    from aebrisk.aeb.state_machine import AEBState
    from aebrisk.metrics.events import InterventionEvent

    return InterventionEvent(onset_s=onset, offset_s=offset, max_state=AEBState(state_name))


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def test_a_run_with_no_braking_has_no_interventions() -> None:
    """An empty road produces no events, not one event of length zero."""

    events = load_events_module()

    assert events.extract_interventions(trace("monitor", "monitor")) == ()


def test_a_contiguous_run_of_braking_is_one_event() -> None:
    """Steps are what the simulation produces; events are what a reader counts."""

    events = load_events_module()

    extracted = events.extract_interventions(
        trace("monitor", "partial_brake", "partial_brake", "partial_brake", "monitor")
    )

    assert len(extracted) == 1
    assert extracted[0].onset_s == pytest.approx(0.1)
    assert extracted[0].offset_s == pytest.approx(0.4)


def test_the_offset_is_when_braking_stopped() -> None:
    """One step past the last braking step, so the duration is the number of steps."""

    events = load_events_module()

    extracted = events.extract_interventions(trace("full_brake"))

    assert extracted[0].onset_s == 0.0
    assert extracted[0].offset_s == pytest.approx(0.1)


def test_two_runs_separated_by_monitoring_are_two_events() -> None:
    """A release and a re-application are two interventions, not one long one."""

    events = load_events_module()

    extracted = events.extract_interventions(
        trace("partial_brake", "monitor", "monitor", "full_brake")
    )

    assert len(extracted) == 2


def test_warning_is_not_an_intervention() -> None:
    """The vehicle does not decelerate in warning.

    Counting it would make a corrupted run look like it intervened when
    nothing happened to the passengers.
    """

    events = load_events_module()

    assert events.extract_interventions(trace("warning", "warning")) == ()


def test_warning_does_not_split_a_braking_run() -> None:
    """It also does not join one: the two braking runs here are separate events."""

    events = load_events_module()

    extracted = events.extract_interventions(trace("partial_brake", "warning", "partial_brake"))

    assert len(extracted) == 2


def test_an_event_records_the_most_severe_state_it_reached() -> None:
    """Partial and full braking are different interventions to a passenger."""

    events = load_events_module()

    extracted = events.extract_interventions(trace("partial_brake", "full_brake", "partial_brake"))

    assert extracted[0].max_state.value == "full_brake"


def test_an_event_that_only_ever_partly_braked_says_so() -> None:
    """The pair to the test above, so the field cannot be constant."""

    events = load_events_module()

    extracted = events.extract_interventions(trace("partial_brake", "partial_brake"))

    assert extracted[0].max_state.value == "partial_brake"


def test_an_event_that_runs_to_the_end_of_the_trace_is_closed() -> None:
    """A scenario can end mid-brake, and the event still has a duration."""

    events = load_events_module()

    extracted = events.extract_interventions(trace("monitor", "full_brake", "full_brake"))

    assert len(extracted) == 1
    assert extracted[0].offset_s == pytest.approx(0.3)


def test_the_step_duration_scales_the_times() -> None:
    """Events are reported in seconds, not in steps."""

    events = load_events_module()

    extracted = events.extract_interventions(trace("monitor", "full_brake"), dt_s=0.05)

    assert extracted[0].onset_s == pytest.approx(0.05)


@pytest.mark.parametrize("bad_value", [0.0, -0.1, float("nan")])
def test_an_impossible_step_duration_is_refused(bad_value: float) -> None:
    """A zero step would place every event at time zero."""

    events = load_events_module()

    with pytest.raises(ValueError, match=r"^dt_s must "):
        events.extract_interventions(trace("full_brake"), dt_s=bad_value)


def test_an_empty_trace_has_no_events() -> None:
    """The degenerate case, which an index-based scan gets wrong first."""

    events = load_events_module()

    assert events.extract_interventions(()) == ()


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def test_two_identical_traces_produce_no_failures() -> None:
    """The base case: perfect perception matches itself."""

    events = load_events_module()
    both = (event(1.0, 2.0),)

    summary = events.match_interventions(both, both)

    assert summary.missed == 0
    assert summary.false == 0
    assert summary.matched_delays_s == (0.0,)


def test_an_oracle_intervention_with_no_counterpart_is_missed() -> None:
    """The failure the study exists to measure."""

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 2.0),), ())

    assert summary.missed == 1
    assert summary.false == 0


def test_a_corrupted_intervention_with_no_counterpart_is_false() -> None:
    """The cost side of the same trade."""

    events = load_events_module()

    summary = events.match_interventions((), (event(1.0, 2.0),))

    assert summary.false == 1
    assert summary.missed == 0


def test_a_late_intervention_is_missed_as_well_as_matched() -> None:
    """Braking 0.4 s late is not the same protective action arriving slightly later.

    At 15 m/s it is six metres of closing distance that were not braked away.
    The delay is still recorded, because it is a matched pair and the size of
    the lateness is itself a result.
    """

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 2.0),), (event(1.4, 2.4),))

    assert summary.missed == 1
    assert summary.matched_delays_s == pytest.approx((0.4,))
    assert summary.false == 0


def test_a_slightly_late_intervention_is_not_missed() -> None:
    """The other side of the +0.30 s boundary."""

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 2.0),), (event(1.2, 2.2),))

    assert summary.missed == 0
    assert summary.matched_delays_s == pytest.approx((0.2,))


def test_the_lateness_threshold_is_inclusive() -> None:
    """Exactly +0.30 s is not late. The boundary belongs to one side only."""

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 2.0),), (event(1.3, 2.3),))

    assert summary.missed == 0


def test_an_early_intervention_is_not_missed() -> None:
    """Only lateness is a failure; braking sooner than the oracle is not."""

    events = load_events_module()

    summary = events.match_interventions((event(2.0, 3.0),), (event(1.0, 2.0),))

    assert summary.missed == 0
    assert summary.matched_delays_s == pytest.approx((-1.0,))


def test_an_intervention_outside_the_tolerance_is_both_missed_and_false() -> None:
    """Too far apart to be the same action, so it is one of each, not one delayed pair."""

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 2.0),), (event(5.0, 6.0),))

    assert summary.missed == 1
    assert summary.false == 1
    assert summary.matched_delays_s == ()


def test_the_tolerance_boundary_is_inclusive() -> None:
    """Exactly 1.0 s apart still matches."""

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 2.0),), (event(2.0, 3.0),))

    assert summary.missed == 1  # late by 1.0 s, which exceeds +0.30
    assert summary.false == 0
    assert summary.matched_delays_s == pytest.approx((1.0,))


def test_matching_is_one_to_one() -> None:
    """Two corrupted events near one oracle event cannot both be its match.

    The unmatched one is a false intervention, which is exactly what a
    controller that brakes twice for one hazard has done.
    """

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 2.0),), (event(1.05, 1.5), event(1.1, 1.6)))

    assert summary.false == 1
    assert len(summary.matched_delays_s) == 1


def test_the_nearest_counterpart_is_chosen() -> None:
    """Greedy on the smallest absolute delay, so the pairing is not order-dependent."""

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 2.0),), (event(1.5, 2.5), event(1.05, 1.6)))

    assert summary.matched_delays_s == pytest.approx((0.05,))


def test_a_tie_is_broken_by_position() -> None:
    """Two counterparts equally far away must resolve the same way every run.

    Ties happen: the simulation is on a 0.1 s grid, so equal distances either
    side are common rather than exotic.
    """

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 2.0),), (event(0.9, 1.5), event(1.1, 1.7)))

    assert summary.matched_delays_s == pytest.approx((-0.1,))


def test_matching_is_independent_of_input_order() -> None:
    """The corrupted events arrive in trace order, which is not a contract."""

    events = load_events_module()
    oracle = (event(1.0, 2.0), event(5.0, 6.0))
    forwards = (event(1.1, 2.1), event(5.2, 6.2))

    first = events.match_interventions(oracle, forwards)
    second = events.match_interventions(oracle, tuple(reversed(forwards)))

    assert first == second


def test_several_pairs_are_matched_independently() -> None:
    """A realistic trace has more than one intervention."""

    events = load_events_module()
    oracle = (event(1.0, 2.0), event(5.0, 6.0), event(9.0, 10.0))
    corrupted = (event(1.1, 2.1), event(9.5, 10.5))

    summary = events.match_interventions(oracle, corrupted)

    assert summary.missed == 2  # the 5.0 s event is absent, the 9.0 s one is late
    assert summary.false == 0
    assert len(summary.matched_delays_s) == 2


def test_two_empty_traces_produce_an_empty_summary() -> None:
    """No interventions on either side is not a failure of any kind."""

    events = load_events_module()

    summary = events.match_interventions((), ())

    assert (summary.missed, summary.false, summary.matched_delays_s) == (0, 0, ())


def test_the_delays_are_reported_in_onset_order() -> None:
    """A tuple whose order depended on the matching would not be comparable."""

    events = load_events_module()
    oracle = (event(1.0, 2.0), event(5.0, 6.0))
    corrupted = (event(5.1, 6.1), event(1.2, 2.2))

    summary = events.match_interventions(oracle, corrupted)

    assert summary.matched_delays_s == pytest.approx((0.2, 0.1))


@pytest.mark.parametrize("bad_value", [-0.1, float("nan")])
def test_an_impossible_tolerance_is_refused(bad_value: float) -> None:
    """A negative tolerance would match nothing and report everything twice."""

    events = load_events_module()

    with pytest.raises(
        ValueError, match=r"^tolerance_s must (be a number|be finite and non-negative, got )"
    ):
        events.match_interventions((), (), tolerance_s=bad_value)


@pytest.mark.parametrize("bad_value", [-0.1, float("nan")])
def test_an_impossible_lateness_threshold_is_refused(bad_value: float) -> None:
    """A negative threshold would make every matched pair a miss."""

    events = load_events_module()

    with pytest.raises(ValueError, match=r"^missed_delay_s must "):
        events.match_interventions((), (), missed_delay_s=bad_value)


def test_a_lateness_threshold_beyond_the_tolerance_is_refused() -> None:
    """It could never fire: nothing that far apart is matched in the first place."""

    events = load_events_module()

    with pytest.raises(ValueError, match=r"^missed_delay_s 1\.0 exceeds tolerance_s 0\.5;"):
        events.match_interventions((), (), tolerance_s=0.5, missed_delay_s=1.0)


def test_the_event_is_frozen() -> None:
    """It is the record of one intervention."""

    import dataclasses

    load_events_module()
    one = event(1.0, 2.0)

    with pytest.raises(dataclasses.FrozenInstanceError):
        one.onset_s = 0.0  # type: ignore[misc]


def test_an_event_that_ends_before_it_starts_is_refused() -> None:
    """Time running backwards inside one event means the extraction was wrong."""

    events = load_events_module()

    with pytest.raises(ValueError, match=r"^offset_s\ "):
        events.InterventionEvent(
            onset_s=2.0,
            offset_s=1.0,
            max_state=events.AEBState.FULL,
        )


def test_an_event_that_never_braked_is_refused() -> None:
    """A monitoring or warning state is not an intervention, by definition."""

    events = load_events_module()

    with pytest.raises(ValueError, match=r"^max_state\ must\ be\ one\ of\ "):
        events.InterventionEvent(
            onset_s=1.0,
            offset_s=2.0,
            max_state=events.AEBState.WARNING,
        )


def test_a_run_that_starts_at_full_braking_and_eases_stays_full() -> None:
    """The severity is the worst reached, not the last seen.

    Easing from full to partial does not undo a full-braking intervention as
    far as a passenger or a stopping distance is concerned.
    """

    events = load_events_module()

    extracted = events.extract_interventions(trace("full_brake", "partial_brake"))

    assert len(extracted) == 1
    assert extracted[0].max_state.value == "full_brake"


@pytest.mark.parametrize("bad_value", [-1.0, float("nan")])
def test_an_event_at_an_impossible_time_is_refused(bad_value: float) -> None:
    """A negative onset means the extraction indexed backwards."""

    events = load_events_module()

    with pytest.raises(
        ValueError, match=r"^(offset_s\ |onset_s\ must\ be\ finite\ and\ non\-negative)"
    ):
        events.InterventionEvent(onset_s=bad_value, offset_s=5.0, max_state=events.AEBState.FULL)


@pytest.mark.parametrize("bad_value", ["0.1", True, None])
def test_a_step_duration_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean a one second step."""

    events = load_events_module()

    with pytest.raises(ValueError, match=r"^dt_s\ must\ be\ a\ number"):
        events.extract_interventions(trace("full_brake"), dt_s=bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["tolerance_s", "missed_delay_s"])
@pytest.mark.parametrize("bad_value", ["1.0", True, None])
def test_a_matching_threshold_that_is_not_a_number_is_refused(
    field: str,
    bad_value: object,
) -> None:
    """Both thresholds decide what counts as a failure, so both are checked."""

    events = load_events_module()

    with pytest.raises(ValueError, match=rf"^{field} must be a number$"):
        events.match_interventions((), (), **{field: bad_value})


def test_one_corrupted_event_cannot_match_two_oracle_events() -> None:
    """The one-to-one rule seen from the other side.

    Two hazards braked for once is one avoided intervention and one missed
    one, not two matches.
    """

    events = load_events_module()

    summary = events.match_interventions((event(1.0, 1.4), event(1.2, 1.6)), (event(1.05, 1.5),))

    assert summary.missed == 1
    assert summary.false == 0
    assert len(summary.matched_delays_s) == 1
