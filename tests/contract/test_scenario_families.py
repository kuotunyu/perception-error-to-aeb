"""The family mapping must name types nuPlan tags, and say so in one place only.

Two failures are possible here and neither shows up in any other test.

The first is a family type that nuPlan does not tag. The pinned devkit ships no
canonical list, so a mapping written from expectation compiles, passes every
unit test, and freezes an empty cohort months later. Five of protocol v1's
fifteen types were exactly that. `configs/nuplan_scenario_vocabulary.yaml`
records what the databases actually carry, and the first test refuses any type
that is not in it.

The second is drift between the protocol file and the code. The mapping is
written twice: in `configs/protocols/nuplan_aeb_v2.yaml`, whose bytes are the
protocol hash every run record cites, and in `aebrisk.cohort.filters`, which is
what actually selects scenarios. Nothing tied them together, so a published hash
could describe a mapping the code never used. The second test ties them.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO_ROOT / "configs" / "protocols" / "nuplan_aeb_v2.yaml"
VOCABULARY_PATH = REPO_ROOT / "configs" / "nuplan_scenario_vocabulary.yaml"


def load_filters_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.cohort import filters
    except ImportError:  # pragma: no cover - the module exists; this names its absence
        pytest.fail("aebrisk.cohort.filters is missing", pytrace=False)
    return filters


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        pytest.fail(f"{path.relative_to(REPO_ROOT).as_posix()} is missing", pytrace=False)
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document


def test_every_pinned_scenario_type_is_one_nuplan_actually_tags() -> None:
    """A family type absent from the data freezes an empty cohort, months later."""

    filters = load_filters_module()
    vocabulary = set(read_yaml(VOCABULARY_PATH)["scenario_types"])

    pinned = {
        scenario_type
        for scenario_types in filters.FAMILY_TYPES.values()
        for scenario_type in scenario_types
    }
    unknown = sorted(pinned - vocabulary)

    assert unknown == [], (
        f"these types are pinned by a family but nuPlan tags nothing with them: {unknown}; "
        "the observed vocabulary is configs/nuplan_scenario_vocabulary.yaml"
    )


def test_the_protocol_file_and_the_code_agree_on_every_family() -> None:
    """The hash cites the file; the selection uses the code. They must be one mapping."""

    filters = load_filters_module()
    families = read_yaml(PROTOCOL_PATH)["scenario_families"]

    from_file = {family: tuple(types) for family, types in families.items()}

    assert from_file == filters.FAMILY_TYPES


def test_every_family_the_results_schema_knows_is_mapped_and_no_other() -> None:
    """A cohort and a result set stratified differently cannot be joined."""

    filters = load_filters_module()

    assert tuple(sorted(filters.FAMILY_TYPES)) == tuple(sorted(filters.SCENARIO_FAMILIES))


def test_a_scenario_type_belongs_to_exactly_one_family() -> None:
    """A type in two families would let one scenario be counted in two strata."""

    filters = load_filters_module()

    seen: dict[str, str] = {}
    duplicated: list[str] = []
    for family, scenario_types in filters.FAMILY_TYPES.items():
        for scenario_type in scenario_types:
            if scenario_type in seen:
                duplicated.append(f"{scenario_type} in {seen[scenario_type]} and {family}")
            seen[scenario_type] = family

    assert duplicated == []


def test_the_vocabulary_records_where_it_came_from() -> None:
    """An observed list with no provenance is indistinguishable from an invented one."""

    vocabulary = read_yaml(VOCABULARY_PATH)
    provenance = vocabulary["provenance"]

    assert vocabulary["schema_version"] == "nuplan-scenario-vocabulary/v1"
    assert provenance["dataset"] == "nuplan-v1.1"
    assert provenance["log_databases_read"] >= 1
    assert provenance["observed_on"]
    assert vocabulary["scenario_types"] == sorted(set(vocabulary["scenario_types"]))


def test_the_protocol_file_and_the_code_agree_on_the_simulation_timing() -> None:
    """Rate and duration decide every reported duration; two sources would drift.

    Only these two are compared. The protocol's `agents: non_reactive_logged` and
    the wiring's `NON_REACTIVE_AGENTS = "log_playback_agents"` name the same
    decision in two vocabularies — one the study's, one the devkit's — and
    asserting a string transformation between them would test the transformation
    rather than the agreement. `build_simulation_wiring` already refuses any
    policy but that one.
    """

    from aebrisk.nuplan_adapter import simulation

    timing = read_yaml(PROTOCOL_PATH)["simulation"]

    assert float(timing["frequency_hz"]) == simulation.PROTOCOL_FREQUENCY_HZ
    assert float(timing["scenario_duration_s"]) == simulation.PROTOCOL_SCENARIO_DURATION_S
