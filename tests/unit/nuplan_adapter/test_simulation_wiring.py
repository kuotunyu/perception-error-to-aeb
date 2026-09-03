"""Contracts for how the devkit simulation is wired, checked without a database.

Three wiring choices decide whether this study measures what it claims to, and
each is the kind of setting that is wrong silently because the simulation still
runs and still produces numbers.

AGENTS ARE NON-REACTIVE. They replay what the log recorded. A reactive agent
policy would respond to the ego's braking, so a perception error would change
the OTHER vehicles' behaviour as well as the ego's, and the attribution could
not separate the two causal paths. The cost is that the scenarios are slightly
unrealistic; the benefit is that the experiment has one independent variable.

THE EGO IS CLOSED LOOP. If it replayed its own log the AEB would command a brake
that never happened, and every configuration would produce the same trajectory.

THE EXPERT'S BRAKING IS NOT AVAILABLE. The logged driver already avoided most of
these collisions. A controller with access to that braking would inherit an
avoidance the perception pipeline never earned.
"""

from __future__ import annotations

from types import ModuleType

import pytest


def load_wiring_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.nuplan_adapter import simulation
    except ImportError:
        pytest.fail("aebrisk.nuplan_adapter.simulation is missing", pytrace=False)
    return simulation


def load_planner_module() -> ModuleType:
    try:
        from aebrisk.nuplan_adapter import planner
    except ImportError:
        pytest.fail("aebrisk.nuplan_adapter.planner is missing", pytrace=False)
    return planner


# --------------------------------------------------------------------------
# The simulation wiring
# --------------------------------------------------------------------------


def test_the_default_wiring_replays_agents_rather_than_reacting() -> None:
    """The study's one independent variable depends on it."""

    simulation = load_wiring_module()

    wiring = simulation.build_simulation_wiring()

    assert wiring.agent_policy == simulation.NON_REACTIVE_AGENTS


def test_the_default_wiring_controls_the_ego() -> None:
    """A replayed ego would give every configuration the same trajectory."""

    simulation = load_wiring_module()

    wiring = simulation.build_simulation_wiring()

    assert wiring.ego_control == simulation.CLOSED_LOOP_EGO


def test_the_default_wiring_runs_at_the_protocol_rate() -> None:
    """10 Hz is what every latency and counter in this study is expressed in."""

    simulation = load_wiring_module()

    assert simulation.build_simulation_wiring().frequency_hz == 10.0


def test_expert_longitudinal_braking_is_off_by_default() -> None:
    """The logged driver already avoided most of these collisions."""

    simulation = load_wiring_module()

    assert simulation.build_simulation_wiring().expert_longitudinal_braking is False


def test_a_reactive_agent_policy_is_refused() -> None:
    """It would make perception error change the other vehicles too."""

    simulation = load_wiring_module()

    with pytest.raises(ValueError, match="agent_policy"):
        simulation.build_simulation_wiring(agent_policy="idm_agents")


def test_an_open_loop_ego_is_refused() -> None:
    """The AEB would command a brake that never happened."""

    simulation = load_wiring_module()

    with pytest.raises(ValueError, match="ego_control"):
        simulation.build_simulation_wiring(ego_control="log_playback")


def test_expert_braking_cannot_be_switched_on() -> None:
    """Not a discouraged option: an avoidance the perception never earned."""

    simulation = load_wiring_module()

    with pytest.raises(ValueError, match="expert_longitudinal_braking"):
        simulation.build_simulation_wiring(expert_longitudinal_braking=True)


def test_another_rate_is_refused() -> None:
    """Every counter in the committed policy is a duration at 10 Hz."""

    simulation = load_wiring_module()

    with pytest.raises(ValueError, match="frequency_hz"):
        simulation.build_simulation_wiring(frequency_hz=20.0)


def test_the_wiring_is_frozen() -> None:
    """It is the evidence for what the simulation actually did."""

    import dataclasses

    simulation = load_wiring_module()
    wiring = simulation.build_simulation_wiring()

    with pytest.raises(dataclasses.FrozenInstanceError):
        wiring.expert_longitudinal_braking = True  # type: ignore[misc]


def test_the_wiring_has_a_stable_identity() -> None:
    """A run record cites it, so it must be nameable and not change silently."""

    simulation = load_wiring_module()

    assert simulation.build_simulation_wiring().wiring_id == simulation.WIRING_ID


def test_nothing_here_imports_the_devkit() -> None:
    """This module must answer before a database exists, and before the order gate opens.

    Importing the devkit at module scope would also pull in its own heavy
    dependencies, which this project deliberately does not install.
    """

    simulation = load_wiring_module()

    reachable = [
        getattr(value, "__module__", "") or getattr(value, "__name__", "")
        for value in vars(simulation).values()
    ]
    assert not [name for name in reachable if isinstance(name, str) and name.startswith("nuplan")]


# --------------------------------------------------------------------------
# The planner identity
# --------------------------------------------------------------------------


def test_the_planner_has_a_stable_identity() -> None:
    """Every run record names it, and two runs naming the same one must agree."""

    planner = load_planner_module()

    assert planner.PLANNER_ID
    assert planner.planner_identity() == planner.PLANNER_ID


def test_the_planner_declares_that_it_reads_no_sensor_data() -> None:
    """The scope of this study, stated where the devkit would be configured."""

    planner = load_planner_module()

    assert planner.REQUIRED_SENSOR_CHANNELS == ()


def test_the_planner_declares_the_observation_it_consumes() -> None:
    """World state only: tracks and the ego, never images or points."""

    planner = load_planner_module()

    assert planner.OBSERVATION_KIND == "world_state"


def test_a_planner_configured_for_sensors_is_refused() -> None:
    """The one way this study could quietly become a different one."""

    planner = load_planner_module()

    with pytest.raises(ValueError, match="sensor"):
        planner.validate_planner_scope(("CAM_F0",))


def test_no_sensor_channels_passes_the_scope_check() -> None:
    """The pair to the test above, so the check cannot always raise."""

    planner = load_planner_module()

    planner.validate_planner_scope(())
