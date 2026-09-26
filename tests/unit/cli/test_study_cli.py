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

`study verify` and `study preflight` write their gate files. The first is run on
five synthetic arms simulated here beside the released command's cells, so it
checks the gates against what the runner actually writes; the second reads
small fixtures in the shape of the released run's two private records.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
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


def test_a_relative_output_directory_is_read_from_the_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aebrisk.cli.study import refuse_frozen_output

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    repository = tmp_path / "repository"

    with pytest.raises(ValueError, match="refused"):
        refuse_frozen_output(Path("docs/studies/aeb-policy-v2"), repository)
    refuse_frozen_output(Path("artifacts/pilot/A-v1-replication"), repository)


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


def test_a_run_log_line_that_spans_lines_is_written_as_one(tmp_path: Path) -> None:
    """A reader that splits the log into lines finds one stamped line per call."""

    from aebrisk.cli.study import append_run_log

    path = tmp_path / "run.log"
    stamp = datetime(2026, 9, 27, 4, 5, 6, 7, tzinfo=timezone.utc)

    append_run_log(path, "one\ntwo\r\nthree\rfour\u2028five", now=lambda: stamp)

    assert path.read_bytes() == b"2026-09-27T04:05:06.000007Z one two three four five\n"


def test_an_invalid_token_whose_reason_spans_lines_keeps_one_run_log_line(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason quotes the simulator's exception, whose message may span lines."""

    from aebrisk.simulation.synthetic import SyntheticLeadScenario

    def fail(*arguments: object) -> None:
        raise ValueError("the first line\nthe second line")

    monkeypatch.setattr(SyntheticLeadScenario, "simulate", fail)
    output = workspace.root / "arms" / "E-v2-channel-rng"

    result = study_simulate(workspace, "E-v2-channel-rng", output)

    assert result.exit_code == 0, result.output
    lines = (output / "run.log").read_text(encoding="utf-8").splitlines()
    assert all(RUN_LOG_LINE.fullmatch(line) for line in lines), lines
    assert f"{TOKEN} lead_or_stopping INVALID " in lines[2]
    assert lines[2].endswith(": the first line the second line")
    assert len(lines) == 4


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


# --------------------------------------------------------------------------
# study verify
# --------------------------------------------------------------------------


def verified_study_for(manifest: Path, path: Path) -> Path:
    """The committed study file, recording the synthetic cohort and protocol instead.

    The integrity gate holds every arm's run context to the protocol and the
    cohort the study file records.
    """

    document = yaml.safe_load(COMMITTED_STUDY.read_text(encoding="utf-8"))
    document["seed_namespace_protocol_sha256"] = PROTOCOL_SHA
    document["cohort_membership_sha256"] = membership_sha256(load_manifest(manifest))
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def synthetic_attempt(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Released cells, the five arms of one attempt and one pilot arm, all synthetic."""

    root = tmp_path_factory.mktemp("synthetic-attempt")
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(root)
        patch.setenv("AEBRISK_COMMIT", COMMIT)
        patch.setenv("AEBRISK_IMAGE_DIGEST", IMAGE)
        (root / "protocol.yaml").write_text(PROTOCOL_TEXT, encoding="utf-8")
        for split, name in (("evaluation", "evaluation.json"), ("smoke", "smoke.json")):
            (root / name).write_text(json.dumps(manifest_document(split)), encoding="utf-8")
        verified_study_for(root / "evaluation.json", root / "study.yaml")
        common = ("--protocol", "protocol.yaml", "--scenario-source", "synthetic")
        arm_run = ("study", "simulate", "--study", "study.yaml", "--arm")
        runs = [
            ("simulate", "--config-id", "all", "--manifest", "evaluation.json", *common),
            *((*arm_run, arm, "--manifest", "evaluation.json", *common) for arm in ARM_FACTORS),
            (*arm_run, "E-v2-channel-rng", "--manifest", "smoke.json", "--pilot", *common),
        ]
        outputs = [
            "released",
            *(f"attempt-1/{arm}" for arm in ARM_FACTORS),
            "artifacts/pilot/E-v2-channel-rng",
        ]
        for arguments, output in zip(runs, outputs):
            result = invoke(*arguments, "--output-dir", output)
            assert result.exit_code == 0, result.output
    return root


@pytest.fixture()
def attempt(synthetic_attempt: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the synthetic attempt, as the working directory."""

    root = tmp_path / "repository"
    shutil.copytree(synthetic_attempt, root)
    monkeypatch.chdir(root)
    return root


def study_verify(*extra: str, released: bool = True) -> Any:
    return invoke(
        "study",
        "verify",
        "--study",
        "study.yaml",
        "--arms-root",
        "attempt-1",
        "--manifest",
        "evaluation.json",
        *(("--released-root", "released") if released else ()),
        *extra,
    )


def gate_outcomes(path: Path) -> list[tuple[str, bool]]:
    from aebrisk.study.gates import load_gates

    return [(gate.gate, gate.passed) for gate in load_gates(path).gates]


def change_one_released_oracle_document(attempt: Path) -> None:
    """A released document whose bytes differ and whose content does not."""

    released = attempt / "released" / "oracle_aeb" / f"{TOKEN}.json"
    released.write_bytes(released.read_bytes().replace(b'"replicate": 2', b'"replicate":  2'))


def test_study_verify_and_preflight_take_the_documented_options() -> None:
    verify = invoke("study", "verify", "--help")
    preflight = invoke("study", "preflight", "--help")

    assert verify.exit_code == 0, verify.output
    for option in (
        "--study",
        "--arms-root",
        "--released-root",
        "--manifest",
        "--arm",
        "--reference",
        "--pilot",
        "--output",
    ):
        assert option in verify.output
    assert preflight.exit_code == 0, preflight.output
    for option in (
        "--study",
        "--protocol",
        "--released-root",
        "--released-hashes",
        "--input-databases",
        "--manifest",
        "--split",
        "--output",
    ):
        assert option in preflight.output


def test_study_verify_passes_every_gate_on_the_arms_the_runner_writes(attempt: Path) -> None:
    """Arm A reproduces the released cells, no_aeb is one, and the oracles agree by policy."""

    from aebrisk.study.gates import load_gates

    result = study_verify("--output", "attempt-1.gates.json")

    assert result.exit_code == 0, result.output
    gates = load_gates(attempt / "attempt-1.gates.json")
    assert [(gate.gate, gate.passed) for gate in gates.gates] == [
        ("G1", True),
        ("G2", True),
        ("G3", True),
        ("G5", True),
    ]
    assert gates.arms_checked == tuple(ARM_FACTORS)
    assert (gates.reference, gates.exploratory) == ("released", False)
    assert gates.study_sha256 == hashlib.sha256((attempt / "study.yaml").read_bytes()).hexdigest()
    assert gates.protocol_sha256 == PROTOCOL_SHA
    assert gates.gates[0].counts["documents"] == 4 * 8 + 4
    for gate in ("G1", "G2", "G3", "G5"):
        assert f"{gate} passed" in result.output


def test_study_verify_exits_1_when_a_required_gate_fails_and_still_writes_the_gate_file(
    attempt: Path,
) -> None:
    from aebrisk.study.gates import load_gates

    change_one_released_oracle_document(attempt)

    result = study_verify("--output", "attempt-1.gates.json")

    assert result.exit_code == 1
    assert gate_outcomes(attempt / "attempt-1.gates.json") == [
        ("G1", True),
        ("G2", False),
        ("G3", False),
        ("G5", True),
    ]
    gates = load_gates(attempt / "attempt-1.gates.json")
    assert gates.gates[1].counts["oracle_aeb"] == 1
    assert "G2 failed" in result.output


def test_study_verify_in_arm_a_reference_mode_labels_the_gate_file_exploratory(
    attempt: Path,
) -> None:
    from aebrisk.study.gates import load_gates

    change_one_released_oracle_document(attempt)

    result = study_verify("--reference", "arm-a", "--output", "attempt-1.gates.json")

    assert result.exit_code == 0, result.output
    gates = load_gates(attempt / "attempt-1.gates.json")
    assert (gates.reference, gates.exploratory) == ("arm-a", True)
    assert [(gate.gate, gate.passed) for gate in gates.gates] == [
        ("G1", True),
        ("G2", False),
        ("G3", True),
        ("G5", True),
    ]


def test_study_verify_of_arm_a_alone_runs_g1_g2_and_g5(attempt: Path) -> None:
    from aebrisk.study.gates import load_gates

    for arm in list(ARM_FACTORS)[1:]:
        shutil.rmtree(attempt / "attempt-1" / arm)

    result = study_verify("--arm", "A-v1-replication", "--output", "attempt-1.gates-A.json")

    assert result.exit_code == 0, result.output
    assert gate_outcomes(attempt / "attempt-1.gates-A.json") == [
        ("G1", True),
        ("G2", True),
        ("G5", True),
    ]
    assert load_gates(attempt / "attempt-1.gates-A.json").arms_checked == ("A-v1-replication",)


def test_a_pilot_verify_needs_no_released_root(attempt: Path) -> None:
    from aebrisk.study.gates import load_gates

    result = invoke(
        "study",
        "verify",
        "--pilot",
        "--study",
        "study.yaml",
        "--arms-root",
        "artifacts/pilot",
        "--manifest",
        "smoke.json",
        "--arm",
        "E-v2-channel-rng",
        "--output",
        "pilot.verify.json",
    )

    assert result.exit_code == 0, result.output
    assert gate_outcomes(attempt / "pilot.verify.json") == [("G1", True), ("G5", True)]
    assert load_gates(attempt / "pilot.verify.json").arms_checked == ("E-v2-channel-rng",)


def test_a_pilot_verify_outside_artifacts_pilot_is_refused(attempt: Path) -> None:
    result = study_verify("--pilot", "--output", "pilot.verify.json", released=False)

    assert result.exit_code == 1
    assert "artifacts/pilot/" in result.output
    assert not (attempt / "pilot.verify.json").exists()


@pytest.mark.parametrize(
    ("extra", "released", "message"),
    [
        ((), False, "released records"),
        (("--reference", "arm-b"), True, "reference"),
        (("--arm", "F-unknown"), True, "F-unknown"),
    ],
)
def test_study_verify_refuses_what_it_cannot_check(
    attempt: Path, extra: tuple[str, ...], released: bool, message: str
) -> None:
    result = study_verify(*extra, "--output", "attempt-1.gates.json", released=released)

    assert result.exit_code == 1
    assert message in result.output
    assert not (attempt / "attempt-1.gates.json").exists()


# --------------------------------------------------------------------------
# study preflight
# --------------------------------------------------------------------------

LOG = "2021.01.01.00.00.00_veh-01_00000_00100.db"


def crlf_json(path: Path, value: Any) -> Path:
    """JSON in the shape of the released run's private records: CRLF, a final LF."""

    path.write_bytes(("\r\n".join(json.dumps(value, indent=2).split("\n")) + "\n").encode())
    return path


@dataclass(frozen=True)
class PreflightInputs:
    root: Path
    study: Path
    hashes: Path
    log: Path
    output: Path

    def run(self) -> Any:
        return invoke(
            "study",
            "preflight",
            "--study",
            str(self.study),
            "--protocol",
            str(self.root / "protocol.yaml"),
            "--released-root",
            str(self.root / "released"),
            "--released-hashes",
            str(self.hashes),
            "--input-databases",
            str(self.root / "d2-input-databases.json"),
            "--manifest",
            str(self.root / "evaluation.json"),
            "--split",
            "val",
            "--output",
            str(self.output),
        )


@pytest.fixture()
def preflight_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PreflightInputs:
    """One released document, one log, and the two private records that list them."""

    (tmp_path / "protocol.yaml").write_text(PROTOCOL_TEXT, encoding="utf-8")
    manifest = tmp_path / "evaluation.json"
    manifest.write_text(
        json.dumps({**manifest_document("evaluation"), "log_names": [LOG]}), encoding="utf-8"
    )
    released = tmp_path / "released" / "no_aeb" / f"{TOKEN}.json"
    released.parent.mkdir(parents=True)
    released.write_bytes(b"{}\n")
    hashes = crlf_json(
        tmp_path / "output-hashes.json",
        {
            "schema_version": "aeb-d2-output-hashes/v2",
            "generated_at_utc": "2026-09-06T20:54:13.4306365Z",
            "formal_files": [
                {
                    "path": f"no_aeb/{TOKEN}.json",
                    "bytes": 3,
                    "sha256": hashlib.sha256(b"{}\n").hexdigest(),
                }
            ],
            "operation_files": [],
        },
    )
    data_root = tmp_path / "data"
    log = data_root / "nuplan-v1.1" / "splits" / "val" / LOG
    log.parent.mkdir(parents=True)
    log.write_bytes(b"not a real log\n" * 64)
    record = crlf_json(
        tmp_path / "d2-input-databases.json",
        {
            "kind": "private-d2-dataset-input-fingerprint",
            "dataset": "nuplan-v1.1",
            "split": "val",
            "started_at_utc": "2026-09-06T16:43:44.8540585Z",
            "finished_at_utc": "2026-09-06T16:51:21.6058631Z",
            "files": [
                {
                    "relative_path": f"nuplan-v1.1/splits/val/{LOG}",
                    "bytes": log.stat().st_size,
                    "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                    "last_write_time_utc": "2022-09-23T05:47:01.0000000Z",
                }
            ],
        },
    )
    study = verified_study_for(manifest, tmp_path / "study.yaml")
    document = yaml.safe_load(study.read_text(encoding="utf-8"))
    document["cohort_manifest_file_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    document["released_output_hashes_sha256"] = hashlib.sha256(hashes.read_bytes()).hexdigest()
    document["input_databases_sha256"] = hashlib.sha256(record.read_bytes()).hexdigest()
    study.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("NUPLAN_DATA_ROOT", str(data_root))
    return PreflightInputs(
        root=tmp_path,
        study=study,
        hashes=hashes,
        log=log,
        output=tmp_path / "artifacts" / "formal" / "aeb_policy_v2" / "preflight.json",
    )


def test_preflight_writes_its_result_and_names_what_it_checked(
    preflight_inputs: PreflightInputs,
) -> None:
    from aebrisk.study.gates import load_gates

    result = preflight_inputs.run()

    assert result.exit_code == 0, result.output
    study_sha256 = hashlib.sha256(preflight_inputs.study.read_bytes()).hexdigest()
    assert f"study file SHA-256 {study_sha256}" in result.output
    gates = load_gates(preflight_inputs.output)
    assert [(gate.gate, gate.passed) for gate in gates.gates] == [("preflight", True)]
    assert gates.arms_checked == ()
    assert gates.study_sha256 == study_sha256
    assert gates.protocol_sha256 == PROTOCOL_SHA
    assert gates.cohort_manifest_sha256 == membership_sha256(
        load_manifest(preflight_inputs.root / "evaluation.json")
    )
    assert (gates.reference, gates.exploratory) == ("released", False)
    assert gates.gates[0].counts["referenced_logs"] == 1
    assert gates.gates[0].counts["log_bytes"] == preflight_inputs.log.stat().st_size
    detail = gates.artifacts_only_detail["preflight"]
    assert f"split directory {preflight_inputs.log.parent}" in detail
    hashes_sha256 = hashlib.sha256(preflight_inputs.hashes.read_bytes()).hexdigest()
    assert f"released output hash list SHA-256 {hashes_sha256}" in detail
    record_sha256 = hashlib.sha256(
        (preflight_inputs.root / "d2-input-databases.json").read_bytes()
    ).hexdigest()
    assert f"input databases record SHA-256 {record_sha256}" in detail
    modified = datetime.fromtimestamp(
        preflight_inputs.log.stat().st_mtime_ns // 10**9, timezone.utc
    )
    assert any(line.startswith(f"{LOG} modified {modified:%Y-%m-%dT%H:%M:%S}.") for line in detail)


def test_preflight_exits_1_and_writes_a_failed_result_when_a_log_differs(
    preflight_inputs: PreflightInputs,
) -> None:
    from aebrisk.study.gates import load_gates

    data = bytearray(preflight_inputs.log.read_bytes())
    data[0] ^= 0x01
    preflight_inputs.log.write_bytes(bytes(data))

    result = preflight_inputs.run()

    assert result.exit_code == 1
    gates = load_gates(preflight_inputs.output)
    assert [(gate.gate, gate.passed) for gate in gates.gates] == [("preflight", False)]
    assert gates.gates[0].counts["log_hash_mismatches"] == 1
    assert "preflight failed" in result.output


def test_preflight_refuses_a_hash_list_other_than_the_pinned_one(
    preflight_inputs: PreflightInputs,
) -> None:
    preflight_inputs.hashes.write_bytes(preflight_inputs.hashes.read_bytes() + b"\n")

    result = preflight_inputs.run()

    assert result.exit_code == 1
    assert "pins" in result.output
    assert not preflight_inputs.output.exists()


def test_preflight_refuses_without_a_data_root(
    preflight_inputs: PreflightInputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NUPLAN_DATA_ROOT")

    result = preflight_inputs.run()

    assert result.exit_code == 1
    assert "NUPLAN_DATA_ROOT" in result.output
    assert not preflight_inputs.output.exists()


# --------------------------------------------------------------------------
# study analyse, study addendum and study evidence
#
# Each command hands its files to the function that does the work
# (`analyse_study`, `analyse_addendum`, `write_study_evidence` and
# `write_addendum_evidence`, each tested on its own) and writes what it
# returns. These tests hold the commands to their option names, to what they
# pass on, and to refusing with a diagnostic, and writing nothing, whenever
# that function refuses.
# --------------------------------------------------------------------------

STUDY_ARMS = tuple(ARM_FACTORS)
PREREGISTRATION_COMMIT = "abcdef0123456789abcdef0123456789abcdef01"
MERGED_AT = "2026-09-26T08:12:34Z"

ANALYSE_OPTIONS = (
    "--study",
    "--arms-root",
    "--released-root",
    "--evidence-dir",
    "--manifest",
    "--gates",
    "--reference",
    "--output",
)
ADDENDUM_OPTIONS = (
    "--released-root",
    "--released-hashes",
    "--evidence-dir",
    "--manifest",
    "--eligibility",
    "--output",
)
EVIDENCE_OPTIONS = (
    "--part",
    "--summary",
    "--gates",
    "--arms-root",
    "--g0",
    "--preregistration-pr",
    "--preregistration-commit",
    "--preregistration-merged-at",
    "--output-dir",
    "--not-completed",
    "--earlier-gates",
    "--g4",
    "--addendum",
)


def declared_options(command: str) -> set[str]:
    """Every option name a `study` command declares, read from the command itself."""

    import click
    import typer.main

    root = typer.main.get_command(app)
    assert isinstance(root, click.Group)
    group = root.commands["study"]
    assert isinstance(group, click.Group)
    return {option for parameter in group.commands[command].params for option in parameter.opts}


def study_summary(reference: str = "released") -> Any:
    """The smallest valid policy v2 summary: what `study analyse` writes, with no contrast."""

    from aebrisk.artifacts.study_documents import PolicyV2SummaryV1

    return PolicyV2SummaryV1.model_validate(
        {
            "schema_version": "aeb-policy-v2-summary/v1",
            "study_sha256": "5" * 64,
            "protocol_sha256": PROTOCOL_SHA,
            "cohort_manifest_sha256": "6" * 64,
            "common_valid_tokens": 1,
            "reference": reference,
            "exploratory": reference == "arm-a",
            "g4": {"gate": "G4", "passed": True, "counts": {"cells": 8}},
            "hypotheses": [],
            "q1_label": "no_support",
            "h5_qualifier": False,
            "statements": {"h3": "H3.", "h4": "H4.", "h5": "H5."},
            "sensitivity": [],
            "q3_ratio": {
                "outcome": "braking_share",
                "b_terms": [],
                "e_terms": [],
                "sd_b": 0.0,
                "sd_e": 0.0,
                "ratio": None,
            },
            "secondary": [],
            "descriptive_contrasts": [],
            "levels": [],
        }
    )


def addendum_summary() -> Any:
    """The smallest valid addendum summary: both games, in order, and no interval."""

    from aebrisk.artifacts.study_documents import (
        ADDENDUM_GAMES,
        COLLISION_GAME_SENTENCE,
        AttributionAddendumV1,
    )

    proportion = {"events": 0, "tokens": 1, "confidence": 0.95, "low": 0.0, "high": 0.975}
    return AttributionAddendumV1.model_validate(
        {
            "schema_version": "aeb-attribution-addendum/v1",
            "protocol_sha256": PROTOCOL_SHA,
            "cohort_manifest_sha256": "6" * 64,
            "cohort_size": 1,
            "common_valid_tokens": 1,
            "released_output_hashes_sha256": "4" * 64,
            "reproduction_gate": {"gate": "reproduction", "passed": True, "counts": {"values": 1}},
            "bootstrap": {"cluster": "family-log", "clusters": 1, "resamples": 5000, "seed": 1},
            "games": [
                {
                    "game": game,
                    "shapley_values": {},
                    "localization_shape_differences": [],
                    "other_differences": [],
                    "caution": COLLISION_GAME_SENTENCE if game == "collision_indicator" else None,
                }
                for game in ADDENDUM_GAMES
            ],
            "configuration_contrasts": [],
            "oracle_collisions": {
                "avoided": proportion,
                "induced": proportion,
                "oracle_aeb_contacts_not_at_fault": 0,
                "computed_before_plan": True,
            },
            "brake_activations": [],
            "zero_event_configurations": [],
            "matched_onset_delays": [],
            "stops_and_collision_speeds": [],
        }
    )


def write_gate_file(path: Path, reference: str = "released", failed: tuple[str, ...] = ()) -> Path:
    """A gate file of the committed study over all five arms, as `study verify` writes one."""

    from typing import cast

    from aebrisk.artifacts.study_documents import Reference
    from aebrisk.study.gates import GateResult, gates_document, write_gates

    results = [
        GateResult(name, name not in failed, {"checked": 1}, ())
        for name in ("G1", "G2", "G3", "G5")
    ]
    mode = cast("Reference", reference)
    write_gates(path, gates_document(COMMITTED_STUDY, STUDY_ARMS, results, mode))
    return path


def study_analyse(*extra: str) -> Any:
    return invoke(
        "study",
        "analyse",
        "--study",
        str(COMMITTED_STUDY),
        "--arms-root",
        "attempt-1",
        "--released-root",
        "released",
        "--evidence-dir",
        "evidence",
        "--manifest",
        "evaluation.json",
        "--gates",
        "attempt-1.gates.json",
        *extra,
    )


def study_addendum(*extra: str) -> Any:
    return invoke(
        "study",
        "addendum",
        "--released-root",
        "released",
        "--released-hashes",
        "output-hashes.json",
        "--evidence-dir",
        "evidence",
        "--manifest",
        "evaluation.json",
        "--eligibility",
        "evaluation-eligibility.json",
        *extra,
    )


def written_files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


@pytest.mark.parametrize(
    ("command", "options"),
    [("analyse", ANALYSE_OPTIONS), ("addendum", ADDENDUM_OPTIONS), ("evidence", EVIDENCE_OPTIONS)],
)
def test_study_analyse_addendum_and_evidence_take_exactly_the_documented_options(
    command: str, options: tuple[str, ...]
) -> None:
    """No option name is derived from a parameter name, such as `--summary-path`."""

    assert declared_options(command) == set(options)
    assert invoke("study", command, "--help").exit_code == 0


@pytest.mark.parametrize("reference", ["released", "arm-a"])
def test_study_analyse_analyses_with_the_gate_file_it_loads_and_writes_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    """G4 is written beside the summary, and `--reference arm-a` selects arm-A reference mode."""

    from aebrisk.artifacts.study_documents import PolicyV2SummaryV1
    from aebrisk.study.gates import load_gates

    monkeypatch.chdir(tmp_path)
    gate_file = write_gate_file(tmp_path / "attempt-1.gates.json", reference, ("G2",))
    summary = study_summary(reference)
    seen: dict[str, Any] = {}

    def analyse(
        study: Path,
        arms_root: Path,
        released_root: Path,
        evidence_dir: Path,
        manifest: Path,
        gates: Any,
        reference: str = "released",
        g4_path: Optional[Path] = None,
    ) -> Any:
        seen.update(
            study=study,
            arms_root=arms_root,
            released_root=released_root,
            evidence_dir=evidence_dir,
            manifest=manifest,
            gates=gates,
            reference=reference,
            g4_path=g4_path,
        )
        return summary

    monkeypatch.setattr("aebrisk.cli.study.analyse_study", analyse)
    output = Path("artifacts") / "studies" / "aeb_policy_v2" / "summary.json"
    flag = ("--reference", reference) if reference == "arm-a" else ()

    result = study_analyse(*flag, "--output", str(output))

    assert result.exit_code == 0, result.output
    assert seen == {
        "study": COMMITTED_STUDY,
        "arms_root": Path("attempt-1"),
        "released_root": Path("released"),
        "evidence_dir": Path("evidence"),
        "manifest": Path("evaluation.json"),
        "gates": load_gates(gate_file),
        "reference": reference,
        "g4_path": output.parent / "g4.json",
    }
    written = (tmp_path / output).read_bytes()
    assert PolicyV2SummaryV1.model_validate_json(written) == summary
    assert written.endswith(b"}\n")
    assert str(output) in result.output


@pytest.mark.parametrize(
    ("gate_reference", "flag", "message"),
    [
        ("released", (), "failed"),
        ("arm-a", (), "reference"),
        ("released", ("--reference", "arm-a"), "reference"),
        ("released", ("--reference", "arm-b"), "unknown reference"),
    ],
)
def test_study_analyse_refuses_unless_the_gates_passed_as_the_mode_requires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gate_reference: str,
    flag: tuple[str, ...],
    message: str,
) -> None:
    """A failed G2 is accepted only in arm-A reference mode, from a gate file of that mode."""

    monkeypatch.chdir(tmp_path)
    write_gate_file(tmp_path / "attempt-1.gates.json", gate_reference, ("G2",))

    result = study_analyse(*flag, "--output", "summary.json")

    assert result.exit_code == 1
    assert "the study is not analysed" in result.output
    assert message in result.output
    assert written_files(tmp_path) == ["attempt-1.gates.json"]


def test_study_analyse_refuses_a_gate_file_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "attempt-1.gates.json").write_text("{}", encoding="utf-8")

    result = study_analyse("--output", "summary.json")

    assert result.exit_code == 1
    assert "the study is not analysed" in result.output
    assert written_files(tmp_path) == ["attempt-1.gates.json"]


def test_study_addendum_holds_the_hash_list_to_the_value_pinned_when_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinned SHA-256 is read from its module at run time, so a patched value reaches it."""

    from aebrisk.artifacts.study_documents import AttributionAddendumV1

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aebrisk.study.addendum.RELEASED_OUTPUT_HASHES_SHA256", "e" * 64)
    summary = addendum_summary()
    seen: dict[str, Any] = {}

    def analyse(
        released_root: Path,
        released_hashes: Path,
        evidence_dir: Path,
        manifest: Path,
        eligibility: Path,
        expected_hashes_sha256: str = "not passed",
    ) -> Any:
        seen.update(
            released_root=released_root,
            released_hashes=released_hashes,
            evidence_dir=evidence_dir,
            manifest=manifest,
            eligibility=eligibility,
            expected_hashes_sha256=expected_hashes_sha256,
        )
        return summary

    monkeypatch.setattr("aebrisk.cli.study.analyse_addendum", analyse)
    output = Path("artifacts") / "posthoc" / "addendum-summary.json"

    result = study_addendum("--output", str(output))

    assert result.exit_code == 0, result.output
    assert seen == {
        "released_root": Path("released"),
        "released_hashes": Path("output-hashes.json"),
        "evidence_dir": Path("evidence"),
        "manifest": Path("evaluation.json"),
        "eligibility": Path("evaluation-eligibility.json"),
        "expected_hashes_sha256": "e" * 64,
    }
    assert AttributionAddendumV1.model_validate_json((tmp_path / output).read_bytes()) == summary
    assert str(output) in result.output


def test_study_addendum_refuses_a_hash_list_other_than_the_pinned_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reproduction gate fails before any record is read, and nothing is written."""

    monkeypatch.chdir(tmp_path)
    (tmp_path / "output-hashes.json").write_text("{}\n", encoding="utf-8")

    result = study_addendum("--output", "addendum-summary.json")

    assert result.exit_code == 1
    assert "the addendum is not computed" in result.output
    assert "reproduction gate failed" in result.output
    assert written_files(tmp_path) == ["output-hashes.json"]


def study_evidence(*arguments: str) -> Any:
    return invoke("study", "evidence", *arguments)


STUDY_EVIDENCE_INPUTS = (
    "--gates",
    "artifacts/formal/aeb_policy_v2/attempt-2.gates.json",
    "--arms-root",
    "artifacts/formal/aeb_policy_v2/attempt-2",
    "--g0",
    "artifacts/formal/aeb_policy_v2/g0.json",
    "--preregistration-pr",
    "5",
    "--preregistration-commit",
    PREREGISTRATION_COMMIT,
    "--preregistration-merged-at",
    MERGED_AT,
    "--output-dir",
    "docs/studies/aeb-policy-v2/evidence",
)


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (
            (
                "--summary",
                "artifacts/studies/aeb_policy_v2/summary.json",
                "--earlier-gates",
                "artifacts/formal/aeb_policy_v2/attempt-1.gates-A.json",
            ),
            {
                "summary_path": Path("artifacts/studies/aeb_policy_v2/summary.json"),
                "not_completed": False,
                "earlier_gates": (Path("artifacts/formal/aeb_policy_v2/attempt-1.gates-A.json"),),
                "g4_path": None,
            },
        ),
        (
            (
                "--not-completed",
                "--earlier-gates",
                "artifacts/formal/aeb_policy_v2/attempt-1.gates.json",
                "--earlier-gates",
                "artifacts/formal/aeb_policy_v2/attempt-0.gates.json",
                "--g4",
                "artifacts/studies/aeb_policy_v2/g4.json",
            ),
            {
                "summary_path": None,
                "not_completed": True,
                "earlier_gates": (
                    Path("artifacts/formal/aeb_policy_v2/attempt-1.gates.json"),
                    Path("artifacts/formal/aeb_policy_v2/attempt-0.gates.json"),
                ),
                "g4_path": Path("artifacts/studies/aeb_policy_v2/g4.json"),
            },
        ),
        (
            ("--not-completed",),
            {"summary_path": None, "not_completed": True, "earlier_gates": (), "g4_path": None},
        ),
    ],
)
def test_study_evidence_of_the_study_passes_every_input_to_its_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: tuple[str, ...],
    expected: dict[str, Any],
) -> None:
    """A completed study names its summary; one reported not completed names none."""

    monkeypatch.chdir(tmp_path)
    seen: dict[str, Any] = {}

    def write(
        summary_path: Optional[Path],
        gates_path: Path,
        g0_path: Path,
        arms_root: Path,
        preregistration_pr: int,
        preregistration_commit: str,
        preregistration_merged_at: str,
        output_dir: Path,
        not_completed: bool = False,
        earlier_gates: Any = (),
        g4_path: Optional[Path] = None,
    ) -> tuple[Path, ...]:
        seen.update(
            summary_path=summary_path,
            gates_path=gates_path,
            g0_path=g0_path,
            arms_root=arms_root,
            preregistration_pr=preregistration_pr,
            preregistration_commit=preregistration_commit,
            preregistration_merged_at=preregistration_merged_at,
            output_dir=output_dir,
            not_completed=not_completed,
            earlier_gates=earlier_gates,
            g4_path=g4_path,
        )
        return (output_dir / "gates.json", output_dir / "reproduction.json")

    monkeypatch.setattr("aebrisk.cli.study.write_study_evidence", write)

    result = study_evidence("--part", "study", *STUDY_EVIDENCE_INPUTS, *extra)

    assert result.exit_code == 0, result.output
    evidence = Path("docs/studies/aeb-policy-v2/evidence")
    assert seen == {
        "gates_path": Path("artifacts/formal/aeb_policy_v2/attempt-2.gates.json"),
        "g0_path": Path("artifacts/formal/aeb_policy_v2/g0.json"),
        "arms_root": Path("artifacts/formal/aeb_policy_v2/attempt-2"),
        "preregistration_pr": 5,
        "preregistration_commit": PREREGISTRATION_COMMIT,
        "preregistration_merged_at": MERGED_AT,
        "output_dir": evidence,
        **expected,
    }
    for name in ("gates.json", "reproduction.json"):
        assert str(evidence / name) in result.output


def test_study_evidence_of_the_addendum_writes_its_two_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aebrisk.artifacts.documents import write_document
    from aebrisk.artifacts.study_documents import AttributionAddendumV1

    monkeypatch.chdir(tmp_path)
    cohort = tmp_path / "docs" / "evidence" / "nuplan_aeb_v2" / "cohort" / "evaluation.json"
    cohort.parent.mkdir(parents=True)
    cohort.write_text(json.dumps(manifest_document("evaluation")), encoding="utf-8")
    summary = addendum_summary()
    write_document(summary, tmp_path / "artifacts" / "addendum-summary.json")
    evidence = Path("docs") / "posthoc" / "nuplan_aeb_v2-addendum" / "evidence"

    result = study_evidence(
        "--part",
        "addendum",
        "--addendum",
        "artifacts/addendum-summary.json",
        "--output-dir",
        str(evidence),
    )

    assert result.exit_code == 0, result.output
    assert written_files(tmp_path / evidence) == [
        "addendum-summary.json",
        "attribution-addendum-evidence.json",
    ]
    copied = (tmp_path / evidence / "addendum-summary.json").read_bytes()
    assert AttributionAddendumV1.model_validate_json(copied) == summary
    for name in ("addendum-summary.json", "attribution-addendum-evidence.json"):
        assert str(evidence / name) in result.output


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--part", "released", "--output-dir", "evidence"), "--part is study or addendum"),
        (("--part", "addendum", "--output-dir", "evidence"), "--part addendum needs --addendum"),
        (
            (
                "--part",
                "addendum",
                "--addendum",
                "a.json",
                "--gates",
                "g.json",
                "--not-completed",
                "--output-dir",
                "evidence",
            ),
            "--part addendum does not take --gates, --not-completed",
        ),
        (
            ("--part", "study", *STUDY_EVIDENCE_INPUTS, "--summary", "s.json", "--addendum", "a"),
            "--part study does not take --addendum",
        ),
        (
            ("--part", "study", *STUDY_EVIDENCE_INPUTS[:4], *STUDY_EVIDENCE_INPUTS[6:]),
            "--part study needs --g0",
        ),
        (
            ("--part", "study", *STUDY_EVIDENCE_INPUTS, "--summary", "s.json", "--not-completed"),
            "has no summary",
        ),
        (
            (
                "--part",
                "addendum",
                "--addendum",
                "a.json",
                "--output-dir",
                "docs/evidence/nuplan_aeb_v2/extra",
            ),
            "never written under docs/evidence/nuplan_aeb_v2/",
        ),
    ],
)
def test_study_evidence_refuses_what_it_cannot_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: tuple[str, ...], message: str
) -> None:
    """Options of the other part, a missing input and a writer's refusal each write nothing."""

    monkeypatch.chdir(tmp_path)

    result = study_evidence(*arguments)

    assert result.exit_code == 1
    assert message in result.output
    assert written_files(tmp_path) == []
