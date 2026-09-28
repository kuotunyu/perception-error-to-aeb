"""Deterministic, fail-fast repository verification.

One command, one fixed order, no arguments that change what "verified" means.
The stage names and their order are identical to the other two portfolio
repositories so that a release gate can be compared across all three. Here the
test stage records branch coverage once and branch_coverage_100 reports on that
data, instead of running the suite a second time.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ValidationError

from aebrisk.artifacts.documents import DOCUMENT_MODELS
from aebrisk.cohort.manifest import CohortEligibilityV1, CohortManifestV1

VERIFY_STAGES: tuple[str, ...] = (
    "private_guard",
    "format_check",
    "lint",
    "typecheck",
    "unit_and_integration_tests",
    "branch_coverage_100",
    "schema_contracts",
    "docs_links",
)

#: Every document kind published under the directories of PUBLISHED_DOCUMENT_ROOTS,
#: by its schema_version. A document whose version is not listed fails the gate
#: instead of passing unread.
EVIDENCE_MODELS: dict[str, type[BaseModel]] = {
    **DOCUMENT_MODELS,
    "aeb-cohort-manifest/v1": CohortManifestV1,
    "aeb-cohort-eligibility/v1": CohortEligibilityV1,
}

#: Where published JSON documents live: the released evidence, the policy v2
#: study and the post-hoc addendum. Every JSON file under them is read.
PUBLISHED_DOCUMENT_ROOTS: tuple[tuple[str, ...], ...] = (
    ("docs", "evidence"),
    ("docs", "studies"),
    ("docs", "posthoc"),
)

StageRunner = Callable[[str, Sequence[str], Path], int]
_MARKDOWN_LINK = re.compile(r"!?\[[^]]*\]\((?P<target><[^>]+>|[^)\s]+)")
_EXCLUDED_DIRECTORIES = frozenset({".git", ".venv", "build", "dist", "htmlcov", "datasets"})


def _stage_commands() -> dict[str, tuple[str, ...]]:
    python = sys.executable
    return {
        "private_guard": (python, "-m", "aebrisk.private_guard"),
        "format_check": (python, "-m", "ruff", "format", "--check", "."),
        "lint": (python, "-m", "ruff", "check", "."),
        "typecheck": (python, "-m", "mypy", "src", "tests"),
        # The suite runs once. It records branch coverage without judging it, so
        # a shortfall fails the stage named for it rather than the test stage.
        "unit_and_integration_tests": (
            python,
            "-m",
            "pytest",
            "--cov=aebrisk",
            "--cov-branch",
            "--cov-report=",
            "--cov-fail-under=0",
        ),
        "branch_coverage_100": (
            python,
            "-m",
            "coverage",
            "report",
            "--show-missing",
            "--fail-under=100",
        ),
        "schema_contracts": (python, "-m", "aebrisk.dev", "schema-contracts"),
        "docs_links": (python, "-m", "aebrisk.dev", "docs-links"),
    }


def subprocess_runner(stage: str, command: Sequence[str], cwd: Path) -> int:
    """Run one verification subprocess and preserve its exact exit code."""

    del stage
    return subprocess.run(list(command), cwd=str(cwd), check=False).returncode


def verify_repository(repo_root: Path, runner: StageRunner = subprocess_runner) -> int:
    """Run every stage in order, stopping at the first non-zero exit.

    Stopping early is deliberate: a later stage run against a tree that already
    failed an earlier one reports failures that are consequences, and a reader
    then has to work out which failure is the cause.
    """

    commands = _stage_commands()
    for stage in VERIFY_STAGES:
        print(f"[verify] {stage}")
        exit_code = runner(stage, commands[stage], repo_root)
        if exit_code != 0:
            print(f"{stage} failed with exit code {exit_code}", file=sys.stderr)
            return exit_code
    return 0


def verify_schema_contracts(repo_root: Path) -> int:
    """Parse schemas and validate every published document against its model."""

    invalid: list[Path] = []
    for path in sorted((repo_root / "schemas").glob("**/*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            invalid.append(path)

    invalid_evidence: list[Path] = []
    unregistered_evidence: list[Path] = []
    published = (
        path
        for parts in PUBLISHED_DOCUMENT_ROOTS
        for path in repo_root.joinpath(*parts).glob("**/*.json")
    )
    for path in sorted(published):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            version = payload.get("schema_version") if isinstance(payload, dict) else None
            model = EVIDENCE_MODELS.get(version) if isinstance(version, str) else None
            if model is None:
                unregistered_evidence.append(path)
            else:
                model.model_validate(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            invalid_evidence.append(path)

    for path in invalid:
        print(
            f"invalid JSON schema: {path.relative_to(repo_root).as_posix()}",
            file=sys.stderr,
        )
    for path in invalid_evidence:
        print(
            f"invalid evidence document: {path.relative_to(repo_root).as_posix()}",
            file=sys.stderr,
        )
    for path in unregistered_evidence:
        print(
            f"unregistered evidence document: {path.relative_to(repo_root).as_posix()}",
            file=sys.stderr,
        )
    return 1 if invalid or invalid_evidence or unregistered_evidence else 0


def _iter_markdown_files(repo_root: Path) -> list[Path]:
    return [
        path
        for path in sorted(repo_root.glob("**/*.md"))
        if not _EXCLUDED_DIRECTORIES.intersection(path.relative_to(repo_root).parts)
    ]


def verify_docs_links(repo_root: Path) -> int:
    """Report every local file a Markdown link points at and does not exist."""

    broken: list[tuple[Path, str]] = []
    for markdown_path in _iter_markdown_files(repo_root):
        text = markdown_path.read_text(encoding="utf-8")
        for match in _MARKDOWN_LINK.finditer(text):
            raw_target = match.group("target").strip("<>")
            parsed = urlsplit(raw_target)
            # Written as a positive condition rather than an early `continue`
            # because CPython 3.9 attributes no line to a bare `continue`, so
            # the branch is untraceable and the coverage gate cannot pass.
            is_local_file = bool(parsed.path) and not parsed.scheme and raw_target[:1] != "#"
            if is_local_file:
                decoded_path = unquote(parsed.path)
                if decoded_path.startswith("/"):
                    target = repo_root / decoded_path.lstrip("/")
                else:
                    target = markdown_path.parent / decoded_path
                if not target.exists():
                    broken.append((markdown_path, raw_target))

    for source, missing_target in broken:
        print(
            f"broken local link: {source.relative_to(repo_root).as_posix()} -> {missing_target}",
            file=sys.stderr,
        )
    return 1 if broken else 0


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: StageRunner = subprocess_runner,
) -> int:
    """Dispatch the public developer verification commands."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "schema-contracts", "docs-links"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    if args.command == "verify":
        return verify_repository(repo_root, runner=runner)
    if args.command == "schema-contracts":
        return verify_schema_contracts(repo_root)
    return verify_docs_links(repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
