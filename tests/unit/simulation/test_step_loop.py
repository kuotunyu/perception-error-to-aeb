"""Contracts for the one loop every configuration is driven through.

Until now this loop existed only inside `tests/integration/test_synthetic_simulation.py`,
written by hand. A simulation the study rests on that lives in a test file has no
production twin, and the synthetic scenario was therefore evidence about itself
rather than about the code a real scenario would run. Moving it here makes the
synthetic test a check on the shipped loop.

Four things must hold for the experiment to mean anything, and each is a test
below rather than a comment.

- **The nominal controller never sees the observation.** Oracle and corrupted
  runs of the same scenario must produce the same nominal request at every step;
  only the AEB's command may differ. Otherwise a perception error changes the
  driving as well as the braking and nothing can be attributed.
- **The command trace comes back.** Missed and false interventions are found by
  comparing one run's braking against the oracle run's, so a loop that returns
  only a result throws away half of what the study measures.
- **The error channels are bound once per run.** Dropout and fragmentation carry
  state between steps; rebuilding them per step would redraw every choice and no
  track would ever stay lost for its configured delay.
- **A collision stops the run.** Integrating through a body would let an ego that
  has already hit a pedestrian go on to record a comfortable stop.
"""

from __future__ import annotations

import math
from types import ModuleType
from typing import Any, Optional

import numpy as np
import pytest

from aebrisk.aeb.state_machine import AEBState
from aebrisk.observation.models import TrackState
from aebrisk.simulation.common_cohort import ExperimentConfiguration

DT_S = 0.1
EGO_SIZE = (4.0, 2.0)
TOKEN = "loop-0001"
PROTOCOL_HASH = "c" * 64
FIRST_TIMESTAMP_US = 1_600_000_000_000_000
CHANNELS = ("dropout", "localization_shape", "latency", "track_instability")


def load_step_loop_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        import aebrisk.simulation.step_loop as step_loop
    except ImportError:  # pragma: no cover - names the absence during RED
        pytest.fail("aebrisk.simulation.step_loop is missing", pytrace=False)
    return step_loop


def configuration(
    identifier: str = "oracle_aeb",
    *,
    aeb: bool = True,
    mode: str = "oracle",
    severity: str = "zero",
) -> ExperimentConfiguration:
    return ExperimentConfiguration(
        configuration_id=identifier,
        aeb_enabled=aeb,
        observation_mode=mode,
        severity_by_channel=dict.fromkeys(CHANNELS, severity),
        replicate_count=1,
    )


def lead_at(distance_m: float, category: str = "vehicle") -> Any:
    """A stationary body straight ahead of an ego that starts at the origin."""

    def tracks(step: int, timestamp_us: int) -> tuple[TrackState, ...]:
        return (
            TrackState(
                track_id="lead-1",
                category=category,
                center_xy_m=(distance_m, 0.0),
                yaw_rad=0.0,
                size_lw_m=EGO_SIZE,
                velocity_xy_mps=(0.0, 0.0),
                visible=True,
                source_timestamp_us=timestamp_us,
                covariance_xy=(0.0, 0.0, 0.0, 0.0),
            ),
        )

    return tracks


def empty_road(step: int, timestamp_us: int) -> tuple[TrackState, ...]:
    return ()


def stamped(tracks: Any, dt_s: float = DT_S) -> Any:
    """A world source on the log-like clock: one frame per step, stamped by the source."""

    def frame_at_step(step: int) -> tuple[int, Any]:
        timestamp_us = FIRST_TIMESTAMP_US + step * round(dt_s * 1_000_000)
        return timestamp_us, tracks(step, timestamp_us)

    return frame_at_step


def run(
    module: ModuleType,
    tracks: Any = empty_road,
    *,
    config: Optional[ExperimentConfiguration] = None,
    steps: int = 90,
    initial_speed_mps: float = 10.0,
    replicate: int = 0,
    dt_s: float = DT_S,
    map_speed_limit_mps: Optional[float] = None,
    source: Optional[Any] = None,
) -> Any:
    return module.run_steps(
        token=TOKEN,
        route_xy=np.array([[0.0, 0.0], [400.0, 0.0]], dtype=np.float64),
        frame_at_step=stamped(tracks, dt_s) if source is None else source,
        steps=steps,
        initial_speed_mps=initial_speed_mps,
        ego_size_lw_m=EGO_SIZE,
        configuration=config if config is not None else configuration(),
        replicate=replicate,
        protocol_hash=PROTOCOL_HASH,
        dt_s=dt_s,
        map_speed_limit_mps=map_speed_limit_mps,
    )


def test_the_loop_returns_the_command_trace_beside_the_measurements() -> None:
    """Missed and false interventions are found in the trace, not in the result."""

    module = load_step_loop_module()

    outcome = run(module, lead_at(60.0))

    assert len(outcome.commands) == len(outcome.states)
    assert outcome.states[0] is AEBState.MONITOR
    assert AEBState.PARTIAL in outcome.states


def test_an_ego_with_no_threat_holds_its_speed_and_never_brakes() -> None:
    """The control condition: nothing to see, nothing to do."""

    module = load_step_loop_module()

    outcome = run(module)

    assert set(outcome.states) == {AEBState.MONITOR}
    assert outcome.collisions == {"vru": 0, "vehicle": 0, "object": 0}
    assert outcome.max_deceleration_mps2 == pytest.approx(0.0, abs=1e-9)
    assert outcome.distance_travelled_m == pytest.approx(10.0 * 90 * DT_S, rel=1e-6)


def test_one_stopped_control_step_reports_every_public_numeric_output() -> None:
    """A one-step run is valid and its zero-valued measurements remain explicit."""

    module = load_step_loop_module()

    outcome = run(
        module,
        config=configuration("no_aeb", aeb=False),
        steps=1,
        initial_speed_mps=0.0,
    )

    assert outcome.states == (AEBState.MONITOR,)
    assert outcome.nominal_accelerations_mps2 == (0.0,)
    assert outcome.collision_energy_j == 0.0
    assert outcome.min_clearance_m == 0.0
    assert outcome.max_deceleration_mps2 == 0.0
    assert outcome.max_abs_jerk_mps3 == 0.0
    assert outcome.intervention_duration_s == 0.0
    assert outcome.distance_travelled_m == 0.0
    assert outcome.final_speed_mps == 0.0
    assert outcome.stop_distance_m == 0.0
    assert outcome.ran_out_of_route is False


def test_nondefault_dt_and_map_limit_drive_hand_calculated_control_outputs() -> None:
    """Three 0.2 s steps apply -1,-2,-3 m/s2 toward a 5 m/s map limit."""

    module = load_step_loop_module()

    outcome = run(
        module,
        config=configuration("no_aeb", aeb=False),
        steps=3,
        initial_speed_mps=10.0,
        dt_s=0.2,
        map_speed_limit_mps=5.0,
    )

    assert outcome.states == (AEBState.MONITOR,) * 3
    assert outcome.nominal_accelerations_mps2 == pytest.approx((-5.0, -4.8, -4.4))
    assert outcome.final_speed_mps == pytest.approx(8.8)
    assert outcome.distance_travelled_m == pytest.approx(5.6)
    assert outcome.max_deceleration_mps2 == pytest.approx(3.0)
    assert outcome.max_abs_jerk_mps3 == pytest.approx(5.0)
    assert outcome.intervention_duration_s == 0.0
    assert outcome.stop_distance_m is None


def test_the_aeb_keeps_the_ego_off_a_stationary_lead() -> None:
    """The same scenario the no-AEB run below drives into, survived.

    The ego is NOT asserted to end at rest, and that is the state machine
    working rather than a weaker test. Once the ego has slowed enough that no
    threat qualifies for five consecutive steps, the AEB releases by design, and
    the perception-blind nominal controller then does what it always does and
    accelerates back toward its target. A run asserted to end stopped would be
    asserting that the release rule does not exist.
    """

    module = load_step_loop_module()

    outcome = run(module, lead_at(60.0))

    assert outcome.collisions == {"vru": 0, "vehicle": 0, "object": 0}
    assert outcome.min_clearance_m > 0.0
    # Partial braking, -3.0 m/s2, was enough: the closest this run came was 1.7 s
    # of time to collision, and full braking needs under 1.5 s. Asserting full
    # braking here would be asserting that the staged policy has only one stage.
    assert outcome.max_deceleration_mps2 == pytest.approx(3.0)
    assert AEBState.PARTIAL in outcome.states
    assert AEBState.FULL not in outcome.states


def test_without_the_aeb_the_same_scenario_collides() -> None:
    """If the no-AEB baseline also stopped, the AEB would be measuring nothing."""

    module = load_step_loop_module()

    outcome = run(module, lead_at(60.0), config=configuration("no_aeb", aeb=False))

    assert outcome.collisions["vehicle"] == 1
    assert outcome.collision_energy_j == pytest.approx(75_000.0)
    assert outcome.final_speed_mps == pytest.approx(10.0)


def test_a_collision_ends_the_run_rather_than_integrating_through_the_body() -> None:
    """An ego that drove on through a body would record a comfortable stop after it."""

    module = load_step_loop_module()

    outcome = run(module, lead_at(60.0), config=configuration("no_aeb", aeb=False))

    assert len(outcome.states) < 90
    assert outcome.min_clearance_m == 0.0


def test_a_vulnerable_road_user_is_counted_in_its_own_column() -> None:
    """One collision total cannot answer a question about pedestrians.

    `pedestrian` and `bicycle` are the observation categories; `vru` is the
    column they are counted in. Keeping the two vocabularies apart is what stops
    a cyclist being filed as scenery.
    """

    module = load_step_loop_module()

    outcome = run(
        module,
        lead_at(60.0, category="pedestrian"),
        config=configuration("no_aeb", aeb=False),
    )

    assert outcome.collisions == {"vru": 1, "vehicle": 0, "object": 0}


def test_the_nominal_request_is_identical_whatever_the_observation_shows() -> None:
    """The study's control: only the AEB may differ between configurations.

    Driven with the AEB disabled so nothing overrides the nominal command, once
    on an empty road and once with a body straight ahead. A nominal controller
    that could see would slow for the second and the two traces would diverge.
    """

    module = load_step_loop_module()

    blind = run(module, empty_road, config=configuration("no_aeb", aeb=False), steps=30)
    seeing = run(module, lead_at(200.0), config=configuration("no_aeb", aeb=False), steps=30)

    assert blind.nominal_accelerations_mps2 == seeing.nominal_accelerations_mps2


def test_the_error_channels_are_bound_once_for_the_whole_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebuilt per step, no track would ever stay lost for its configured delay."""

    module = load_step_loop_module()
    built: list[int] = []
    original = module.BIND_CHANNELS

    def counting(*arguments: Any, **keywords: Any) -> Any:
        built.append(1)
        return original(*arguments, **keywords)

    monkeypatch.setattr(module, "BIND_CHANNELS", counting)
    run(module, lead_at(60.0), config=configuration("corrupted", mode="corrupted"), steps=20)

    assert built == [1]


def test_the_oracle_mode_never_reaches_the_error_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oracle run that passed through the channels would not be the reference."""

    module = load_step_loop_module()
    calls: list[int] = []
    original = module.APPLY_ERRORS

    def counting(*arguments: Any, **keywords: Any) -> Any:
        calls.append(1)
        return original(*arguments, **keywords)

    monkeypatch.setattr(module, "APPLY_ERRORS", counting)
    run(module, lead_at(60.0), steps=10)

    assert calls == []


def test_two_runs_of_one_configuration_and_replicate_are_identical() -> None:
    """A result that changed between identical runs could not be compared with anything."""

    module = load_step_loop_module()
    corrupted = configuration("corrupted", mode="corrupted", severity="medium")

    first = run(module, lead_at(60.0), config=corrupted, steps=40)
    second = run(module, lead_at(60.0), config=corrupted, steps=40)

    assert first.states == second.states
    assert first.distance_travelled_m == pytest.approx(second.distance_travelled_m)


def test_a_different_replicate_of_a_stochastic_configuration_differs() -> None:
    """Replicates that all agreed would report a spread the study never measured."""

    module = load_step_loop_module()
    corrupted = configuration("corrupted", mode="corrupted", severity="high")

    first = run(module, lead_at(45.0), config=corrupted, steps=60, replicate=0)
    second = run(module, lead_at(45.0), config=corrupted, steps=60, replicate=1)

    assert (first.states, first.distance_travelled_m) != (
        second.states,
        second.distance_travelled_m,
    )


def test_the_ego_follows_the_route_rather_than_a_straight_line() -> None:
    """A corner driven straight would put the ego through the scenery."""

    module = load_step_loop_module()

    outcome = module.run_steps(
        token=TOKEN,
        route_xy=np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 90.0]], dtype=np.float64),
        frame_at_step=stamped(empty_road),
        steps=30,
        initial_speed_mps=10.0,
        ego_size_lw_m=EGO_SIZE,
        configuration=configuration("no_aeb", aeb=False),
        replicate=0,
        protocol_hash=PROTOCOL_HASH,
        dt_s=DT_S,
        map_speed_limit_mps=None,
    )

    # 30 m of travel: 10 east, then 20 north.
    assert outcome.final_pose_xy_m == pytest.approx((10.0, 20.0), abs=1e-6)


def test_running_out_of_route_ends_the_run_rather_than_inventing_road() -> None:
    """Extrapolating past the recording would place agents beside an ego that is nowhere."""

    module = load_step_loop_module()

    outcome = module.run_steps(
        token=TOKEN,
        route_xy=np.array([[0.0, 0.0], [12.0, 0.0]], dtype=np.float64),
        frame_at_step=stamped(empty_road),
        steps=90,
        initial_speed_mps=10.0,
        ego_size_lw_m=EGO_SIZE,
        configuration=configuration("no_aeb", aeb=False),
        replicate=0,
        protocol_hash=PROTOCOL_HASH,
        dt_s=DT_S,
        map_speed_limit_mps=None,
    )

    assert outcome.ran_out_of_route is True
    assert len(outcome.states) < 90


def test_the_jerk_and_deceleration_the_run_reached_are_reported() -> None:
    """Comfort is one of the outcomes; a run that did not record it cannot report it."""

    module = load_step_loop_module()

    outcome = run(module, lead_at(60.0))

    assert outcome.max_deceleration_mps2 > 0.0
    assert outcome.max_abs_jerk_mps3 == pytest.approx(5.0)
    assert math.isfinite(outcome.max_abs_jerk_mps3)
    braking_steps = sum(state in (AEBState.PARTIAL, AEBState.FULL) for state in outcome.states)
    assert outcome.intervention_duration_s == pytest.approx(braking_steps * DT_S)


def test_the_minimum_time_to_collision_seen_is_reported() -> None:
    """A run with a threat that never records its closest approach hides the near miss."""

    module = load_step_loop_module()

    outcome = run(module, lead_at(60.0))

    assert outcome.min_ttc_s is not None
    assert outcome.min_ttc_s > 0.0
    assert run(module).min_ttc_s is None


def test_an_unknown_category_is_refused_rather_than_counted_as_scenery() -> None:
    """A new devkit category filed under `object` would understate the VRU column."""

    module = load_step_loop_module()

    with pytest.raises(ValueError, match=r"^no collision column for category "):
        module._category_column("tram")


@pytest.mark.parametrize("bad_steps", [0, -1])
def test_a_run_of_no_steps_is_refused(bad_steps: int) -> None:
    """A cell of the matrix that simulated nothing would report a comfortable stop."""

    module = load_step_loop_module()

    with pytest.raises(ValueError, match=r"^steps must be at least one"):
        run(module, steps=bad_steps)


@pytest.mark.parametrize("bad_dt", [0.0, -0.1, math.nan, math.inf])
def test_an_impossible_step_length_is_refused(bad_dt: float) -> None:
    """Every duration reported is a step count times dt, so a bad dt scales them all."""

    module = load_step_loop_module()

    with pytest.raises(ValueError, match=r"^dt_s must be finite and positive"):
        run(module, dt_s=bad_dt)


def test_the_world_source_owns_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frame the loop stamped itself would carry a clock its tracks do not share."""

    module = load_step_loop_module()
    seen: list[int] = []
    original = module.APPLY_ERRORS

    def capturing(history: Any, index: int, *arguments: Any, **keywords: Any) -> Any:
        seen.append(history[index].timestamp_us)
        return original(history, index, *arguments, **keywords)

    monkeypatch.setattr(module, "APPLY_ERRORS", capturing)
    stamps = [7_000_000 + step * 99_986 for step in range(5)]

    run(
        module,
        config=configuration("corrupted", mode="corrupted"),
        steps=5,
        source=lambda step: (stamps[step], ()),
    )

    assert seen == stamps


def test_a_world_source_that_goes_backwards_is_refused() -> None:
    """A history assembled out of order decides every latency selection by that order."""

    module = load_step_loop_module()
    stamps = [7_000_000, 7_100_000, 7_050_000]

    with pytest.raises(ValueError, match=r"^the world source went backwards in time at step 2"):
        run(module, steps=3, source=lambda step: (stamps[step], ()))


def bodies(*centres: tuple[float, float]) -> Any:
    """Several stationary vehicles, wherever they are put."""

    def tracks(step: int, timestamp_us: int) -> tuple[TrackState, ...]:
        return tuple(
            TrackState(
                track_id=f"body-{index}",
                category="vehicle",
                center_xy_m=centre,
                yaw_rad=0.0,
                size_lw_m=EGO_SIZE,
                velocity_xy_mps=(0.0, 0.0),
                visible=True,
                source_timestamp_us=timestamp_us,
                covariance_xy=(0.0, 0.0, 0.0, 0.0),
            )
            for index, centre in enumerate(centres)
        )

    return tracks


def test_a_body_that_cannot_be_the_closest_does_not_change_the_clearance() -> None:
    """The reported clearance stays exact; a far body only loses its geometry.

    A real urban frame carries over two hundred tracked objects and two of them
    matter. Measuring every one of them exactly cost 90 percent of a simulated
    scenario's time, so a body whose arithmetic bound already exceeds the closest
    approach seen so far is skipped — which cannot change the minimum, because
    the bound is never above the true clearance.
    """

    module = load_step_loop_module()

    alone = run(module, bodies((60.0, 0.0)), steps=60)
    crowded = run(module, bodies((60.0, 0.0), (200.0, 80.0)), steps=60)

    assert crowded.min_clearance_m == alone.min_clearance_m
    assert crowded.collisions == alone.collisions
    assert crowded.states == alone.states


# --------------------------------------------------------------------------
# A contact is not the same thing as a collision the ego caused
# --------------------------------------------------------------------------


def approaching_from_behind(speed_mps: float = 6.0, start_m: float = -12.0) -> Any:
    """A body overtaking the ego along its own heading, as a log replay does."""

    def frame_at_step(step: int) -> tuple[int, tuple[TrackState, ...]]:
        timestamp_us = FIRST_TIMESTAMP_US + step * round(DT_S * 1_000_000)
        return timestamp_us, (
            TrackState(
                track_id="follower",
                category="vehicle",
                center_xy_m=(start_m + speed_mps * step * DT_S, 0.0),
                yaw_rad=0.0,
                size_lw_m=EGO_SIZE,
                velocity_xy_mps=(speed_mps, 0.0),
                visible=True,
                source_timestamp_us=timestamp_us,
                covariance_xy=(0.0, 0.0, 0.0, 0.0),
            ),
        )

    return frame_at_step


def test_a_stopped_ego_struck_from_behind_is_not_a_collision_it_caused() -> None:
    """This inverted the study's headline before it was measured.

    The agents replay the recording and never react, so an ego that brakes for a
    pedestrian is then driven into by the vehicle that was following it. The
    first smoke over real nuPlan scenarios, on 2026-09-06, put `oracle_aeb` at
    twelve collisions against `no_aeb`'s three, every one of them at an ego speed
    of 0.00 m/s with the striking body five metres behind at 6.2 m/s.
    """

    module = load_step_loop_module()

    outcome = run(
        module,
        steps=40,
        initial_speed_mps=0.0,
        config=configuration("no_aeb", aeb=False),
        source=approaching_from_behind(),
    )

    assert outcome.collisions == {"vru": 0, "vehicle": 0, "object": 0}
    assert outcome.contacts_not_at_fault == 1
    assert outcome.collision_energy_j == 0.0


def test_a_contact_the_ego_did_not_cause_does_not_end_the_run() -> None:
    """Ending it would give the braking configurations less exposure than the rest.

    That is the same bias one level down: a configuration that brakes gets
    rear-ended sooner, is truncated sooner, and meets fewer of the hazards it
    would have been measured on.
    """

    module = load_step_loop_module()

    outcome = run(
        module,
        steps=40,
        initial_speed_mps=0.0,
        config=configuration("no_aeb", aeb=False),
        source=approaching_from_behind(),
    )

    assert len(outcome.states) == 40


def test_one_body_is_counted_once_however_long_it_overlaps() -> None:
    """A rear-ended ego stays overlapped for as long as the recording drives through it."""

    module = load_step_loop_module()

    outcome = run(
        module,
        steps=90,
        initial_speed_mps=0.0,
        config=configuration("no_aeb", aeb=False),
        source=approaching_from_behind(speed_mps=1.0, start_m=-8.0),
    )

    assert outcome.contacts_not_at_fault == 1


def test_an_ego_that_drove_into_a_body_is_at_fault() -> None:
    """The measurement the study exists to make must survive the exclusion above."""

    module = load_step_loop_module()

    outcome = run(module, lead_at(30.0), steps=60, config=configuration("no_aeb", aeb=False))

    assert outcome.collisions["vehicle"] == 1
    assert outcome.contacts_not_at_fault == 0
    assert outcome.collision_energy_j > 0.0


def test_a_moving_ego_is_at_fault_for_what_is_in_front_of_it() -> None:
    """Only a body BEHIND the ego and closing is the follower's responsibility."""

    module = load_step_loop_module()
    ahead = TrackState(
        track_id="lead",
        category="vehicle",
        center_xy_m=(20.0, 0.0),
        yaw_rad=0.0,
        size_lw_m=EGO_SIZE,
        velocity_xy_mps=(30.0, 0.0),
        visible=True,
        source_timestamp_us=FIRST_TIMESTAMP_US,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )

    assert module.ego_at_fault((0.0, 0.0), 0.0, 10.0, ahead) is True


def test_a_body_behind_but_slower_than_the_ego_is_the_ego_s_fault() -> None:
    """Reversing into something is not being rear-ended by it."""

    module = load_step_loop_module()
    behind = TrackState(
        track_id="parked",
        category="vehicle",
        center_xy_m=(-6.0, 0.0),
        yaw_rad=0.0,
        size_lw_m=EGO_SIZE,
        velocity_xy_mps=(0.0, 0.0),
        visible=True,
        source_timestamp_us=FIRST_TIMESTAMP_US,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )

    assert module.ego_at_fault((0.0, 0.0), 0.0, 10.0, behind) is True


@pytest.mark.parametrize(
    ("pose", "yaw", "ego_speed", "centre", "velocity", "expected"),
    [
        ((5.0, 7.0), math.pi / 2.0, 0.01, (5.0, 8.0), (0.0, 0.0), False),
        ((5.0, 7.0), math.pi / 2.0, 4.9, (25.0, 6.0), (3.0, 4.0), False),
        ((5.0, 7.0), math.pi / 2.0, 4.9, (-15.0, 6.0), (4.0, 3.0), False),
        ((5.0, 7.0), math.pi / 2.0, 5.0, (25.0, 6.0), (3.0, 4.0), True),
        ((5.0, 7.0), 0.0, 4.9, (5.0, 20.0), (3.0, 4.0), True),
        ((5.0, 7.0), 0.0, 4.9, (5.5, -13.0), (3.0, 4.0), True),
        ((5.0, 7.0), math.pi / 4.0, 4.9, (7.0, 8.0), (3.0, 4.0), True),
        ((5.0, 7.0), math.pi / 4.0, 4.9, (3.0, 6.0), (3.0, 4.0), False),
    ],
)
def test_fault_rule_boundaries_in_translated_rotated_coordinates(
    pose: tuple[float, float],
    yaw: float,
    ego_speed: float,
    centre: tuple[float, float],
    velocity: tuple[float, float],
    expected: bool,
) -> None:
    """Pin the frozen stopped and behind-plus-faster rule on its accepted boundaries."""

    module = load_step_loop_module()
    track = TrackState(
        track_id="boundary",
        category="vehicle",
        center_xy_m=centre,
        yaw_rad=0.0,
        size_lw_m=EGO_SIZE,
        velocity_xy_mps=velocity,
        visible=True,
        source_timestamp_us=FIRST_TIMESTAMP_US,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )

    assert module.ego_at_fault(pose, yaw, ego_speed, track) is expected
