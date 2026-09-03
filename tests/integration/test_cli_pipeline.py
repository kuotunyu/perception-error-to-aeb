"""The command line, end to end, on synthetic inputs.

Every other test in this project calls a function. This one runs the commands a
person actually types, in the order they type them, and checks that the files
one stage writes are the files the next stage reads. That seam is where a
pipeline breaks, and it breaks silently: each stage passes its own tests while
producing something the next one cannot use.

THE INPUTS ARE SYNTHETIC AND NOTHING HERE IS A RESULT. The portfolio order gate
forbids reading real nuPlan data until `bev-calibration-lab` is released, and
this file exists precisely so the pipeline can be shown to work before then.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aebrisk.cli.app import app


def run(*arguments: str):
    return CliRunner().invoke(app, list(arguments))


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A protocol, a cohort manifest, an evaluation and a claims file."""

    protocol = tmp_path / "protocol.yaml"
    protocol.write_text("protocol: nuplan_aeb_v1\n", encoding="utf-8")

    manifest = tmp_path / "cohort.json"
    manifest.write_text(json.dumps({"scenario_tokens": ["s-0002", "s-0001"]}), encoding="utf-8")

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evaluation.json").write_text(
        json.dumps(
            {
                "configurations": [
                    {"configuration_id": "no_aeb", "scenarios": 2, "collisions": 2},
                    {"configuration_id": "oracle_aeb", "scenarios": 2, "collisions": 0},
                    {"configuration_id": "dropout-medium", "scenarios": 2, "collisions": 1},
                    {
                        "configuration_id": "coalition-dropout+latency",
                        "scenarios": 2,
                        "collisions": 1,
                    },
                    {
                        "configuration_id": "calibration_imported_0123456789abcdef",
                        "scenarios": 2,
                        "collisions": 1,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "exclusions.json").write_text(
        json.dumps({"excluded": [{"scenario_token": "s-0003", "phase": "step"}]}),
        encoding="utf-8",
    )

    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "allowed_evidence_types: [observed, derived, synthetic, illustrative]\n"
        "allowed_statuses: [draft, verified, rejected, superseded]\n"
        "claim_required_fields: [claim_id, text, evidence_type, artifact_path, status]\n"
        "claims:\n"
        "  - claim_id: synthetic-pipeline\n"
        "    text: the pipeline runs end to end on synthetic inputs\n"
        "    evidence_type: synthetic\n"
        "    artifact_path: artifacts/evaluation.json\n"
        "    status: draft\n",
        encoding="utf-8",
    )
    return tmp_path


# --------------------------------------------------------------------------
# simulate
# --------------------------------------------------------------------------


def test_simulate_writes_a_run_context(workspace: Path) -> None:
    """The first stage names what it ran, which every later stage depends on."""

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


def test_the_cohort_hash_does_not_depend_on_the_manifest_order(workspace: Path) -> None:
    """A cohort is a set. Two manifests listing it differently are one cohort."""

    reversed_manifest = workspace / "reversed.json"
    reversed_manifest.write_text(
        json.dumps({"scenario_tokens": ["s-0001", "s-0002"]}), encoding="utf-8"
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
            "synthetic",
        )

    first = json.loads((workspace / "a" / "run_context.json").read_text(encoding="utf-8"))
    second = json.loads((workspace / "b" / "run_context.json").read_text(encoding="utf-8"))
    assert first["cohort_sha256"] == second["cohort_sha256"]


def test_the_nuplan_source_is_closed_until_the_order_gate_opens(workspace: Path) -> None:
    """Refusing loudly beats reading a database this study may not read yet."""

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
    assert "order gate" in result.output


# --------------------------------------------------------------------------
# evaluate
# --------------------------------------------------------------------------


def test_evaluate_reads_a_run_index_and_writes_a_summary(workspace: Path) -> None:
    """Reading an index rather than scanning a directory makes what ran a decision."""

    index = workspace / "runs.json"
    index.write_text(json.dumps({"runs": ["oracle_aeb", "no_aeb"]}), encoding="utf-8")

    result = run("evaluate", "--run-index", str(index), "--output-dir", str(workspace / "metrics"))

    assert result.exit_code == 0, result.output
    written = json.loads((workspace / "metrics" / "evaluation.json").read_text(encoding="utf-8"))
    assert written["evaluated_runs"] == ["no_aeb", "oracle_aeb"]


def test_an_index_naming_no_runs_is_refused(workspace: Path) -> None:
    """An empty evaluation would produce a report with no data and no error."""

    index = workspace / "empty.json"
    index.write_text(json.dumps({"runs": []}), encoding="utf-8")

    result = run("evaluate", "--run-index", str(index), "--output-dir", str(workspace / "metrics"))

    assert result.exit_code != 0


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

    assert "synthetic-pipeline" in page
    assert "the pipeline runs end to end on synthetic inputs" in page


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

    fields = {
        "claim_id": "out-of-vocabulary",
        "text": "something",
        "evidence_type": "synthetic",
        "artifact_path": "artifacts/evaluation.json",
        "status": "draft",
    }
    fields[field] = value

    claims = workspace / f"bad-{field}.yaml"
    claims.write_text(
        "allowed_evidence_types: [observed, derived, synthetic, illustrative]\n"
        "allowed_statuses: [draft, verified, rejected, superseded]\n"
        "claim_required_fields: [claim_id, text, evidence_type, artifact_path, status]\n"
        "claims:\n"
        + "".join(
            f"  {'-' if name == 'claim_id' else ' '} {name}: {item}\n"
            for name, item in fields.items()
        ),
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
    assert "registry" in result.output
