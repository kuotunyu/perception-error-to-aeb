"""The tree the mutation audit runs in: what it mutates and what it copies beside it.

`mutmut run` builds `mutants/` in three steps: it copies every path of
`[tool.mutmut] paths_to_mutate`, writes each mutated file over its copy, and
only then copies every path of `also_copy`, in the order listed. The suite then
runs in that tree alone. So a file that is both mutated and copied is replaced
by its unmutated copy, and every mutant of it survives without a test ever
seeing it; a path the suite reads and `also_copy` misses fails the suite before
any mutant is scored; and a listed path that does not exist is skipped without a
word. These tests hold the configuration to all three.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import tomli

ROOT = Path(__file__).resolve().parents[2]
SOURCE = PurePosixPath("src/aebrisk")

#: The released audit's eight targets, then the study's code, the velocity
#: filter and the arm factors on the configuration.
PATHS_TO_MUTATE = [
    "src/aebrisk/aeb",
    "src/aebrisk/errors",
    "src/aebrisk/metrics",
    "src/aebrisk/attribution",
    "src/aebrisk/cohort/filters.py",
    "src/aebrisk/cohort/splits.py",
    "src/aebrisk/simulation/step_loop.py",
    "src/aebrisk/simulation/route_follower.py",
    "src/aebrisk/study",
    "src/aebrisk/observation/tracking.py",
    "src/aebrisk/simulation/common_cohort.py",
]

#: What the study's tests read outside the source tree: the two plans and their
#: future evidence, the figures, the released replays and notice, the root
#: notice, and the formal run's compose settings.
STUDY_READS = (
    "docs/studies",
    "docs/posthoc",
    "docs/figures",
    "docs/evidence/nuplan_aeb_v2/replays",
    "docs/evidence/nuplan_aeb_v2-NOTICE.md",
    "NOTICE",
    "compose.formal-cpu.yaml",
)


def mutmut_config() -> dict[str, Any]:
    document = tomli.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    config: dict[str, Any] = document["tool"]["mutmut"]
    return config


def covers(entry: str, path: PurePosixPath) -> bool:
    """Whether the listed path `entry` is `path` or one of its directories."""

    listed = PurePosixPath(entry)
    return path == listed or listed in path.parents


def test_the_audit_mutates_the_released_targets_and_the_study_code() -> None:
    assert mutmut_config()["paths_to_mutate"] == PATHS_TO_MUTATE


def test_the_audit_copies_what_the_study_tests_read() -> None:
    also_copy = mutmut_config()["also_copy"]

    assert set(STUDY_READS) <= set(also_copy)
    assert len(also_copy) == len(set(also_copy))


def test_no_mutated_path_is_also_copied() -> None:
    """A copy made after mutation would put the unmutated file back."""

    config = mutmut_config()
    overlaps = [
        (target, copied)
        for target in config["paths_to_mutate"]
        for copied in config["also_copy"]
        if covers(target, PurePosixPath(copied)) or covers(copied, PurePosixPath(target))
    ]

    assert overlaps == []


def test_every_source_file_reaches_the_mutation_tree_exactly_once() -> None:
    config = mutmut_config()
    listed = [*config["paths_to_mutate"], *config["also_copy"]]
    reached = {
        path.relative_to(ROOT).as_posix(): [
            entry for entry in listed if covers(entry, PurePosixPath(path.relative_to(ROOT)))
        ]
        for path in sorted((ROOT / SOURCE).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }

    assert reached
    assert {name: entries for name, entries in reached.items() if len(entries) != 1} == {}


def test_every_listed_path_exists_and_every_copied_file_has_its_directory() -> None:
    """mutmut skips a missing path, and cannot copy a file into a directory it has not made."""

    config = mutmut_config()
    made = {PurePosixPath(".")}
    for target in config["paths_to_mutate"]:
        path = PurePosixPath(target)
        assert (ROOT / target).exists(), target
        directory = path if (ROOT / target).is_dir() else path.parent
        made.update({directory, *directory.parents})
    for copied in config["also_copy"]:
        path = PurePosixPath(copied)
        assert (ROOT / copied).exists(), copied
        if (ROOT / copied).is_dir():
            made.update({path, *path.parents})
            made.update(
                PurePosixPath(child.relative_to(ROOT).as_posix())
                for child in (ROOT / copied).rglob("*")
                if child.is_dir()
            )
        else:
            assert path.parent in made, copied
