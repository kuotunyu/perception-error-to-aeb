"""Contracts for the `running-common-cohort-aeb-studies` skill.

A skill is prose, and prose drifts from the code it describes. These tests are
what stop that: the validator it ships is executed against workspaces built
here, and the commands the skill tells an agent to run are checked to exist.

The skill's job is to stop a plausible number from leaving the repository. That
makes its FALSE-NEGATIVE behaviour the important one — a validator that passed a
broken study would be worse than no validator, because it would carry authority
— so most of what follows plants one specific defect and asserts it is caught.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[3] / ".agents" / "skills" / "running-common-cohort-aeb-studies"
)
VALIDATOR = SKILL_DIR / "scripts" / "validate_common_cohort.py"
SKILL_FILE = SKILL_DIR / "SKILL.md"

GOOD_SETUP = {
    "frequency_hz": 10.0,
    "planner_id": "aebrisk-closed-loop/v1",
    "controller_id": "aebrisk-jerk-limited/v1",
    "termination_s": 15.0,
    "initial_speed_mps": 8.0,
    "route_signature": "a" * 64,
}
TOKENS = [f"s-{index:04d}" for index in range(8)]


def build(
    root: Path,
    *,
    setups: dict[str, dict[str, Any]] | None = None,
    tokens: dict[str, list[str]] | None = None,
    exclusions: dict[str, list[str]] | None = None,
) -> tuple[Path, Path]:
    """A study workspace, comparable unless a caller plants something."""

    names = ("no_aeb", "oracle_aeb", "dropout-medium")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"scenario_tokens": TOKENS}), encoding="utf-8")

    runs = root / "runs"
    for name in names:
        directory = runs / name
        directory.mkdir(parents=True)
        (directory / "run_context.json").write_text(
            json.dumps(
                {
                    "configuration_id": name,
                    "setup": (setups or {}).get(name, GOOD_SETUP),
                    "scenario_tokens": (tokens or {}).get(name, TOKENS),
                }
            ),
            encoding="utf-8",
        )
        excluded = (exclusions or {}).get(name)
        if excluded is not None:
            (directory / "exclusions.json").write_text(
                json.dumps(
                    {"excluded": [{"scenario_token": t, "phase": "step"} for t in excluded]}
                ),
                encoding="utf-8",
            )
    return runs, manifest


def validate(runs: Path, manifest: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--runs-dir",
            str(runs),
            "--manifest",
            str(manifest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


# --------------------------------------------------------------------------
# The validator accepts what it should
# --------------------------------------------------------------------------


def test_comparable_runs_are_accepted(tmp_path: Path) -> None:
    """A validator that refused everything would teach an agent to ignore it."""

    result = validate(*build(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "comparable" in result.stdout


# --------------------------------------------------------------------------
# ...and refuses what it should
# --------------------------------------------------------------------------


def test_a_configuration_at_another_rate_is_refused(tmp_path: Path) -> None:
    """20 Hz is a different controller, not a faster version of the same one."""

    runs, manifest = build(
        tmp_path, setups={"dropout-medium": {**GOOD_SETUP, "frequency_hz": 20.0}}
    )
    result = validate(runs, manifest)

    assert result.returncode == 1
    assert "frequency_hz" in result.stderr


@pytest.mark.parametrize(
    "field",
    ["planner_id", "controller_id", "termination_s", "initial_speed_mps", "route_signature"],
)
def test_any_differing_setup_field_is_refused(tmp_path: Path, field: str) -> None:
    """Each field is part of the sentence the study's claim rests on."""

    runs, manifest = build(tmp_path, setups={"oracle_aeb": {**GOOD_SETUP, field: "something else"}})
    result = validate(runs, manifest)

    assert result.returncode == 1
    assert field in result.stderr


def test_a_partial_matrix_is_refused(tmp_path: Path) -> None:
    """A configuration that ran fewer scenarios is averaged over other roads."""

    runs, manifest = build(tmp_path, tokens={"dropout-medium": TOKENS[:5]})
    result = validate(runs, manifest)

    assert result.returncode == 1
    assert "did not run" in result.stderr


def test_a_configuration_that_ran_extra_scenarios_is_refused(tmp_path: Path) -> None:
    """The cohort is frozen; a run that added to it is not the study's run."""

    runs, manifest = build(tmp_path, tokens={"no_aeb": [*TOKENS, "s-9999"]})
    result = validate(runs, manifest)

    assert result.returncode == 1
    assert "does not name" in result.stderr


def test_a_local_only_exclusion_is_refused(tmp_path: Path) -> None:
    """The quietest failure of the three: each run's own denominator is consistent."""

    runs, manifest = build(
        tmp_path, exclusions={"oracle_aeb": ["s-0003"], "no_aeb": [], "dropout-medium": []}
    )
    result = validate(runs, manifest)

    assert result.returncode == 1
    assert "s-0003" in result.stderr


def test_a_global_exclusion_is_accepted(tmp_path: Path) -> None:
    """The pair to the test above. Excluding everywhere is correct, not an error."""

    runs, manifest = build(
        tmp_path,
        exclusions={"oracle_aeb": ["s-0003"], "no_aeb": ["s-0003"], "dropout-medium": ["s-0003"]},
    )
    result = validate(runs, manifest)

    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# Missing inputs stop rather than default
# --------------------------------------------------------------------------


def test_a_missing_runs_directory_stops(tmp_path: Path) -> None:
    """Exit 2, distinct from 1: nothing was checked, as opposed to something failed."""

    _, manifest = build(tmp_path)
    result = validate(tmp_path / "absent", manifest)

    assert result.returncode == 2


def test_a_missing_manifest_stops(tmp_path: Path) -> None:
    """Without the cohort there is nothing to compare the runs against."""

    runs, _ = build(tmp_path)
    result = validate(runs, tmp_path / "absent.json")

    assert result.returncode == 2


def test_an_empty_runs_directory_stops(tmp_path: Path) -> None:
    """Zero configurations trivially agree with each other, which is not a pass."""

    _, manifest = build(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    result = validate(empty, manifest)

    assert result.returncode == 2


# --------------------------------------------------------------------------
# The resume commands
# --------------------------------------------------------------------------


def test_an_incomplete_run_gets_an_exact_resume_command(tmp_path: Path) -> None:
    """ "Finish the remaining work" must be a command, not an instruction to think."""

    runs, manifest = build(tmp_path, tokens={"dropout-medium": TOKENS[:5]})
    result = validate(runs, manifest)

    assert "aeb-risk simulate" in result.stderr
    assert "--config-id dropout-medium" in result.stderr


def test_the_resume_command_names_a_real_subcommand() -> None:
    """A command the CLI does not have would waste the reader's next five minutes."""

    from aebrisk.cli.app import app

    names = {group.name for group in app.registered_groups}

    assert "simulate" in names


# --------------------------------------------------------------------------
# The skill document
# --------------------------------------------------------------------------


def test_the_skill_declares_its_frontmatter() -> None:
    """Without a description the skill never activates, and prose never runs."""

    text = SKILL_FILE.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    assert "name: running-common-cohort-aeb-studies" in text
    assert "description:" in text


def test_the_skill_names_the_validator_it_ships() -> None:
    """A skill that told an agent to run a script that is not there teaches nothing."""

    text = SKILL_FILE.read_text(encoding="utf-8")

    assert "validate_common_cohort.py" in text
    assert VALIDATOR.is_file()


@pytest.mark.parametrize(
    "requirement",
    [
        "perception-blind",
        "common",
        "global",
        "10 Hz",
        "handoff",
        "sensor",
    ],
)
def test_the_skill_states_every_requirement(requirement: str) -> None:
    """The six checks the RED baseline showed a capable agent omits."""

    assert requirement in SKILL_FILE.read_text(encoding="utf-8")


def test_the_skill_says_what_it_is_not_for() -> None:
    """A skill that activated on every mention of braking would be noise."""

    text = SKILL_FILE.read_text(encoding="utf-8")

    assert "not for" in text.lower()


def test_a_run_directory_with_no_context_is_refused(tmp_path: Path) -> None:
    """A broken run must be refused, not skipped.

    Silently ignoring a directory that has no run context is a false negative
    in a gate whose whole job is to stop a plausible number: the remaining
    configurations would agree with each other and pass.
    """

    runs, manifest = build(tmp_path)
    (runs / "half-written").mkdir()

    result = validate(runs, manifest)

    assert result.returncode == 1
    assert "half-written" in result.stderr
