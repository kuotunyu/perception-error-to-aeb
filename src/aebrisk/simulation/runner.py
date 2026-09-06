"""Running one scenario token through every configuration, under one setup.

The claim this study makes is that the CONTROLLER, the ROUTE, the INITIAL STATE,
the RATE and the TERMINATION were identical across configurations and only the
perception differed. That claim is made true here or nowhere: the setup is built
once per token and the same object is handed to every configuration, so there is
no code path in which one could receive a different one. A test asserts it on
object identity rather than equality, because equality would pass for two
objects that merely happen to agree today.

The second rule is the guarded transaction. A token is run for all its
configurations or it is used in none. On a failure the run stops — a token
already doomed must not consume compute proving it again — and both the raw
per-configuration failure and the exclusion record are returned, because an
exclusion manifest says a token left the cohort while only the per-configuration
record says which configuration it left from.

A COLLISION IS NOT A FAILURE. It is the measurement. Nothing in this module
treats an outcome as an infrastructure problem.

The third rule is why the records are built HERE rather than by the simulator.
Missed and false interventions are not properties of a run: they are properties
of a run compared with the oracle's run of the same token, and no single
simulation can know the other. This module is the only place that holds all of a
token's runs, so it is the only place those two numbers can be produced honestly.
A simulator therefore returns what it measured — including the braking trace —
and the record, with the comparison in it, is assembled once every configuration
has finished.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from aebrisk.aeb.state_machine import AEBCommand
from aebrisk.artifacts.results import (
    SCENARIO_FAMILIES,
    AEBScenarioResultV1,
    ScenarioFamily,
)
from aebrisk.metrics.events import (
    EventMatchSummary,
    extract_interventions,
    match_interventions,
)
from aebrisk.simulation.common_cohort import ExperimentConfiguration
from aebrisk.simulation.step_loop import StepLoopOutcome
from aebrisk.simulation.validity import InvalidScenario, invalid_from_exception

#: The protocol fixes the simulation rate. Another rate is a different
#: experiment wearing the same name.
PROTOCOL_FREQUENCY_HZ = 10.0

#: The observation mode of the configuration every other one is measured
#: against. Named rather than spelled inline, because the reference is the whole
#: definition of a missed intervention.
REFERENCE_OBSERVATION_MODE = "oracle"

#: What a configuration with no AEB is scored as. It has nothing to brake with,
#: so the oracle's interventions are not ones it MISSED: they are ones it was
#: configured not to have. Counting them would make the no-AEB baseline — which
#: exists to show the scenario was dangerous — look like the study's worst
#: perception failure.
NO_AEB_SUMMARY = EventMatchSummary(missed=0, false=0, matched_delays_s=())


@dataclass(frozen=True)
class ScenarioSetup:
    """Everything that must be identical across a token's configurations."""

    scenario_token: str
    family: ScenarioFamily
    initial_speed_mps: float
    route_signature: str
    planner_id: str
    controller_id: str
    frequency_hz: float
    termination_s: float

    def __post_init__(self) -> None:
        if not self.scenario_token:
            raise ValueError("scenario_token must not be empty")
        if self.family not in SCENARIO_FAMILIES:
            raise ValueError(f"family must be one of {SCENARIO_FAMILIES}, got {self.family!r}")
        if not math.isfinite(self.initial_speed_mps) or self.initial_speed_mps < 0.0:
            raise ValueError("initial_speed_mps must be finite and non-negative")
        if not self.route_signature:
            raise ValueError("route_signature must not be empty")
        if not self.planner_id or not self.controller_id:
            raise ValueError("planner_id and controller_id must both name something")
        if not math.isfinite(self.frequency_hz) or self.frequency_hz <= 0.0:
            raise ValueError(f"frequency_hz must be finite and positive, got {self.frequency_hz!r}")
        if not math.isfinite(self.termination_s) or self.termination_s <= 0.0:
            raise ValueError(
                f"termination_s must be finite and positive, got {self.termination_s!r}; "
                "a scenario with no horizon would run until the process was killed"
            )


def _failed_result(
    setup: ScenarioSetup,
    configuration: ExperimentConfiguration,
    replicate: int,
    invalid: InvalidScenario,
) -> AEBScenarioResultV1:
    """A result that records a failure rather than a measurement.

    Every measurement field is zero because nothing was measured. The `valid`
    flag and its reason are what any reader must go by, and the cohort excludes
    the token on both.
    """

    return AEBScenarioResultV1(
        schema_version="aeb-scenario-result/v1",
        scenario_token=setup.scenario_token,
        family=setup.family,
        configuration_id=configuration.configuration_id,
        replicate=replicate,
        valid=False,
        invalid_reason=invalid.reason,
        collision_vru=0,
        collision_vehicle=0,
        collision_object=0,
        collision_energy=0.0,
        contacts_not_at_fault=0,
        min_ttc_s=None,
        min_clearance_m=0.0,
        missed_interventions=0,
        false_interventions=0,
        matched_delay_s=(),
        stop_distance_m=None,
        max_deceleration_mps2=0.0,
        max_abs_jerk_mps3=0.0,
        intervention_duration_s=0.0,
    )


def _reference_configuration(
    configurations: tuple[ExperimentConfiguration, ...],
) -> ExperimentConfiguration:
    """The one configuration every other is compared against, or a refusal.

    Checked before anything is simulated, so a matrix that cannot define its own
    headline metrics costs a second rather than a cohort's worth of compute.
    """

    references = [
        configuration
        for configuration in configurations
        if configuration.aeb_enabled
        and configuration.observation_mode == REFERENCE_OBSERVATION_MODE
    ]
    if not references:
        raise ValueError(
            "no configuration is the oracle with its AEB enabled, so a missed or "
            "false intervention has nothing to be measured against; a matrix "
            "without a reference would still produce numbers that look like results"
        )
    if len(references) > 1:
        names = [configuration.configuration_id for configuration in references]
        raise ValueError(
            f"more than one oracle configuration has its AEB enabled: {names}; the "
            "reference every other configuration is compared against must be one run"
        )
    return references[0]


def _record_from_outcome(
    setup: ScenarioSetup,
    configuration: ExperimentConfiguration,
    outcome: StepLoopOutcome,
    summary: EventMatchSummary,
) -> AEBScenarioResultV1:
    """Assemble one run's record from what it measured and what it was compared with."""

    return AEBScenarioResultV1(
        schema_version="aeb-scenario-result/v1",
        scenario_token=setup.scenario_token,
        family=setup.family,
        configuration_id=configuration.configuration_id,
        replicate=outcome.replicate,
        valid=True,
        invalid_reason=None,
        collision_vru=outcome.collisions["vru"],
        collision_vehicle=outcome.collisions["vehicle"],
        collision_object=outcome.collisions["object"],
        collision_energy=outcome.collision_energy_j,
        contacts_not_at_fault=outcome.contacts_not_at_fault,
        min_ttc_s=outcome.min_ttc_s,
        min_clearance_m=outcome.min_clearance_m,
        missed_interventions=summary.missed,
        false_interventions=summary.false,
        matched_delay_s=summary.matched_delays_s,
        stop_distance_m=outcome.stop_distance_m,
        max_deceleration_mps2=outcome.max_deceleration_mps2,
        max_abs_jerk_mps3=outcome.max_abs_jerk_mps3,
        intervention_duration_s=outcome.intervention_duration_s,
    )


def _reference_trace(
    runs: list[tuple[ExperimentConfiguration, StepLoopOutcome]],
    reference: ExperimentConfiguration,
) -> tuple[AEBCommand, ...]:
    """The braking every other run is compared against, or a refusal.

    The oracle sees the world exactly as it is and passes through no error
    channel, so its replicates must be identical. If they are not, something in
    the simulation is not deterministic, and picking one of them as THE reference
    would decide every missed intervention in this token by which one was picked.
    """

    traces = {outcome.commands for configuration, outcome in runs if configuration is reference}
    if len(traces) != 1:
        raise ValueError(
            f"the {reference.configuration_id!r} configuration produced "
            f"{len(traces)} different braking traces across its replicates; the "
            "oracle passes through no error channel and must be deterministic, and "
            "without one reference every missed intervention would depend on which "
            "replicate was chosen"
        )
    return traces.pop()


def run_common_scenario(
    scenario: Any,
    configurations: tuple[ExperimentConfiguration, ...],
    protocol: Any,
) -> tuple[tuple[AEBScenarioResultV1, ...], Optional[InvalidScenario]]:
    """Run one token through every configuration under a single shared setup."""

    if not configurations:
        raise ValueError("configurations must not be empty; a token run against nothing")
    identifiers = [configuration.configuration_id for configuration in configurations]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(
            f"duplicate configuration_id in {identifiers}; two cells with the same "
            "name would overwrite each other in every artifact"
        )
    reference = _reference_configuration(configurations)

    try:
        setup = scenario.build_setup(protocol)
    except Exception as error:
        return (), invalid_from_exception(
            getattr(scenario, "token", "unknown"), "initialization", error
        )

    runs: list[tuple[ExperimentConfiguration, StepLoopOutcome]] = []
    for configuration in configurations:
        for replicate in range(configuration.replicate_count):
            try:
                outcome = scenario.simulate(setup, configuration, replicate)
            except Exception as error:
                # Only the failure is returned. The runs that had already
                # finished were measured correctly, but their missed and false
                # interventions are defined by a comparison this token will now
                # never complete, and a record carrying zeros for those would be
                # a fabricated result rather than a partial one.
                invalid = invalid_from_exception(setup.scenario_token, "step", error)
                return (_failed_result(setup, configuration, replicate, invalid),), invalid

            if outcome.token != setup.scenario_token:
                raise ValueError(
                    f"simulator returned scenario_token {outcome.token!r} "
                    f"for token {setup.scenario_token!r}"
                )
            if outcome.configuration_id != configuration.configuration_id:
                raise ValueError(
                    f"simulator returned configuration_id {outcome.configuration_id!r} "
                    f"for configuration {configuration.configuration_id!r}"
                )
            if outcome.replicate != replicate:
                raise ValueError(
                    f"simulator returned replicate {outcome.replicate} for replicate "
                    f"{replicate}; two runs filed under one number would overwrite "
                    "each other and a third would be missing"
                )
            runs.append((configuration, outcome))

    # The events are read on the protocol's grid, which the setup pins, so a
    # brake onset is a time rather than a step index nobody can interpret.
    dt_s = 1.0 / setup.frequency_hz
    oracle_events = extract_interventions(_reference_trace(runs, reference), dt_s=dt_s)

    records = [
        _record_from_outcome(
            setup,
            configuration,
            outcome,
            match_interventions(oracle_events, extract_interventions(outcome.commands, dt_s=dt_s))
            if configuration.aeb_enabled
            else NO_AEB_SUMMARY,
        )
        for configuration, outcome in runs
    ]
    return tuple(records), None
