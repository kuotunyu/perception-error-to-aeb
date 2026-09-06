"""Contracts for counting what a split actually holds, before anything is frozen.

The protocol names four families and the scenario types in each. Whether those
types exist, and whether each family has enough logs to be split into a
development half and a locked evaluation half that share none, is a fact about
the recording that no amount of code review can settle. This is the command that
settles it, and it must answer three questions rather than one.

How many scenarios each family has is the obvious one. **How many LOGS each
family spans is the load-bearing one**: `log_disjoint` means a family with one
log cannot be divided at all, however many scenarios that log holds. And which
pinned types are missing is what turns "the cohort came out empty" months later
into "these five names are not in the vocabulary" now.

Every count here describes the recording. None of them is a result.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import pytest


def load_census_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.cohort import census
    except ImportError:  # pragma: no cover - names the absence during RED
        pytest.fail("aebrisk.cohort.census is missing", pytrace=False)
    return census


FAMILIES = {
    "lead_or_stopping": ("stopping_with_lead", "following_lane_with_lead"),
    "bicycle_or_vru": ("behind_bike",),
    "cut_in_or_crossing": ("changing_lane",),
    "pedestrian_or_crosswalk": ("waiting_for_pedestrian_to_cross",),
}

#: Two logs, deliberately uneven: `lead_or_stopping` spans both, `bicycle_or_vru`
#: only one, and `cut_in_or_crossing` none at all.
TAGS_BY_LOG = {
    "a.db": [
        ("stopping_with_lead", "t1"),
        ("stopping_with_lead", "t2"),
        ("behind_bike", "t3"),
        ("stationary", "t4"),
    ],
    "b.db": [
        ("stopping_with_lead", "t5"),
        ("following_lane_with_lead", "t6"),
        ("waiting_for_pedestrian_to_cross", "t7"),
        ("stationary", "t8"),
    ],
}


def patch_tag_query(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    tags: Optional[dict] = None,
    failing: Optional[str] = None,
) -> None:
    """Replace the database call so no test opens a file."""

    source = TAGS_BY_LOG if tags is None else tags

    def query(log_file: str) -> Any:
        name = Path(log_file).name
        if failing is not None and name == failing:
            raise RuntimeError("database is locked")
        return iter(source.get(name, []))

    monkeypatch.setattr(module, "QUERY_TAGS", query)


def run_census(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Any:
    module = load_census_module()
    patch_tag_query(monkeypatch, module, **kwargs)
    return module.census_split(
        split="mini",
        log_databases=(Path("a.db"), Path("b.db")),
        family_types=FAMILIES,
    )


def test_a_family_is_counted_in_logs_as_well_as_scenarios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A family in one log cannot be split log-disjointly, whatever its scenario count."""

    report = run_census(monkeypatch)

    families = {entry.family: entry for entry in report.families}
    assert families["lead_or_stopping"].scenarios == 4
    assert families["lead_or_stopping"].logs == 2
    assert families["bicycle_or_vru"].scenarios == 1
    assert families["bicycle_or_vru"].logs == 1


def test_a_family_with_fewer_than_two_logs_cannot_be_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict is computed, not left for a reader to work out from two numbers."""

    report = run_census(monkeypatch)

    families = {entry.family: entry for entry in report.families}
    assert families["lead_or_stopping"].splittable is True
    assert families["bicycle_or_vru"].splittable is False
    assert families["cut_in_or_crossing"].splittable is False


def test_a_pinned_type_the_split_does_not_carry_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is the whole point: an absent name found now, not at the freeze."""

    report = run_census(monkeypatch)

    assert report.pinned_absent == ("changing_lane",)
    assert "stopping_with_lead" in report.pinned_present


def test_types_the_split_carries_but_the_protocol_does_not_map_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A family written from the wrong vocabulary is only visible beside the right one."""

    report = run_census(monkeypatch)

    assert report.unmapped_present == ("stationary",)


def test_every_type_carries_its_own_scenario_and_log_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A family total hides that one type carries all of it and the others none."""

    report = run_census(monkeypatch)

    counts = {entry.scenario_type: entry for entry in report.types}
    assert counts["stopping_with_lead"].scenarios == 3
    assert counts["stopping_with_lead"].logs == 2
    assert counts["behind_bike"].scenarios == 1
    assert counts["behind_bike"].logs == 1


def test_a_database_that_cannot_be_read_is_named_rather_than_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silently skipped log makes every count below it quietly wrong."""

    report = run_census(monkeypatch, failing="a.db")

    assert report.unreadable == ("a.db: database is locked",)
    assert report.databases_read == 1


def test_the_report_records_the_split_it_describes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two splits' censuses are different facts and must not be confusable."""

    report = run_census(monkeypatch)

    assert report.split == "mini"
    assert report.databases_read == 2


def test_the_report_serialises_deterministically(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two censuses of one split must be comparable byte for byte."""

    module = load_census_module()
    first = run_census(monkeypatch)
    second = run_census(monkeypatch)

    assert module.census_json_bytes(first) == module.census_json_bytes(second)
    assert module.census_json_bytes(first).endswith(b"\n")
    assert b"\r" not in module.census_json_bytes(first)


def test_the_serialised_report_names_every_section_a_reader_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A census whose shape drifts cannot be compared with the one before it."""

    import json

    module = load_census_module()
    report = run_census(monkeypatch)

    document = json.loads(module.census_json_bytes(report))

    assert set(document) == {
        "schema_version",
        "split",
        "databases_read",
        "unreadable",
        "families",
        "types",
        "pinned_present",
        "pinned_absent",
        "unmapped_present",
    }
    assert document["schema_version"] == "nuplan-scenario-census/v1"
    assert document["families"][0]["family"] == sorted(FAMILIES)[0]
