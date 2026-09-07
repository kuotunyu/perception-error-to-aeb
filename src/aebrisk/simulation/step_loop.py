"""The one loop every configuration is driven through.

This is the study's experiment, and its whole design is that exactly one thing
differs between cells of the matrix: what the AEB was told. The nominal
controller is perception-blind by construction, the route is the logged one, the
step rate and maximum horizon are the protocol's, and the ego's motion is integrated the
same way whatever the observation showed. Only the braking command changes.

Until 2026-09-06 this loop existed solely inside
`tests/integration/test_synthetic_simulation.py`, written by hand. A simulation
the study rests on that lives in a test file has no production twin, and the
synthetic scenario was therefore evidence about itself. It now drives this.

The study distinguishes counted collisions from contacts excluded by its
declared simulation rule. Logged actors do not react to the simulated ego.
The initial real-data smoke revealed contacts while ego was stopped, motivating
separate reporting of this classification. Counting all contacts changes the
reported comparison; neither category alone establishes a causal safety effect.

The rule excludes a stopped ego or a body behind ego with greater speed
magnitude. It does not test relative longitudinal closing velocity or establish
full equivalence to an upstream collision classifier. Excluded contacts are
counted separately and do not terminate the run; counted collisions terminate
it. Actual exposure is recorded for either outcome.

Five more decisions here would be wrong silently, so each is stated where it is
made:
the channels are bound once per run because two of them carry state between
steps; the oracle mode never touches the error pipeline at all, because the
reference must not pass through the thing being measured; a collision ends the
run rather than integrating through a body; running past the end of the
recording ends the run rather than inventing road; and THE WORLD SOURCE OWNS THE
CLOCK — a frame carries the timestamp the recording gave it, not one the loop
counted out, because a frame stamped on a synthetic grid while its tracks carry
the log's own microseconds would put two clocks inside one observation.

What comes back is deliberately NOT a result record. Missed and false
interventions are found by comparing a run's braking against the oracle run's
for the same token, which no single run can know, so the loop returns its
measurements and its command trace and leaves the record to whoever holds all of
a token's runs.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional

import numpy as np
import numpy.typing as npt

from aebrisk.aeb.controller import limit_acceleration
from aebrisk.aeb.state_machine import AEBCommand, AEBMemory, AEBState, update_aeb
from aebrisk.aeb.threat import (
    EgoKinematicState,
    assess_threat,
    oriented_box_polygon,
    polygon_clearance,
    separation_at_least,
)
from aebrisk.errors.channels import ScenarioChannels
from aebrisk.errors.pipeline import ErrorConfiguration, ErrorKey, apply_error_pipeline
from aebrisk.observation.models import TrackState, WorldFrame
from aebrisk.simulation.common_cohort import ExperimentConfiguration
from aebrisk.simulation.route_follower import build_nominal_plan, pose_at_distance, route_length_m

Float64Array = npt.NDArray[np.float64]

#: Named so a test can count how often they are called without reading source.
BIND_CHANNELS = ScenarioChannels
APPLY_ERRORS = apply_error_pipeline

#: The mass used to turn a collision speed into an energy. One number for every
#: body, because the study compares configurations rather than vehicles, and a
#: per-category mass would make the energy a statement about the fleet.
COLLIDING_MASS_KG = 1500.0

#: Below this the ego is stopped for the purpose of reporting a stopping
#: distance. A hard zero would almost never be reached by an integrator.
STOPPED_SPEED_MPS = 0.01

#: The channel the error key names. Every channel derives its own draws from this
#: key, so which one is named here does not change any channel's behaviour; it is
#: recorded rather than chosen.
KEY_CHANNEL = "dropout"


@dataclass(frozen=True)
class StepLoopOutcome:
    """What one run measured, and the trace the study reads interventions from."""

    token: str
    configuration_id: str
    replicate: int
    states: tuple[AEBState, ...]
    commands: tuple[AEBCommand, ...]
    nominal_accelerations_mps2: tuple[float, ...]
    collisions: dict[str, int]
    collision_energy_j: float
    min_ttc_s: Optional[float]
    min_clearance_m: float
    max_deceleration_mps2: float
    max_abs_jerk_mps3: float
    intervention_duration_s: float
    #: Contacts excluded by the declared stopped-ego or behind-and-faster-speed
    #: rule. Report these beside counted collisions; exclusion does not establish
    #: that a contact was physically unavoidable or assign legal responsibility.
    contacts_not_at_fault: int
    distance_travelled_m: float
    final_speed_mps: float
    final_pose_xy_m: tuple[float, float]
    stop_distance_m: Optional[float]
    ran_out_of_route: bool


#: How an observation category is counted in the collision columns the spec asks
#: for: vulnerable road user, vehicle, object. Pedestrians and cyclists are the
#: vulnerable ones, and merging them into a single "object" total is exactly the
#: aggregation this study exists to refuse.
COLLISION_COLUMN_BY_CATEGORY: dict[str, str] = {
    "pedestrian": "vru",
    "bicycle": "vru",
    "vehicle": "vehicle",
    "object": "object",
}


def ego_at_fault(
    pose_xy_m: tuple[float, float],
    yaw_rad: float,
    ego_speed_mps: float,
    track: TrackState,
) -> bool:
    """Classify a contact under this study's frozen simulation rule.

    Exclude a stopped ego, or a body behind ego whose speed magnitude is
    greater than ego's speed. The latter comparison is not a relative
    longitudinal closing-velocity test. All other contacts are counted.

    This operational classification addresses non-reactive logged actors;
    it does not establish legal responsibility or full equivalence to an
    upstream collision classifier. Excluded contacts remain separately counted.
    """

    if ego_speed_mps <= STOPPED_SPEED_MPS:
        return False
    ahead = (track.center_xy_m[0] - pose_xy_m[0]) * math.cos(yaw_rad) + (
        track.center_xy_m[1] - pose_xy_m[1]
    ) * math.sin(yaw_rad)
    closing = math.hypot(track.velocity_xy_mps[0], track.velocity_xy_mps[1]) > ego_speed_mps
    return not (ahead < 0.0 and closing)


def _category_column(category: str) -> str:
    """Which collision column a track's category is counted in, or refuse.

    Failing closed rather than defaulting to `object`: a new observation category
    silently counted as scenery would understate exactly the class the study
    protects, and no published number would look wrong.
    """

    try:
        return COLLISION_COLUMN_BY_CATEGORY[category]
    except KeyError:
        raise ValueError(
            f"no collision column for category {category!r}; add it to "
            "COLLISION_COLUMN_BY_CATEGORY rather than letting it count as scenery"
        ) from None


def run_steps(
    *,
    token: str,
    route_xy: Float64Array,
    frame_at_step: Callable[[int], tuple[int, Sequence[TrackState]]],
    steps: int,
    initial_speed_mps: float,
    ego_size_lw_m: tuple[float, float],
    configuration: ExperimentConfiguration,
    replicate: int,
    protocol_hash: str,
    dt_s: float,
    map_speed_limit_mps: Optional[float],
) -> StepLoopOutcome:
    """Drive one configuration of one scenario, and report what happened."""

    if steps < 1:
        raise ValueError(f"steps must be at least one, got {steps}")
    if dt_s <= 0.0 or not math.isfinite(dt_s):
        raise ValueError(f"dt_s must be finite and positive, got {dt_s!r}")

    total_route_m = route_length_m(route_xy)

    error_configuration = ErrorConfiguration(
        configuration_id=configuration.configuration_id,
        severity_by_channel=configuration.severity_by_channel,
    )
    key = ErrorKey(
        scenario_token=token,
        channel=KEY_CHANNEL,
        severity=configuration.severity_by_channel[KEY_CHANNEL],
        replicate=replicate,
        protocol_hash=protocol_hash,
    )
    # Bound ONCE for the whole run. Dropout keeps its draw record and
    # fragmentation its track memory across steps, so rebuilding per step would
    # redraw every choice and no track would ever stay lost for its delay.
    bound = BIND_CHANNELS(error_configuration, dt_s=dt_s)

    history: list[WorldFrame] = []
    states: list[AEBState] = []
    commands: list[AEBCommand] = []
    nominal_requests: list[float] = []
    collisions = {"vru": 0, "vehicle": 0, "object": 0}
    collision_energy = 0.0
    min_ttc: Optional[float] = None
    min_clearance = math.inf
    max_deceleration = 0.0
    max_jerk = 0.0
    intervention_steps = 0
    not_at_fault = 0
    contacted: set[str] = set()
    struck: Optional[TrackState] = None

    memory = AEBMemory(
        state=AEBState.MONITOR,
        warning_qualifying_steps=0,
        release_clear_steps=0,
        previous_acceleration_mps2=0.0,
    )
    travelled = 0.0
    stop_distance: Optional[float] = None
    speed = float(initial_speed_mps)
    applied = 0.0
    pose, yaw = pose_at_distance(route_xy, travelled)
    ran_out = False

    for step in range(steps):
        stamp, observed_tracks = frame_at_step(step)
        if history and stamp < history[-1].timestamp_us:
            raise ValueError(
                f"the world source went backwards in time at step {step}: {stamp} is "
                f"before {history[-1].timestamp_us}; every latency selection out of a "
                "history assembled in that order depends on the order rather than on "
                "the recording"
            )
        tracks = tuple(observed_tracks)
        history.append(
            WorldFrame(
                scenario_token=token,
                timestamp_us=stamp,
                ego_center_xy_m=pose,
                ego_yaw_rad=yaw,
                ego_speed_mps=speed,
                tracks=tracks,
            )
        )

        if configuration.observation_mode == "oracle":
            # The reference must not pass through the thing being measured.
            observed: Sequence[TrackState] = history[-1].tracks
        else:
            observed = APPLY_ERRORS(
                history,
                len(history) - 1,
                error_configuration,
                key,
                stages=bound.stages(),
            )

        ego = EgoKinematicState(
            center_xy_m=pose,
            yaw_rad=yaw,
            size_lw_m=ego_size_lw_m,
            speed_mps=speed,
            velocity_xy_mps=(speed * math.cos(yaw), speed * math.sin(yaw)),
            acceleration_mps2=applied,
        )

        threats = tuple(assess_threat(ego, track) for track in observed if track.visible)
        for threat in threats:
            if threat.ttc_s is not None:
                min_ttc = threat.ttc_s if min_ttc is None else min(min_ttc, threat.ttc_s)

        if configuration.aeb_enabled:
            memory, command = update_aeb(memory, threats, dt_s=dt_s)
            commands.append(command)
            states.append(command.state)
        else:
            states.append(AEBState.MONITOR)

        nominal = build_nominal_plan(
            route_xy, speed, initial_speed_mps, map_speed_limit_mps
        ).nominal_acceleration_mps2
        nominal_requests.append(nominal)

        target = nominal
        if commands and configuration.aeb_enabled:
            latest = commands[-1]
            if latest.state in (AEBState.PARTIAL, AEBState.FULL):
                intervention_steps += 1
                target = latest.target_acceleration_mps2

        previous = applied
        applied = limit_acceleration(previous, target, dt_s=dt_s)
        max_deceleration = max(max_deceleration, -applied)
        max_jerk = max(max_jerk, abs(applied - previous) / dt_s)

        speed = max(0.0, speed + applied * dt_s)
        travelled += speed * dt_s
        if stop_distance is None and speed <= STOPPED_SPEED_MPS:
            # The FIRST time it came to rest, not the last. The AEB releases
            # after its clear steps and the blind nominal controller then
            # re-accelerates, so a stopping distance read off the final speed
            # would report a run that stopped as never having stopped at all.
            stop_distance = travelled
        if travelled >= total_route_m:
            # The recording ended. Continuing would invent road nobody drove.
            travelled = total_route_m
            ran_out = True
        pose, yaw = pose_at_distance(route_xy, travelled)

        ego_polygon = oriented_box_polygon(pose, yaw, ego_size_lw_m)
        for track in history[-1].tracks:
            # Arithmetic before geometry. The bound is never above the true
            # clearance, so a body is measured exactly only when it could be the
            # closest approach or could be touching; a positive bound rules
            # contact out. A real urban frame carries over two hundred tracks and
            # two of them matter.
            at_least = separation_at_least(pose, ego_size_lw_m, track.center_xy_m, track.size_lw_m)
            if at_least <= 0.0 or at_least < min_clearance:
                clearance = polygon_clearance(
                    ego_polygon,
                    oriented_box_polygon(track.center_xy_m, track.yaw_rad, track.size_lw_m),
                )
                min_clearance = min(min_clearance, clearance)
                # Each body is counted once. A rear-ended ego stays overlapped
                # for as long as the recording drives through it, and counting
                # every step of that would report one contact as forty.
                if clearance == 0.0 and track.track_id not in contacted:
                    contacted.add(track.track_id)
                    if ego_at_fault(pose, yaw, speed, track):
                        struck = track if struck is None else struck
                    else:
                        not_at_fault += 1

        if struck is not None:
            collisions[_category_column(struck.category)] += 1
            collision_energy = 0.5 * COLLIDING_MASS_KG * speed * speed
            break
        if ran_out:
            break

    return StepLoopOutcome(
        token=token,
        configuration_id=configuration.configuration_id,
        replicate=replicate,
        states=tuple(states),
        commands=tuple(commands),
        nominal_accelerations_mps2=tuple(nominal_requests),
        collisions=collisions,
        collision_energy_j=collision_energy,
        min_ttc_s=min_ttc,
        min_clearance_m=0.0 if min_clearance is math.inf else min_clearance,
        max_deceleration_mps2=max(0.0, max_deceleration),
        max_abs_jerk_mps3=max_jerk,
        intervention_duration_s=intervention_steps * dt_s,
        contacts_not_at_fault=not_at_fault,
        distance_travelled_m=travelled,
        final_speed_mps=speed,
        final_pose_xy_m=pose,
        stop_distance_m=stop_distance,
        ran_out_of_route=ran_out,
    )
