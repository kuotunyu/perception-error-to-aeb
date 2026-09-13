"""The command line, end to end, on synthetic inputs.

Every other test in this project calls a function. This one runs the commands a
person actually types, in the order they type them, and checks that the files
one stage writes are the files the next stage reads. That seam is where a
pipeline breaks, and it breaks silently: each stage passes its own tests while
producing something the next one cannot use.

THE INPUTS ARE SYNTHETIC AND NOTHING HERE IS A RESULT. The pipeline is shown to
work without a licensed dataset, which is what lets it be checked in CI and on
any machine; the real cohort is exercised by the integration tests that mount a
nuPlan split and skip when none is there.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel
from typer.testing import CliRunner

from aebrisk.analysis.claims import generate_claims
from aebrisk.artifacts.documents import (
    AEBEvaluationV1,
    AEBExclusionsV1,
    AEBIntervalsV1,
    AEBShapleyV1,
)
from aebrisk.artifacts.family_interventions import FAMILY_INTERVENTION_CONFIGURATION_IDS
from aebrisk.artifacts.results import SCENARIO_FAMILIES
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.attribution.shapley import ATTRIBUTED_METRICS, CHANNELS
from aebrisk.cli.app import app
from aebrisk.cohort.manifest import CohortManifestV1, membership_sha256

PROTOCOL_TEXT = "protocol: nuplan_aeb_v2\n"
PROTOCOL_SHA = hashlib.sha256(PROTOCOL_TEXT.encode()).hexdigest()


def run(*arguments: str):
    return CliRunner().invoke(app, list(arguments))


def cohort_document(tokens: tuple[str, ...]) -> dict:
    """A frozen cohort manifest, which is what `simulate` reads."""

    return {
        "schema_version": "aeb-cohort-manifest/v1",
        "split": "evaluation",
        "protocol_sha256": PROTOCOL_SHA,
        "families": {
            "lead_or_stopping": list(tokens),
            "cut_in_or_crossing": [],
            "pedestrian_or_crosswalk": [],
            "bicycle_or_vru": [],
        },
        "log_names": ["one.db"],
    }


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A protocol, a cohort manifest, an evaluation and a claims file."""

    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(PROTOCOL_TEXT, encoding="utf-8")

    manifest = tmp_path / "cohort.json"
    manifest.write_text(json.dumps(cohort_document(("s-0002", "s-0001"))), encoding="utf-8")

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    cohort_hash = "1" * 64

    def evaluation_row(
        configuration_id: str,
        group: str,
        seconds: float,
        collisions: int,
        contacts_not_at_fault: int,
    ) -> dict:
        return {
            "configuration_id": configuration_id,
            "group": group,
            "scenarios": 6,
            "simulated_seconds": seconds,
            "collisions": collisions,
            "collisions_vru": 0,
            "collisions_vehicle": collisions,
            "collisions_object": 0,
            "contacts_not_at_fault": contacts_not_at_fault,
            "collision_energy_total": float(collisions * 10),
            "missed_interventions": 0,
            "false_interventions": 0,
            "mean_intervention_duration_s": 0.0,
            "max_deceleration_mps2": 0.0,
            "max_abs_jerk_mps3": 0.0,
            "min_ttc_s": 0.0,
            "min_clearance_m": 0.0,
            "collisions_per_1000_scenarios": collisions * 1000.0 / 6.0,
            "collisions_per_hour": collisions * 3600.0 / seconds,
            "collisions_per_100km": None,
        }

    evaluation_rows = [
        evaluation_row("no_aeb", "baseline", 40.0, 2, 0),
        evaluation_row("oracle_aeb", "baseline", 50.0, 0, 1),
        evaluation_row("dropout-medium", "single_channel", 60.0, 1, 2),
        evaluation_row("coalition-none", "coalition", 70.0, 1, 3),
        evaluation_row("coalition-dropout+latency", "coalition", 80.0, 1, 4),
        evaluation_row(
            "coalition-dropout+localization_shape+latency+track_instability",
            "coalition",
            90.0,
            0,
            5,
        ),
        evaluation_row("calibration_imported_0123456789abcdef", "imported", 100.0, 1, 6),
    ]
    (artifacts / "evaluation.json").write_text(
        json.dumps(
            {
                "schema_version": "aeb-evaluation/v1",
                "protocol_sha256": PROTOCOL_SHA,
                "cohort_manifest_sha256": cohort_hash,
                "cohort_size": 3,
                "common_valid_tokens": 2,
                "simulated_seconds": sum(row["simulated_seconds"] for row in evaluation_rows),
                "configurations": evaluation_rows,
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "exclusions.json").write_text(
        json.dumps(
            {
                "schema_version": "aeb-exclusions/v1",
                "protocol_sha256": PROTOCOL_SHA,
                "cohort_manifest_sha256": cohort_hash,
                "cohort_size": 3,
                "common_valid_tokens": 2,
                "excluded": [
                    {
                        "scenario_token": "s-0003",
                        "phase": "step",
                        "reason": "synthetic exclusion",
                        "exception_type": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    family_rows = []
    for family in SCENARIO_FAMILIES:
        valid_tokens = 2 if family == "lead_or_stopping" else 0
        for configuration_id in FAMILY_INTERVENTION_CONFIGURATION_IDS:
            family_rows.append(
                {
                    "family": family,
                    "configuration_id": configuration_id,
                    "valid_tokens": valid_tokens,
                    "replicate_count": 3,
                    "scenario_replicates": valid_tokens * 3,
                    "missed_interventions": 0,
                    "false_interventions": 0,
                    "missed_per_1000_scenario_replicates": 0.0 if valid_tokens else None,
                    "false_per_1000_scenario_replicates": 0.0 if valid_tokens else None,
                }
            )
    (artifacts / "family-interventions.json").write_text(
        json.dumps(
            {
                "schema_version": "aeb-family-interventions/v1",
                "protocol_sha256": PROTOCOL_SHA,
                "cohort_manifest_sha256": cohort_hash,
                "common_valid_tokens": 2,
                "evaluation_per_family": 100,
                "rows": family_rows,
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "intervals.json").write_text(
        json.dumps(
            {
                "schema_version": "aeb-intervals/v1",
                "protocol_sha256": PROTOCOL_SHA,
                "cohort_manifest_sha256": cohort_hash,
                "cohort_size": 3,
                "common_valid_tokens": 2,
                "intervals": {},
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "shapley.json").write_text(
        json.dumps(
            {
                "schema_version": "aeb-shapley/v1",
                "protocol_sha256": PROTOCOL_SHA,
                "cohort_manifest_sha256": cohort_hash,
                "cohort_size": 3,
                "common_valid_tokens": 2,
                "metrics": {
                    metric: {
                        "values": dict.fromkeys(CHANNELS, 0.0),
                        "efficiency_max_abs_residual": 0.0,
                        "scenarios_attributed": 2,
                    }
                    for metric in ATTRIBUTED_METRICS
                },
            }
        ),
        encoding="utf-8",
    )

    claims = tmp_path / "claims.yaml"
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='synthetic-report'\n", encoding="utf-8"
    )
    registry = generate_claims(artifacts, PROTOCOL_SHA, cohort_hash)
    claims.write_text(
        yaml.safe_dump(
            registry.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    return tmp_path


# --------------------------------------------------------------------------
# simulate
# --------------------------------------------------------------------------


def test_simulate_writes_a_run_context(workspace: Path) -> None:
    """The first stage names what it ran, which every later stage depends on."""

    synthetic_manifest(workspace)
    result = run(
        "simulate",
        "--protocol",
        str(workspace / "protocol.yaml"),
        "--manifest",
        str(workspace / "cohort.json"),
        "--config-id",
        "oracle_aeb",
        "--output-dir",
        str(workspace / "runs"),
        "--scenario-source",
        "synthetic",
    )

    assert result.exit_code == 0, result.output
    context = json.loads((workspace / "runs" / "run_context.json").read_text(encoding="utf-8"))
    assert context["configuration_id"] == "oracle_aeb"
    assert len(context["cohort_sha256"]) == 64
    assert (workspace / "runs" / "oracle_aeb" / "synthetic-lead-0001.json").is_file()


def test_the_cohort_hash_does_not_depend_on_the_manifest_order(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cohort is a set. Two manifests listing it differently are one cohort."""

    mounted_split(tmp_path, monkeypatch)
    reversed_manifest = workspace / "reversed.json"
    reversed_manifest.write_text(
        json.dumps(cohort_document(("s-0001", "s-0002"))), encoding="utf-8"
    )

    for name, manifest in (("a", "cohort.json"), ("b", "reversed.json")):
        run(
            "simulate",
            "--protocol",
            str(workspace / "protocol.yaml"),
            "--manifest",
            str(workspace / manifest),
            "--config-id",
            "oracle_aeb",
            "--output-dir",
            str(workspace / name),
            "--scenario-source",
            "nuplan",
            "--split",
            "mini",
            "--dry-run",
        )

    first = json.loads((workspace / "a" / "run_context.json").read_text(encoding="utf-8"))
    second = json.loads((workspace / "b" / "run_context.json").read_text(encoding="utf-8"))
    assert first["cohort_sha256"] == second["cohort_sha256"]


def test_the_nuplan_source_refuses_when_no_split_is_mounted(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Naming the missing root beats a stack trace from deep inside a query.

    The portfolio order gate that once closed this source opened when
    `driving-risk-metrics` released. What remains is an operational check: the
    command needs a mounted split, and an operator who forgot to mount one
    should be told which path was looked for, in a second, rather than after a
    driver error three layers down.
    """

    monkeypatch.delenv("NUPLAN_DATA_ROOT", raising=False)

    result = run(
        "simulate",
        "--protocol",
        str(workspace / "protocol.yaml"),
        "--manifest",
        str(workspace / "cohort.json"),
        "--config-id",
        "oracle_aeb",
        "--output-dir",
        str(workspace / "runs"),
    )

    assert result.exit_code != 0
    assert "NUPLAN_DATA_ROOT" in result.output
    assert "order gate" not in result.output


def mounted_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "one.db") -> Path:
    """An installation with one empty database, which is all CI may have."""

    root = tmp_path / "root"
    split = root / "nuplan-v1.1" / "splits" / "mini"
    split.mkdir(parents=True, exist_ok=True)
    (split / name).write_bytes(b"")
    # The installation is a split AND its maps; the resolver checks both, so a
    # fixture that made only the split would pass for the wrong reason.
    (root / "maps").mkdir(exist_ok=True)
    monkeypatch.setenv("NUPLAN_DATA_ROOT", str(root))
    return root


def test_the_nuplan_source_is_accepted_when_a_split_is_mounted(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The source the study actually uses must be reachable, not permanently refused.

    `--dry-run` is what stops here: no licensed data may be read in CI, and the
    empty database this fixture mounts holds no scenario to run. What is checked
    is that the command accepted the mount, the manifest and the cell, and wrote
    the context the later stages read.
    """

    mounted_split(tmp_path, monkeypatch)

    result = run(
        "simulate",
        "--protocol",
        str(workspace / "protocol.yaml"),
        "--manifest",
        str(workspace / "cohort.json"),
        "--config-id",
        "oracle_aeb",
        "--output-dir",
        str(workspace / "runs"),
        "--split",
        "mini",
        "--dry-run",
    )

    assert result.exit_code == 0, result.output
    context = json.loads((workspace / "runs" / "run_context.json").read_text(encoding="utf-8"))
    assert context["configuration_id"] == "oracle_aeb"
    assert "nothing was simulated" in result.output


def test_a_manifest_the_split_cannot_supply_stops_the_run(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without `--dry-run` the command resolves the cohort, and says which log is absent.

    The manifest names `one.db`; this installation has `other.db`. Running the
    tokens that happened to resolve would measure a cohort nobody chose, and the
    manifest hash beside the results would still match.
    """

    mounted_split(tmp_path, monkeypatch, name="other.db")

    result = run(
        "simulate",
        "--protocol",
        str(workspace / "protocol.yaml"),
        "--manifest",
        str(workspace / "cohort.json"),
        "--config-id",
        "oracle_aeb",
        "--output-dir",
        str(workspace / "runs"),
        "--split",
        "mini",
    )

    assert result.exit_code != 0
    assert "does not have" in result.output
    assert not (workspace / "runs" / "oracle_aeb").exists()


def test_a_manifest_that_is_not_a_frozen_cohort_is_refused(workspace: Path, tmp_path: Path) -> None:
    """A document with the right name and the wrong shape must not reach the runner."""

    manifest = tmp_path / "not-a-cohort.json"
    manifest.write_text(json.dumps({"scenario_tokens": ["s-0001"]}), encoding="utf-8")

    result = run(
        "simulate",
        "--protocol",
        str(workspace / "protocol.yaml"),
        "--manifest",
        str(manifest),
        "--config-id",
        "oracle_aeb",
        "--output-dir",
        str(workspace / "runs"),
        "--scenario-source",
        "synthetic",
    )

    assert result.exit_code != 0
    assert "cohort manifest" in result.output


def test_the_whole_matrix_can_be_asked_for_in_one_pass(workspace: Path) -> None:
    """`all` is the cheaper path: the reference is computed once per token."""

    synthetic_manifest(workspace)
    result = run(
        "simulate",
        "--protocol",
        str(workspace / "protocol.yaml"),
        "--manifest",
        str(workspace / "cohort.json"),
        "--config-id",
        "all",
        "--output-dir",
        str(workspace / "runs"),
        "--scenario-source",
        "synthetic",
    )

    assert result.exit_code == 0, result.output
    context = json.loads((workspace / "runs" / "run_context.json").read_text(encoding="utf-8"))
    assert context["configuration_id"] == "all"


# --------------------------------------------------------------------------
# evaluate
# --------------------------------------------------------------------------


def _formal_record(configuration: str, token: str, replicate: int, collision: int) -> dict:
    return {
        "schema_version": "aeb-scenario-result/v2",
        "scenario_token": token,
        "family": "lead_or_stopping",
        "configuration_id": configuration,
        "replicate": replicate,
        "valid": True,
        "invalid_reason": None,
        "collision_vru": collision,
        "collision_vehicle": 0,
        "collision_object": 0,
        "collision_energy": float(collision),
        "contacts_not_at_fault": 0,
        "min_ttc_s": None,
        "min_clearance_m": 2.0,
        "missed_interventions": 0,
        "false_interventions": 0,
        "matched_delay_s": [],
        "stop_distance_m": None,
        "max_deceleration_mps2": 1.0,
        "max_abs_jerk_mps3": 2.0,
        "intervention_duration_s": float(collision),
        "simulated_duration_s": 2.0,
    }


def formal_evaluation_fixture(workspace: Path) -> tuple[Path, Path]:
    """A complete 26-cell set; the strict reader never gets a reduced test matrix."""

    manifest = CohortManifestV1.model_validate(cohort_document(("s-0001", "s-0002")))
    manifest_directory = workspace / "manifests"
    manifest_directory.mkdir()
    manifest_path = manifest_directory / "evaluation.json"
    for split in ("smoke", "development", "evaluation"):
        split_manifest = manifest.model_copy(update={"split": split})
        (manifest_directory / f"{split}.json").write_text(
            json.dumps(split_manifest.model_dump(mode="json")), encoding="utf-8"
        )
    eligibility = {
        "schema_version": "aeb-cohort-eligibility/v1",
        "examined": [],
        "scenarios_in_split_by_family": {},
    }
    for split in ("development", "evaluation"):
        (manifest_directory / f"{split}-eligibility.json").write_text(
            json.dumps(eligibility), encoding="utf-8"
        )
    cohort_hash = membership_sha256(manifest)
    results = workspace / "formal"
    for configuration in formal_configurations():
        directory = results / configuration.configuration_id
        directory.mkdir(parents=True)
        for token in ("s-0001", "s-0002"):
            collision = int(
                configuration.configuration_id == "dropout-medium" and token == "s-0002"
            )
            payload = {
                "schema_version": "aeb-token-results/v1",
                "scenario_token": token,
                "family": "lead_or_stopping",
                "split": "evaluation",
                "configuration_id": configuration.configuration_id,
                "protocol_sha256": PROTOCOL_SHA,
                "cohort_manifest_sha256": cohort_hash,
                "valid": True,
                "invalid_reason": None,
                "invalid_phase": None,
                "results": [
                    _formal_record(configuration.configuration_id, token, index, collision)
                    for index in range(3)
                ],
            }
            (directory / f"{token}.json").write_text(json.dumps(payload), encoding="utf-8")
    (results / "run_complete.json").write_text(
        json.dumps(
            {
                "schema_version": "aeb-run-complete/v1",
                "cohort_manifest_sha256": cohort_hash,
                "tokens": ["s-0001", "s-0002"],
            }
        ),
        encoding="utf-8",
    )
    return results, manifest_path


def test_evaluate_validates_a_complete_matrix_and_writes_four_documents(workspace: Path) -> None:
    results, manifest = formal_evaluation_fixture(workspace)
    output = workspace / "metrics"

    result = run(
        "evaluate",
        "--results-dir",
        str(results),
        "--manifest",
        str(manifest),
        "--output-dir",
        str(output),
    )

    assert result.exit_code == 0, result.output
    models: dict[str, type[BaseModel]] = {
        "evaluation.json": AEBEvaluationV1,
        "intervals.json": AEBIntervalsV1,
        "shapley.json": AEBShapleyV1,
        "exclusions.json": AEBExclusionsV1,
    }
    for name, model in models.items():
        model.model_validate_json((output / name).read_text(encoding="utf-8"))
    for name in (
        "smoke.json",
        "development.json",
        "evaluation.json",
        "development-eligibility.json",
        "evaluation-eligibility.json",
    ):
        assert (output / "cohort" / name).read_bytes() == (manifest.parent / name).read_bytes()
    evaluation = json.loads((output / "evaluation.json").read_text(encoding="utf-8"))
    assert len(evaluation["configurations"]) == 26
    assert evaluation["common_valid_tokens"] == 2


def test_evaluate_refuses_a_completion_marker_for_another_manifest(workspace: Path) -> None:
    results, manifest = formal_evaluation_fixture(workspace)
    marker = json.loads((results / "run_complete.json").read_text(encoding="utf-8"))
    marker["cohort_manifest_sha256"] = "f" * 64
    (results / "run_complete.json").write_text(json.dumps(marker), encoding="utf-8")

    result = run(
        "evaluate",
        "--results-dir",
        str(results),
        "--manifest",
        str(manifest),
        "--output-dir",
        str(workspace / "metrics"),
    )

    assert result.exit_code != 0
    assert "completion marker cohort hash" in result.output


def test_evaluate_refuses_corrupt_cohort_metadata_before_writing_output(workspace: Path) -> None:
    results, manifest = formal_evaluation_fixture(workspace)
    (manifest.parent / "development-eligibility.json").write_text("{}", encoding="utf-8")
    output = workspace / "metrics"

    result = run(
        "evaluate",
        "--results-dir",
        str(results),
        "--manifest",
        str(manifest),
        "--output-dir",
        str(output),
    )

    assert result.exit_code != 0
    assert "invalid cohort eligibility document" in result.output
    assert not output.exists()


def test_evaluate_requires_every_published_cohort_metadata_file(workspace: Path) -> None:
    results, manifest = formal_evaluation_fixture(workspace)
    (manifest.parent / "smoke.json").unlink()

    result = run(
        "evaluate",
        "--results-dir",
        str(results),
        "--manifest",
        str(manifest),
        "--output-dir",
        str(workspace / "metrics"),
    )

    assert result.exit_code != 0
    assert "required cohort metadata" in result.output


def test_evaluate_refuses_to_copy_an_evaluation_other_than_the_analysis_manifest(
    workspace: Path,
) -> None:
    results, manifest = formal_evaluation_fixture(workspace)
    analysis_manifest = manifest.with_name("analysis-input.json")
    analysis_document = json.loads(manifest.read_text(encoding="utf-8"))
    analysis_document["log_names"] = ["different-log.db"]
    analysis_manifest.write_text(json.dumps(analysis_document), encoding="utf-8")
    output = workspace / "metrics"

    result = run(
        "evaluate",
        "--results-dir",
        str(results),
        "--manifest",
        str(analysis_manifest),
        "--output-dir",
        str(output),
    )

    assert result.exit_code != 0
    assert (
        "copied evaluation manifest does not match the supplied analysis manifest" in result.output
    )
    assert not output.exists()


def test_evaluate_refuses_a_manifest_whose_filename_and_declared_split_disagree(
    workspace: Path,
) -> None:
    results, manifest = formal_evaluation_fixture(workspace)
    development = manifest.with_name("development.json")
    development_document = json.loads(development.read_text(encoding="utf-8"))
    development_document["split"] = "smoke"
    development.write_text(json.dumps(development_document), encoding="utf-8")
    output = workspace / "metrics"

    result = run(
        "evaluate",
        "--results-dir",
        str(results),
        "--manifest",
        str(manifest),
        "--output-dir",
        str(output),
    )

    assert result.exit_code != 0
    assert "development.json declares split 'smoke'; expected 'development'" in result.output
    assert not output.exists()


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def test_report_builds_a_page(workspace: Path) -> None:
    """The last stage, reading what the earlier ones wrote."""

    result = run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code == 0, result.output
    assert (workspace / "site" / "index.html").is_file()


def test_the_page_keeps_the_configuration_groups_apart(workspace: Path) -> None:
    """An imported calibration error and a chosen severity are different numbers.

    Presenting them in one table would invite a reader to compare a measurement
    against a study parameter as though they were the same kind of thing.
    """

    run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )
    page = (workspace / "site" / "index.html").read_text(encoding="utf-8")

    for heading in ("Baseline", "Single Channel", "Coalition", "Imported"):
        assert heading in page


def test_report_leads_with_question_observations_figures_and_replays(
    workspace: Path,
) -> None:
    run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )
    page = (workspace / "site" / "index.html").read_text(encoding="utf-8")

    headings = [
        page.index(label)
        for label in ("Research question", "Observed results", "Figures", "Replays")
    ]
    assert headings == sorted(headings)
    assert '<details id="complete-trace">' in page
    assert "Scenario-replicates" in page
    assert "contacts_not_at_fault" in page
    assert "<script" not in page

    observed = page[
        page.index('<section id="observations">') : page.index('<section id="figures">')
    ]
    assert "coalition-none" in observed
    assert "coalition-dropout+localization_shape+latency+track_instability" in observed
    assert "stopped" in page
    assert "closing velocity" in page
    assert "legal responsibility" in page
    assert "position differences" in page
    assert "no uncertainty interval" in page
    assert "nuBoard" in page
    assert "per-distance" in page

    appendix = page[page.index('<details id="complete-trace">') :]
    assert appendix.count("contacts_not_at_fault") >= 4
    assert appendix.count("Measured exposure (s)") >= 4


def test_report_copies_derived_figures_and_replays_with_relative_links(workspace: Path) -> None:
    figures = workspace / "figures"
    figures.mkdir()
    (figures / "shapley-contributions.svg").write_text("<svg>shapley</svg>\n", encoding="utf-8")
    (figures / "intervention-rates-by-family.svg").write_text(
        "<svg>families</svg>\n", encoding="utf-8"
    )
    (figures / "error-severity-sensitivity.svg").write_text(
        "<svg>severity</svg>\n", encoding="utf-8"
    )
    replays = workspace / "artifacts" / "replays"
    replays.mkdir()
    (replays / "lead_or_stopping--oracle_aeb.html").write_text(
        "<!doctype html><title>replay</title>\n", encoding="utf-8"
    )

    run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )
    page = (workspace / "site" / "index.html").read_text(encoding="utf-8")

    assert 'src="figures/shapley-contributions.svg"' in page
    assert 'src="figures/error-severity-sensitivity.svg"' in page
    assert (workspace / "site" / "figures" / "error-severity-sensitivity.svg").read_text() == (
        "<svg>severity</svg>\n"
    )
    assert 'href="replays/lead_or_stopping--oracle_aeb.html"' in page
    assert (workspace / "site" / "figures" / "shapley-contributions.svg").read_text() == (
        "<svg>shapley</svg>\n"
    )
    assert (workspace / "site" / "replays" / "lead_or_stopping--oracle_aeb.html").is_file()
    assert "grid-template-columns: 1fr" in page


def test_report_exposes_the_family_sample_shortfall_without_an_efficacy_claim(
    workspace: Path,
) -> None:
    run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )
    page = (workspace / "site" / "index.html").read_text(encoding="utf-8")

    assert "0 valid tokens" in page
    assert "target of 100" in page
    assert "sample shortfall" in page


def test_report_refuses_an_actual_family_document_that_breaks_its_strict_contract(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    source = repository / "docs" / "evidence" / "nuplan_aeb_v2"
    artifacts = tmp_path / "evidence"
    artifacts.mkdir()
    for name in ("evaluation.json", "family-interventions.json"):
        shutil.copyfile(source / name, artifacts / name)
    family_path = artifacts / "family-interventions.json"
    family = json.loads(family_path.read_text(encoding="utf-8"))
    bicycle_oracle = next(
        row
        for row in family["rows"]
        if row["family"] == "bicycle_or_vru" and row["configuration_id"] == "oracle_aeb"
    )
    bicycle_oracle["valid_tokens"] = 45
    family_path.write_text(json.dumps(family), encoding="utf-8")

    result = run(
        "report",
        "--claims",
        str(repository / "docs" / "claims.yaml"),
        "--artifacts-dir",
        str(artifacts),
        "--output-dir",
        str(tmp_path / "site"),
    )

    assert result.exit_code != 0
    assert "family intervention evidence is invalid" in result.output
    assert not (tmp_path / "site" / "index.html").exists()


def test_report_refuses_schema_valid_family_values_that_disagree_with_registry(
    workspace: Path,
) -> None:
    family_path = workspace / "artifacts" / "family-interventions.json"
    family = json.loads(family_path.read_text(encoding="utf-8"))
    lead_oracle = family["rows"][0]
    lead_oracle["false_interventions"] = 1
    lead_oracle["false_per_1000_scenario_replicates"] = 1000.0 / 6.0
    family_path.write_text(json.dumps(family), encoding="utf-8")

    result = run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code != 0
    assert "family-interventions.json disagrees with the claim registry" in result.output
    assert not (workspace / "site" / "index.html").exists()


def test_report_refuses_schema_valid_evaluation_values_that_disagree_with_registry(
    workspace: Path,
) -> None:
    evaluation_path = workspace / "artifacts" / "evaluation.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["configurations"][0]["contacts_not_at_fault"] = 1
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")

    result = run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code != 0
    assert "evaluation.json disagrees with the claim registry" in result.output
    assert not (workspace / "site" / "index.html").exists()


@pytest.mark.parametrize("field", ["protocol_sha256", "cohort_manifest_sha256"])
def test_report_refuses_family_provenance_that_disagrees_with_registry(
    workspace: Path,
    field: str,
) -> None:
    family_path = workspace / "artifacts" / "family-interventions.json"
    family = json.loads(family_path.read_text(encoding="utf-8"))
    family[field] = "2" * 64
    family_path.write_text(json.dumps(family), encoding="utf-8")

    result = run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code != 0
    assert "family-interventions.json disagrees with the claim registry" in result.output
    assert not (workspace / "site" / "index.html").exists()


@pytest.mark.parametrize("registry_fault", ["missing", "extra", "ambiguous", "duplicate"])
def test_report_requires_one_complete_unambiguous_family_claim_set(
    workspace: Path,
    registry_fault: str,
) -> None:
    claims_path = workspace / "claims.yaml"
    registry = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    family_claims = [
        claim
        for claim in registry["claims"]
        if Path(claim["artifact_path"]).name == "family-interventions.json"
    ]
    if registry_fault == "missing":
        registry["claims"].remove(family_claims[0])
    elif registry_fault == "extra":
        extra = dict(family_claims[0])
        extra["claim_id"] = "p3.family-interventions.unregistered-extra"
        registry["claims"].append(extra)
    elif registry_fault == "ambiguous":
        family_claims[0]["artifact_path"] = "alternate/family-interventions.json"
    else:
        registry["claims"].append(dict(family_claims[0]))
    claims_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")

    result = run(
        "report",
        "--claims",
        str(claims_path),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code != 0
    assert "family-interventions.json disagrees with the claim registry" in result.output
    assert not (workspace / "site" / "index.html").exists()


def test_report_finds_a_partial_figure_set_beside_a_nested_docs_evidence_dir(
    workspace: Path,
) -> None:
    from aebrisk.report.builder import build_site

    artifacts = workspace / "docs" / "evidence" / "run"
    artifacts.mkdir(parents=True)
    for name in ("evaluation.json", "family-interventions.json"):
        shutil.copyfile(workspace / "artifacts" / name, artifacts / name)
    figures = workspace / "docs" / "figures"
    figures.mkdir()
    (figures / "shapley-contributions.svg").write_text("<svg/>\n", encoding="utf-8")

    build_site(workspace / "claims.yaml", artifacts, workspace / "nested-site")

    assert (workspace / "nested-site" / "figures" / "shapley-contributions.svg").is_file()
    assert not (workspace / "nested-site" / "figures" / "intervention-rates-by-family.svg").exists()


def test_the_page_always_shows_the_exclusions(workspace: Path) -> None:
    """A study that reported only what succeeded would report a chosen cohort."""

    run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )
    page = (workspace / "site" / "index.html").read_text(encoding="utf-8")

    assert "Cohort exclusions" in page
    assert "1 scenario(s) were excluded" in page


def test_the_page_states_every_claim(workspace: Path) -> None:
    """A number on the page with no claim behind it is a number nobody owns."""

    run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )
    page = (workspace / "site" / "index.html").read_text(encoding="utf-8")

    assert "p3.baseline.scenarios.no_aeb" in page
    assert "no_aeb scenarios is 6" in page


def test_a_claim_missing_its_artifact_is_refused(workspace: Path) -> None:
    """A claim that names no evidence is an assertion, not a claim."""

    claims = workspace / "bad.yaml"
    claims.write_text(
        "claim_required_fields: [claim_id, text, evidence_type, artifact_path, status]\n"
        "claims:\n"
        "  - claim_id: unsupported\n"
        "    text: something\n"
        "    evidence_type: observed\n"
        "    status: draft\n",
        encoding="utf-8",
    )

    result = run(
        "report",
        "--claims",
        str(claims),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code != 0


def test_the_page_is_byte_identical_between_builds(workspace: Path) -> None:
    """A report that differed between builds could not be checked by rebuilding it."""

    for name in ("one", "two"):
        run(
            "report",
            "--claims",
            str(workspace / "claims.yaml"),
            "--artifacts-dir",
            str(workspace / "artifacts"),
            "--output-dir",
            str(workspace / name),
        )

    assert (workspace / "one" / "index.html").read_bytes() == (
        workspace / "two" / "index.html"
    ).read_bytes()


def test_the_page_pins_its_line_ending(workspace: Path) -> None:
    """Every artifact this portfolio writes fixes its bytes."""

    run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert b"\r\n" not in (workspace / "site" / "index.html").read_bytes()


def test_a_report_with_no_results_still_builds(workspace: Path) -> None:
    """The claims are the report. Missing results are shown as missing, not fatal."""

    empty = workspace / "empty-artifacts"
    empty.mkdir()

    result = run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(empty),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code == 0, result.output
    assert "No results in this group." in (workspace / "site" / "index.html").read_text(
        encoding="utf-8"
    )


def test_a_missing_artifacts_directory_is_refused(workspace: Path) -> None:
    """Building a report over a directory that is not there would report nothing."""

    result = run(
        "report",
        "--claims",
        str(workspace / "claims.yaml"),
        "--artifacts-dir",
        str(workspace / "absent"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code != 0
    assert "artifacts directory" in result.output


def test_a_claims_file_with_no_claims_is_refused(workspace: Path) -> None:
    """An empty claims list is not a report with nothing to say; it is a mistake."""

    empty = workspace / "no-claims.yaml"
    empty.write_text("claims: []\n", encoding="utf-8")

    result = run(
        "report",
        "--claims",
        str(empty),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code != 0


def test_preflight_reports_a_valid_installation(tmp_path: Path) -> None:
    """The success path, built from a directory shaped like a nuPlan mini split.

    No database is opened: preflight exists to answer in a second, and that is
    exactly what makes it checkable without the real data the order gate still
    forbids.
    """

    root = tmp_path / "nuplan"
    split = root / "nuplan-v1.1" / "splits" / "mini"
    split.mkdir(parents=True)
    (split / "2021.05.12.00.00.00_veh-01_00000_00001.db").write_bytes(b"")
    (root / "maps").mkdir()

    result = run(
        "data",
        "preflight",
        "--db-root",
        str(root),
        "--map-root",
        str(root / "maps"),
        "--split",
        "mini",
        "--output",
        str(tmp_path / "preflight.json"),
    )

    assert result.exit_code == 0, result.output
    report_document = json.loads((tmp_path / "preflight.json").read_text(encoding="utf-8"))
    assert report_document["log_database_count"] == 1
    assert report_document["split"] == "mini"


def test_preflight_refuses_a_map_root_it_will_not_read(tmp_path: Path) -> None:
    """`--map-root` is a check, not a second source of truth.

    The adapter derives the maps root from the data root. An operator who
    mounted maps somewhere else has a configuration this study will silently
    ignore, and finding that out in a second is the whole purpose of preflight.
    """

    root = tmp_path / "nuplan"
    split = root / "nuplan-v1.1" / "splits" / "mini"
    split.mkdir(parents=True)
    (split / "2021.05.12.00.00.00_veh-01_00000_00001.db").write_bytes(b"")
    (root / "maps").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = run(
        "data",
        "preflight",
        "--db-root",
        str(root),
        "--map-root",
        str(elsewhere),
        "--split",
        "mini",
        "--output",
        str(tmp_path / "preflight.json"),
    )

    assert result.exit_code != 0
    assert "map root" in result.output


@pytest.mark.parametrize(
    ("option", "value"),
    [("--scenario-source", "guesswork"), ("--protocol", "absent.yaml")],
)
def test_simulate_refuses_an_impossible_argument(
    workspace: Path,
    option: str,
    value: str,
) -> None:
    """Each argument is checked before anything long-running starts."""

    arguments = {
        "--protocol": str(workspace / "protocol.yaml"),
        "--manifest": str(workspace / "cohort.json"),
        "--config-id": "oracle_aeb",
        "--output-dir": str(workspace / "runs"),
        "--scenario-source": "synthetic",
    }
    arguments[option] = value if option != "--protocol" else str(workspace / value)

    flat: list[str] = ["simulate"]
    for name, argument in arguments.items():
        flat += [name, argument]

    assert run(*flat).exit_code != 0


def test_simulate_refuses_a_missing_manifest(workspace: Path) -> None:
    """The cohort is what makes the comparison controlled; without it there is none."""

    result = run(
        "simulate",
        "--protocol",
        str(workspace / "protocol.yaml"),
        "--manifest",
        str(workspace / "absent.json"),
        "--config-id",
        "oracle_aeb",
        "--output-dir",
        str(workspace / "runs"),
        "--scenario-source",
        "synthetic",
    )

    assert result.exit_code != 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("evidence_type", "vibes"), ("status", "probably")],
)
def test_a_claim_outside_the_registry_vocabulary_is_refused(
    workspace: Path,
    field: str,
    value: str,
) -> None:
    """The registry declares what an evidence type and a status may be.

    Checking against the registry rather than against a list in the code means
    a vocabulary change is one edit to a committed file, and no second list can
    disagree with it.
    """

    claims = workspace / f"bad-{field}.yaml"
    registry = yaml.safe_load((workspace / "claims.yaml").read_text(encoding="utf-8"))
    registry["claims"][0][field] = value
    claims.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")

    result = run(
        "report",
        "--claims",
        str(claims),
        "--artifacts-dir",
        str(workspace / "artifacts"),
        "--output-dir",
        str(workspace / "site"),
    )

    assert result.exit_code != 0
    assert field in result.output


@pytest.mark.parametrize("resume", [False, True])
def test_a_resolved_cohort_is_run_and_written(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, resume: bool
) -> None:
    """The command's own wiring: resolve, run, write, and say what it wrote.

    The cohort and the simulation are replaced here because CI has no licensed
    log to read; what is under test is that `simulate` joins them, writes
    through the real writer, and reports the counts an operator reads off the
    end of a run that took hours. The join over a real recording is exercised by
    `tests/integration/test_nuplan_mini_adapter.py`, which skips without data.
    """

    from aebrisk.artifacts.results import AEBScenarioResultV2
    from aebrisk.cli.simulate import RunContext, container_digest, write_run_context
    from aebrisk.cohort.manifest import load_manifest, membership_sha256
    from aebrisk.simulation.orchestrate import TokenRun, write_token_run

    mounted_split(tmp_path, monkeypatch)

    def record(token: str) -> AEBScenarioResultV2:
        return AEBScenarioResultV2(
            schema_version="aeb-scenario-result/v2",
            simulated_duration_s=1.0,
            scenario_token=token,
            family="lead_or_stopping",
            configuration_id="oracle_aeb",
            replicate=0,
            valid=True,
            invalid_reason=None,
            collision_vru=0,
            collision_vehicle=0,
            collision_object=0,
            collision_energy=0.0,
            contacts_not_at_fault=0,
            min_ttc_s=2.0,
            min_clearance_m=1.0,
            missed_interventions=0,
            false_interventions=0,
            matched_delay_s=(),
            stop_distance_m=None,
            max_deceleration_mps2=3.0,
            max_abs_jerk_mps3=4.0,
            intervention_duration_s=0.5,
        )

    from aebrisk.nuplan_adapter.query_scenario import ScenarioReference
    from aebrisk.simulation.orchestrate import CohortScenario

    cohort = tuple(
        CohortScenario(
            ScenarioReference("one.db", token, "stopping_with_lead", 123), "lead_or_stopping"
        )
        for token in ("s-0001", "s-0002")
    )
    monkeypatch.setattr("aebrisk.cli.simulate.resolve_cohort", lambda manifest, layout: cohort)
    preserved = b""
    if resume:
        document = load_manifest(workspace / "cohort.json")
        write_run_context(
            RunContext(
                "oracle_aeb",
                PROTOCOL_SHA,
                membership_sha256(document),
                container_digest(),
                os.environ.get("AEBRISK_COMMIT", "0" * 40),
            ),
            workspace / "runs" / "run_context.json",
        )
        write_token_run(
            TokenRun("s-0001", "lead_or_stopping", (record("s-0001"),), None),
            load_manifest(workspace / "cohort.json"),
            workspace / "runs",
            written_configurations=("oracle_aeb",),
            protocol_sha256=PROTOCOL_SHA,
            cohort_manifest_sha256=membership_sha256(document),
        )
        preserved = (workspace / "runs" / "oracle_aeb" / "s-0001.json").read_bytes()
    monkeypatch.setattr(
        "aebrisk.cli.simulate.run_cohort",
        lambda cohort, configurations, protocol_hash, protocol, on_token, workers=1: tuple(
            on_token(run) or run
            for run in (
                TokenRun(
                    token=token,
                    family="lead_or_stopping",
                    results=(record(token),),
                    invalid=None,
                )
                for token in (scenario.reference.token for scenario in cohort)
            )
        ),
    )

    result = run(
        "simulate",
        "--protocol",
        str(workspace / "protocol.yaml"),
        "--manifest",
        str(workspace / "cohort.json"),
        "--config-id",
        "oracle_aeb",
        "--output-dir",
        str(workspace / "runs"),
        "--split",
        "mini",
        *(("--resume",) if resume else ()),
    )

    assert result.exit_code == 0, result.output
    assert "resolved 2 tokens" in result.output
    if resume:
        assert "s-0001 lead_or_stopping ok" not in result.output
        assert "wrote 1 result documents: 1 tokens, 1 valid, 0 invalid, 1 records" in result.output
        assert (workspace / "runs" / "oracle_aeb" / "s-0001.json").read_bytes() == preserved
    else:
        assert "s-0001 lead_or_stopping ok" in result.output
        assert "wrote 2 result documents: 2 tokens, 2 valid, 0 invalid, 2 records" in result.output
    assert (workspace / "runs" / "oracle_aeb" / "s-0001.json").is_file()
    assert json.loads((workspace / "runs" / "run_complete.json").read_text())["tokens"] == [
        "s-0001",
        "s-0002",
    ]


def synthetic_manifest(workspace: Path) -> None:
    document = cohort_document(("synthetic-lead-0001",))
    document.update(split="smoke", log_names=["synthetic"])
    (workspace / "cohort.json").write_text(json.dumps(document), encoding="utf-8")


def simulate_synthetic(workspace: Path, *extra: str):
    return run(
        "simulate",
        "--protocol",
        str(workspace / "protocol.yaml"),
        "--manifest",
        str(workspace / "cohort.json"),
        "--config-id",
        "oracle_aeb",
        "--output-dir",
        str(workspace / "runs"),
        "--scenario-source",
        "synthetic",
        *extra,
    )


def test_cli_writes_a_token_before_the_runner_returns(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aebrisk.simulation.orchestrate import run_cohort

    synthetic_manifest(workspace)

    def checked_runner(*args, **kwargs):
        results = run_cohort(*args, **kwargs)
        assert (workspace / "runs" / "oracle_aeb" / "synthetic-lead-0001.json").is_file()
        assert not (workspace / "runs" / "run_complete.json").exists()
        return results

    monkeypatch.setattr("aebrisk.cli.simulate.run_cohort", checked_runner)
    result = simulate_synthetic(workspace, "--workers", "2")
    assert result.exit_code == 0, result.exception


@pytest.mark.parametrize("resume", [False, True])
def test_resume_refuses_completed_run_without_overwriting_context(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    synthetic_manifest(workspace)
    output = workspace / "runs"
    output.mkdir()
    (output / "run_complete.json").write_bytes(b"completion evidence")
    (output / "run_context.json").write_bytes(b"original context")

    def interrupted_replacement(*args, **kwargs):
        (output / "partial-replacement.json").write_bytes(b"replacement started")
        raise RuntimeError("replacement interrupted")

    monkeypatch.setattr("aebrisk.cli.simulate.run_cohort", interrupted_replacement)
    result = simulate_synthetic(workspace, *(("--resume",) if resume else ()))
    assert result.exit_code != 0
    assert "this run is already complete" in result.output
    assert (output / "run_context.json").read_bytes() == b"original context"
    assert (output / "run_complete.json").read_bytes() == b"completion evidence"
    assert not (output / "partial-replacement.json").exists()


def test_resume_with_all_files_present_only_finishes_marker(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic_manifest(workspace)
    assert simulate_synthetic(workspace).exit_code == 0
    output = workspace / "runs"
    (output / "run_complete.json").unlink(missing_ok=True)
    evidence = {p.relative_to(output): p.read_bytes() for p in output.rglob("*.json")}

    def unexpected_run(*args, **kwargs):
        pytest.fail("a finished token must not be simulated again")

    monkeypatch.setattr("aebrisk.cli.simulate.run_cohort", unexpected_run)
    result = simulate_synthetic(workspace, "--resume")
    assert result.exit_code == 0, result.exception
    assert (output / "run_complete.json").is_file()
    assert {p: (output / p).read_bytes() for p in evidence} == evidence


def test_resume_runs_tokens_missing_any_configuration(workspace: Path) -> None:
    synthetic_manifest(workspace)
    result = simulate_synthetic(workspace, "--resume")
    assert result.exit_code == 0, result.exception
    assert (workspace / "runs" / "oracle_aeb" / "synthetic-lead-0001.json").is_file()
    assert (workspace / "runs" / "run_complete.json").is_file()


def test_workers_zero_is_refused_even_for_dry_run(workspace: Path) -> None:
    result = simulate_synthetic(workspace, "--workers", "0", "--dry-run")
    assert result.exit_code != 0
    assert not (workspace / "runs").exists()


def saved_bytes(workspace: Path) -> dict[Path, bytes]:
    return {
        p.relative_to(workspace / "runs"): p.read_bytes()
        for p in (workspace / "runs").rglob("*")
        if p.is_file()
    }


def interrupted_synthetic_run(workspace: Path) -> Path:
    synthetic_manifest(workspace)
    result = simulate_synthetic(workspace)
    assert result.exit_code == 0, result.exception
    (workspace / "runs" / "run_complete.json").unlink()
    return workspace / "runs"


@pytest.mark.parametrize(
    "field",
    [
        "configuration_id",
        "protocol_sha256",
        "cohort_sha256",
        "commit",
        "container_digest",
        "missing",
        "malformed",
        "unknown-field",
        "not-object",
    ],
)
def test_resume_refuses_context_drift_without_changing_evidence(
    workspace: Path, field: str
) -> None:
    output = interrupted_synthetic_run(workspace)
    path = output / "run_context.json"
    context = json.loads(path.read_bytes())
    if field == "missing":
        del context["protocol_sha256"]
    elif field == "unknown-field":
        context["unrecognized"] = "value"
    elif field == "not-object":
        context = []
    elif field != "malformed":
        context[field] = "different"
    path.write_bytes(b"{truncated" if field == "malformed" else json.dumps(context).encode())
    before = saved_bytes(workspace)
    result = simulate_synthetic(workspace, "--resume")
    assert result.exit_code != 0
    assert "resume context" in result.output
    assert saved_bytes(workspace) == before


def test_resume_refuses_evidence_without_context(workspace: Path) -> None:
    output = interrupted_synthetic_run(workspace)
    (output / "run_context.json").unlink()
    before = saved_bytes(workspace)
    result = simulate_synthetic(workspace, "--resume")
    assert result.exit_code != 0
    assert "resume context" in result.output
    assert saved_bytes(workspace) == before


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "scenario_token",
        "family",
        "configuration_id",
        "split",
        "protocol_sha256",
        "cohort_manifest_sha256",
        "missing",
        "malformed",
        "valid-type",
        "record-token",
        "record-family",
        "record-configuration",
    ],
)
def test_resume_refuses_unrelated_or_malformed_token_documents(workspace: Path, field: str) -> None:
    output = interrupted_synthetic_run(workspace)
    path = output / "oracle_aeb" / "synthetic-lead-0001.json"
    document = json.loads(path.read_bytes())
    if field == "missing":
        del document["protocol_sha256"]
    elif field == "valid-type":
        document["valid"] = "true"
    elif field.startswith("record-"):
        key, value = {
            "record-token": ("scenario_token", "another-token"),
            "record-family": ("family", "bicycle_or_vru"),
            "record-configuration": ("configuration_id", "no_aeb"),
        }[field]
        document["results"][0][key] = value
    elif field != "malformed":
        document[field] = {
            "schema_version": "aeb-token-results/v0",
            "scenario_token": "another-token",
            "family": "bicycle_or_vru",
            "configuration_id": "no_aeb",
            "split": "evaluation",
            "protocol_sha256": "b" * 64,
            "cohort_manifest_sha256": "b" * 64,
        }[field]
    path.write_bytes(b"{truncated" if field == "malformed" else json.dumps(document).encode())
    before = saved_bytes(workspace)
    result = simulate_synthetic(workspace, "--resume")
    assert result.exit_code != 0
    assert "resume token" in result.output
    assert saved_bytes(workspace) == before


@pytest.mark.parametrize("drift", ["token", "family", "source"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_synthetic_manifest_drift_is_refused_before_publication(
    workspace: Path, drift: str, dry_run: bool
) -> None:
    synthetic_manifest(workspace)
    path = workspace / "cohort.json"
    manifest = json.loads(path.read_bytes())
    if drift == "token":
        manifest["families"]["lead_or_stopping"] = ["different-token"]
    elif drift == "family":
        manifest["families"]["bicycle_or_vru"] = manifest["families"]["lead_or_stopping"]
        manifest["families"]["lead_or_stopping"] = []
    else:
        manifest["log_names"] = ["one.db"]
    path.write_text(json.dumps(manifest))
    output = workspace / "runs"
    output.mkdir()
    (output / "run_context.json").write_bytes(b"existing evidence")
    before = saved_bytes(workspace)
    result = simulate_synthetic(workspace, *(("--dry-run",) if dry_run else ()))
    assert result.exit_code != 0
    assert saved_bytes(workspace) == before


@pytest.mark.parametrize("resume", [False, True])
def test_protocol_bytes_must_match_manifest_before_any_publication(
    workspace: Path, resume: bool
) -> None:
    interrupted_synthetic_run(workspace)
    (workspace / "protocol.yaml").write_text("different protocol\n")
    before = saved_bytes(workspace)
    result = simulate_synthetic(workspace, *(("--resume",) if resume else ()))
    assert result.exit_code != 0
    assert "protocol hash" in result.output
    assert saved_bytes(workspace) == before


@pytest.mark.parametrize("changed_input", ["protocol", "cohort", "configuration"])
def test_resume_cannot_certify_old_files_under_new_inputs(
    workspace: Path, changed_input: str
) -> None:
    interrupted_synthetic_run(workspace)
    arguments: tuple[str, ...] = ("--resume",)
    manifest_path = workspace / "cohort.json"
    document = json.loads(manifest_path.read_bytes())
    if changed_input == "protocol":
        new_protocol = b"protocol: different-version\n"
        (workspace / "protocol.yaml").write_bytes(new_protocol)
        document["protocol_sha256"] = hashlib.sha256(new_protocol).hexdigest()
    elif changed_input == "cohort":
        document["split"] = "evaluation"
    else:
        arguments += ("--config-id", "no_aeb")
    manifest_path.write_text(json.dumps(document))
    before = saved_bytes(workspace)
    result = simulate_synthetic(workspace, *arguments)
    assert result.exit_code != 0
    assert "resume context" in result.output
    assert saved_bytes(workspace) == before
