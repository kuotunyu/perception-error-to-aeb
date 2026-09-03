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
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from aebrisk.artifacts.results import (
    SCENARIO_FAMILIES,
    AEBScenarioResultV1,
    ScenarioFamily,
)
from aebrisk.simulation.common_cohort import ExperimentConfiguration
from aebrisk.simulation.validity import InvalidScenario, invalid_from_exception

#: The protocol fixes the simulation rate. Another rate is a different
#: experiment wearing the same name.
PROTOCOL_FREQUENCY_HZ = 10.0


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

    try:
        setup = scenario.build_setup(protocol)
    except Exception as error:
        return (), invalid_from_exception(
            getattr(scenario, "token", "unknown"), "initialization", error
        )

    results: list[AEBScenarioResultV1] = []
    for configuration in configurations:
        for replicate in range(configuration.replicate_count):
            try:
                record = scenario.simulate(setup, configuration, replicate)
            except Exception as error:
                invalid = invalid_from_exception(setup.scenario_token, "step", error)
                results.append(_failed_result(setup, configuration, replicate, invalid))
                return tuple(results), invalid

            if record.scenario_token != setup.scenario_token:
                raise ValueError(
                    f"simulator returned scenario_token {record.scenario_token!r} "
                    f"for token {setup.scenario_token!r}"
                )
            if record.configuration_id != configuration.configuration_id:
                raise ValueError(
                    f"simulator returned configuration_id {record.configuration_id!r} "
                    f"for configuration {configuration.configuration_id!r}"
                )
            results.append(record)

    return tuple(results), None
