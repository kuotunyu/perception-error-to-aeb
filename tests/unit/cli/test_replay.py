"""Replay selection and display normalization do not leak dataset identities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from typer.testing import CliRunner

from aebrisk.analysis.aggregate import FormalResults
from aebrisk.artifacts.results import AEBScenarioResultV2, ScenarioFamily
from aebrisk.cohort.manifest import CohortManifestV1
from aebrisk.simulation.orchestrate import TokenResultsV1


def _document(token: str, family: ScenarioFamily, duration: float) -> TokenResultsV1:
    records = tuple(
        AEBScenarioResultV2(
            schema_version="aeb-scenario-result/v2",
            scenario_token=token,
            family=family,
            configuration_id="oracle_aeb",
            replicate=index,
            valid=True,
            collision_vru=0,
            collision_vehicle=0,
            collision_object=0,
            collision_energy=0.0,
            contacts_not_at_fault=0,
            min_ttc_s=None,
            min_clearance_m=1.0,
            missed_interventions=0,
            false_interventions=0,
            max_deceleration_mps2=1.0,
            max_abs_jerk_mps3=1.0,
            intervention_duration_s=duration,
            simulated_duration_s=2.0,
        )
        for index in range(3)
    )
    return TokenResultsV1(
        schema_version="aeb-token-results/v1",
        scenario_token=token,
        family=family,
        split="evaluation",
        configuration_id="oracle_aeb",
        protocol_sha256="a" * 64,
        cohort_manifest_sha256="b" * 64,
        valid=True,
        results=records,
    )


def _manifest(tokens: tuple[str, ...]) -> CohortManifestV1:
    return CohortManifestV1(
        schema_version="aeb-cohort-manifest/v1",
        split="evaluation",
        protocol_sha256="a" * 64,
        families={
            "lead_or_stopping": tokens,
            "cut_in_or_crossing": (),
            "pedestrian_or_crosswalk": (),
            "bicycle_or_vru": (),
        },
        log_names=("log.db",),
    )


def test_selection_chooses_the_oracle_duration_nearest_the_family_median() -> None:
    from aebrisk.cli.replay import select_replay_tokens

    oracle = {
        "short": _document("short", "lead_or_stopping", 1.0),
        "middle": _document("middle", "lead_or_stopping", 4.0),
        "long": _document("long", "lead_or_stopping", 9.0),
    }

    selected = select_replay_tokens(oracle, _manifest(tuple(oracle)))

    assert [(item.family, item.token) for item in selected] == [("lead_or_stopping", "middle")]


def test_selection_breaks_an_even_median_tie_by_token_hash() -> None:
    from aebrisk.cli.replay import select_replay_tokens

    oracle = {
        "alpha": _document("alpha", "lead_or_stopping", 1.0),
        "omega": _document("omega", "lead_or_stopping", 3.0),
    }
    expected = min(oracle, key=lambda token: hashlib.sha256(token.encode()).hexdigest())

    selected = select_replay_tokens(oracle, _manifest(tuple(oracle)))

    assert selected[0].token == expected


def test_selection_omits_an_empty_family_and_reads_only_oracle_documents() -> None:
    from aebrisk.cli.replay import select_replay_tokens

    oracle = {"one": _document("one", "lead_or_stopping", 2.0)}
    selected = select_replay_tokens(oracle, _manifest(("one",)))

    assert len(selected) == 1


def test_selection_refuses_a_valid_oracle_document_without_replicates() -> None:
    from aebrisk.cli.replay import select_replay_tokens

    empty = _document("one", "lead_or_stopping", 2.0).model_copy(update={"results": ()})

    with pytest.raises(ValueError, match="has no replicate results"):
        select_replay_tokens({"one": empty}, _manifest(("one",)))


def test_display_normalization_uses_local_origin_and_anonymous_actor_ids() -> None:
    from aebrisk.aeb.state_machine import AEBState
    from aebrisk.cli.replay import normalize_display_frames
    from aebrisk.observation.models import TrackState
    from aebrisk.report.replay import ReplayFrame

    track = TrackState(
        track_id="native-sensitive-token",
        category="vehicle",
        center_xy_m=(102.0, 201.0),
        yaw_rad=0.0,
        size_lw_m=(4.0, 2.0),
        velocity_xy_mps=(0.0, 0.0),
        visible=True,
        source_timestamp_us=123,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )
    raw = (
        ReplayFrame(
            time_s=0.0,
            ego_center_xy_m=(100.0, 200.0),
            ego_yaw_rad=0.0,
            ego_size_lw_m=(4.0, 2.0),
            ego_speed_mps=3.0,
            tracks=(track,),
            aeb_state=AEBState.MONITOR,
            ttc_s=None,
        ),
    )

    normalized = normalize_display_frames(raw)

    assert normalized[0].ego_center_xy_m == (0.0, 0.0)
    assert normalized[0].tracks[0].center_xy_m == (2.0, 1.0)
    assert normalized[0].tracks[0].track_id == "actor-001"


def test_display_normalization_accepts_an_empty_timeline() -> None:
    from aebrisk.cli.replay import normalize_display_frames

    assert normalize_display_frames(()) == ()


def test_replay_command_refuses_missing_inputs(tmp_path: Path) -> None:
    from aebrisk.cli.app import app

    result = CliRunner().invoke(
        app,
        [
            "replay",
            "--manifest",
            str(tmp_path / "missing.json"),
            "--results-dir",
            str(tmp_path / "missing"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 1
    assert "replay failed" in result.output


def test_replay_command_reports_frozen_parity_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aebrisk.cli import replay
    from aebrisk.cli.app import app

    monkeypatch.setattr(replay, "load_formal_results", lambda path: object())
    monkeypatch.setattr(replay, "load_manifest", lambda path: object())
    monkeypatch.setattr(
        replay,
        "write_selected_replays",
        lambda loaded, manifest, output: tuple(output / f"{index}.html" for index in range(3)),
    )

    result = CliRunner().invoke(
        app,
        [
            "replay",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--results-dir",
            str(tmp_path / "results"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    assert "after frozen replicate-0" in result.output


def test_replay_outcome_matches_the_frozen_replicate_zero_record() -> None:
    from aebrisk.aeb.state_machine import AEBCommand, AEBState
    from aebrisk.cli.replay import validate_replay_outcome
    from aebrisk.simulation.step_loop import StepLoopOutcome

    outcome = StepLoopOutcome(
        token="token",
        configuration_id="oracle_aeb",
        replicate=0,
        states=(AEBState.MONITOR, AEBState.PARTIAL),
        commands=(
            AEBCommand(AEBState.MONITOR, 0.0, None),
            AEBCommand(AEBState.PARTIAL, -3.0, None),
        ),
        nominal_accelerations_mps2=(0.0, 0.0),
        collisions={"vru": 0, "vehicle": 1, "object": 0},
        collision_energy_j=12.5,
        min_ttc_s=1.2,
        min_clearance_m=0.4,
        max_deceleration_mps2=3.0,
        max_abs_jerk_mps3=30.0,
        intervention_duration_s=0.1,
        contacts_not_at_fault=2,
        distance_travelled_m=1.9,
        final_speed_mps=9.0,
        final_pose_xy_m=(1.9, 0.0),
        stop_distance_m=None,
        ran_out_of_route=False,
    )
    frozen = AEBScenarioResultV2(
        schema_version="aeb-scenario-result/v2",
        scenario_token="token",
        family="lead_or_stopping",
        configuration_id="oracle_aeb",
        replicate=0,
        valid=True,
        simulated_duration_s=0.2,
        collision_vru=0,
        collision_vehicle=1,
        collision_object=0,
        collision_energy=12.5,
        contacts_not_at_fault=2,
        min_ttc_s=1.2,
        min_clearance_m=0.4,
        missed_interventions=0,
        false_interventions=0,
        stop_distance_m=None,
        max_deceleration_mps2=3.0,
        max_abs_jerk_mps3=30.0,
        intervention_duration_s=0.1,
    )

    validate_replay_outcome(outcome, frozen, frequency_hz=10.0)


def test_replay_outcome_refuses_a_difference_from_the_frozen_record() -> None:
    from aebrisk.aeb.state_machine import AEBCommand, AEBState
    from aebrisk.cli.replay import validate_replay_outcome
    from aebrisk.simulation.step_loop import StepLoopOutcome

    outcome = StepLoopOutcome(
        token="token",
        configuration_id="oracle_aeb",
        replicate=0,
        states=(AEBState.MONITOR,),
        commands=(AEBCommand(AEBState.MONITOR, 0.0, None),),
        nominal_accelerations_mps2=(0.0,),
        collisions={"vru": 0, "vehicle": 0, "object": 0},
        collision_energy_j=0.0,
        min_ttc_s=None,
        min_clearance_m=1.0,
        max_deceleration_mps2=0.0,
        max_abs_jerk_mps3=0.0,
        intervention_duration_s=0.0,
        contacts_not_at_fault=0,
        distance_travelled_m=1.0,
        final_speed_mps=10.0,
        final_pose_xy_m=(1.0, 0.0),
        stop_distance_m=None,
        ran_out_of_route=False,
    )
    frozen = AEBScenarioResultV2(
        schema_version="aeb-scenario-result/v2",
        scenario_token="token",
        family="lead_or_stopping",
        configuration_id="oracle_aeb",
        replicate=0,
        valid=True,
        simulated_duration_s=0.1,
        collision_vru=0,
        collision_vehicle=1,
        collision_object=0,
        collision_energy=0.0,
        contacts_not_at_fault=0,
        min_ttc_s=None,
        min_clearance_m=1.0,
        missed_interventions=0,
        false_interventions=0,
        stop_distance_m=None,
        max_deceleration_mps2=0.0,
        max_abs_jerk_mps3=0.0,
        intervention_duration_s=0.0,
    )

    with pytest.raises(ValueError, match="collision_vehicle"):
        validate_replay_outcome(outcome, frozen, frequency_hz=10.0)

    with pytest.raises(ValueError, match="selected frozen replay record is invalid"):
        validate_replay_outcome(
            outcome,
            frozen.model_copy(update={"valid": False, "invalid_reason": "step failed"}),
            frequency_hz=10.0,
        )


def test_selected_replays_validate_every_outcome_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aebrisk.cli import replay

    configuration_ids = replay.FAMILY_INTERVENTION_CONFIGURATION_IDS
    configurations = tuple(
        SimpleNamespace(configuration_id=configuration_id) for configuration_id in configuration_ids
    )
    documents = {
        configuration_id: {"token": SimpleNamespace(results=(SimpleNamespace(replicate=0),))}
        for configuration_id in configuration_ids
    }
    selected = replay.ReplaySelection("lead_or_stopping", "token", 1.0)
    reference = SimpleNamespace(token="token")
    cohort_scenario = SimpleNamespace(reference=reference, family="lead_or_stopping")
    validations: list[str] = []
    writes: list[Path] = []

    monkeypatch.setattr(replay, "formal_configurations", lambda: configurations)
    monkeypatch.setattr(replay, "validate_formal_set", lambda *args, **kwargs: None)
    monkeypatch.setattr(replay, "common_cohort", lambda loaded: ("token",))
    monkeypatch.setattr(replay, "select_replay_tokens", lambda oracle, manifest: (selected,))
    monkeypatch.setattr(replay, "resolve_installation", lambda root, split: object())
    monkeypatch.setattr(replay, "resolve_cohort", lambda manifest, installation: (cohort_scenario,))
    monkeypatch.setattr(replay, "BUILD_TOKEN", lambda *args: object())
    monkeypatch.setattr(
        replay,
        "_replay_frames_and_outcome",
        lambda scenario, configuration, manifest: (
            (SimpleNamespace(),),
            SimpleNamespace(configuration_id=configuration.configuration_id),
            10.0,
        ),
    )
    monkeypatch.setattr(
        replay,
        "validate_replay_outcome",
        lambda outcome, frozen, frequency_hz: validations.append(outcome.configuration_id),
    )

    def write_after_validation(frames, title, path):
        assert validations == list(configuration_ids)
        writes.append(path)

    monkeypatch.setattr(replay, "write_replay_html", write_after_validation)

    written = replay.write_selected_replays(
        cast(FormalResults, documents), _manifest(("token",)), tmp_path
    )

    assert validations == list(configuration_ids)
    assert writes == list(written)


def test_selected_replays_refuse_an_unexpected_output_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aebrisk.cli import replay

    unexpected = tmp_path / "native-output.html"
    unexpected.write_text("private", encoding="utf-8")
    monkeypatch.setattr(replay, "formal_configurations", lambda: ())
    monkeypatch.setattr(replay, "validate_formal_set", lambda *args, **kwargs: None)
    monkeypatch.setattr(replay, "common_cohort", lambda loaded: ())
    monkeypatch.setattr(replay, "select_replay_tokens", lambda oracle, manifest: ())
    monkeypatch.setattr(replay, "resolve_installation", lambda root, split: object())
    monkeypatch.setattr(replay, "resolve_cohort", lambda manifest, installation: ())

    with pytest.raises(ValueError, match="unexpected file"):
        replay.write_selected_replays(
            cast(FormalResults, {"oracle_aeb": {}}), _manifest(("token",)), tmp_path
        )


def test_replay_refuses_a_setup_without_the_recorded_world_state() -> None:
    from aebrisk.attribution.factorial import formal_configurations
    from aebrisk.cli import replay
    from aebrisk.simulation.runner import ScenarioSetup

    setup = ScenarioSetup(
        scenario_token="token",
        family="lead_or_stopping",
        initial_speed_mps=1.0,
        route_signature="route",
        planner_id="planner",
        controller_id="controller",
        frequency_hz=10.0,
        termination_s=15.0,
    )
    scenario = SimpleNamespace(build_setup=lambda protocol: setup)
    configuration = next(
        item for item in formal_configurations() if item.configuration_id == "oracle_aeb"
    )

    with pytest.raises(ValueError, match="did not expose its world-state recording"):
        replay._replay_frames(scenario, configuration, _manifest(("token",)))


@pytest.mark.parametrize(
    ("route_end", "final_x", "state", "target_acceleration", "final_speed", "mismatch"),
    [
        (False, 1.0, "monitor", 0.0, 10.0, False),
        (True, 0.5, "monitor", 0.0, 10.0, False),
        (False, 0.995, "full_brake", -6.0, 9.95, False),
        (False, 1.1, "monitor", 0.0, 10.0, True),
    ],
)
def test_replay_includes_terminal_simulated_pose_speed_and_duration(
    monkeypatch: pytest.MonkeyPatch,
    route_end: bool,
    final_x: float,
    state: str,
    target_acceleration: float,
    final_speed: float,
    mismatch: bool,
) -> None:
    from aebrisk.aeb.state_machine import AEBCommand, AEBState
    from aebrisk.attribution.factorial import formal_configurations
    from aebrisk.cli import replay
    from aebrisk.simulation.runner import ScenarioSetup
    from aebrisk.simulation.step_loop import StepLoopOutcome

    raw_track = replay.TrackState(
        track_id="native",
        category="vehicle",
        center_xy_m=(1.0, 0.0),
        yaw_rad=0.0,
        size_lw_m=(4.0, 2.0),
        velocity_xy_mps=(0.0, 0.0),
        visible=True,
        source_timestamp_us=100,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )
    route = np.array([[0.0, 0.0], [final_x if route_end else 100.0, 0.0]])
    recording = SimpleNamespace(
        frames=(SimpleNamespace(timestamp_us=100, tracks=(raw_track,)),),
        route_xy=route,
        ego_size_lw_m=(4.0, 2.0),
        initial_speed_mps=10.0,
    )
    setup = ScenarioSetup(
        scenario_token="token",
        family="lead_or_stopping",
        initial_speed_mps=10.0,
        route_signature="route",
        planner_id="planner",
        controller_id="controller",
        frequency_hz=10.0,
        termination_s=15.0,
    )
    scenario = SimpleNamespace(build_setup=lambda protocol: setup, _recording=recording)
    configuration = next(
        item for item in formal_configurations() if item.configuration_id == "oracle_aeb"
    )
    aeb_state = AEBState(state)
    outcome = StepLoopOutcome(
        token="token",
        configuration_id="oracle_aeb",
        replicate=0,
        states=(aeb_state,),
        commands=(AEBCommand(aeb_state, target_acceleration, None),),
        nominal_accelerations_mps2=(0.0,),
        collisions={"vru": 0, "vehicle": 1, "object": 0},
        collision_energy_j=1.0,
        min_ttc_s=0.0,
        min_clearance_m=0.0,
        max_deceleration_mps2=0.0,
        max_abs_jerk_mps3=0.0,
        intervention_duration_s=0.0,
        contacts_not_at_fault=0,
        distance_travelled_m=final_x,
        final_speed_mps=final_speed,
        final_pose_xy_m=(final_x, 0.0),
        stop_distance_m=None,
        ran_out_of_route=route_end,
    )

    def run_one_step(**kwargs):
        kwargs["frame_at_step"](0)
        return outcome

    monkeypatch.setattr(replay, "run_steps", run_one_step)
    protocol = _manifest(("token",))

    if mismatch:
        with pytest.raises(ValueError, match="kinematics do not reproduce"):
            replay._replay_frames(scenario, configuration, protocol)
        return

    frames = replay._replay_frames(scenario, configuration, protocol)

    assert len(frames) == 2
    assert frames[-1].time_s == 0.1
    assert frames[-1].ego_center_xy_m == (final_x, 0.0)
    assert frames[-1].ego_speed_mps == outcome.final_speed_mps
    assert frames[-1].aeb_state == outcome.states[-1]
    assert frames[-1].tracks == frames[-2].tracks
