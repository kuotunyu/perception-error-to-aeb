"""How the devkit simulation is wired, decided here rather than in a config file.

Three settings decide whether this study measures what it claims to, and each is
the kind of thing that is wrong silently, because the simulation still runs and
still produces numbers. So none of them is an option: the wrong value raises.

AGENTS ARE NON-REACTIVE. They replay what the log recorded. A reactive agent
policy would respond to the ego's braking, so a perception error would change
the OTHER vehicles' behaviour as well as the ego's, and the attribution could
not separate the two causal paths. The cost is that the scenarios are slightly
unrealistic; the benefit is that the experiment has one independent variable.

THE EGO IS CLOSED LOOP. A replayed ego would give every configuration the same
trajectory, and the AEB would command brakes that never happened.

THE EXPERT'S BRAKING IS NOT AVAILABLE. The logged driver already avoided most of
these collisions; a controller with access to that braking would inherit an
avoidance the perception pipeline never earned, and every configuration would
look competent.

Nothing here imports the devkit. This module must answer before a database
exists and before the portfolio order gate opens, and importing the devkit at
module scope would pull in dependencies this project deliberately does not have.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The devkit's playback observation: agents do exactly what the log recorded.
NON_REACTIVE_AGENTS = "log_playback_agents"

#: The ego is driven by this project's controller, not by its own log.
CLOSED_LOOP_EGO = "closed_loop_controlled"

#: Cited in every run record alongside the planner identity.
WIRING_ID = "aebrisk-simulation-wiring/v1"

PROTOCOL_FREQUENCY_HZ = 10.0
#: How long every scenario runs, in seconds. Fixed by the protocol so that no
#: configuration can change how long it is measured over; a contract test ties
#: this to the protocol file, whose bytes are the published hash.
PROTOCOL_SCENARIO_DURATION_S = 15.0


@dataclass(frozen=True)
class SimulationWiring:
    """The settings a run record must be able to prove it used."""

    wiring_id: str
    agent_policy: str
    ego_control: str
    frequency_hz: float
    expert_longitudinal_braking: bool


def build_simulation_wiring(
    agent_policy: str = NON_REACTIVE_AGENTS,
    ego_control: str = CLOSED_LOOP_EGO,
    frequency_hz: float = PROTOCOL_FREQUENCY_HZ,
    expert_longitudinal_braking: bool = False,
) -> SimulationWiring:
    """Build the wiring, refusing every value that would change what is measured."""

    if agent_policy != NON_REACTIVE_AGENTS:
        raise ValueError(
            f"agent_policy must be {NON_REACTIVE_AGENTS!r}, got {agent_policy!r}; "
            "reactive agents would respond to the ego's braking, so a perception "
            "error would change their behaviour too and the attribution could not "
            "separate the two causal paths"
        )
    if ego_control != CLOSED_LOOP_EGO:
        raise ValueError(
            f"ego_control must be {CLOSED_LOOP_EGO!r}, got {ego_control!r}; a replayed "
            "ego gives every configuration the same trajectory"
        )
    if expert_longitudinal_braking:
        raise ValueError(
            "expert_longitudinal_braking must stay off; the logged driver already "
            "avoided most of these collisions, and inheriting that braking would "
            "credit the perception pipeline with an avoidance it never earned"
        )
    if frequency_hz != PROTOCOL_FREQUENCY_HZ:
        raise ValueError(
            f"frequency_hz must be {PROTOCOL_FREQUENCY_HZ}, got {frequency_hz!r}; every "
            "latency and counter in the committed policy is a duration at that rate"
        )

    return SimulationWiring(
        wiring_id=WIRING_ID,
        agent_policy=agent_policy,
        ego_control=ego_control,
        frequency_hz=frequency_hz,
        expert_longitudinal_braking=expert_longitudinal_braking,
    )
