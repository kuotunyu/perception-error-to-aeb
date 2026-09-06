"""One logged nuPlan scenario, presented as something the cohort runner can drive.

This is the last piece of the bridge. `query_scenario` reads the log, `scenario`
turns one iteration into a `WorldFrame`, `step_loop` drives a configuration, and
this module is what holds a token together: it answers `build_setup` and
`simulate`, which is the whole of what the runner asks of a scenario.

THE RECORDING IS READ ONCE. Every configuration and every replicate of a token
is driven over the same tuple of frames, held here after `build_setup`. That is
not a cache for speed, though it is much faster than 78 passes over the
database: it is the study's control condition made structural. Re-reading per
configuration would leave a code path in which two cells of the matrix could be
driven over different readings of one recording, and the difference would be
reported as an effect of perception error.

THE ROUTE IS THE LOGGED EGO PATH. The lateral line the nominal controller
follows is where the vehicle actually went, sampled at the protocol's rate, so
every configuration drives the same road and only the longitudinal command
differs. A scenario whose ego never moved carries no route and no heading, and
is refused at setup rather than at the first step.

NOTHING HERE READS A SENSOR OR A MAP. The frames come from the log's own tables
through the devkit's read-only queries. The map API that would supply a posted
speed limit needs the devkit's map stack, which needs rasterio and OpenCV, and
this container has neither by design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import numpy.typing as npt

from aebrisk.artifacts.results import ScenarioFamily
from aebrisk.nuplan_adapter.planner import planner_identity
from aebrisk.nuplan_adapter.query_scenario import ScenarioReference, build_scenario
from aebrisk.nuplan_adapter.scenario import oracle_world_frame
from aebrisk.nuplan_adapter.simulation import (
    PROTOCOL_SCENARIO_DURATION_S,
    build_simulation_wiring,
)
from aebrisk.observation.models import TrackState, WorldFrame
from aebrisk.simulation.common_cohort import ExperimentConfiguration
from aebrisk.simulation.route_follower import build_nominal_plan, plan_bytes, route_length_m
from aebrisk.simulation.runner import ScenarioSetup
from aebrisk.simulation.step_loop import StepLoopOutcome, run_steps

Float64Array = npt.NDArray[np.float64]

#: Named so a test can drive this adapter without a log database.
BUILD_SCENARIO = build_scenario
ORACLE_FRAME = oracle_world_frame
RUN_STEPS = run_steps

#: The controller every configuration is driven by, cited in every run record.
CONTROLLER_ID = "aebrisk-jerk-limited/v1"

#: What the nominal controller is told about the road's posted limit: nothing.
#: Reading one needs the devkit's map stack, which this container does not have,
#: so the target speed is the smaller of the speed the log was driven at and the
#: study's own 13.9 m/s cap. The narrowing is stated rather than silent, and it
#: is one-sided: no scenario is driven FASTER than it was recorded, so a missing
#: limit cannot make any cell of the matrix look better than it is.
MAP_SPEED_LIMIT_MPS: Optional[float] = None


@dataclass(frozen=True)
class Recording:
    """What one reading of the log gives every configuration of a token."""

    frames: tuple[WorldFrame, ...]
    route_xy: Float64Array
    ego_size_lw_m: tuple[float, float]
    initial_speed_mps: float


class NuPlanScenario:
    """One nuPlan token, read once and driven by every configuration."""

    def __init__(
        self,
        reference: ScenarioReference,
        family: ScenarioFamily,
        protocol_hash: str,
    ) -> None:
        if not protocol_hash:
            raise ValueError(
                "protocol_hash must name the frozen protocol; every error draw in "
                "this scenario is keyed on it, and an empty one would make two "
                "protocols produce the same corruption"
            )
        self._reference = reference
        self._family = family
        self._protocol_hash = protocol_hash
        self._recording: Optional[Recording] = None

    @property
    def token(self) -> str:
        """How the cohort names this scenario."""

        return self._reference.token

    def build_setup(self, protocol: Any) -> ScenarioSetup:
        """Read the recording once, and pin what every configuration must share.

        The duration and the rate come from `nuplan_adapter.simulation`, whose
        constants a contract test ties to the protocol file's bytes, rather than
        from the document passed in. Two sources for one number is how they come
        to disagree, and the file is the one that is published.
        """

        wiring = build_simulation_wiring()
        scenario = BUILD_SCENARIO(
            self._reference.log_file,
            self.token,
            duration_s=PROTOCOL_SCENARIO_DURATION_S,
            frequency_hz=wiring.frequency_hz,
        )
        steps = int(scenario.get_number_of_iterations())
        frames = tuple(ORACLE_FRAME(scenario, iteration) for iteration in range(steps))

        route_xy = np.array([frame.ego_center_xy_m for frame in frames], dtype=np.float64)
        if route_length_m(route_xy) <= 0.0:
            raise ValueError(
                f"the ego never moved in {self.token}: the recording gives no route to "
                "follow and no heading to face, so there is nothing for a perception "
                "error to change"
            )

        footprint = scenario.get_ego_state_at_iteration(0).car_footprint
        recording = Recording(
            frames=frames,
            route_xy=route_xy,
            ego_size_lw_m=(float(footprint.length), float(footprint.width)),
            initial_speed_mps=frames[0].ego_speed_mps,
        )
        self._recording = recording

        plan = build_nominal_plan(
            route_xy,
            recording.initial_speed_mps,
            recording.initial_speed_mps,
            MAP_SPEED_LIMIT_MPS,
        )
        return ScenarioSetup(
            scenario_token=self.token,
            family=self._family,
            initial_speed_mps=recording.initial_speed_mps,
            # The plan's own bytes, so "every configuration drove the same route"
            # is checkable in the artifacts rather than asserted here.
            route_signature=plan_bytes(plan).hex(),
            planner_id=planner_identity(),
            controller_id=CONTROLLER_ID,
            frequency_hz=wiring.frequency_hz,
            termination_s=PROTOCOL_SCENARIO_DURATION_S,
        )

    def simulate(
        self,
        setup: ScenarioSetup,
        configuration: ExperimentConfiguration,
        replicate: int,
    ) -> StepLoopOutcome:
        """Drive one configuration over the frames every other configuration gets."""

        recording = self._recording
        if recording is None:
            raise ValueError(
                f"{self.token} was simulated before it was read; build_setup is what "
                "reads the recording, and without it each configuration would read "
                "its own"
            )

        def frame_at_step(step: int) -> tuple[int, tuple[TrackState, ...]]:
            """The world at one step, on the recording's own clock."""

            frame = recording.frames[step]
            return frame.timestamp_us, frame.tracks

        return RUN_STEPS(
            token=self.token,
            route_xy=recording.route_xy,
            frame_at_step=frame_at_step,
            steps=len(recording.frames),
            initial_speed_mps=recording.initial_speed_mps,
            ego_size_lw_m=recording.ego_size_lw_m,
            configuration=configuration,
            replicate=replicate,
            protocol_hash=self._protocol_hash,
            dt_s=1.0 / setup.frequency_hz,
            map_speed_limit_mps=MAP_SPEED_LIMIT_MPS,
        )
