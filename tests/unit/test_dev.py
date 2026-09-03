"""Behavior tests for the deterministic verification runner."""

from __future__ import annotations

import json
import runpy
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from aebrisk import dev


def record_runner(recorded: list[str], failing_stage: str = "") -> dev.StageRunner:
    """A runner that records the stages it is asked to run and can fail one."""

    def runner(stage: str, command: Sequence[str], cwd: Path) -> int:
        del command, cwd
        recorded.append(stage)
        return 3 if stage == failing_stage else 0

    return runner


def test_the_stage_order_is_fixed() -> None:
    """Reordering the gate would let a commit pass a stage it should not reach."""

    assert dev.VERIFY_STAGES == (
        "private_guard",
        "format_check",
        "lint",
        "typecheck",
        "unit_and_integration_tests",
        "branch_coverage_100",
        "schema_contracts",
        "docs_links",
    )


def test_every_stage_has_a_command() -> None:
    """A stage without a command would silently verify nothing."""

    commands = dev._stage_commands()

    assert tuple(commands) == dev.VERIFY_STAGES
    for command in commands.values():
        assert command[0] == sys.executable
        assert len(command) > 1


def test_verify_runs_every_stage_in_order_and_returns_zero(tmp_path: Path) -> None:
    """The success path must run the whole gate, not stop at the first pass."""

    recorded: list[str] = []

    assert dev.verify_repository(tmp_path, runner=record_runner(recorded)) == 0
    assert tuple(recorded) == dev.VERIFY_STAGES


def test_verify_stops_at_the_first_failing_stage_and_returns_its_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Continuing past a failure reports consequences instead of the cause."""

    recorded: list[str] = []

    exit_code = dev.verify_repository(tmp_path, runner=record_runner(recorded, "lint"))

    assert exit_code == 3
    assert tuple(recorded) == ("private_guard", "format_check", "lint")
    assert "lint failed with exit code 3" in capsys.readouterr().err


def test_subprocess_runner_returns_the_child_exit_code(tmp_path: Path) -> None:
    """The gate is only as honest as the exit code it passes through."""

    assert (
        dev.subprocess_runner("probe", (sys.executable, "-c", "raise SystemExit(7)"), tmp_path) == 7
    )


def write_schema(repo_root: Path, name: str, text: str) -> Path:
    schemas = repo_root / "schemas"
    schemas.mkdir(exist_ok=True)
    path = schemas / name
    path.write_text(text, encoding="utf-8")
    return path


def test_schema_contracts_accepts_valid_json(tmp_path: Path) -> None:
    """The success path must pass, or the stage would block every commit."""

    write_schema(tmp_path, "run_record_v1.json", json.dumps({"type": "object"}))

    assert dev.verify_schema_contracts(tmp_path) == 0


def test_schema_contracts_reports_every_malformed_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stopping at the first malformed schema hides the rest from the operator."""

    write_schema(tmp_path, "a.json", "{not json")
    write_schema(tmp_path, "b.json", "[")

    assert dev.verify_schema_contracts(tmp_path) == 1
    error = capsys.readouterr().err
    assert "invalid JSON schema: schemas/a.json" in error
    assert "invalid JSON schema: schemas/b.json" in error


def markdown(repo_root: Path, name: str, text: str) -> Path:
    path = repo_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "link",
    [
        "[external](https://example.com/missing.md)",
        "[anchor](#section)",
        "[query only](?q=1)",
    ],
)
def test_docs_links_ignores_links_that_are_not_local_files(tmp_path: Path, link: str) -> None:
    """Resolving remote or in-page links as paths would report false breakage."""

    markdown(tmp_path, "README.md", link)

    assert dev.verify_docs_links(tmp_path) == 0


def test_docs_links_accepts_relative_and_root_relative_targets(tmp_path: Path) -> None:
    """Both link styles appear in the docs, so both must resolve."""

    markdown(tmp_path, "docs/page.md", "[sibling](other.md) and [root](/README.md)")
    markdown(tmp_path, "docs/other.md", "target")
    markdown(tmp_path, "README.md", "target")

    assert dev.verify_docs_links(tmp_path) == 0


def test_docs_links_decodes_percent_escapes(tmp_path: Path) -> None:
    """A link to a file whose name has a space is written escaped."""

    markdown(tmp_path, "README.md", "[spaced](docs/a%20b.md)")
    markdown(tmp_path, "docs/a b.md", "target")

    assert dev.verify_docs_links(tmp_path) == 0


def test_docs_links_reports_a_missing_local_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A broken link in a released README is a promise the repository breaks."""

    markdown(tmp_path, "README.md", "[gone](docs/missing.md)")

    assert dev.verify_docs_links(tmp_path) == 1
    assert "broken local link: README.md -> docs/missing.md" in capsys.readouterr().err


def test_docs_links_skips_excluded_directories(tmp_path: Path) -> None:
    """Vendored or mounted trees are not this repository's promises to keep."""

    markdown(tmp_path, "datasets/vendor.md", "[gone](nowhere.md)")

    assert dev.verify_docs_links(tmp_path) == 0


@pytest.mark.parametrize(
    ("command", "expected"),
    [("schema-contracts", 0), ("docs-links", 0)],
)
def test_main_dispatches_the_read_only_commands(
    tmp_path: Path,
    command: str,
    expected: int,
) -> None:
    """Each subcommand must reach its own checker, not the full gate."""

    write_schema(tmp_path, "ok.json", "{}")
    markdown(tmp_path, "README.md", "no links")

    assert dev.main([command, "--repo-root", str(tmp_path)]) == expected


def test_main_dispatches_verify_through_the_injected_runner(tmp_path: Path) -> None:
    """`verify` is the command CI runs; it must reach the stage sequence."""

    recorded: list[str] = []

    exit_code = dev.main(["verify", "--repo-root", str(tmp_path)], runner=record_runner(recorded))

    assert exit_code == 0
    assert tuple(recorded) == dev.VERIFY_STAGES


def test_main_defaults_the_repository_root_to_the_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI invokes the module from the repository root without an argument."""

    write_schema(tmp_path, "ok.json", "{}")
    monkeypatch.chdir(tmp_path)

    assert dev.main(["schema-contracts"]) == 0


def test_module_entrypoint_exits_with_the_command_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `python -m aebrisk.dev` form CI uses must return the same code."""

    assert dev.__file__ is not None
    write_schema(tmp_path, "broken.json", "{")
    monkeypatch.setattr(sys, "argv", ["dev.py", "schema-contracts", "--repo-root", str(tmp_path)])

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(Path(dev.__file__)), run_name="__main__")

    assert raised.value.code == 1


def test_module_entrypoint_is_wired_to_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`python -m aebrisk.cli.app --help` is the smoke check a clean clone runs."""

    from aebrisk.cli import app as app_module

    assert app_module.__file__ is not None
    monkeypatch.setattr(sys, "argv", ["app.py", "--help"])

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(Path(app_module.__file__)), run_name="__main__")

    assert raised.value.code == 0
