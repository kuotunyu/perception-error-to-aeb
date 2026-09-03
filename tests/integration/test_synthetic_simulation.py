"""The pieces, wired together, on a scenario simple enough to reason about by hand.

Every other test in this project checks one module. This one checks that the
modules compose into something that brakes: observation to error channels to
threat geometry to the state machine to the limiter to the vehicle's motion, and
back into a result record the cohort can read.

THIS IS SYNTHETIC VERIFICATION, NOT A RESULT. The scenario is a stationary lead
vehicle on a straight road, chosen because its outcome can be computed with
arithmetic rather than trusted from a simulator. Nothing here may be reported as
a finding about nuPlan, about real perception error, or about any real AEB; the
portfolio order gate forbids reading real nuPlan data at all until P2 is
released, and this file exists precisely so that the pipeline can be shown to
work before then.

The arithmetic: the ego starts at the origin at 10 m/s and the lead vehicle
stands at x = 60. Bodies are 4 m long, so their faces meet when the ego's centre
reaches x = 56. With no AEB the ego holds 10 m/s and arrives there at t = 5.6 s.
With the AEB on, the corridor's front edge is 2.5 m ahead of the ego's centre,
so a predicted overlap appears at 2.5 s of time to collision once the ego passes
x = 32.5, which is 23 m short of contact — more than the 17 m it needs to stop.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pytest

from aebrisk.aeb.controller import limit_acceleration
from aebrisk.aeb.state_machine import AEBMemory, AEBState, update_aeb
from aebrisk.aeb.threat import (
    EgoKinematicState,
    assess_threat,
    oriented_box_polygon,
    polygon_clearance,
)
from aebrisk.artifacts.results import AEBScenarioResultV1
from aebrisk.errors.pipeline import ErrorConfiguration, ErrorKey, apply_error_pipeline
from aebrisk.nuplan_adapter.planner import planner_identity
from aebrisk.nuplan_adapter.simulation import build_simulation_wiring
from aebrisk.observation.models import TrackState, WorldFrame
from aebrisk.simulation.common_cohort import ExperimentConfiguration, common_valid_scenarios
from aebrisk.simulation.route_follower import build_nominal_plan, plan_bytes
from aebrisk.simulation.runner import ScenarioSetup, run_common_scenario

DT_S = 0.1
STEPS = 90
EGO_SIZE = (4.0, 2.0)
LEAD_X = 60.0
INITIAL_SPEED = 10.0
TOKEN = "synthetic-lead-0001"
PROTOCOL_HASH = "b" * 64


def lead_track(timestamp_us: int) -> TrackState:
    return TrackState(
        track_id="lead-0001",
        category="vehicle",
        center_xy_m=(LEAD_X, 0.0),
        yaw_rad=0.0,
        size_lw_m=EGO_SIZE,
        velocity_xy_mps=(0.0, 0.0),
        visible=True,
        source_timestamp_us=timestamp_us,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )


class SyntheticLeadScenario:
    """A stationary lead vehicle, driven by this project's own controller stack."""

    token = TOKEN

    def build_setup(self, protocol: Any) -> ScenarioSetup:
        plan = build_nominal_plan(
            np.array([[index * 2.0, 0.0] for index in range(60)], dtype=np.float64),
            INITIAL_SPEED,
            INITIAL_SPEED,
            None,
        )
        # The route signature is the nominal plan's own bytes, which is what
        # makes "every configuration drove the same route" checkable rather
        # than asserted.
        return ScenarioSetup(
            scenario_token=TOKEN,
            family="lead_or_stopping",
            initial_speed_mps=INITIAL_SPEED,
            route_signature=plan_bytes(plan).hex(),
            planner_id=planner_identity(),
            controller_id="aebrisk-jerk-limited/v1",
            frequency_hz=build_simulation_wiring().frequency_hz,
            termination_s=STEPS * DT_S,
        )

    def simulate(
        self,
        setup: ScenarioSetup,
        configuration: ExperimentConfiguration,
        replicate: int,
    ) -> AEBScenarioResultV1:
        position = 0.0
        speed = setup.initial_speed_mps
        applied = 0.0
        memory = AEBMemory(
            state=AEBState.MONITOR,
            warning_qualifying_steps=0,
            release_clear_steps=0,
            previous_acceleration_mps2=0.0,
        )
        key = ErrorKey(
            scenario_token=TOKEN,
            channel="dropout",
            severity="zero",
            replicate=replicate,
            protocol_hash=PROTOCOL_HASH,
        )
        error_configuration = ErrorConfiguration(
            configuration_id=configuration.configuration_id,
            severity_by_channel=configuration.severity_by_channel,
        )

        history: list[WorldFrame] = []
        collided = False
        min_clearance = math.inf
        min_ttc: Optional[float] = None
        max_deceleration = 0.0
        max_jerk = 0.0
        intervention_steps = 0

        for step in range(STEPS):
            stamp = 1_600_000_000_000_000 + step * int(DT_S * 1_000_000)
            history.append(
                WorldFrame(
                    scenario_token=TOKEN,
                    timestamp_us=stamp,
                    ego_center_xy_m=(position, 0.0),
                    ego_yaw_rad=0.0,
                    ego_speed_mps=speed,
                    tracks=(lead_track(stamp),),
                )
            )

            if configuration.observation_mode == "oracle":
                observed = history[-1].tracks
            else:
                observed = apply_error_pipeline(history, len(history) - 1, error_configuration, key)

            ego = EgoKinematicState(
                center_xy_m=(position, 0.0),
                yaw_rad=0.0,
                size_lw_m=EGO_SIZE,
                speed_mps=speed,
                velocity_xy_mps=(speed, 0.0),
                acceleration_mps2=applied,
            )

            threats = tuple(assess_threat(ego, track) for track in observed if track.visible)
            for threat in threats:
                # The threat's own clearance is what the controller PREDICTED,
                # and it is zero the moment an overlap is predicted. The result
                # records what actually happened, measured below.
                if threat.ttc_s is not None:
                    min_ttc = threat.ttc_s if min_ttc is None else min(min_ttc, threat.ttc_s)

            if configuration.aeb_enabled:
                memory, command = update_aeb(memory, threats, dt_s=DT_S)
            else:
                command = None

            nominal = build_nominal_plan(
                np.array([[position, 0.0], [position + 10.0, 0.0]], dtype=np.float64),
                speed,
                setup.initial_speed_mps,
                None,
            ).nominal_acceleration_mps2

            target = nominal
            if command is not None and command.state in (AEBState.PARTIAL, AEBState.FULL):
                intervention_steps += 1
                target = command.target_acceleration_mps2

            previous = applied
            applied = limit_acceleration(previous, target, dt_s=DT_S)
            max_deceleration = max(max_deceleration, -applied)
            max_jerk = max(max_jerk, abs(applied - previous) / DT_S)

            speed = max(0.0, speed + applied * DT_S)
            position += speed * DT_S

            clearance = polygon_clearance(
                oriented_box_polygon((position, 0.0), 0.0, EGO_SIZE),
                oriented_box_polygon((LEAD_X, 0.0), 0.0, EGO_SIZE),
            )
            min_clearance = min(min_clearance, clearance)
            if clearance == 0.0:
                collided = True
                break

        return AEBScenarioResultV1(
            schema_version="aeb-scenario-result/v1",
            scenario_token=TOKEN,
            family="lead_or_stopping",
            configuration_id=configuration.configuration_id,
            replicate=replicate,
            valid=True,
            invalid_reason=None,
            collision_vru=0,
            collision_vehicle=1 if collided else 0,
            collision_object=0,
            collision_energy=0.5 * 1500.0 * speed * speed if collided else 0.0,
            min_ttc_s=min_ttc,
            min_clearance_m=0.0 if min_clearance is math.inf else min_clearance,
            missed_interventions=0,
            false_interventions=0,
            matched_delay_s=(),
            stop_distance_m=None if speed > 0.01 else LEAD_X - position,
            max_deceleration_mps2=max(0.0, max_deceleration),
            max_abs_jerk_mps3=max_jerk,
            intervention_duration_s=intervention_steps * DT_S,
        )


def no_aeb() -> ExperimentConfiguration:
    return ExperimentConfiguration(
        configuration_id="no_aeb",
        aeb_enabled=False,
        observation_mode="oracle",
        severity_by_channel=dict.fromkeys(
            ("dropout", "localization_shape", "latency", "track_instability"), "zero"
        ),
        replicate_count=1,
    )


def oracle_aeb() -> ExperimentConfiguration:
    return ExperimentConfiguration(
        configuration_id="oracle_aeb",
        aeb_enabled=True,
        observation_mode="oracle",
        severity_by_channel=dict.fromkeys(
            ("dropout", "localization_shape", "latency", "track_instability"), "zero"
        ),
        replicate_count=1,
    )


def corrupted_zero() -> ExperimentConfiguration:
    return ExperimentConfiguration(
        configuration_id="corrupted_zero",
        aeb_enabled=True,
        observation_mode="corrupted",
        severity_by_channel=dict.fromkeys(
            ("dropout", "localization_shape", "latency", "track_instability"), "zero"
        ),
        replicate_count=1,
    )


def run(*configurations: ExperimentConfiguration) -> dict[str, AEBScenarioResultV1]:
    results, invalid = run_common_scenario(
        SyntheticLeadScenario(), configurations, protocol=object()
    )
    assert invalid is None, invalid
    return {record.configuration_id: record for record in results}


def test_without_the_aeb_the_ego_hits_the_lead_vehicle() -> None:
    """The scenario has to be dangerous, or avoiding it would prove nothing."""

    outcome = run(no_aeb())["no_aeb"]

    assert outcome.collision_vehicle == 1
    assert outcome.collision_energy > 0.0


def test_with_the_aeb_the_ego_stops_short() -> None:
    """The payoff: the same scenario, the same controller, one thing different."""

    outcome = run(oracle_aeb())["oracle_aeb"]

    assert outcome.collision_vehicle == 0
    assert outcome.min_clearance_m > 0.0


def test_the_aeb_intervention_is_recorded() -> None:
    """An avoidance with no intervention would mean the geometry, not the AEB, saved it."""

    outcome = run(oracle_aeb())["oracle_aeb"]

    assert outcome.intervention_duration_s > 0.0
    assert outcome.max_deceleration_mps2 > 0.0


def test_the_braking_respects_the_actuator_envelope() -> None:
    """The limiter is in the loop, not bypassed by the state machine's target."""

    outcome = run(oracle_aeb())["oracle_aeb"]

    assert outcome.max_deceleration_mps2 <= 6.0
    assert outcome.max_abs_jerk_mps3 <= 5.0 + 1e-9


def test_the_all_zero_error_pipeline_matches_the_oracle() -> None:
    """Severity zero is the reference, so it must be the exact identity end to end.

    Every measured effect in this study is a difference from this configuration.
    If the pipeline changed anything at zero, every reported effect would be
    shifted by an amount no result could expose.
    """

    outcomes = run(oracle_aeb(), corrupted_zero())

    reference = outcomes["oracle_aeb"]
    through_pipeline = outcomes["corrupted_zero"]

    assert through_pipeline.collision_vehicle == reference.collision_vehicle
    assert through_pipeline.min_clearance_m == pytest.approx(reference.min_clearance_m)
    assert through_pipeline.intervention_duration_s == pytest.approx(
        reference.intervention_duration_s
    )
    assert through_pipeline.max_deceleration_mps2 == pytest.approx(reference.max_deceleration_mps2)


def test_the_configurations_share_a_cohort() -> None:
    """All three finish, so the token is comparable across all three."""

    results, _ = run_common_scenario(
        SyntheticLeadScenario(),
        (no_aeb(), oracle_aeb(), corrupted_zero()),
        protocol=object(),
    )
    by_config: dict[str, tuple[AEBScenarioResultV1, ...]] = {}
    for record in results:
        by_config.setdefault(record.configuration_id, ())
        by_config[record.configuration_id] += (record,)

    assert common_valid_scenarios(by_config) == (TOKEN,)


def test_the_run_is_deterministic() -> None:
    """Two runs of the same cell must agree, or nothing downstream means anything."""

    first = run(corrupted_zero())["corrupted_zero"]
    second = run(corrupted_zero())["corrupted_zero"]

    assert first == second
