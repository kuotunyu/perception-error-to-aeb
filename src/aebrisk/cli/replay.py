"""Select and rebuild the bounded, anonymous, derived replay inventory."""

# Typer on the pinned Python 3.9 runtime needs evaluated Annotated metadata.

import hashlib
import math
import os
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

import typer

from aebrisk.aeb.controller import limit_acceleration
from aebrisk.aeb.state_machine import AEBState
from aebrisk.aeb.threat import EgoKinematicState, assess_threat
from aebrisk.analysis.aggregate import (
    FormalResults,
    common_cohort,
    load_formal_results,
    validate_formal_set,
)
from aebrisk.artifacts.family_interventions import FAMILY_INTERVENTION_CONFIGURATION_IDS
from aebrisk.artifacts.results import SCENARIO_FAMILIES, AEBScenarioResultV2
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import CohortManifestV1, load_manifest
from aebrisk.errors.channels import ScenarioChannels
from aebrisk.errors.pipeline import ErrorConfiguration, ErrorKey, apply_error_pipeline
from aebrisk.nuplan_adapter.database import resolve_installation
from aebrisk.nuplan_adapter.nuplan_scenario import MAP_SPEED_LIMIT_MPS
from aebrisk.observation.models import TrackState, WorldFrame
from aebrisk.report.replay import ReplayFrame, write_replay_html
from aebrisk.simulation.orchestrate import BUILD_TOKEN, TokenResultsV1, resolve_cohort
from aebrisk.simulation.route_follower import pose_at_distance, route_length_m
from aebrisk.simulation.step_loop import KEY_CHANNEL, StepLoopOutcome, run_steps

DATA_ROOT_VAR = "NUPLAN_DATA_ROOT"


@dataclass(frozen=True)
class ReplaySelection:
    family: str
    token: str
    oracle_mean_intervention_duration_s: float


def _oracle_mean(document: TokenResultsV1) -> float:
    values = [record.intervention_duration_s for record in document.results]
    if not values:
        raise ValueError(f"oracle token {document.scenario_token!r} has no replicate results")
    return statistics.fmean(values)


def select_replay_tokens(
    oracle_documents: Mapping[str, TokenResultsV1], manifest: CohortManifestV1
) -> tuple[ReplaySelection, ...]:
    """Choose one predeclared median-nearest oracle token per non-empty family."""

    selections: list[ReplaySelection] = []
    for family in SCENARIO_FAMILIES:
        candidates = [
            (token, _oracle_mean(oracle_documents[token]))
            for token in manifest.families[family]
            if token in oracle_documents and oracle_documents[token].valid
        ]
        if candidates:
            median = statistics.median(value for _, value in candidates)
            token, value = min(
                candidates,
                key=lambda item: (
                    abs(item[1] - median),
                    hashlib.sha256(item[0].encode("utf-8")).hexdigest(),
                ),
            )
            selections.append(ReplaySelection(family, token, value))
    return tuple(selections)


def normalize_display_frames(frames: tuple[ReplayFrame, ...]) -> tuple[ReplayFrame, ...]:
    """Move geometry to a fixed local origin and replace native actor identities."""

    if not frames:
        return ()
    origin_x, origin_y = frames[0].ego_center_xy_m
    native_ids = sorted({track.track_id for frame in frames for track in frame.tracks})
    display_ids = {native: f"actor-{index:03d}" for index, native in enumerate(native_ids, 1)}
    normalized: list[ReplayFrame] = []
    for frame in frames:
        tracks = tuple(
            track.__class__(
                track_id=display_ids[track.track_id],
                category=track.category,
                center_xy_m=(track.center_xy_m[0] - origin_x, track.center_xy_m[1] - origin_y),
                yaw_rad=track.yaw_rad,
                size_lw_m=track.size_lw_m,
                velocity_xy_mps=track.velocity_xy_mps,
                visible=track.visible,
                source_timestamp_us=track.source_timestamp_us,
                covariance_xy=track.covariance_xy,
            )
            for track in frame.tracks
        )
        normalized.append(
            ReplayFrame(
                time_s=frame.time_s,
                ego_center_xy_m=(
                    frame.ego_center_xy_m[0] - origin_x,
                    frame.ego_center_xy_m[1] - origin_y,
                ),
                ego_yaw_rad=frame.ego_yaw_rad,
                ego_size_lw_m=frame.ego_size_lw_m,
                ego_speed_mps=frame.ego_speed_mps,
                tracks=tracks,
                aeb_state=frame.aeb_state,
                ttc_s=frame.ttc_s,
            )
        )
    return tuple(normalized)


def _observed_tracks(
    raw: Sequence[tuple[int, tuple[TrackState, ...]]],
    token: str,
    configuration: Any,
    protocol_hash: str,
    dt_s: float,
) -> tuple[tuple[TrackState, ...], ...]:
    error_configuration = ErrorConfiguration(
        configuration_id=configuration.configuration_id,
        severity_by_channel=configuration.severity_by_channel,
    )
    key = ErrorKey(
        scenario_token=token,
        channel=KEY_CHANNEL,
        severity=configuration.severity_by_channel[KEY_CHANNEL],
        replicate=0,
        protocol_hash=protocol_hash,
    )
    bound = ScenarioChannels(error_configuration, dt_s=dt_s)
    history: list[WorldFrame] = []
    output: list[tuple[TrackState, ...]] = []
    for index, (stamp, tracks) in enumerate(raw):
        history.append(
            WorldFrame(
                scenario_token=token,
                timestamp_us=stamp,
                ego_center_xy_m=(0.0, 0.0),
                ego_yaw_rad=0.0,
                ego_speed_mps=0.0,
                tracks=tracks,
            )
        )
        observed = (
            history[-1].tracks
            if configuration.observation_mode == "oracle"
            else apply_error_pipeline(
                history,
                index,
                error_configuration,
                key,
                stages=bound.stages(),
            )
        )
        output.append(tuple(observed))
    return tuple(output)


def validate_replay_outcome(
    outcome: StepLoopOutcome,
    frozen: AEBScenarioResultV2,
    *,
    frequency_hz: float,
) -> None:
    """Require a selected rerun to reproduce every shared frozen measurement."""

    actual: dict[str, object] = {
        "scenario_token": outcome.token,
        "configuration_id": outcome.configuration_id,
        "replicate": outcome.replicate,
        "simulated_duration_s": len(outcome.states) / frequency_hz,
        "collision_vru": outcome.collisions["vru"],
        "collision_vehicle": outcome.collisions["vehicle"],
        "collision_object": outcome.collisions["object"],
        "collision_energy": outcome.collision_energy_j,
        "contacts_not_at_fault": outcome.contacts_not_at_fault,
        "min_ttc_s": outcome.min_ttc_s,
        "min_clearance_m": outcome.min_clearance_m,
        "stop_distance_m": outcome.stop_distance_m,
        "max_deceleration_mps2": outcome.max_deceleration_mps2,
        "max_abs_jerk_mps3": outcome.max_abs_jerk_mps3,
        "intervention_duration_s": outcome.intervention_duration_s,
    }
    if not frozen.valid:
        raise ValueError("selected frozen replay record is invalid")
    for metric, value in actual.items():
        expected = getattr(frozen, metric)
        if value != expected:
            raise ValueError(
                f"selected replay differs from frozen record for {metric}: "
                f"rerun={value!r}, frozen={expected!r}"
            )


def _replay_frames_and_outcome(
    scenario: Any, configuration: Any, protocol: CohortManifestV1
) -> tuple[tuple[ReplayFrame, ...], StepLoopOutcome, float]:
    setup = scenario.build_setup(protocol)
    recording = getattr(scenario, "_recording", None)
    if recording is None:
        raise ValueError("scenario setup did not expose its world-state recording")
    raw: list[tuple[int, tuple[TrackState, ...]]] = []

    def recording_frame(step: int) -> tuple[int, tuple[TrackState, ...]]:
        frame = recording.frames[step]
        provided = (frame.timestamp_us, frame.tracks)
        raw.append(provided)
        return provided

    dt_s = 1.0 / setup.frequency_hz
    outcome = run_steps(
        token=setup.scenario_token,
        route_xy=recording.route_xy,
        frame_at_step=recording_frame,
        steps=len(recording.frames),
        initial_speed_mps=recording.initial_speed_mps,
        ego_size_lw_m=recording.ego_size_lw_m,
        configuration=configuration,
        replicate=0,
        protocol_hash=protocol.protocol_sha256,
        dt_s=dt_s,
        map_speed_limit_mps=MAP_SPEED_LIMIT_MPS,
    )
    observed_by_step = _observed_tracks(
        raw, setup.scenario_token, configuration, protocol.protocol_sha256, dt_s
    )
    total_route = route_length_m(recording.route_xy)
    travelled = 0.0
    speed = recording.initial_speed_mps
    applied = 0.0
    frames: list[ReplayFrame] = []
    for index, state in enumerate(outcome.states):
        pose, yaw = pose_at_distance(recording.route_xy, travelled)
        observed = observed_by_step[index]
        ego = EgoKinematicState(
            center_xy_m=pose,
            yaw_rad=yaw,
            size_lw_m=recording.ego_size_lw_m,
            speed_mps=speed,
            velocity_xy_mps=(speed * math.cos(yaw), speed * math.sin(yaw)),
            acceleration_mps2=applied,
        )
        ttc_values = [
            threat.ttc_s
            for threat in (assess_threat(ego, track) for track in observed if track.visible)
            if threat.ttc_s is not None
        ]
        frames.append(
            ReplayFrame(
                time_s=index * dt_s,
                ego_center_xy_m=pose,
                ego_yaw_rad=yaw,
                ego_size_lw_m=recording.ego_size_lw_m,
                ego_speed_mps=speed,
                tracks=observed,
                aeb_state=state,
                ttc_s=min(ttc_values) if ttc_values else None,
            )
        )
        target = outcome.nominal_accelerations_mps2[index]
        if configuration.aeb_enabled and state in (AEBState.PARTIAL, AEBState.FULL):
            target = outcome.commands[index].target_acceleration_mps2
        applied = limit_acceleration(applied, target, dt_s=dt_s)
        speed = max(0.0, speed + applied * dt_s)
        travelled = min(total_route, travelled + speed * dt_s)
    reconstructed_pose, terminal_yaw = pose_at_distance(recording.route_xy, travelled)
    if not (
        math.isclose(reconstructed_pose[0], outcome.final_pose_xy_m[0], abs_tol=1e-9)
        and math.isclose(reconstructed_pose[1], outcome.final_pose_xy_m[1], abs_tol=1e-9)
        and math.isclose(speed, outcome.final_speed_mps, abs_tol=1e-9)
    ):
        raise ValueError("replay kinematics do not reproduce the simulation outcome")
    terminal_tracks = observed_by_step[-1]
    terminal_ego = EgoKinematicState(
        center_xy_m=outcome.final_pose_xy_m,
        yaw_rad=terminal_yaw,
        size_lw_m=recording.ego_size_lw_m,
        speed_mps=outcome.final_speed_mps,
        velocity_xy_mps=(
            outcome.final_speed_mps * math.cos(terminal_yaw),
            outcome.final_speed_mps * math.sin(terminal_yaw),
        ),
        acceleration_mps2=applied,
    )
    terminal_ttc = [
        threat.ttc_s
        for threat in (
            assess_threat(terminal_ego, track) for track in terminal_tracks if track.visible
        )
        if threat.ttc_s is not None
    ]
    frames.append(
        ReplayFrame(
            time_s=len(outcome.states) * dt_s,
            ego_center_xy_m=outcome.final_pose_xy_m,
            ego_yaw_rad=terminal_yaw,
            ego_size_lw_m=recording.ego_size_lw_m,
            ego_speed_mps=outcome.final_speed_mps,
            tracks=terminal_tracks,
            aeb_state=outcome.states[-1],
            ttc_s=min(terminal_ttc) if terminal_ttc else None,
        )
    )
    return normalize_display_frames(tuple(frames)), outcome, setup.frequency_hz


def _replay_frames(
    scenario: Any, configuration: Any, protocol: CohortManifestV1
) -> tuple[ReplayFrame, ...]:
    frames, _, _ = _replay_frames_and_outcome(scenario, configuration, protocol)
    return frames


def write_selected_replays(
    loaded: FormalResults,
    manifest: CohortManifestV1,
    output_dir: Path,
) -> tuple[Path, ...]:
    """Resolve and rebuild the fixed three-cell display for each selected family."""

    configurations = formal_configurations()
    tokens = tuple(token for family in SCENARIO_FAMILIES for token in manifest.families[family])
    validate_formal_set(loaded, configurations, tokens, manifest=manifest)
    common = set(common_cohort(loaded))
    oracle = {
        token: document for token, document in loaded["oracle_aeb"].items() if token in common
    }
    selections = select_replay_tokens(oracle, manifest)
    selected_manifest = manifest.model_copy(
        update={
            "families": {
                family: tuple(item.token for item in selections if item.family == family)
                for family in SCENARIO_FAMILIES
            }
        }
    )
    installation = resolve_installation(Path(os.environ.get(DATA_ROOT_VAR, "")), split="val")
    cohort = resolve_cohort(selected_manifest, installation)
    by_token = {scenario.reference.token: scenario for scenario in cohort}
    by_configuration = {
        configuration.configuration_id: configuration for configuration in configurations
    }
    expected = tuple(
        output_dir / f"{selection.family}--{configuration_id}.html"
        for selection in selections
        for configuration_id in FAMILY_INTERVENTION_CONFIGURATION_IDS
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    unexpected = sorted(path for path in output_dir.iterdir() if path not in expected)
    if unexpected:
        raise ValueError(f"replay output directory contains unexpected file {unexpected[0]}")
    prepared: list[tuple[tuple[ReplayFrame, ...], str, Path]] = []
    for selection in selections:
        cohort_scenario = by_token[selection.token]
        scenario = BUILD_TOKEN(
            cohort_scenario.reference, cohort_scenario.family, manifest.protocol_sha256
        )
        for configuration_id in FAMILY_INTERVENTION_CONFIGURATION_IDS:
            frames, outcome, frequency_hz = _replay_frames_and_outcome(
                scenario, by_configuration[configuration_id], manifest
            )
            frozen = cast(
                AEBScenarioResultV2,
                next(
                    record
                    for record in loaded[configuration_id][selection.token].results
                    if record.replicate == 0
                ),
            )
            validate_replay_outcome(outcome, frozen, frequency_hz=frequency_hz)
            path = output_dir / f"{selection.family}--{configuration_id}.html"
            prepared.append((frames, f"{selection.family} / {configuration_id}", path))
    for frames, title, path in prepared:
        write_replay_html(frames, title, path)
    return expected


def replay(
    manifest: Annotated[Path, typer.Option("--manifest")],
    results_dir: Annotated[Path, typer.Option("--results-dir")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
) -> None:
    """Write the bounded replay inventory from a validated completed formal set."""

    try:
        written = write_selected_replays(
            load_formal_results(results_dir), load_manifest(manifest), output_dir
        )
    except (OSError, UnicodeDecodeError, ValueError) as error:
        typer.echo(f"replay failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"replay wrote {len(written)} derived HTML files after frozen replicate-0 "
        "collision/contact/exposure/intervention parity; final pose and speed are "
        "checked against the fresh simulator outcome because frozen records do not store them"
    )
