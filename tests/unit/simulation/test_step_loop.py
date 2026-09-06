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
CHANNELS = ("dropout", "localization_shape", "latency", "track_instability")


def load_step_loop_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.simulation import step_loop
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


def run(
    module: ModuleType,
    tracks: Any = empty_road,
    *,
    config: Optional[ExperimentConfiguration] = None,
    steps: int = 90,
    initial_speed_mps: float = 10.0,
    replicate: int = 0,
    dt_s: float = DT_S,
) -> Any:
    return module.run_steps(
        token=TOKEN,
        route_xy=np.array([[0.0, 0.0], [400.0, 0.0]], dtype=np.float64),
        tracks_at_step=tracks,
        steps=steps,
        first_timestamp_us=1_600_000_000_000_000,
        initial_speed_mps=initial_speed_mps,
        ego_size_lw_m=EGO_SIZE,
        configuration=config if config is not None else configuration(),
        replicate=replicate,
        protocol_hash=PROTOCOL_HASH,
        dt_s=dt_s,
        map_speed_limit_mps=None,
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
    assert outcome.collision_energy_j > 0.0


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
        tracks_at_step=empty_road,
        steps=30,
        first_timestamp_us=1_600_000_000_000_000,
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
        tracks_at_step=empty_road,
        steps=90,
        first_timestamp_us=1_600_000_000_000_000,
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
    assert outcome.max_abs_jerk_mps3 > 0.0
    assert math.isfinite(outcome.max_abs_jerk_mps3)


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
