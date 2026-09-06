"""Count what a split actually holds, before any cohort is frozen.

The protocol names four families and the scenario types in each, but nuPlan
publishes no enumeration of its types: they live in each log database's
`scenario_tag` table. A mapping written from expectation therefore compiles,
passes every unit test, and is discovered to be wrong only when a cohort comes
out empty. This is the command that discovers it first instead.

It answers three questions, and the second is the one that decides whether a
freeze is possible at all.

- **How many tagged scenarios does each family have?** The obvious one — and it
  counts TAG ROWS. One lidar frame can carry several of a family's types at once,
  so this is an upper bound on the distinct scenarios a freeze will see: measured
  on one validation database, 55 pedestrian-family tag rows belonged to 30
  tokens. It is the right number for "does this family exist here at all", which
  is what the census is for, and the wrong one for "how large can the cohort be".
- **How many LOGS does each family span?** Development and locked evaluation
  never share a log, so a family confined to one log cannot be divided however
  many scenarios that log holds. `splittable` states that verdict rather than
  leaving a reader to derive it from two numbers.
- **Which pinned types does the split not carry, and which types does it carry
  that no family maps?** The first turns a silent empty family into a named
  absence; the second is the only way a family written from the wrong vocabulary
  becomes visible beside the right one.

Every number here describes the recording. None of them is a result, and nothing
here reads a sensor, a map or an image: the tags come through the devkit's own
read-only query, and a database that cannot be opened is NAMED rather than
skipped, because a silently skipped log makes every count below it quietly wrong.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from nuplan.database.nuplan_db.nuplan_scenario_queries import (
    get_lidarpc_tokens_with_scenario_tag_from_db,
)

#: The database call, named here so a test can replace it without a database.
QUERY_TAGS = get_lidarpc_tokens_with_scenario_tag_from_db

CENSUS_SCHEMA_VERSION = "nuplan-scenario-census/v1"

#: A family needs at least this many logs before development and locked
#: evaluation can be drawn from it without sharing one.
MINIMUM_LOGS_TO_SPLIT = 2


@dataclass(frozen=True)
class TypeCount:
    """What one scenario type contributes, in scenarios and in logs."""

    scenario_type: str
    scenarios: int
    logs: int


@dataclass(frozen=True)
class FamilyCount:
    """What one family holds, and whether it can be split log-disjointly."""

    family: str
    scenarios: int
    logs: int
    splittable: bool


@dataclass(frozen=True)
class CensusReport:
    """One split, counted."""

    split: str
    databases_read: int
    unreadable: tuple[str, ...]
    families: tuple[FamilyCount, ...]
    types: tuple[TypeCount, ...]
    pinned_present: tuple[str, ...]
    pinned_absent: tuple[str, ...]
    unmapped_present: tuple[str, ...]


def census_split(
    split: str,
    log_databases: Sequence[Path],
    family_types: Mapping[str, Sequence[str]],
) -> CensusReport:
    """Count every scenario tag in one split, by type and by family."""

    scenarios_by_type: dict[str, int] = {}
    logs_by_type: dict[str, set[str]] = {}
    unreadable: list[str] = []
    databases_read = 0

    for database in log_databases:
        try:
            pairs: Iterable[tuple[str, str]] = list(QUERY_TAGS(str(database)))
        except Exception as error:
            unreadable.append(f"{database.name}: {error}")
            continue
        databases_read += 1
        for scenario_type, _token in pairs:
            name = str(scenario_type)
            scenarios_by_type[name] = scenarios_by_type.get(name, 0) + 1
            logs_by_type.setdefault(name, set()).add(database.name)

    pinned = {
        scenario_type: family
        for family, scenario_types in family_types.items()
        for scenario_type in scenario_types
    }
    present = set(scenarios_by_type)

    families: list[FamilyCount] = []
    for family in sorted(family_types):
        types = family_types[family]
        scenarios = sum(scenarios_by_type.get(name, 0) for name in types)
        logs = {log for name in types for log in logs_by_type.get(name, set())}
        families.append(
            FamilyCount(
                family=family,
                scenarios=scenarios,
                logs=len(logs),
                splittable=len(logs) >= MINIMUM_LOGS_TO_SPLIT,
            )
        )

    types_seen = tuple(
        TypeCount(
            scenario_type=name,
            scenarios=scenarios_by_type[name],
            logs=len(logs_by_type[name]),
        )
        for name in sorted(scenarios_by_type)
    )

    return CensusReport(
        split=split,
        databases_read=databases_read,
        unreadable=tuple(unreadable),
        families=tuple(families),
        types=types_seen,
        pinned_present=tuple(sorted(name for name in pinned if name in present)),
        pinned_absent=tuple(sorted(name for name in pinned if name not in present)),
        unmapped_present=tuple(sorted(name for name in present if name not in pinned)),
    )


def census_json_bytes(report: CensusReport) -> bytes:
    """Serialise a census the same way twice, so two of them can be compared.

    Sorted keys, a fixed separator and an LF ending, for the same reason every
    other document in this project is written that way: a report whose bytes
    depend on the platform that wrote it cannot be compared by hash.
    """

    document = {
        "schema_version": CENSUS_SCHEMA_VERSION,
        "split": report.split,
        "databases_read": report.databases_read,
        "unreadable": list(report.unreadable),
        "families": [asdict(entry) for entry in report.families],
        "types": [asdict(entry) for entry in report.types],
        "pinned_present": list(report.pinned_present),
        "pinned_absent": list(report.pinned_absent),
        "unmapped_present": list(report.unmapped_present),
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
