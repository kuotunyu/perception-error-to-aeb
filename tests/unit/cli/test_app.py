"""Contracts for the command line, which is thin on purpose.

Every stage of this study is a function that takes explicit inputs and returns
explicit outputs. The CLI parses arguments, calls one of those functions, and
turns a refusal into an exit code. It holds no logic of its own, because logic
in a CLI is logic that can only be tested by running a process.

Two behaviours belong to the CLI rather than to the stages, and both are tested
here. A REFUSAL EXITS NON-ZERO with the reason on stderr, so that a pipeline
stops instead of continuing over a missing cohort. And EVERY STAGE RECORDS THE
HASHES that identify what it ran: the configuration, the cohort, the container
and the commit. A result whose inputs cannot be named is not reproducible, and
this is where the naming happens.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner


def runner() -> CliRunner:
    return CliRunner()


def invoke(*arguments: str) -> Any:
    from aebrisk.cli.app import app

    return runner().invoke(app, list(arguments))


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_the_application_answers_help() -> None:
    """Part of the packaging contract since the first commit."""

    result = invoke("--help")

    assert result.exit_code == 0


@pytest.mark.parametrize("command", ["data", "simulate", "evaluate", "report"])
def test_every_stage_is_reachable(command: str) -> None:
    """The four stages of the study, each addressable on its own."""

    result = invoke(command, "--help")

    assert result.exit_code == 0


def test_no_arguments_prints_usage_rather_than_doing_nothing() -> None:
    """Printing usage is the framework's answer to a bare invocation, and it is enough.

    The dangerous case is a STAGE that does nothing and exits zero, and each of
    those refuses explicitly in the tests below. A bare `aeb-risk` is not a
    pipeline step, so its exit code is not load-bearing here.
    """

    result = invoke()

    assert "Usage" in result.output


# --------------------------------------------------------------------------
# Refusals exit non-zero
# --------------------------------------------------------------------------


def test_a_missing_mount_exits_non_zero(tmp_path: Path) -> None:
    """A pipeline must stop here, not continue over an absent database."""

    result = invoke(
        "data",
        "preflight",
        "--db-root",
        str(tmp_path / "absent"),
        "--map-root",
        str(tmp_path / "absent"),
        "--split",
        "mini",
        "--output",
        str(tmp_path / "preflight.json"),
    )

    assert result.exit_code != 0


def test_a_refusal_says_why(tmp_path: Path) -> None:
    """An exit code alone cannot be acted on."""

    result = invoke(
        "data",
        "preflight",
        "--db-root",
        str(tmp_path / "absent"),
        "--map-root",
        str(tmp_path / "absent"),
        "--split",
        "mini",
        "--output",
        str(tmp_path / "preflight.json"),
    )

    assert "absent" in result.output or "does not exist" in result.output


def test_an_unknown_configuration_is_refused(tmp_path: Path) -> None:
    """A typo would otherwise produce an empty result directory and exit zero."""

    result = invoke(
        "simulate",
        "--protocol",
        str(tmp_path),
        "--manifest",
        str(tmp_path),
        "--config-id",
        "not-a-configuration",
        "--output-dir",
        str(tmp_path / "out"),
    )

    assert result.exit_code != 0
    assert "not-a-configuration" in result.output


def test_a_missing_run_index_is_refused(tmp_path: Path) -> None:
    """Evaluating nothing would produce a report with no data and no error."""

    result = invoke(
        "evaluate",
        "--run-index",
        str(tmp_path / "absent.json"),
        "--output-dir",
        str(tmp_path / "out"),
    )

    assert result.exit_code != 0


def test_missing_claims_are_refused(tmp_path: Path) -> None:
    """A report is a set of claims about artifacts; without them it is decoration."""

    result = invoke(
        "report",
        "--claims",
        str(tmp_path / "absent.yaml"),
        "--artifacts-dir",
        str(tmp_path),
        "--output-dir",
        str(tmp_path / "site"),
    )

    assert result.exit_code != 0


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_the_run_context_names_everything_that_identifies_a_run() -> None:
    """A result whose inputs cannot be named is not reproducible.

    The four hashes are what a reader compares when two runs disagree, so the
    field list is asserted rather than described.
    """

    import dataclasses

    from aebrisk.cli.simulate import RunContext

    names = {field.name for field in dataclasses.fields(RunContext)}

    assert names == {
        "configuration_id",
        "protocol_sha256",
        "cohort_sha256",
        "container_digest",
        "commit",
    }


def test_the_run_context_refuses_an_unnamed_input() -> None:
    """An empty hash reads as recorded and identifies nothing."""

    from aebrisk.cli.simulate import RunContext

    with pytest.raises(ValueError, match=r"^protocol_sha256 must not be empty; "):
        RunContext(
            configuration_id="oracle_aeb",
            protocol_sha256="",
            cohort_sha256="a" * 64,
            container_digest="sha256:" + "b" * 64,
            commit="c" * 40,
        )


def test_the_container_digest_is_read_from_the_environment() -> None:
    """The image is part of the environment a result was produced in.

    It is read rather than guessed, because a wrong digest is worse than an
    absent one: it claims a reproducibility that was never checked.
    """

    from aebrisk.cli.simulate import container_digest

    digest = container_digest({"AEBRISK_IMAGE_DIGEST": "sha256:" + "a" * 64})

    assert digest == "sha256:" + "a" * 64


def test_an_absent_container_digest_is_recorded_as_unknown() -> None:
    """Running outside the container is possible and must be visible, not silent."""

    from aebrisk.cli.simulate import container_digest

    assert container_digest({}) == "unknown"


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def test_a_written_run_record_pins_its_line_ending(tmp_path: Path) -> None:
    """Every artifact this portfolio writes fixes its bytes."""

    from aebrisk.cli.simulate import RunContext, write_run_context

    path = tmp_path / "run.json"
    write_run_context(
        RunContext(
            configuration_id="oracle_aeb",
            protocol_sha256="a" * 64,
            cohort_sha256="b" * 64,
            container_digest="unknown",
            commit="c" * 40,
        ),
        path,
    )

    assert b"\r\n" not in path.read_bytes()


def test_a_written_run_record_is_readable_json(tmp_path: Path) -> None:
    """It is read back by the evaluate stage, so it has to parse."""

    from aebrisk.cli.simulate import RunContext, write_run_context

    path = tmp_path / "run.json"
    write_run_context(
        RunContext(
            configuration_id="oracle_aeb",
            protocol_sha256="a" * 64,
            cohort_sha256="b" * 64,
            container_digest="unknown",
            commit="c" * 40,
        ),
        path,
    )

    assert json.loads(path.read_text(encoding="utf-8"))["configuration_id"] == "oracle_aeb"


def test_an_empty_container_digest_is_refused() -> None:
    """ "Unknown" is the honest value; an empty string reads as recorded."""

    from aebrisk.cli.simulate import RunContext

    with pytest.raises(
        ValueError, match=r"^container_digest\ must\ not\ be\ empty;\ use\ 'unknown'\ instead"
    ):
        RunContext(
            configuration_id="oracle_aeb",
            protocol_sha256="a" * 64,
            cohort_sha256="b" * 64,
            container_digest="",
            commit="c" * 40,
        )


# --------------------------------------------------------------------------
# data census
# --------------------------------------------------------------------------


def _installation(root: Path, split: str = "mini", databases: int = 2) -> Path:
    """Build the smallest tree `resolve_installation` accepts."""

    split_dir = root / "nuplan-v1.1" / "splits" / split
    split_dir.mkdir(parents=True)
    for index in range(databases):
        (split_dir / f"log{index}.db").write_bytes(b"")
    (root / "maps").mkdir()
    return root


def _protocol(path: Path) -> Path:
    """A protocol with two families, enough to exercise the mapping."""

    path.write_text(
        "scenario_families:\n"
        "  lead_or_stopping:\n"
        "    - stopping_with_lead\n"
        "  bicycle_or_vru:\n"
        "    - behind_bike\n",
        encoding="utf-8",
    )
    return path


def test_the_census_counts_families_by_log_and_writes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether a family spans two logs decides whether a cohort can be frozen at all."""

    from aebrisk.cohort import census as census_module

    tags = {
        "log0.db": [("stopping_with_lead", "t1"), ("behind_bike", "t2")],
        "log1.db": [("stopping_with_lead", "t3")],
    }
    monkeypatch.setattr(
        census_module, "QUERY_TAGS", lambda log_file: iter(tags[Path(log_file).name])
    )
    root = _installation(tmp_path / "root")
    output = tmp_path / "out" / "mini.json"

    result = invoke(
        "data",
        "census",
        "--db-root",
        str(root),
        "--protocol",
        str(_protocol(tmp_path / "protocol.yaml")),
        "--split",
        "mini",
        "--output",
        str(output),
    )

    assert result.exit_code == 0, result.output
    document = json.loads(output.read_text(encoding="utf-8"))
    families = {entry["family"]: entry for entry in document["families"]}
    assert families["lead_or_stopping"]["logs"] == 2
    assert families["lead_or_stopping"]["splittable"] is True
    assert families["bicycle_or_vru"]["logs"] == 1
    assert families["bicycle_or_vru"]["splittable"] is False
    assert document["databases_read"] == 2


def test_a_census_of_a_missing_installation_is_refused(tmp_path: Path) -> None:
    """Failing in a second with the path beats a driver error deep in a query."""

    result = invoke(
        "data",
        "census",
        "--db-root",
        str(tmp_path / "absent"),
        "--protocol",
        str(_protocol(tmp_path / "protocol.yaml")),
        "--output",
        str(tmp_path / "out.json"),
    )

    assert result.exit_code == 1
    assert "census failed" in result.output


def _full_protocol(path: Path) -> Path:
    """A protocol naming all four families, which is what a manifest requires."""

    path.write_text(
        "scenario_families:\n"
        "  lead_or_stopping:\n"
        "    - stopping_with_lead\n"
        "  cut_in_or_crossing:\n"
        "    - changing_lane\n"
        "  pedestrian_or_crosswalk:\n"
        "    - waiting_for_pedestrian_to_cross\n"
        "  bicycle_or_vru:\n"
        "    - behind_bike\n",
        encoding="utf-8",
    )
    return path


def _frozen(split: Any = "development") -> Any:
    """A frozen cohort, so the command's own wiring can be tested without recordings."""

    from aebrisk.cohort.freeze import FrozenSplit
    from aebrisk.cohort.manifest import CohortManifestV1
    from aebrisk.cohort.prefilter import Eligibility

    manifest = CohortManifestV1(
        schema_version="aeb-cohort-manifest/v1",
        split=split,
        protocol_sha256="a" * 64,
        families={
            "lead_or_stopping": ("t-0001",),
            "cut_in_or_crossing": (),
            "pedestrian_or_crosswalk": (),
            "bicycle_or_vru": (),
        },
        log_names=("log0.db",),
    )
    return FrozenSplit(
        split=split,
        manifest=manifest,
        eligibility=(
            Eligibility(
                scenario_token="t-0001",
                log_name="log0.db",
                scenario_type="stopping_with_lead",
                family="lead_or_stopping",
                official_split="train",
                accepted=True,
                reason="",
                initial_ego_speed_mps=8.0,
                oracle_enters_corridor_within_4s=True,
                oracle_min_ttc_within_4s=1.2,
            ),
        ),
        scenarios_in_split_by_family={
            "lead_or_stopping": 12,
            "cut_in_or_crossing": 0,
            "pedestrian_or_crosswalk": 0,
            "bicycle_or_vru": 0,
        },
    )


def test_the_freeze_writes_a_manifest_and_the_evidence_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest is what a published number is checked against; the evidence says why.

    The cohort itself is built in `tests/unit/cohort/test_freeze.py` over fake
    recordings. What is under test here is the command: that it derives the
    official split from the cohort, writes both documents, and reports the
    counts an operator needs.
    """

    monkeypatch.setattr("aebrisk.cli.data.freeze_split", lambda *args, **keywords: _frozen())
    root = _installation(tmp_path / "root", split="train")
    output = tmp_path / "manifests"

    result = invoke(
        "data",
        "freeze",
        "--db-root",
        str(root),
        "--protocol",
        str(_full_protocol(tmp_path / "protocol.yaml")),
        "--output-dir",
        str(output),
        "--split",
        "development",
    )

    assert result.exit_code == 0, result.output
    assert "freezing development from 2 'train' databases" in result.output
    assert "lead_or_stopping: 1 frozen of 12 in the split" in result.output
    assert (output / "development.json").is_file()
    evidence = json.loads((output / "development-eligibility.json").read_text(encoding="utf-8"))
    assert evidence["examined"][0]["scenario_token"] == "t-0001"


def test_refreezing_a_cohort_that_exists_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freeze that can be silently redone invalidates every result that cited it."""

    monkeypatch.setattr("aebrisk.cli.data.freeze_split", lambda *args, **keywords: _frozen())
    root = _installation(tmp_path / "root", split="train")
    output = tmp_path / "manifests"
    arguments = (
        "data",
        "freeze",
        "--db-root",
        str(root),
        "--protocol",
        str(_full_protocol(tmp_path / "protocol.yaml")),
        "--output-dir",
        str(output),
        "--split",
        "development",
    )

    assert invoke(*arguments).exit_code == 0
    second = invoke(*arguments)

    assert second.exit_code == 1
    assert "refusing to overwrite" in second.output


def test_a_freeze_of_a_missing_installation_is_refused(tmp_path: Path) -> None:
    """Hours of reading recordings must not start against a root that is not there."""

    result = invoke(
        "data",
        "freeze",
        "--db-root",
        str(tmp_path / "absent"),
        "--protocol",
        str(_full_protocol(tmp_path / "protocol.yaml")),
        "--output-dir",
        str(tmp_path / "manifests"),
    )

    assert result.exit_code == 1
    assert "freeze failed" in result.output


def test_a_cohort_name_that_is_not_a_cohort_is_refused(tmp_path: Path) -> None:
    """Only two cohorts exist, and a third name would silently freeze neither."""

    result = invoke(
        "data",
        "freeze",
        "--db-root",
        str(_installation(tmp_path / "root", split="train")),
        "--protocol",
        str(_full_protocol(tmp_path / "protocol.yaml")),
        "--output-dir",
        str(tmp_path / "manifests"),
        "--split",
        "holdout",
    )

    assert result.exit_code != 0
    assert "holdout" in result.output
