"""What policy v2 changes in the controller's decisions, against policy v1.

Policy v2 (`docs/studies/aeb-policy-v2/analysis-plan.md`, section 4.2) keeps
every threshold, stage and release rule of policy v1 and changes one thing:
the AEB selects only among the visible bodies the collision-course gate admits.
These tests mirror the policy v1 known-limitation tests
(`tests/unit/aeb/test_policy_v1_known_limitations.py`), with the same ego
(4.8 x 2.0 m), the same car (4.5 x 1.9 m) and the same speeds, and invert the
expectation where the gate is meant to change the outcome.

Two kinds of test are used. The masking cases feed one assessed frame to the
real state machine for a few steps, as the policy v1 tests do, so that their
stage sequences can be compared entry by entry; the step loop's first step,
where the ego is exactly where the frame puts it, is checked to issue the same
command. The cases that say "never" or "exactly" drive the step loop itself
over a whole run, with the configuration naming the policy.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence

import numpy as np

from aebrisk.aeb.state_machine import (
    AEBCommand,
    AEBMemory,
    AEBState,
    policy_for,
    update_aeb,
)
from aebrisk.aeb.threat import EgoKinematicState, assess_threat, collision_course_candidates
from aebrisk.errors.pipeline import ERROR_CHANNELS
from aebrisk.observation.models import TrackState
from aebrisk.simulation.common_cohort import ExperimentConfiguration
from aebrisk.simulation.step_loop import StepLoopOutcome, run_steps

EGO_SIZE_LW_M = (4.8, 2.0)
CAR_SIZE_LW_M = (4.5, 1.9)
DT_S = 0.1
FIRST_TIMESTAMP_US = 1_600_000_000_000_000

#: A body as a world source places it: identifier, centre at the first step,
#: and the constant velocity it moves at in the world.
Body = tuple[str, tuple[float, float], tuple[float, float]]


def ego(speed_mps: float) -> EgoKinematicState:
    return EgoKinematicState(
        center_xy_m=(0.0, 0.0),
        yaw_rad=0.0,
        size_lw_m=EGO_SIZE_LW_M,
        speed_mps=speed_mps,
        velocity_xy_mps=(speed_mps, 0.0),
        acceleration_mps2=0.0,
    )


def car(
    track_id: str,
    center_xy_m: tuple[float, float],
    velocity_xy_mps: tuple[float, float] = (0.0, 0.0),
    timestamp_us: int = FIRST_TIMESTAMP_US,
) -> TrackState:
    return TrackState(
        track_id=track_id,
        category="vehicle",
        center_xy_m=center_xy_m,
        yaw_rad=0.0,
        size_lw_m=CAR_SIZE_LW_M,
        velocity_xy_mps=velocity_xy_mps,
        visible=True,
        source_timestamp_us=timestamp_us,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )


def monitoring() -> AEBMemory:
    return AEBMemory(
        state=AEBState.MONITOR,
        warning_qualifying_steps=0,
        release_clear_steps=0,
        previous_acceleration_mps2=0.0,
    )


def commands(
    ego_state: EgoKinematicState, tracks: Sequence[TrackState], steps: int, version: str
) -> list[AEBCommand]:
    """Feed one frame, gated by the named policy, to the real state machine for ``steps`` steps.

    The frame is assessed, gated and decided on as the step loop does it: every
    visible track is assessed, the policy's target selection picks the
    candidates, and the state machine reads the same policy.
    """

    policy = policy_for(version)
    visible = tuple(track for track in tracks if track.visible)
    threats = tuple(assess_threat(ego_state, track) for track in visible)
    candidates = collision_course_candidates(
        ego_state, visible, threats, policy.get("target_selection")
    )
    memory = monitoring()
    issued = []
    for _ in range(steps):
        memory, command = update_aeb(memory, candidates, policy=policy)
        issued.append(command)
    return issued


def world(*bodies: Body) -> Callable[[int], tuple[int, tuple[TrackState, ...]]]:
    """A world source whose bodies move at their own constant velocity."""

    def frame_at_step(step: int) -> tuple[int, tuple[TrackState, ...]]:
        timestamp_us = FIRST_TIMESTAMP_US + step * round(DT_S * 1_000_000)
        elapsed_s = step * DT_S
        return timestamp_us, tuple(
            car(
                track_id,
                (start[0] + velocity[0] * elapsed_s, start[1] + velocity[1] * elapsed_s),
                velocity,
                timestamp_us,
            )
            for track_id, start, velocity in bodies
        )

    return frame_at_step


def drive(
    source: Callable[[int], tuple[int, tuple[TrackState, ...]]],
    speed_mps: float,
    steps: int,
    version: str,
) -> StepLoopOutcome:
    """Drive the step loop along a straight road, with the AEB on and the named policy."""

    return run_steps(
        token="policy-v2-0001",
        route_xy=np.array([[0.0, 0.0], [400.0, 0.0]], dtype=np.float64),
        frame_at_step=source,
        steps=steps,
        initial_speed_mps=speed_mps,
        ego_size_lw_m=EGO_SIZE_LW_M,
        configuration=ExperimentConfiguration(
            configuration_id="oracle_aeb",
            aeb_enabled=True,
            observation_mode="oracle",
            severity_by_channel=dict.fromkeys(ERROR_CHANNELS, "zero"),
            replicate_count=1,
            aeb_policy=version,
        ),
        replicate=0,
        protocol_hash="c" * 64,
        dt_s=DT_S,
        map_speed_limit_mps=None,
    )


# --------------------------------------------------------------------------
# Braking policy v1 did for bodies off the collision course
# --------------------------------------------------------------------------


def test_v2_never_brakes_for_a_parked_car_beside_the_path() -> None:
    """Policy v1 brakes fully for this car at once; policy v2 drives past it.

    The car is 3.5 m to the side and never moves, so no overlap is ever
    predicted, and once the ego is past it the car is behind as well.
    """

    source = world(("parked-beside", (20.0, 3.5), (0.0, 0.0)))

    v1 = drive(source, 15.0, 30, "v1")
    v2 = drive(source, 15.0, 30, "v2")

    assert v1.commands[0] == AEBCommand(AEBState.FULL, -6.0, "parked-beside")
    assert set(v2.states) == {AEBState.MONITOR}
    assert {
        (command.target_acceleration_mps2, command.selected_track_id) for command in v2.commands
    } == {(0.0, None)}
    assert v2.intervention_duration_s == 0.0
    assert v2.collisions == {"vru": 0, "vehicle": 0, "object": 0}
    # Policy v1 spent most of the run braking for the car; policy v2 drives past it.
    assert v1.intervention_duration_s > 2.0
    assert v2.final_pose_xy_m[0] > 20.0


def test_v2_never_brakes_for_a_slower_car_behind() -> None:
    """Policy v1 brakes fully for a car 7 m behind at 10 m/s; policy v2 never does."""

    source = world(("behind", (-7.0, 0.0), (10.0, 0.0)))

    v1 = drive(source, 15.0, 30, "v1")
    v2 = drive(source, 15.0, 30, "v2")

    assert v1.commands[0] == AEBCommand(AEBState.FULL, -6.0, "behind")
    assert set(v2.states) == {AEBState.MONITOR}
    assert {
        (command.target_acceleration_mps2, command.selected_track_id) for command in v2.commands
    } == {(0.0, None)}
    assert v2.intervention_duration_s == 0.0


# --------------------------------------------------------------------------
# Masking
# --------------------------------------------------------------------------


def test_v2_removes_the_masking_of_an_in_path_lead() -> None:
    """The parked car that outranked the stopped lead under v1 is no longer a candidate.

    Policy v1 selects the parked car, whose required deceleration is larger, and
    only warns. Policy v2 drops it before selection and brakes for the lead, as
    both policies do when the lead is alone.
    """

    lead = car("in-path-lead", (25.0, 0.0))
    parked = car("parked-beside", (18.0, 4.0))

    v1 = commands(ego(10.0), (lead, parked), 2, "v1")
    v2 = commands(ego(10.0), (lead, parked), 2, "v2")

    assert [command.state for command in v1] == [AEBState.MONITOR, AEBState.WARNING]
    assert v1[-1].selected_track_id == "parked-beside"
    assert [command.state for command in v2] == [AEBState.PARTIAL, AEBState.PARTIAL]
    assert [command.target_acceleration_mps2 for command in v2] == [-3.0, -3.0]
    assert {command.selected_track_id for command in v2} == {"in-path-lead"}
    assert v2 == commands(ego(10.0), (lead,), 2, "v1")

    source = world(
        ("in-path-lead", (25.0, 0.0), (0.0, 0.0)), ("parked-beside", (18.0, 4.0), (0.0, 0.0))
    )
    assert drive(source, 10.0, 1, "v1").commands == (v1[0],)
    assert drive(source, 10.0, 1, "v2").commands == (v2[0],)


def test_v2_removes_masking_below_the_warning_threshold() -> None:
    """Where policy v1 stayed in monitor throughout, policy v2 brakes for the lead.

    The parked car's required deceleration is above the lead's but not above the
    warning threshold, so policy v1 selected it and demanded no stage at all.
    """

    lead = car("in-path-lead", (13.0, 0.0))
    parked = car("parked-beside", (11.5, 4.0))

    v1 = commands(ego(5.0), (lead, parked), 4, "v1")
    v2 = commands(ego(5.0), (lead, parked), 4, "v2")

    assert [command.state for command in v1] == [AEBState.MONITOR] * 4
    assert [command.target_acceleration_mps2 for command in v1] == [0.0] * 4
    assert [command.state for command in v2] == [AEBState.PARTIAL] * 4
    assert [command.target_acceleration_mps2 for command in v2] == [-3.0] * 4
    assert {command.selected_track_id for command in v2} == {"in-path-lead"}

    source = world(
        ("in-path-lead", (13.0, 0.0), (0.0, 0.0)), ("parked-beside", (11.5, 4.0), (0.0, 0.0))
    )
    assert drive(source, 5.0, 1, "v1").commands == (v1[0],)
    assert drive(source, 5.0, 1, "v2").commands == (v2[0],)


# --------------------------------------------------------------------------
# What policy v2 keeps
# --------------------------------------------------------------------------


def test_v2_brakes_for_a_stationary_lead_exactly_as_v1() -> None:
    """With one body in the path, both policies brake, release and move the ego identically.

    The lead is dropped by the gate only on steps where the ego has slowed so
    much that no overlap is predicted within the rollout's horizon. On those
    steps policy v1 demands nothing of the lead either, and the AEB is waiting
    out its release under both policies; the one difference is that policy
    v2's command names no body there, where policy v1's names the lead.
    """

    source = world(("in-path-lead", (60.0, 0.0), (0.0, 0.0)))

    v1 = drive(source, 10.0, 90, "v1")
    v2 = drive(source, 10.0, 90, "v2")

    def braking(outcome: StepLoopOutcome) -> list[tuple[AEBState, float]]:
        return [(command.state, command.target_acceleration_mps2) for command in outcome.commands]

    assert AEBState.PARTIAL in v2.states
    assert braking(v2) == braking(v1)
    assert dataclasses.replace(v2, commands=v1.commands) == v1
    named = {
        (first.selected_track_id, second.selected_track_id)
        for first, second in zip(v1.commands, v2.commands)
        if first != second
    }
    assert named == {("in-path-lead", None)}
