"""Refuse private state, credentials, and licensed data in the Git index.

The guard reads Git's index rather than the working directory on purpose. A
developer's untracked `.env`, a mounted dataset, or a local simulation output
is not a leak; the same bytes staged for commit are. Reading the index is also
what makes the guard usable as a pre-commit gate: it answers the question the
commit is about to make permanent.

It fails closed. Anything it cannot decode, cannot read, or does not recognise
stops the commit rather than being waved through.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

PRIVATE_MARKER = "PRIVATE HANDOFF" + " - DO NOT COMMIT"

#: Credential shapes that have a distinctive prefix and a fixed length. A
#: general entropy heuristic was rejected: it flags base64 fixtures and hashes,
#: and a guard that cries wolf is a guard that gets bypassed.
CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"hf_[A-Za-z0-9]{34,}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{82}"),
)

#: nuPlan ships SQLite log databases and GeoPackage maps; both are licensed to
#: the account holder. The archive and model suffixes are here because a
#: repository that accepts one large binary tends to accept the next.
FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    ".db",
    ".gpkg",
    ".npz",
    ".onnx",
    ".pt",
    ".pth",
    ".tar.gz",
    ".tgz",
    ".zip",
)


class GitIndexError(RuntimeError):
    """Raised when the guard cannot inspect the repository index safely."""


def _normalise_path(tracked_path: str) -> str:
    normalised = tracked_path.replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised[2:]
    return PurePosixPath(normalised).as_posix()


def find_forbidden_tracked_files(
    repo_root: Path,
    tracked_paths: Sequence[str],
    tracked_text: dict[str, str],
) -> tuple[str, ...]:
    """Return every violation, sorted, without touching the filesystem.

    Keeping this a pure function of paths and text is what makes the rules
    testable one at a time: every case below is reachable from a fixture rather
    than from a repository somebody has to construct.
    """

    del repo_root
    violations: set[str] = set()

    for tracked_path in tracked_paths:
        normalised = _normalise_path(tracked_path)
        lowered = normalised.casefold()
        parts = PurePosixPath(normalised).parts
        basename = parts[-1].casefold() if parts else ""

        text = tracked_text.get(tracked_path, tracked_text.get(normalised, ""))
        if PRIVATE_MARKER in text:
            violations.add(f"{normalised}: contains private handoff marker")
        if any(pattern.search(text) for pattern in CREDENTIAL_PATTERNS):
            violations.add(f"{normalised}: possible credential")

        if basename == ".env" or (basename.startswith(".env.") and basename != ".env.example"):
            violations.add(f"{normalised}: forbidden environment file")
        if "handoff" in (part.casefold() for part in parts[:-1]) and lowered.endswith(".md"):
            violations.add(f"{normalised}: forbidden handoff file")
        if lowered.endswith(FORBIDDEN_SUFFIXES):
            violations.add(f"{normalised}: forbidden data or model artifact")

    return tuple(sorted(violations))


def _run_git(repo_root: Path, arguments: Sequence[str]) -> bytes:
    result = subprocess.run(
        ["git", *list(arguments)],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitIndexError(detail or f"git exited with code {result.returncode}")
    return result.stdout


def check_repository(repo_root: Path) -> tuple[str, ...]:
    """Read paths and blobs from Git's index, then apply the pure guard."""

    try:
        tracked_paths = tuple(
            path
            for path in _run_git(repo_root, ["ls-files", "-z"]).decode("utf-8").split("\0")
            if path
        )
    except UnicodeDecodeError as exc:
        raise GitIndexError("tracked path is not valid UTF-8") from exc

    tracked_text: dict[str, str] = {}
    for tracked_path in tracked_paths:
        blob = _run_git(repo_root, ["show", f":./{tracked_path}"])
        try:
            tracked_text[tracked_path] = blob.decode("utf-8")
        except UnicodeDecodeError:
            # A binary blob carries no readable marker, but its path is still
            # checked above; skipping the text is not skipping the file.
            continue

    return find_forbidden_tracked_files(repo_root, tracked_paths, tracked_text)


def main(argv: Sequence[str] | None = None) -> int:
    """Print every violation and return a process exit code."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    try:
        violations = check_repository(args.repo_root.resolve())
    except GitIndexError as exc:
        print(f"unable to read Git index: {exc}", file=sys.stderr)
        return 2

    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
