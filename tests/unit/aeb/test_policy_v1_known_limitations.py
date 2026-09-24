"""Characterization of AEB policy v1 target selection, as released.

These tests pin behaviour the frozen study ran with; they do not endorse it.
Policy v1 selects the ONE track with the highest required deceleration among
every visible track, and required deceleration is ``v^2 / (2 d)`` on the
absolute longitudinal gap along the ego's heading. Nothing in that selection
asks whether the body is in the ego's path or ahead of it, and the full and
warning stages fire on required deceleration alone, with or without a predicted
overlap.

The evaluation harness surfaced three consequences while the released braking
numbers were being explained. Each is pinned here with the real threat and
state-machine modules so that the public description in
``docs/simulation-contract.md`` ("Known controller and modelling choices")
cannot drift from the code:

- a stationary vehicle beside the path demands full braking;
- a slower vehicle behind the ego demands full braking;
- an off-path body can be selected in place of an in-path threat, turning a
  partial brake into a warning, or into no stage at all when the off-path
  body's required deceleration does not exceed the warning threshold; neither
  makes a braking demand (masking).

Changing any of this is a new policy version, not a fix to this one: every
released number was produced by the behaviour below.
"""

from __future__ import annotations

import pytest

from aebrisk.aeb.state_machine import AEBCommand, AEBMemory, AEBState, load_policy, update_aeb
from aebrisk.aeb.threat import (
    EgoKinematicState,
    assess_threat,
    select_highest_required_deceleration,
)
from aebrisk.observation.models import TrackState

#: A mid-size ego and a mid-size parked car. Only the lengths enter the
#: longitudinal gap; the widths only decide whether the rollout overlaps.
EGO_SIZE_LW_M = (4.8, 2.0)
CAR_SIZE_LW_M = (4.5, 1.9)


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
) -> TrackState:
    return TrackState(
        track_id=track_id,
        category="vehicle",
        center_xy_m=center_xy_m,
        yaw_rad=0.0,
        size_lw_m=CAR_SIZE_LW_M,
        velocity_xy_mps=velocity_xy_mps,
        visible=True,
        source_timestamp_us=1_600_000_000_000_000,
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
    ego_state: EgoKinematicState, tracks: tuple[TrackState, ...], steps: int
) -> list[AEBCommand]:
    """Feed the same assessed frame to the real state machine for ``steps`` steps."""

    threats = tuple(assess_threat(ego_state, track) for track in tracks)
    memory = monitoring()
    issued = []
    for _ in range(steps):
        memory, command = update_aeb(memory, threats)
        issued.append(command)
    return issued


def test_a_parked_car_beside_the_path_demands_full_braking() -> None:
    """Phantom braking: no predicted overlap, yet the deceleration alone is above 5."""

    parked = car("parked-beside", (20.0, 3.5))
    threat = assess_threat(ego(15.0), parked)

    assert threat.predicted_overlap is False
    assert threat.ttc_s is None
    # 15^2 / (2 * (20 - 4.8/2 - 4.5/2)): the gap ignores the 3.5 m offset.
    assert threat.required_deceleration_mps2 == pytest.approx(225.0 / 30.7)

    [command] = commands(ego(15.0), (parked,), steps=1)

    assert command.state is AEBState.FULL
    assert command.target_acceleration_mps2 == -6.0
    assert command.selected_track_id == "parked-beside"


def test_a_slower_car_behind_the_ego_demands_full_braking() -> None:
    """The gap is an absolute value, so a body behind the ego closes like one ahead."""

    follower = car("behind", (-7.0, 0.0), velocity_xy_mps=(10.0, 0.0))
    threat = assess_threat(ego(15.0), follower)

    assert threat.predicted_overlap is False
    assert threat.ttc_s is None
    # (15 - 10)^2 / (2 * (7 - 4.8/2 - 4.5/2)).
    assert threat.required_deceleration_mps2 == pytest.approx(25.0 / 4.7)

    [command] = commands(ego(15.0), (follower,), steps=1)

    assert command.state is AEBState.FULL
    assert command.target_acceleration_mps2 == -6.0
    assert command.selected_track_id == "behind"


def test_an_off_path_body_masks_an_in_path_threat() -> None:
    """Selection by required deceleration lets a parked car outrank a stopped lead."""

    lead = car("in-path-lead", (25.0, 0.0))
    parked = car("parked-beside", (18.0, 4.0))

    alone = commands(ego(10.0), (lead,), steps=2)
    assert [command.state for command in alone] == [AEBState.PARTIAL, AEBState.PARTIAL]
    assert [command.target_acceleration_mps2 for command in alone] == [-3.0, -3.0]

    lead_threat = assess_threat(ego(10.0), lead)
    parked_threat = assess_threat(ego(10.0), parked)
    assert lead_threat.predicted_overlap is True
    assert lead_threat.ttc_s == pytest.approx(2.0)
    assert parked_threat.predicted_overlap is False
    assert parked_threat.required_deceleration_mps2 > lead_threat.required_deceleration_mps2
    assert select_highest_required_deceleration((lead_threat, parked_threat)) == parked_threat

    masked = commands(ego(10.0), (lead, parked), steps=2)
    assert [command.state for command in masked] == [AEBState.MONITOR, AEBState.WARNING]
    assert [command.target_acceleration_mps2 for command in masked] == [0.0, 0.0]
    assert masked[-1].selected_track_id == "parked-beside"


def test_a_masking_body_below_the_warning_threshold_leaves_no_stage() -> None:
    """Masking need not end in a warning: the AEB can stay in monitor throughout."""

    lead = car("in-path-lead", (13.0, 0.0))
    parked = car("parked-beside", (11.5, 4.0))

    alone = commands(ego(5.0), (lead,), steps=4)
    assert [command.state for command in alone] == [AEBState.PARTIAL] * 4
    assert [command.target_acceleration_mps2 for command in alone] == [-3.0] * 4

    lead_threat = assess_threat(ego(5.0), lead)
    parked_threat = assess_threat(ego(5.0), parked)
    assert lead_threat.predicted_overlap is True
    assert parked_threat.predicted_overlap is False
    assert parked_threat.ttc_s is None
    # 5^2 / (2 * (11.5 - 4.8/2 - 4.5/2)), above the lead's but not above the
    # warning stage's required-deceleration threshold.
    assert parked_threat.required_deceleration_mps2 == pytest.approx(25.0 / 13.7)
    assert parked_threat.required_deceleration_mps2 > lead_threat.required_deceleration_mps2
    warning_threshold = load_policy()["warning"]["required_decel_gt_mps2"]
    assert parked_threat.required_deceleration_mps2 <= warning_threshold
    assert select_highest_required_deceleration((lead_threat, parked_threat)) == parked_threat

    masked = commands(ego(5.0), (lead, parked), steps=4)
    assert [command.state for command in masked] == [AEBState.MONITOR] * 4
    assert [command.target_acceleration_mps2 for command in masked] == [0.0] * 4
