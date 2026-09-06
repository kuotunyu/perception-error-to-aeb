"""The scenario every configuration is smoked on when no licensed data may be read."""

from __future__ import annotations

from typing import Any

import numpy as np

from aebrisk.nuplan_adapter.planner import planner_identity
from aebrisk.nuplan_adapter.query_scenario import ScenarioReference
from aebrisk.nuplan_adapter.simulation import build_simulation_wiring
from aebrisk.observation.models import TrackState
from aebrisk.simulation.common_cohort import CohortScenario, ExperimentConfiguration
from aebrisk.simulation.route_follower import build_nominal_plan, plan_bytes
from aebrisk.simulation.runner import ScenarioSetup
from aebrisk.simulation.step_loop import StepLoopOutcome, run_steps

DT_S = 0.1
STEPS = 90
EGO_SIZE = (4.0, 2.0)
LEAD_X = 60.0
INITIAL_SPEED = 10.0
TOKEN = "synthetic-lead-0001"
PROTOCOL_HASH = "b" * 64
FIRST_TIMESTAMP_US = 1_600_000_000_000_000

#: A straight road long enough that ninety steps at 10 m/s never reach its end.
#: An ego that ran out of recording would stop for a reason this scenario is not
#: about, and the stop would look like the AEB's work.
ROUTE_XY = np.array([[index * 2.0, 0.0] for index in range(60)], dtype=np.float64)


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


def lead_vehicle(step: int) -> tuple[int, tuple[TrackState, ...]]:
    """The world at one step: one stationary vehicle, on the recording's own clock."""

    timestamp_us = FIRST_TIMESTAMP_US + step * round(DT_S * 1_000_000)
    return timestamp_us, (lead_track(timestamp_us),)


class SyntheticLeadScenario:
    """A stationary lead vehicle, driven through the production step loop.

    The loop is `aebrisk.simulation.step_loop.run_steps` rather than a copy of it
    written here, which is what makes this file evidence about the shipped code
    instead of evidence about itself.
    """

    token = TOKEN

    def build_setup(self, protocol: Any) -> ScenarioSetup:
        plan = build_nominal_plan(ROUTE_XY, INITIAL_SPEED, INITIAL_SPEED, None)
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
    ) -> StepLoopOutcome:
        return run_steps(
            token=TOKEN,
            route_xy=ROUTE_XY,
            frame_at_step=lead_vehicle,
            steps=STEPS,
            initial_speed_mps=setup.initial_speed_mps,
            ego_size_lw_m=EGO_SIZE,
            configuration=configuration,
            replicate=replicate,
            protocol_hash=PROTOCOL_HASH,
            dt_s=DT_S,
            map_speed_limit_mps=None,
        )


SYNTHETIC_LOG = "synthetic"


def synthetic_cohort() -> tuple[CohortScenario, ...]:
    """One synthetic token, shaped like a frozen cohort entry."""

    return (
        CohortScenario(
            reference=ScenarioReference(
                log_file=SYNTHETIC_LOG,
                token=TOKEN,
                scenario_type="stopping_with_lead",
                timestamp_us=FIRST_TIMESTAMP_US,
            ),
            family="lead_or_stopping",
        ),
    )
