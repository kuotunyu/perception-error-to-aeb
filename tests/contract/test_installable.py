"""Installation and repository-layout contracts for the package foundation."""

from __future__ import annotations

import importlib
import subprocess
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTION = "perception-error-to-aeb"
PINNED_NUPLAN_URL = (
    "git+https://github.com/motional/nuplan-devkit@e9241677997dd86bfc0bcd44817ab04fe631405b"
)
EXCLUDED_STACKS = ("torch", "ray", "bokeh", "jupyter", "opencv", "pytorch-lightning")


def test_console_entrypoint_imports() -> None:
    """Removing ``aebrisk.cli.app`` must break the public CLI contract."""

    try:
        module = importlib.import_module("aebrisk.cli.app")
    except ModuleNotFoundError:
        pytest.fail("the declared aebrisk.cli.app module is missing", pytrace=False)

    assert callable(module.app)


def test_nuplan_is_pinned_by_commit_in_the_package_metadata() -> None:
    """The dependency must name the commit, so a moving branch can never be installed."""

    requirements = [Requirement(value) for value in metadata.requires(DISTRIBUTION) or []]
    nuplan = [requirement for requirement in requirements if requirement.name == "nuplan-devkit"]

    assert len(nuplan) == 1, requirements
    requirement = nuplan[0]
    assert requirement.url == PINNED_NUPLAN_URL
    assert not requirement.extras
    assert not requirement.specifier
    assert requirement.marker is None


def test_requires_python_is_exactly_the_3_9_series() -> None:
    """nuPlan at the pinned commit is a 3.9 codebase; the package must say so.

    The clauses are compared as a set because the build backend is free to
    reorder them, and a test that pins their order would fail on a packaging
    upgrade while saying nothing about the interpreter.
    """

    declared = metadata.metadata(DISTRIBUTION)["Requires-Python"]

    assert {clause.strip() for clause in declared.split(",")} == {">=3.9", "<3.10"}


def test_no_learned_model_or_sensor_stack_is_declared() -> None:
    """P3 trains nothing and reads no sensor data, so those stacks stay out of the lock."""

    requirements = [value.lower() for value in metadata.requires(DISTRIBUTION) or []]

    for stack in EXCLUDED_STACKS:
        assert not any(value.startswith(stack) for value in requirements), stack


def test_package_code_under_src_is_not_ignored() -> None:
    """A broad dataset ignore must never hide package code under ``src``."""

    if not (REPO_ROOT / ".git").exists():
        # The mutation audit runs this suite from a copy of the tree inside the
        # checkout, which the checkout's own ignore rules cover as a whole.
        pytest.skip("a copy of the tree, such as mutmut's mutants/, is ignored by its checkout")

    result = subprocess.run(
        ["git", "check-ignore", "-q", "src/aebrisk/__init__.py"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stderr


def check_ignore(path: str) -> int:
    return subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    ).returncode


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "mutants/src/aebrisk/aeb/threat.py",
    ],
)
def test_local_secrets_and_mutation_output_are_ignored(path: str) -> None:
    """A broad `git add -A` must not be able to publish local secrets or mutants."""

    assert check_ignore(path) == 0


def test_an_environment_template_stays_committable() -> None:
    """The private guard accepts `.env.example`, so the ignore rules must too."""

    if not (REPO_ROOT / ".git").exists():
        # See test_package_code_under_src_is_not_ignored.
        pytest.skip("a copy of the tree, such as mutmut's mutants/, is ignored by its checkout")

    assert check_ignore(".env.example") == 1


def test_verifier_declares_the_eight_fixed_stages() -> None:
    """The release gate is only comparable across repositories if the stages match."""

    try:
        from aebrisk.dev import VERIFY_STAGES
    except ModuleNotFoundError:
        pytest.fail("aebrisk.dev is missing", pytrace=False)

    assert VERIFY_STAGES == (
        "private_guard",
        "format_check",
        "lint",
        "typecheck",
        "unit_and_integration_tests",
        "branch_coverage_100",
        "schema_contracts",
        "docs_links",
    )


def test_installed_console_command_prints_help() -> None:
    """A wheel that installs but cannot answer --help is not usable."""

    result = subprocess.run(
        ["aeb-risk", "--help"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "perception errors" in result.stdout
