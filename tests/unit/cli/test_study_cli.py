"""`aeb-risk study simulate`: one arm of the policy v2 study, run as the study file says.

An arm is the released runner with the arm's three factors on the study's
cells, so these tests hold the command to three things. Arm A, which sets no
factor, writes the bytes the released `simulate` command writes. Every arm's
run context names the study file, the arm, and each input and environment the
gates check. And the command refuses, before anything is simulated, to write
into the released or published records, to rerun a finished arm, to resume one
arm's directory as another's, to run a formal arm on another cohort or without
the commit and image that identify it, and to run a pilot anywhere but on the
smoke manifest into `artifacts/pilot/`.

The scenarios are synthetic, so nothing here reads a licensed log and nothing
here is a result. A formal run (without `--pilot`) sets `AEBRISK_COMMIT` and
`AEBRISK_IMAGE_DIGEST`, and reads a study file written for the synthetic
manifest, because the committed study file records the released cohort.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy
import pytest
import yaml
from typer.testing import CliRunner

from aebrisk.cli.app import app
from aebrisk.cohort.manifest import load_manifest, membership_sha256

ROOT = Path(__file__).resolve().parents[3]
COMMITTED_STUDY = ROOT / "configs" / "experiments" / "aeb_policy_v2_study.yaml"

PROTOCOL_TEXT = "protocol: nuplan_aeb_v2\n"
PROTOCOL_SHA = hashlib.sha256(PROTOCOL_TEXT.encode("utf-8")).hexdigest()
TOKEN = "synthetic-lead-0001"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "sha256:" + "d" * 64

FULL_COALITION = "coalition-dropout+localization_shape+latency+track_instability"
STUDY_CELLS = (
    "no_aeb",
    "oracle_aeb",
    "dropout-medium",
    "localization_shape-medium",
    "latency-medium",
    "track_instability-medium",
    "coalition-none",
    FULL_COALITION,
)
ARM_E_CELLS = ("no_aeb", "oracle_aeb", "dropout-medium", FULL_COALITION)

#: Each arm's policy, keying and velocity estimate, as the study file sets them.
ARM_FACTORS = {
    "A-v1-replication": ("v1", "dropout-keyed", "finite-difference"),
    "B-v2-gated": ("v2", "dropout-keyed", "finite-difference"),
    "C-v1-kalman": ("v1", "dropout-keyed", "cv-kalman"),
    "D-v2-kalman": ("v2", "dropout-keyed", "cv-kalman"),
    "E-v2-channel-rng": ("v2", "channel-independent", "finite-difference"),
}

#: The SHA-256 of the committed configs an arm reads, as the study file records them.
POLICY_SHA256 = {
    "v1": "263c4eebf718c8d7b748e4d0ad2d93af6654f17c8ce43a3a4d1144f48547564e",
    "v2": "3ac8c230c2a9df4560eed600e4b6145322449e484169fd0de90b1293ca117709",
}
ERROR_CONFIG_SHA256 = "e13f26aabf749e688fa911fa300e2fa794d32be4e1805443215334398d8f0e4a"

#: The cv-kalman parameters of the analysis plan's section 4.4, by name, sorted.
KALMAN_PARAMETERS = "initial_velocity_std_mps=1.0,measurement_std_m=0.5,process_accel_std_mps2=3.0"

RUN_LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6})Z (\S.*)$")


@dataclass(frozen=True)
class Workspace:
    """A repository root with a protocol, two synthetic manifests and a study file."""

    root: Path
    protocol: Path
    manifest: Path
    smoke_manifest: Path
    study: Path


def manifest_document(split: str) -> dict[str, Any]:
    """A frozen cohort of the one synthetic token, which the synthetic source resolves."""

    return {
        "schema_version": "aeb-cohort-manifest/v1",
        "split": split,
        "protocol_sha256": PROTOCOL_SHA,
        "families": {
            "lead_or_stopping": [TOKEN],
            "cut_in_or_crossing": [],
            "pedestrian_or_crosswalk": [],
            "bicycle_or_vru": [],
        },
        "log_names": ["synthetic"],
    }


def study_for(manifest: Path, path: Path) -> Path:
    """The committed study file, recording the membership hash of `manifest` instead."""

    document = yaml.safe_load(COMMITTED_STUDY.read_text(encoding="utf-8"))
    document["cohort_membership_sha256"] = membership_sha256(load_manifest(manifest))
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    """A formal-run environment in which the working directory is the repository root."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AEBRISK_COMMIT", COMMIT)
    monkeypatch.setenv("AEBRISK_IMAGE_DIGEST", IMAGE)
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(PROTOCOL_TEXT, encoding="utf-8")
    manifest = tmp_path / "evaluation.json"
    manifest.write_text(json.dumps(manifest_document("evaluation")), encoding="utf-8")
    smoke_manifest = tmp_path / "smoke.json"
    smoke_manifest.write_text(json.dumps(manifest_document("smoke")), encoding="utf-8")
    return Workspace(
        root=tmp_path,
        protocol=protocol,
        manifest=manifest,
        smoke_manifest=smoke_manifest,
        study=study_for(manifest, tmp_path / "study.yaml"),
    )


def invoke(*arguments: str) -> Any:
    return CliRunner().invoke(app, list(arguments))


def study_simulate(
    workspace: Workspace,
    arm: str,
    output_dir: Path,
    *extra: str,
    study: Optional[Path] = None,
    manifest: Optional[Path] = None,
) -> Any:
    return invoke(
        "study",
        "simulate",
        "--study",
        str(workspace.study if study is None else study),
        "--arm",
        arm,
        "--protocol",
        str(workspace.protocol),
        "--manifest",
        str(workspace.manifest if manifest is None else manifest),
        "--output-dir",
        str(output_dir),
        "--scenario-source",
        "synthetic",
        *extra,
    )


def files_under(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def run_context(output_dir: Path) -> dict[str, Any]:
    context: dict[str, Any] = json.loads((output_dir / "run_context.json").read_bytes())
    return context


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def test_study_simulate_takes_the_released_option_spellings() -> None:
    result = invoke("study", "simulate", "--help")

    assert result.exit_code == 0, result.output
    for option in (
        "--study",
        "--arm",
        "--protocol",
        "--manifest",
        "--output-dir",
        "--split",
        "--workers",
        "--scenario-source",
        "--resume",
        "--dry-run",
        "--pilot",
    ):
        assert option in result.output


def test_arm_a_writes_the_same_bytes_as_the_released_simulate_command(
    workspace: Workspace,
) -> None:
    """Arm A sets no factor, so each of its cells is the released cell, byte for byte."""

    released = workspace.root / "released"
    released_result = invoke(
        "simulate",
        "--protocol",
        str(workspace.protocol),
        "--manifest",
        str(workspace.manifest),
        "--config-id",
        "all",
        "--output-dir",
        str(released),
        "--scenario-source",
        "synthetic",
    )
    assert released_result.exit_code == 0, released_result.output

    arm = workspace.root / "arms" / "A-v1-replication"
    result = study_simulate(workspace, "A-v1-replication", arm)

    assert result.exit_code == 0, result.output
    written = files_under(arm)
    documents = {name: data for name, data in written.items() if "/" in name}
    assert sorted(documents) == sorted(f"{cell}/{TOKEN}.json" for cell in STUDY_CELLS)
    expected = files_under(released)
    assert documents == {name: expected[name] for name in documents}
    assert written["run_complete.json"] == expected["run_complete.json"]
    assert set(written) - set(documents) == {"run_context.json", "run_complete.json", "run.log"}


@pytest.mark.parametrize("arm", sorted(ARM_FACTORS))
def test_the_run_context_names_the_arm_and_its_inputs(workspace: Workspace, arm: str) -> None:
    output = workspace.root / "arms" / arm
    result = study_simulate(workspace, arm, output, "--dry-run")

    assert result.exit_code == 0, result.output
    policy, scheme, estimator = ARM_FACTORS[arm]
    assert run_context(output) == {
        "configuration_id": f"study:{arm}",
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_sha256": membership_sha256(load_manifest(workspace.manifest)),
        "container_digest": IMAGE,
        "commit": COMMIT,
        "study_sha256": hashlib.sha256(workspace.study.read_bytes()).hexdigest(),
        "arm_id": arm,
        "aeb_policy": policy,
        "policy_sha256": POLICY_SHA256[policy],
        "rng_scheme": scheme,
        "velocity_estimator": estimator,
        "velocity_parameters": KALMAN_PARAMETERS if estimator == "cv-kalman" else "",
        "error_config_sha256": ERROR_CONFIG_SHA256,
        "python_version": platform.python_version(),
        "numpy_version": numpy.__version__,
    }
    raw = (output / "run_context.json").read_bytes()
    assert raw.endswith(b"}\n")
    assert b"\r" not in raw
    assert b": " not in raw


def test_the_run_context_records_the_python_and_numpy_versions(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The versions are read inside the running interpreter, not written down in advance."""

    monkeypatch.setattr(platform, "python_version", lambda: "3.9.99")
    monkeypatch.setattr(numpy, "__version__", "1.99.0")
    output = workspace.root / "arms" / "B-v2-gated"

    result = study_simulate(workspace, "B-v2-gated", output, "--dry-run")

    assert result.exit_code == 0, result.output
    context = run_context(output)
    assert (context["python_version"], context["numpy_version"]) == ("3.9.99", "1.99.0")


# --------------------------------------------------------------------------
# Where an arm may write
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "artifacts/formal/nuplan_aeb_v2",
        "artifacts/formal/nuplan_aeb_v2/A-v1-replication",
        "docs/studies/aeb-policy-v2/arms/A-v1-replication",
    ],
)
@pytest.mark.parametrize("absolute", [False, True])
def test_a_frozen_output_directory_is_refused(
    workspace: Workspace, relative: str, absolute: bool
) -> None:
    output = workspace.root / relative if absolute else Path(relative)

    result = study_simulate(workspace, "E-v2-channel-rng", output)

    assert result.exit_code == 1
    assert "refused" in result.output
    assert not (workspace.root / relative).exists()


@pytest.mark.parametrize(
    "relative",
    [
        "artifacts/formal/nuplan_aeb_v2",
        "artifacts/formal/nuplan_aeb_v2/A-v1-replication/no_aeb",
        "artifacts/formal/aeb_policy_v2/../nuplan_aeb_v2/A-v1-replication",
        "docs",
        "docs/studies/aeb-policy-v2",
    ],
)
def test_only_the_released_records_and_the_documentation_are_frozen(
    tmp_path: Path, relative: str
) -> None:
    from aebrisk.cli.study import refuse_frozen_output

    with pytest.raises(ValueError, match="refused"):
        refuse_frozen_output(tmp_path / relative, tmp_path)


@pytest.mark.parametrize(
    "relative",
    [
        "artifacts/formal/aeb_policy_v2/attempt-1/A-v1-replication",
        "artifacts/formal/nuplan_aeb_v2-rerun",
        "artifacts/formal",
        "artifacts/pilot/A-v1-replication",
        "documents",
        "docs-draft/arms",
    ],
)
def test_a_directory_beside_the_frozen_ones_is_accepted(tmp_path: Path, relative: str) -> None:
    from aebrisk.cli.study import refuse_frozen_output

    refuse_frozen_output(tmp_path / relative, tmp_path)


def test_a_completed_directory_is_refused(workspace: Workspace) -> None:
    output = workspace.root / "arms" / "E-v2-channel-rng"
    output.mkdir(parents=True)
    (output / "run_complete.json").write_bytes(b"completion evidence")
    (output / "run_context.json").write_bytes(b"original context")

    result = study_simulate(workspace, "E-v2-channel-rng", output, "--resume")

    assert result.exit_code == 1
    assert "this run is already complete" in result.output
    assert files_under(output) == {
        "run_complete.json": b"completion evidence",
        "run_context.json": b"original context",
    }


def test_resuming_with_another_arm_is_refused(workspace: Workspace) -> None:
    output = workspace.root / "arms" / "E-v2-channel-rng"
    assert study_simulate(workspace, "E-v2-channel-rng", output).exit_code == 0
    (output / "run_complete.json").unlink()
    before = files_under(output)

    result = study_simulate(workspace, "B-v2-gated", output, "--resume")

    assert result.exit_code == 1
    assert "the resume context does not match the current run inputs" in result.output
    assert files_under(output) == before

    resumed = study_simulate(workspace, "E-v2-channel-rng", output, "--resume")

    assert resumed.exit_code == 0, resumed.output
    assert (output / "run_complete.json").is_file()
    after = files_under(output)
    assert {name: after[name] for name in before if name != "run.log"} == {
        name: data for name, data in before.items() if name != "run.log"
    }
    assert after["run.log"].startswith(before["run.log"])


def test_dry_run_writes_only_the_context(workspace: Workspace) -> None:
    output = workspace.root / "arms" / "C-v1-kalman"

    result = study_simulate(workspace, "C-v1-kalman", output, "--dry-run")

    assert result.exit_code == 0, result.output
    assert "nothing was simulated" in result.output
    assert sorted(files_under(output)) == ["run_context.json"]


# --------------------------------------------------------------------------
# The run log
# --------------------------------------------------------------------------


def test_every_run_log_line_carries_a_utc_timestamp(workspace: Workspace) -> None:
    """Each event and each finished token is one line; the first and last give the run's span."""

    output = workspace.root / "arms" / "E-v2-channel-rng"
    started = datetime.now(timezone.utc).replace(tzinfo=None)

    result = study_simulate(workspace, "E-v2-channel-rng", output)

    finished = datetime.now(timezone.utc).replace(tzinfo=None)
    assert result.exit_code == 0, result.output
    raw = (output / "run.log").read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    matches = []
    for line in raw.decode("utf-8").splitlines():
        match = RUN_LOG_LINE.fullmatch(line)
        assert match is not None, line
        matches.append(match)
    stamps = [datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S.%f") for match in matches]
    assert stamps == sorted(stamps)
    assert started <= stamps[0]
    assert stamps[-1] <= finished
    events = [match.group(2) for match in matches]
    assert events == [
        f"run context for study:E-v2-channel-rng is recorded at {output}",
        "resolved 1 tokens from 1 logs",
        f"{TOKEN} lead_or_stopping ok",
        events[-1],
    ]
    assert events[-1].startswith("wrote 4 result documents: 1 tokens, 1 valid, 0 invalid")
    assert all(event in result.output for event in events)


def test_a_run_log_line_is_stamped_in_utc_with_microseconds(tmp_path: Path) -> None:
    from aebrisk.cli.study import append_run_log

    path = tmp_path / "run.log"
    taipei = timezone(timedelta(hours=8))

    append_run_log(path, "first", now=lambda: datetime(2026, 9, 27, 4, 5, 6, 7, tzinfo=taipei))
    append_run_log(path, "second", now=lambda: datetime(2026, 9, 27, 4, 5, 6, 0, tzinfo=taipei))

    assert path.read_bytes() == (
        b"2026-09-26T20:05:06.000007Z first\n2026-09-26T20:05:06.000000Z second\n"
    )


def test_a_run_log_line_is_stamped_with_the_current_utc_time_by_default(tmp_path: Path) -> None:
    from aebrisk.cli.study import append_run_log

    path = tmp_path / "run.log"
    before = datetime.now(timezone.utc).replace(tzinfo=None)

    append_run_log(path, "event")

    after = datetime.now(timezone.utc).replace(tzinfo=None)
    match = RUN_LOG_LINE.fullmatch(path.read_text(encoding="utf-8").rstrip("\n"))
    assert match is not None
    assert match.group(2) == "event"
    assert before <= datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S.%f") <= after


# --------------------------------------------------------------------------
# A formal run
# --------------------------------------------------------------------------


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_formal_run_with_another_manifest_is_refused(workspace: Workspace, dry_run: bool) -> None:
    """The committed study file records the released cohort, not the synthetic one."""

    output = workspace.root / "arms" / "A-v1-replication"

    result = study_simulate(
        workspace,
        "A-v1-replication",
        output,
        *(("--dry-run",) if dry_run else ()),
        study=COMMITTED_STUDY,
    )

    assert result.exit_code == 1
    assert "membership" in result.output
    assert not output.exists()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("AEBRISK_COMMIT", None),
        ("AEBRISK_COMMIT", ""),
        ("AEBRISK_COMMIT", "0" * 40),
        ("AEBRISK_IMAGE_DIGEST", None),
        ("AEBRISK_IMAGE_DIGEST", ""),
        ("AEBRISK_IMAGE_DIGEST", "unknown"),
    ],
)
def test_a_formal_run_without_a_commit_or_an_image_id_is_refused(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, variable: str, value: Optional[str]
) -> None:
    if value is None:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, value)
    output = workspace.root / "arms" / "A-v1-replication"

    result = study_simulate(workspace, "A-v1-replication", output, "--dry-run")

    assert result.exit_code == 1
    assert variable in result.output
    assert not output.exists()


@pytest.mark.parametrize(
    ("study", "arm"),
    [
        ("absent.yaml", "A-v1-replication"),
        ("not-yaml.yaml", "A-v1-replication"),
        ("wrong-replicates.yaml", "A-v1-replication"),
        ("study.yaml", "F-unknown"),
    ],
)
def test_a_study_file_or_an_arm_that_cannot_be_run_is_refused(
    workspace: Workspace, study: str, arm: str
) -> None:
    (workspace.root / "not-yaml.yaml").write_text("arms: [unclosed\n", encoding="utf-8")
    document = yaml.safe_load(workspace.study.read_text(encoding="utf-8"))
    document["replicates"] = [0, 1]
    (workspace.root / "wrong-replicates.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    output = workspace.root / "arms" / arm

    result = study_simulate(workspace, arm, output, study=workspace.root / study)

    assert result.exit_code == 1
    assert "the study" in result.output
    assert not output.exists()


# --------------------------------------------------------------------------
# A pilot
# --------------------------------------------------------------------------


def test_a_pilot_on_the_smoke_manifest_is_accepted(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pilot needs neither the study's cohort nor a recorded commit or image."""

    monkeypatch.delenv("AEBRISK_COMMIT")
    monkeypatch.delenv("AEBRISK_IMAGE_DIGEST")
    output = workspace.root / "artifacts" / "pilot" / "E-v2-channel-rng"

    result = study_simulate(
        workspace,
        "E-v2-channel-rng",
        output,
        "--pilot",
        study=COMMITTED_STUDY,
        manifest=workspace.smoke_manifest,
    )

    assert result.exit_code == 0, result.output
    assert sorted(files_under(output)) == sorted(
        [
            *(f"{cell}/{TOKEN}.json" for cell in ARM_E_CELLS),
            "run.log",
            "run_complete.json",
            "run_context.json",
        ]
    )
    context = run_context(output)
    assert context["cohort_sha256"] == membership_sha256(load_manifest(workspace.smoke_manifest))
    assert (context["commit"], context["container_digest"]) == ("0" * 40, "unknown")
    assert context["study_sha256"] == hashlib.sha256(COMMITTED_STUDY.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "relative",
    ["artifacts/pilot", "artifacts/pilot-2/E-v2-channel-rng", "arms/E-v2-channel-rng"],
)
def test_a_pilot_outside_artifacts_pilot_is_refused(workspace: Workspace, relative: str) -> None:
    output = workspace.root / relative

    result = study_simulate(
        workspace, "E-v2-channel-rng", output, "--pilot", manifest=workspace.smoke_manifest
    )

    assert result.exit_code == 1
    assert "artifacts/pilot/" in result.output
    assert not output.exists()


def test_a_pilot_on_a_manifest_other_than_smoke_is_refused(workspace: Workspace) -> None:
    """The manifest's own split decides, whatever `--split` names."""

    output = workspace.root / "artifacts" / "pilot" / "E-v2-channel-rng"

    result = study_simulate(workspace, "E-v2-channel-rng", output, "--pilot", "--split", "smoke")

    assert result.exit_code == 1
    assert "smoke" in result.output
    assert not output.exists()
