"""Contracts for running a frozen cohort through the matrix and writing the results.

Two of these are the difference between a study and a pile of numbers.

A TOKEN THE MANIFEST NAMES AND THE DATA DOES NOT HAVE IS A REFUSAL, made before
anything is simulated. Running the rest would measure a cohort nobody chose, and
the manifest hash sitting beside the results would still match.

RESULTS ARE WRITTEN ONLY FOR THE WHOLE COHORT. A partial run whose files are on
disk is indistinguishable from a complete one to everything downstream, so the
token set is compared with the manifest's before the first byte is written.

Nothing here opens a log database or runs a real simulation: the query layer and
the token adapter are replaced, and what is under test is the join between them.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import pytest

PROTOCOL_SHA = "a" * 64
MEMBERSHIP_SHA = "b" * 64
PROTOCOL_HASH = "c" * 64
CHANNELS = ("dropout", "localization_shape", "latency", "track_instability")

FAMILY_OF_TOKEN = {
    "t-lead": ("lead_or_stopping", "stopping_with_lead"),
    "t-cut": ("cut_in_or_crossing", "changing_lane"),
    "t-ped": ("pedestrian_or_crosswalk", "waiting_for_pedestrian_to_cross"),
    "t-bike": ("bicycle_or_vru", "behind_bike"),
}


def load_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.simulation import orchestrate
    except ImportError:  # pragma: no cover - names the absence during RED
        pytest.fail("aebrisk.simulation.orchestrate is missing", pytrace=False)
    return orchestrate


def manifest(**overrides: Any) -> Any:
    from aebrisk.cohort.manifest import CohortManifestV1

    fields: dict[str, Any] = {
        "schema_version": "aeb-cohort-manifest/v1",
        "split": "evaluation",
        "protocol_sha256": PROTOCOL_SHA,
        "families": {family: (token,) for token, (family, _type) in FAMILY_OF_TOKEN.items()},
        "log_names": ("one.db", "two.db"),
    }
    fields.update(overrides)
    return CohortManifestV1(**fields)


def installation(tmp_path: Path, names: tuple[str, ...] = ("one.db", "two.db")) -> Any:
    from aebrisk.nuplan_adapter.database import NuPlanInstallation

    split_dir = tmp_path / "nuplan-v1.1" / "splits" / "val"
    split_dir.mkdir(parents=True, exist_ok=True)
    databases = []
    for name in names:
        path = split_dir / name
        path.write_bytes(b"")
        databases.append(path)
    return NuPlanInstallation(
        data_root=tmp_path,
        maps_root=tmp_path / "maps",
        split="val",
        log_databases=tuple(databases),
    )


def reference(token: str, log_file: str = "one.db", scenario_type: Optional[str] = None) -> Any:
    from aebrisk.nuplan_adapter.query_scenario import ScenarioReference

    return ScenarioReference(
        log_file=log_file,
        token=token,
        scenario_type=FAMILY_OF_TOKEN[token][1] if scenario_type is None else scenario_type,
        timestamp_us=1_600_000_000_000_000,
    )


def patch_query(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, by_log: dict[str, tuple[Any, ...]]
) -> None:
    def query(log_file: str, scenario_types: Any) -> tuple[Any, ...]:
        return by_log.get(Path(log_file).name, ())

    monkeypatch.setattr(module, "QUERY_SCENARIOS", query)


def every_token() -> dict[str, tuple[Any, ...]]:
    return {
        "one.db": (reference("t-lead"), reference("t-cut")),
        "two.db": (reference("t-ped", "two.db"), reference("t-bike", "two.db")),
    }


def configuration(identifier: str, *, aeb: bool = True, mode: str = "corrupted") -> Any:
    from aebrisk.simulation.common_cohort import ExperimentConfiguration

    return ExperimentConfiguration(
        configuration_id=identifier,
        aeb_enabled=aeb,
        observation_mode=mode,
        severity_by_channel=dict.fromkeys(CHANNELS, "zero"),
        replicate_count=1,
    )


def matrix() -> tuple[Any, ...]:
    return (
        configuration("no_aeb", aeb=False, mode="oracle"),
        configuration("oracle_aeb", mode="oracle"),
        configuration("dropout-high"),
    )


class FakeToken:
    """A token that answers the runner without a database or a simulation."""

    failing_configuration = ""

    def __init__(self, reference: Any, family: Any, protocol_hash: str) -> None:
        self.reference = reference
        self.family = family
        self.protocol_hash = protocol_hash

    @property
    def token(self) -> str:
        return str(self.reference.token)

    def build_setup(self, protocol: Any) -> Any:
        from aebrisk.simulation.runner import ScenarioSetup

        return ScenarioSetup(
            scenario_token=self.token,
            family=self.family,
            initial_speed_mps=9.0,
            route_signature="d" * 64,
            planner_id="aebrisk-closed-loop/v1",
            controller_id="aebrisk-jerk-limited/v1",
            frequency_hz=10.0,
            termination_s=15.0,
        )

    def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
        from aebrisk.simulation.step_loop import StepLoopOutcome

        if configuration.configuration_id == self.failing_configuration:
            raise RuntimeError("the log database went away")
        return StepLoopOutcome(
            token=self.token,
            configuration_id=configuration.configuration_id,
            replicate=replicate,
            states=(),
            commands=(),
            nominal_accelerations_mps2=(),
            collisions={"vru": 0, "vehicle": 0, "object": 0},
            collision_energy_j=0.0,
            min_ttc_s=2.0,
            min_clearance_m=1.0,
            max_deceleration_mps2=3.0,
            max_abs_jerk_mps3=4.0,
            intervention_duration_s=0.5,
            distance_travelled_m=40.0,
            final_speed_mps=8.0,
            final_pose_xy_m=(40.0, 0.0),
            stop_distance_m=None,
            ran_out_of_route=False,
        )


def patch_token(monkeypatch: pytest.MonkeyPatch, module: ModuleType, failing: str = "") -> None:
    class Token(FakeToken):
        failing_configuration = failing

    monkeypatch.setattr(module, "BUILD_TOKEN", Token)


def run_everything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failing: str = ""
) -> tuple[ModuleType, Any, tuple[Any, ...]]:
    module = load_module()
    patch_query(monkeypatch, module, every_token())
    patch_token(monkeypatch, module, failing)
    document = manifest()
    cohort = module.resolve_cohort(document, installation(tmp_path))
    runs = module.run_cohort(cohort, matrix(), protocol_hash=PROTOCOL_HASH, protocol=object())
    return module, document, runs


# --------------------------------------------------------------------------
# Resolving the cohort against the logs the manifest names
# --------------------------------------------------------------------------


def test_every_token_is_resolved_from_the_logs_the_manifest_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sweeping the whole split would be slower and would not check the manifest."""

    module = load_module()
    patch_query(monkeypatch, module, every_token())

    cohort = module.resolve_cohort(manifest(), installation(tmp_path))

    assert [scenario.reference.token for scenario in cohort] == [
        "t-lead",
        "t-cut",
        "t-ped",
        "t-bike",
    ]
    assert [scenario.family for scenario in cohort] == list(module.FAMILIES)


def test_a_manifest_naming_a_log_the_split_lacks_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cohort was frozen against a different installation, which is not this one."""

    module = load_module()
    patch_query(monkeypatch, module, every_token())

    with pytest.raises(ValueError, match=r"log\(s\) the 'val' split does not have"):
        module.resolve_cohort(manifest(), installation(tmp_path, names=("one.db",)))


def test_a_token_none_of_the_named_logs_holds_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Running the rest would measure a cohort nobody chose."""

    module = load_module()
    logs = every_token()
    logs["two.db"] = (reference("t-ped", "two.db"),)
    patch_query(monkeypatch, module, logs)

    with pytest.raises(ValueError, match=r"^token 't-bike' of family 'bicycle_or_vru' is in"):
        module.resolve_cohort(manifest(), installation(tmp_path))


def test_a_token_filed_under_the_wrong_family_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stratum that does not hold what it says holds makes every per-family rate wrong."""

    module = load_module()
    logs = every_token()
    logs["one.db"] = (reference("t-lead", scenario_type="behind_bike"), reference("t-cut"))
    patch_query(monkeypatch, module, logs)

    with pytest.raises(ValueError, match=r"^token 't-lead' is filed under 'lead_or_stopping'"):
        module.resolve_cohort(manifest(), installation(tmp_path))


# --------------------------------------------------------------------------
# Which cells one invocation runs
# --------------------------------------------------------------------------


def test_a_single_cell_is_always_run_beside_the_oracle() -> None:
    """A cell alone cannot produce a missed intervention; the comparison defines it."""

    module = load_module()

    chosen = module.configurations_for(matrix(), "dropout-high")

    assert [configuration.configuration_id for configuration in chosen] == [
        "oracle_aeb",
        "dropout-high",
    ]


def test_asking_for_the_oracle_itself_does_not_run_it_twice() -> None:
    """Two runs of one cell in one call would be refused as a duplicate anyway."""

    module = load_module()

    chosen = module.configurations_for(matrix(), "oracle_aeb")

    assert [configuration.configuration_id for configuration in chosen] == ["oracle_aeb"]


def test_asking_for_no_cell_runs_the_whole_matrix() -> None:
    """The cheaper path: the reference is computed once instead of once per cell."""

    module = load_module()

    chosen = module.configurations_for(matrix(), None)

    assert len(chosen) == 3


def test_a_cell_that_is_not_in_the_matrix_is_refused() -> None:
    """A typo would otherwise run the oracle alone and write it under another name."""

    module = load_module()

    with pytest.raises(ValueError, match=r"^'dropout-huge' is not one of the matrix's cells"):
        module.configurations_for(matrix(), "dropout-huge")


# --------------------------------------------------------------------------
# Running the cohort
# --------------------------------------------------------------------------


def test_every_token_of_the_cohort_is_driven_through_every_cell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A token silently skipped would leave the cohort unbalanced and still look whole."""

    _module, _document, runs = run_everything(monkeypatch, tmp_path)

    assert [run.token for run in runs] == ["t-lead", "t-cut", "t-ped", "t-bike"]
    assert all(len(run.results) == 3 for run in runs)
    assert all(run.invalid is None for run in runs)


def test_the_caller_is_told_about_each_token_as_it_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run of hundreds of tokens that says nothing until the end cannot be watched."""

    module = load_module()
    patch_query(monkeypatch, module, every_token())
    patch_token(monkeypatch, module)
    seen: list[str] = []

    module.run_cohort(
        module.resolve_cohort(manifest(), installation(tmp_path)),
        matrix(),
        protocol_hash=PROTOCOL_HASH,
        protocol=object(),
        on_token=lambda run: seen.append(run.token),
    )

    assert seen == ["t-lead", "t-cut", "t-ped", "t-bike"]


def test_an_empty_cohort_is_refused() -> None:
    """A run over nothing produces nothing to compare, and an artifact that says so."""

    module = load_module()

    with pytest.raises(ValueError, match=r"^the cohort is empty"):
        module.run_cohort((), matrix(), protocol_hash=PROTOCOL_HASH, protocol=object())


def test_a_token_that_failed_carries_its_exclusion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failure is not a result, and the record has to say which cell it left from."""

    _module, _document, runs = run_everything(monkeypatch, tmp_path, failing="dropout-high")

    assert all(run.invalid is not None for run in runs)
    assert runs[0].invalid.phase == "step"
    assert runs[0].invalid.exception_type == "RuntimeError"


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def written(module: ModuleType, document: Any, runs: tuple[Any, ...], output: Path) -> Any:
    return module.write_cohort_results(
        runs,
        document,
        output,
        written_configurations=("oracle_aeb", "dropout-high"),
        protocol_sha256=PROTOCOL_SHA,
        cohort_manifest_sha256=MEMBERSHIP_SHA,
    )


def test_one_file_per_token_and_cell_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Filing by cell is what lets one cell be re-run without touching the others."""

    module, document, runs = run_everything(monkeypatch, tmp_path)
    output = tmp_path / "results"

    paths = written(module, document, runs, output)

    assert len(paths) == 8
    assert (output / "oracle_aeb" / "t-lead.json").is_file()
    assert (output / "dropout-high" / "t-bike.json").is_file()
    assert not (output / "no_aeb").exists()


def test_a_written_document_carries_only_its_own_cells_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file that mixed two cells could not be re-run or compared cell by cell."""

    import json

    module, document, runs = run_everything(monkeypatch, tmp_path)
    output = tmp_path / "results"
    written(module, document, runs, output)

    payload = json.loads((output / "dropout-high" / "t-lead.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == "aeb-token-results/v1"
    assert payload["configuration_id"] == "dropout-high"
    assert payload["scenario_token"] == "t-lead"
    assert payload["family"] == "lead_or_stopping"
    assert payload["split"] == "evaluation"
    assert payload["protocol_sha256"] == PROTOCOL_SHA
    assert payload["cohort_manifest_sha256"] == MEMBERSHIP_SHA
    assert payload["valid"] is True
    assert [record["configuration_id"] for record in payload["results"]] == ["dropout-high"]


def test_the_written_bytes_are_the_same_on_every_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two runs of one cell must be comparable byte for byte, not line by line."""

    module, document, runs = run_everything(monkeypatch, tmp_path)
    output = tmp_path / "results"
    written(module, document, runs, output)

    raw = (output / "oracle_aeb" / "t-lead.json").read_bytes()

    assert raw.endswith(b"\n")
    assert b"\r" not in raw


def test_a_failed_token_is_written_as_invalid_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A reader must not have to infer the exclusion from a missing file."""

    import json

    module, document, runs = run_everything(monkeypatch, tmp_path, failing="dropout-high")
    output = tmp_path / "results"
    written(module, document, runs, output)

    payload = json.loads((output / "dropout-high" / "t-lead.json").read_text(encoding="utf-8"))

    assert payload["valid"] is False
    assert payload["invalid_reason"]
    assert payload["invalid_phase"] == "step"


def test_a_run_whose_tokens_are_not_the_manifests_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A partial cohort on disk is indistinguishable from a whole one."""

    module, document, runs = run_everything(monkeypatch, tmp_path)
    output = tmp_path / "results"

    with pytest.raises(ValueError, match=r"the run covered 3 tokens and the evaluation"):
        written(module, document, runs[:3], output)

    assert not output.exists()


def test_the_summary_counts_what_the_operator_needs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The four numbers at the end of a run that took hours."""

    module, _document, runs = run_everything(monkeypatch, tmp_path)

    assert module.summarize(runs) == {
        "tokens": 4,
        "valid": 4,
        "invalid": 0,
        "records": 12,
    }
