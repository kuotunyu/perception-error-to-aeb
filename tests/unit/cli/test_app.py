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


def test_the_cohort_hash_is_order_independent() -> None:
    """A cohort is a set; two orderings of it are the same cohort.

    If the hash depended on order, the same study would look like two.
    """

    from aebrisk.cli.simulate import cohort_sha256

    assert cohort_sha256(("s-2", "s-1")) == cohort_sha256(("s-1", "s-2"))


def test_the_cohort_hash_changes_with_the_cohort() -> None:
    """The pair to the test above; a constant hash would satisfy it alone."""

    from aebrisk.cli.simulate import cohort_sha256

    assert cohort_sha256(("s-1",)) != cohort_sha256(("s-1", "s-2"))


def test_an_empty_cohort_has_no_hash() -> None:
    """Hashing an empty set would give a stable value for "nothing was run"."""

    from aebrisk.cli.simulate import cohort_sha256

    with pytest.raises(ValueError, match=r"^the cohort is empty; "):
        cohort_sha256(())


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
