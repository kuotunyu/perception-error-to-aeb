"""The public operator procedure of the policy v2 study, and the settings its runs use.

`docs/verification/policy-v2-study.md` gives, step by step, the commands that
run the study and publish it. A reader repeats them as written, so every
`aeb-risk study` command there must be one the command line declares, given
only options that command declares, with repository-relative paths and the
dataset root written as `$NUPLAN_DATA_ROOT`.

`compose.formal-cpu.yaml` is the override every formal and pilot arm runs
under. It carries the limits and the single-threaded numerical libraries of
the v1.0.0 run, because arm A must reproduce that run's records byte for byte.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import typer.main
import yaml

from aebrisk.cli.app import app

ROOT = Path(__file__).resolve().parents[2]
PROCEDURE = ROOT / "docs" / "verification" / "policy-v2-study.md"

#: The seven study commands, in the order a study uses them.
STUDY_COMMANDS = ("preflight", "simulate", "verify", "addendum", "analyse", "evidence", "claims")

FENCED_BLOCK = re.compile(r"^```[a-z]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
STUDY_COMMAND = re.compile(r"aeb-risk study (?P<command>\S+)(?P<rest>.*)")
OPTION = re.compile(r"(?<!\S)--[a-z0-9-]+")
DRIVE_PATH = re.compile(r"\b[A-Za-z]:[\\/]")


def code_lines() -> list[str]:
    """Every line of the procedure's fenced blocks, with continued lines joined."""

    text = PROCEDURE.read_text(encoding="utf-8")
    return [
        line
        for block in FENCED_BLOCK.findall(text)
        for line in block.replace("\\\n", " ").splitlines()
    ]


def declared_study_options() -> dict[str, set[str]]:
    root = typer.main.get_command(app)
    assert isinstance(root, click.Group)
    study = root.commands["study"]
    assert isinstance(study, click.Group)
    return {
        name: {option for parameter in command.params for option in parameter.opts}
        for name, command in study.commands.items()
    }


def test_every_study_command_of_the_procedure_is_declared_with_the_options_it_is_given() -> None:
    declared = declared_study_options()
    used: list[str] = []
    for line in code_lines():
        match = STUDY_COMMAND.search(line)
        if match is not None:
            command = match.group("command")
            assert command in declared, line
            assert set(OPTION.findall(match.group("rest"))) <= declared[command], line
            used.append(command)

    assert set(used) == set(STUDY_COMMANDS) == set(declared)


def test_the_procedure_names_repository_relative_paths_and_the_dataset_root_variable() -> None:
    text = PROCEDURE.read_text(encoding="utf-8")
    arguments = [argument for line in code_lines() for argument in line.split()]

    assert DRIVE_PATH.search(text) is None
    assert [argument for argument in arguments if argument.lstrip("\"'").startswith("/")] == []
    assert "$NUPLAN_DATA_ROOT" in text


def test_the_formal_cpu_override_carries_the_limits_and_threads_of_the_released_run() -> None:
    document = yaml.safe_load((ROOT / "compose.formal-cpu.yaml").read_text(encoding="utf-8"))

    assert document == {
        "services": {
            "dev": {
                "cpus": 8.0,
                "mem_limit": "12g",
                "memswap_limit": "12g",
                "environment": {
                    "PYTHONUNBUFFERED": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1",
                    "NUMEXPR_NUM_THREADS": "1",
                },
            }
        }
    }
