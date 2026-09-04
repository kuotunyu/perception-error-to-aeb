"""Contracts for the policy that turns geometry into a brake command.

This is the part of the study that must not move. The claim being made is that
the CONTROLLER was identical across every configuration and only the perception
changed, so every threshold here is read from a committed file and compared
strictly, and the transition table is pinned by a golden sequence rather than
described in prose.

Two asymmetries are deliberate and each has its own test. Warning waits for two
consecutive qualifying steps, because one frame of corrupted perception should
not put a vehicle into an intervention; the braking stages have no such wait,
because a real system that hesitated there would be worse than one that
occasionally brakes early. And returning to monitoring takes five clear steps
while entering takes one, because a threat that flickers is still a threat.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any, Optional

import pytest


def load_state_machine_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.aeb import state_machine
    except ImportError:
        pytest.fail("aebrisk.aeb.state_machine is missing", pytrace=False)
    return state_machine


def threat(
    ttc: Optional[float],
    decel: float,
    track_id: str = "t-0001",
) -> Any:
    from aebrisk.aeb.threat import ThreatAssessment

    return ThreatAssessment(
        track_id=track_id,
        ttc_s=ttc,
        required_deceleration_mps2=decel,
        predicted_overlap=ttc is not None,
        min_clearance_m=0.0 if ttc is not None else 5.0,
    )


def monitoring() -> Any:
    from aebrisk.aeb.state_machine import AEBMemory, AEBState

    return AEBMemory(
        state=AEBState.MONITOR,
        warning_qualifying_steps=0,
        release_clear_steps=0,
        previous_acceleration_mps2=0.0,
    )


# --------------------------------------------------------------------------
# The golden sequence
# --------------------------------------------------------------------------


GOLDEN = [
    # (ttc, required deceleration, expected state, expected target acceleration)
    (None, 0.0, "monitor", 0.0),
    (2.9, 1.0, "monitor", 0.0),  # first qualifying warning step; not yet entered
    (2.8, 1.0, "warning", 0.0),  # second consecutive step enters warning
    (2.6, 1.5, "warning", 0.0),
    (2.4, 2.5, "partial_brake", -3.0),  # ttc below 2.5 enters partial at once
    (2.0, 3.0, "partial_brake", -3.0),
    (1.4, 4.0, "full_brake", -6.0),  # ttc below 1.5 enters full at once
    (1.2, 6.0, "full_brake", -6.0),
    (2.0, 6.0, "full_brake", -6.0),  # required deceleration alone holds full
    (2.0, 3.0, "partial_brake", -3.0),  # demand drops, and so does the state
    (5.0, 0.5, "partial_brake", -3.0),  # first clear step
    (5.0, 0.5, "partial_brake", -3.0),
    (None, 0.0, "partial_brake", -3.0),
    (None, 0.0, "partial_brake", -3.0),
    (None, 0.0, "monitor", 0.0),  # fifth clear step releases
]


def test_the_golden_sequence_of_fifteen_steps() -> None:
    """The whole transition table in one readable list.

    A prose description of a state machine drifts from the code. This does not:
    every entry is a step, and a change to any threshold or counter breaks
    exactly the entries it should.
    """

    state_machine = load_state_machine_module()
    memory = monitoring()

    for index, (ttc, decel, state, acceleration) in enumerate(GOLDEN):
        threats = () if ttc is None and decel == 0.0 else (threat(ttc, decel),)
        memory, command = state_machine.update_aeb(memory, threats)
        assert command.state.value == state, f"step {index} state"
        assert command.target_acceleration_mps2 == pytest.approx(acceleration), (
            f"step {index} acceleration"
        )


# --------------------------------------------------------------------------
# Entering
# --------------------------------------------------------------------------


def test_no_threat_stays_in_monitoring() -> None:
    """An empty road is the base case, and it must command nothing."""

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(monitoring(), ())

    assert command.state is state_machine.AEBState.MONITOR
    assert command.target_acceleration_mps2 == 0.0
    assert command.selected_track_id is None


def test_warning_needs_two_consecutive_qualifying_steps() -> None:
    """One frame of corrupted perception must not start an intervention."""

    state_machine = load_state_machine_module()
    memory = monitoring()

    # Both steps sit in the warning band only: below 3.0 s but not below 2.5,
    # so nothing here can enter a braking stage on its own.
    memory, first = state_machine.update_aeb(memory, (threat(2.9, 1.0),))
    _, second = state_machine.update_aeb(memory, (threat(2.7, 1.0),))

    assert first.state is state_machine.AEBState.MONITOR
    assert second.state is state_machine.AEBState.WARNING


def test_the_warning_counter_resets_when_the_threat_stops_qualifying() -> None:
    """Two steps means two in a row, not two ever."""

    state_machine = load_state_machine_module()
    memory = monitoring()

    memory, _ = state_machine.update_aeb(memory, (threat(2.9, 1.0),))
    memory, _ = state_machine.update_aeb(memory, ())
    _, command = state_machine.update_aeb(memory, (threat(2.9, 1.0),))

    assert command.state is state_machine.AEBState.MONITOR


def test_partial_braking_is_entered_on_the_first_qualifying_step() -> None:
    """The braking stages do not wait; a system that hesitated here would be worse."""

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(monitoring(), (threat(2.4, 1.0),))

    assert command.state is state_machine.AEBState.PARTIAL
    assert command.target_acceleration_mps2 == pytest.approx(-3.0)


def test_full_braking_is_entered_on_the_first_qualifying_step() -> None:
    """The same, one stage up."""

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(monitoring(), (threat(1.4, 1.0),))

    assert command.state is state_machine.AEBState.FULL
    assert command.target_acceleration_mps2 == pytest.approx(-6.0)


def test_full_braking_can_be_demanded_by_the_deceleration_alone() -> None:
    """The stages fire on ANY configured condition, which is what makes them safe.

    An object four seconds away that would need 8 m/s^2 to avoid is already an
    emergency, however comfortable its time to collision looks.
    """

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(monitoring(), (threat(3.9, 8.0),))

    assert command.state is state_machine.AEBState.FULL


def test_a_threat_with_no_predicted_overlap_can_still_demand_braking() -> None:
    """Required deceleration is defined without an overlap, and it still counts."""

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(monitoring(), (threat(None, 8.0),))

    assert command.state is state_machine.AEBState.FULL


# --------------------------------------------------------------------------
# Boundaries
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ttc", "expected"),
    [(1.5, "partial_brake"), (1.4999, "full_brake")],
)
def test_the_full_braking_threshold_is_strict(ttc: float, expected: str) -> None:
    """Exactly 1.5 s does not qualify; the boundary belongs to one side only."""

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(monitoring(), (threat(ttc, 1.0),))

    assert command.state.value == expected


@pytest.mark.parametrize(
    ("decel", "expected"),
    [(5.0, "partial_brake"), (5.0001, "full_brake")],
)
def test_the_full_deceleration_threshold_is_strict(decel: float, expected: str) -> None:
    """Exactly 5.0 m/s^2 does not qualify either."""

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(monitoring(), (threat(2.4, decel),))

    assert command.state.value == expected


@pytest.mark.parametrize(
    ("ttc", "expected"),
    [(2.5, "monitor"), (2.4999, "partial_brake")],
)
def test_the_partial_braking_threshold_is_strict(ttc: float, expected: str) -> None:
    """Exactly 2.5 s is not partial braking. At 2.5 only the warning clock runs."""

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(monitoring(), (threat(ttc, 1.0),))

    assert command.state.value == expected


@pytest.mark.parametrize("ttc", [3.0, 3.5])
def test_a_time_to_collision_at_or_above_three_seconds_does_not_warn(ttc: float) -> None:
    """Exactly 3.0 s does not start the warning clock."""

    state_machine = load_state_machine_module()
    memory = monitoring()

    memory, _ = state_machine.update_aeb(memory, (threat(ttc, 1.0),))
    _, command = state_machine.update_aeb(memory, (threat(ttc, 1.0),))

    assert command.state is state_machine.AEBState.MONITOR


@pytest.mark.parametrize(
    ("decel", "expected"),
    [(2.0, "monitor"), (2.0001, "warning")],
)
def test_the_warning_deceleration_threshold_is_strict(decel: float, expected: str) -> None:
    """Exactly 2.0 m/s^2 does not start the warning clock."""

    state_machine = load_state_machine_module()
    memory = monitoring()

    memory, _ = state_machine.update_aeb(memory, (threat(3.5, decel),))
    _, command = state_machine.update_aeb(memory, (threat(3.5, decel),))

    assert command.state.value == expected


# --------------------------------------------------------------------------
# Releasing
# --------------------------------------------------------------------------


def test_releasing_takes_five_clear_steps() -> None:
    """A threat that flickers is still a threat, so leaving is slower than entering."""

    state_machine = load_state_machine_module()
    memory = monitoring()
    memory, _ = state_machine.update_aeb(memory, (threat(1.0, 8.0),))

    states = []
    for _ in range(5):
        memory, command = state_machine.update_aeb(memory, ())
        states.append(command.state.value)

    assert states == [
        "full_brake",
        "full_brake",
        "full_brake",
        "full_brake",
        "monitor",
    ]


def test_the_release_counter_resets_on_a_qualifying_threat() -> None:
    """Four clear steps and one threat is not five clear steps."""

    state_machine = load_state_machine_module()
    memory = monitoring()
    memory, _ = state_machine.update_aeb(memory, (threat(1.0, 8.0),))
    for _ in range(4):
        memory, _ = state_machine.update_aeb(memory, ())
    memory, _ = state_machine.update_aeb(memory, (threat(3.9, 1.0),))

    _, command = state_machine.update_aeb(memory, ())

    assert command.state is not state_machine.AEBState.MONITOR


@pytest.mark.parametrize(
    ("ttc", "releases"),
    [(4.0, True), (3.9, False)],
)
def test_the_release_threshold_is_strict(ttc: float, releases: bool) -> None:
    """A threat exactly 4.0 s away does not hold the intervention open."""

    state_machine = load_state_machine_module()
    memory = monitoring()
    memory, _ = state_machine.update_aeb(memory, (threat(1.0, 8.0),))
    for _ in range(5):
        memory, command = state_machine.update_aeb(memory, (threat(ttc, 0.5),))

    assert (command.state is state_machine.AEBState.MONITOR) is releases


def test_a_threat_that_demands_nothing_but_is_close_holds_the_state() -> None:
    """The hysteresis band between demanding and releasing, stated on its own."""

    state_machine = load_state_machine_module()
    memory = monitoring()
    memory, _ = state_machine.update_aeb(memory, (threat(1.0, 8.0),))

    for _ in range(20):
        memory, command = state_machine.update_aeb(memory, (threat(3.5, 1.0),))

    assert command.state is state_machine.AEBState.FULL


# --------------------------------------------------------------------------
# Selection and priority
# --------------------------------------------------------------------------


def test_the_command_names_the_track_it_is_braking_for() -> None:
    """A brake nobody can attribute to an object is not auditable."""

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(monitoring(), (threat(1.0, 8.0, track_id="t-0042"),))

    assert command.selected_track_id == "t-0042"


def test_the_most_urgent_of_several_threats_is_selected() -> None:
    """The AEB brakes for one object, and the policy reads that object's numbers."""

    state_machine = load_state_machine_module()

    _, command = state_machine.update_aeb(
        monitoring(),
        (threat(3.5, 1.0, track_id="t-calm"), threat(1.0, 8.0, track_id="t-urgent")),
    )

    assert command.state is state_machine.AEBState.FULL
    assert command.selected_track_id == "t-urgent"


def test_full_outranks_partial_which_outranks_warning() -> None:
    """The priority is a total order, tested as one."""

    state_machine = load_state_machine_module()
    both = (threat(2.4, 1.0, track_id="t-partial"), threat(1.0, 8.0, track_id="t-full"))

    _, command = state_machine.update_aeb(monitoring(), both)

    assert command.state is state_machine.AEBState.FULL


# --------------------------------------------------------------------------
# The memory
# --------------------------------------------------------------------------


def test_the_memory_is_frozen() -> None:
    """It is the controller's state, and the record of why it acted."""

    import dataclasses

    load_state_machine_module()
    memory = monitoring()

    with pytest.raises(dataclasses.FrozenInstanceError):
        memory.release_clear_steps = 3  # type: ignore[misc]


def test_the_input_memory_is_not_mutated() -> None:
    """A caller replaying a step must get the same answer twice."""

    state_machine = load_state_machine_module()
    memory = monitoring()

    first = state_machine.update_aeb(memory, (threat(1.0, 8.0),))[1]
    second = state_machine.update_aeb(memory, (threat(1.0, 8.0),))[1]

    assert first == second
    assert memory.state is state_machine.AEBState.MONITOR


def test_the_applied_acceleration_is_left_for_the_closed_loop_to_record() -> None:
    """This function is the policy, not the actuator.

    It cannot know what was actually applied, because the limiter and the
    nominal controller both run after it. Writing a value here would make the
    jerk limit read from a number that was never commanded.
    """

    state_machine = load_state_machine_module()
    memory = monitoring()

    updated, _ = state_machine.update_aeb(memory, (threat(1.0, 8.0),))

    assert updated.previous_acceleration_mps2 == memory.previous_acceleration_mps2


def test_the_counters_are_carried_in_the_memory() -> None:
    """State that lived in a module global would leak between scenarios."""

    state_machine = load_state_machine_module()

    updated, _ = state_machine.update_aeb(monitoring(), (threat(2.9, 1.0),))

    assert updated.warning_qualifying_steps == 1


# --------------------------------------------------------------------------
# The step duration
# --------------------------------------------------------------------------


def test_a_finer_step_needs_proportionally_more_qualifying_steps() -> None:
    """The policy's counters are durations expressed at 10 Hz.

    Reading them as raw step counts would silently halve the warning delay at
    20 Hz, which changes when every intervention starts without changing any
    number in the committed policy.
    """

    state_machine = load_state_machine_module()
    memory = monitoring()

    states = []
    for _ in range(4):
        memory, command = state_machine.update_aeb(memory, (threat(2.9, 1.0),), dt_s=0.05)
        states.append(command.state.value)

    assert states == ["monitor", "monitor", "monitor", "warning"]


@pytest.mark.parametrize("bad_value", [0.0, -0.1, float("nan")])
def test_an_impossible_step_duration_is_refused(bad_value: float) -> None:
    """A zero step makes every counter infinite."""

    state_machine = load_state_machine_module()

    with pytest.raises(ValueError, match=r"^dt_s must "):
        state_machine.update_aeb(monitoring(), (), dt_s=bad_value)


@pytest.mark.parametrize("bad_value", ["0.1", True, None])
def test_a_step_duration_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean a one second step."""

    state_machine = load_state_machine_module()

    with pytest.raises(ValueError, match=r"^dt_s\ must\ be\ a\ number"):
        state_machine.update_aeb(monitoring(), (), dt_s=bad_value)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The committed policy
# --------------------------------------------------------------------------


def test_the_policy_is_read_from_the_committed_file() -> None:
    """Thresholds in code could be changed without changing any artifact."""

    state_machine = load_state_machine_module()

    policy = state_machine.load_policy()

    assert policy["warning"]["ttc_lt_s"] == 3.0
    assert policy["partial"]["target_accel_mps2"] == -3.0
    assert policy["full"]["target_accel_mps2"] == -6.0
    assert policy["release"]["consecutive_steps"] == 5


def test_the_policy_names_its_schema_version() -> None:
    """A result must say which policy shape produced it."""

    state_machine = load_state_machine_module()

    assert state_machine.load_policy()["schema_version"] == "aeb-policy/v1"


def test_a_braking_target_that_is_not_a_deceleration_is_refused() -> None:
    """A positive target would make the AEB accelerate into the threat."""

    state_machine = load_state_machine_module()
    policy = state_machine.load_policy()
    policy["full"]["target_accel_mps2"] = 1.0

    with pytest.raises(ValueError, match="target_accel_mps2"):
        state_machine.validate_policy(policy)


def test_a_policy_whose_stages_are_out_of_order_is_refused() -> None:
    """Full braking must be the most urgent stage, or the priority is a lie."""

    state_machine = load_state_machine_module()
    policy = state_machine.load_policy()
    policy["full"]["ttc_lt_s"] = 4.0

    with pytest.raises(
        ValueError, match=r"^stage\ ttc_lt_s\ thresholds\ must\ decrease\ with\ urgency,\ got\ "
    ):
        state_machine.validate_policy(policy)


def test_a_policy_missing_a_stage_is_refused() -> None:
    """A missing stage would silently never fire."""

    state_machine = load_state_machine_module()
    policy = state_machine.load_policy()
    del policy["partial"]

    with pytest.raises(ValueError, match="partial"):
        state_machine.validate_policy(policy)


def test_a_non_positive_counter_is_refused() -> None:
    """Zero consecutive steps would enter the state before it qualified."""

    state_machine = load_state_machine_module()
    policy = state_machine.load_policy()
    policy["warning"]["consecutive_steps"] = 0

    with pytest.raises(ValueError, match="consecutive_steps"):
        state_machine.validate_policy(policy)


def test_a_policy_missing_its_release_rule_is_refused() -> None:
    """Without it an intervention could never end, and every run would time out braking."""

    state_machine = load_state_machine_module()
    policy = state_machine.load_policy()
    del policy["release"]

    with pytest.raises(ValueError, match=r"^policy\ is\ missing\ the\ 'release'\ stage"):
        state_machine.validate_policy(policy)


def test_the_committed_policy_passes_its_own_validator() -> None:
    """The file that every run reads must satisfy every rule the code enforces."""

    state_machine = load_state_machine_module()

    state_machine.validate_policy(state_machine.load_policy())
