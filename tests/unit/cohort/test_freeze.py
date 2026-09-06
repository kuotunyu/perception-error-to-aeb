"""Contracts for freezing the two cohorts out of a mounted split.

Three properties are the whole of why this module exists.

THE POOL IS BOUNDED AND THE ORDER IS THE FREEZE'S OWN. A family can hold
hundreds of thousands of scenarios and the cohort keeps a hundred, so only the
first few thousand by freeze order are held. That changes nothing about which
scenarios are chosen — unless the pool runs out before the cap is full, which is
REFUSED, because a short cohort and an exhausted pool look identical in a
manifest and mean opposite things.

THE COHORT COMES FROM THE OFFICIAL SPLIT IT CLAIMS. Freezing development from
validation logs would put held-out recordings in the half that thresholds are
chosen on, and nothing downstream would show it.

EVERY SCENARIO EXAMINED IS RECORDED, accepted or refused. A family that came out
at 43 of 100 has to be readable as the rules the other recordings failed.

Nothing here opens a database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PROTOCOL_SHA = "a" * 64
FIRST_TIMESTAMP_US = 1_600_000_000_000_000
STEP_US = 100_000

FAMILY_TYPES = {
    "lead_or_stopping": ("stopping_with_lead",),
    "cut_in_or_crossing": ("changing_lane",),
    "pedestrian_or_crosswalk": ("waiting_for_pedestrian_to_cross",),
    "bicycle_or_vru": ("behind_bike",),
}


def load_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.cohort import freeze
    except ImportError:  # pragma: no cover - names the absence during RED
        pytest.fail("aebrisk.cohort.freeze is missing", pytrace=False)
    return freeze


@dataclass(frozen=True)
class FakeStateSE2:
    x: float
    y: float
    heading: float


@dataclass(frozen=True)
class FakeVector:
    x: float
    y: float


@dataclass(frozen=True)
class FakeBox:
    center: FakeStateSE2
    length: float
    width: float


@dataclass(frozen=True)
class FakeType:
    name: str


@dataclass(frozen=True)
class FakeAgent:
    track_token: str
    tracked_object_type: FakeType
    box: FakeBox
    velocity: FakeVector


@dataclass(frozen=True)
class FakeDynamicCarState:
    speed: float


@dataclass(frozen=True)
class FakeFootprint:
    length: float
    width: float


@dataclass(frozen=True)
class FakeEgoState:
    center: FakeStateSE2
    dynamic_car_state: FakeDynamicCarState
    time_us: int
    car_footprint: FakeFootprint


class AcceptableScenario:
    """A recording that passes every eligibility rule."""

    token = "fake"

    def get_number_of_iterations(self) -> int:
        return 150

    def get_ego_state_at_iteration(self, iteration: int) -> Any:
        return FakeEgoState(
            center=FakeStateSE2(iteration * 0.8, 0.0, 0.0),
            dynamic_car_state=FakeDynamicCarState(speed=8.0),
            time_us=FIRST_TIMESTAMP_US + iteration * STEP_US,
            car_footprint=FakeFootprint(length=5.176, width=2.297),
        )

    def get_tracked_objects_at_iteration(self, iteration: int) -> Any:
        return iter(
            (
                FakeAgent(
                    track_token="lead-0001",
                    tracked_object_type=FakeType("VEHICLE"),
                    box=FakeBox(FakeStateSE2(45.0, 0.0, 0.0), 5.0, 2.0),
                    velocity=FakeVector(0.0, 0.0),
                ),
            )
        )


def installation(split: str = "train", databases: int = 2) -> Any:
    from aebrisk.nuplan_adapter.database import NuPlanInstallation

    return NuPlanInstallation(
        data_root=Path("/data/nuplan"),
        maps_root=Path("/data/nuplan/maps"),
        split=split,
        log_databases=tuple(Path(f"/data/nuplan/{index:02d}.db") for index in range(databases)),
    )


def references_for(per_family: int, databases: int = 2) -> dict[str, tuple[Any, ...]]:
    """Scenario references of every family, spread evenly over the databases."""

    from aebrisk.nuplan_adapter.query_scenario import ScenarioReference

    by_database: dict[str, list[Any]] = {}
    for family, types in FAMILY_TYPES.items():
        for index in range(per_family):
            name = f"{index % databases:02d}.db"
            by_database.setdefault(name, []).append(
                ScenarioReference(
                    log_file=f"/data/nuplan/{name}",
                    token=f"{family}-{index:05d}",
                    scenario_type=types[0],
                    timestamp_us=FIRST_TIMESTAMP_US,
                )
            )
    return {name: tuple(items) for name, items in by_database.items()}


def refuses_nothing(token: str) -> bool:
    return False


def refuses_everything(token: str) -> bool:
    return True


def refuses_odd_tokens(token: str) -> bool:
    """Half the recordings are too short, which is what a real split looks like."""

    return int(token[-1]) % 2 == 1


def patch_everything(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    per_family: int = 60,
    refuse: Any = refuses_nothing,
    databases: int = 2,
) -> None:
    from aebrisk.cohort import prefilter

    references = references_for(per_family, databases)

    def query(log_file: str, scenario_types: Any) -> tuple[Any, ...]:
        return references.get(Path(log_file).name, ())

    def builder(log_file: str, token: str, duration_s: float, frequency_hz: float) -> Any:
        if refuse(token):
            raise ValueError("has 121 frames after the token and the protocol needs 150")
        return AcceptableScenario()

    monkeypatch.setattr(module, "QUERY_SCENARIOS", query)
    monkeypatch.setattr(prefilter, "BUILD_SCENARIO", builder)


# --------------------------------------------------------------------------
# The bounded sweep
# --------------------------------------------------------------------------


def test_the_pool_keeps_the_first_candidates_in_freeze_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Holding every reference to sort them would cost a gigabyte for a hundred rows."""

    from aebrisk.cohort.splits import selection_key

    module = load_module()
    patch_everything(monkeypatch, module, per_family=40)

    pooled = module.pooled_references(
        installation(), FAMILY_TYPES, protocol_sha256=PROTOCOL_SHA, pool_size=6
    )

    references, total = pooled["lead_or_stopping"]
    assert total == 40
    assert len(references) == 6
    keys = [selection_key(PROTOCOL_SHA, reference.token) for reference in references]
    assert keys == sorted(keys)


def test_the_pool_reports_what_the_split_actually_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "The pool is full" and "the family has this many" are different facts."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=12)

    pooled = module.pooled_references(
        installation(), FAMILY_TYPES, protocol_sha256=PROTOCOL_SHA, pool_size=5000
    )

    assert {family: total for family, (_refs, total) in pooled.items()} == dict.fromkeys(
        FAMILY_TYPES, 12
    )


def test_a_pool_of_nothing_is_refused() -> None:
    """A pool of zero would freeze an empty cohort and look like an empty split."""

    module = load_module()

    with pytest.raises(ValueError, match=r"^pool_size must be at least one"):
        module.pooled_references(
            installation(), FAMILY_TYPES, protocol_sha256=PROTOCOL_SHA, pool_size=0
        )


# --------------------------------------------------------------------------
# Freezing one split
# --------------------------------------------------------------------------


def test_a_cohort_is_frozen_from_the_official_split_it_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Development from validation logs would put held-out recordings in the tuned half."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=4)

    with pytest.raises(ValueError, match=r"^the development cohort is drawn from nuPlan's 'train'"):
        module.freeze_split(
            installation(split="val"),
            FAMILY_TYPES,
            split="development",
            protocol_sha256=PROTOCOL_SHA,
        )


def test_the_frozen_manifest_carries_every_family_and_its_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manifest is what a reader checks a published number against."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=60)

    frozen = module.freeze_split(
        installation(), FAMILY_TYPES, split="development", protocol_sha256=PROTOCOL_SHA
    )

    assert frozen.split == "development"
    assert frozen.manifest.protocol_sha256 == PROTOCOL_SHA
    assert set(frozen.manifest.families) == set(FAMILY_TYPES)
    assert all(len(tokens) == 50 for tokens in frozen.manifest.families.values())
    assert frozen.manifest.log_names == ("00.db", "01.db")


def test_a_family_thinner_than_the_cap_freezes_what_there_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Padding a thin family would fabricate a stratum; the count is recorded instead."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=3)

    frozen = module.freeze_split(
        installation(), FAMILY_TYPES, split="development", protocol_sha256=PROTOCOL_SHA
    )

    assert all(len(tokens) == 3 for tokens in frozen.manifest.families.values())
    assert frozen.scenarios_in_split_by_family == dict.fromkeys(FAMILY_TYPES, 3)


def test_an_exhausted_pool_is_refused_rather_than_reported_as_a_thin_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """These two look identical in a manifest and mean opposite things."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=40, refuse=refuses_everything)

    with pytest.raises(ValueError, match=r"filled 0 of 50 from a pool of 8 candidates"):
        module.freeze_split(
            installation(),
            FAMILY_TYPES,
            split="development",
            protocol_sha256=PROTOCOL_SHA,
            pool_size=8,
        )


def test_every_examined_scenario_is_recorded_with_its_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cohort of 43 has to be readable as the rule the other recordings failed."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=5)

    frozen = module.freeze_split(
        installation(), FAMILY_TYPES, split="development", protocol_sha256=PROTOCOL_SHA
    )

    assert len(frozen.eligibility) == 20
    assert all(record.accepted for record in frozen.eligibility)
    assert {record.family for record in frozen.eligibility} == set(FAMILY_TYPES)
    assert all(record.official_split == "train" for record in frozen.eligibility)


def test_a_refused_recording_is_recorded_beside_the_accepted_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silently skipped recording makes a thin family look like a thin population."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=6, refuse=refuses_odd_tokens)

    frozen = module.freeze_split(
        installation(), FAMILY_TYPES, split="development", protocol_sha256=PROTOCOL_SHA
    )

    refused = [record for record in frozen.eligibility if not record.accepted]
    assert len(refused) == 12
    assert all("needs 150" in record.reason for record in refused)
    assert all(record.initial_ego_speed_mps is None for record in refused)
    assert all(len(tokens) == 3 for tokens in frozen.manifest.families.values())


# --------------------------------------------------------------------------
# The eligibility document
# --------------------------------------------------------------------------


def test_the_eligibility_document_is_byte_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two freezes of one split must be comparable byte for byte, not row by row."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=3)
    frozen = module.freeze_split(
        installation(), FAMILY_TYPES, split="development", protocol_sha256=PROTOCOL_SHA
    )

    first = module.eligibility_json_bytes(frozen.eligibility, frozen.scenarios_in_split_by_family)
    second = module.eligibility_json_bytes(frozen.eligibility, frozen.scenarios_in_split_by_family)

    assert first == second
    assert first.endswith(b"\n")
    assert b"\r" not in first


def test_the_eligibility_document_names_every_section_a_reader_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document whose shape drifts cannot be compared with the one before it."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=3)
    frozen = module.freeze_split(
        installation(), FAMILY_TYPES, split="development", protocol_sha256=PROTOCOL_SHA
    )

    document = json.loads(
        module.eligibility_json_bytes(frozen.eligibility, frozen.scenarios_in_split_by_family)
    )

    assert set(document) == {
        "schema_version",
        "examined",
        "scenarios_in_split_by_family",
    }
    assert document["schema_version"] == "aeb-cohort-eligibility/v1"
    assert set(document["examined"][0]) == {
        "scenario_token",
        "log_name",
        "scenario_type",
        "family",
        "official_split",
        "accepted",
        "reason",
        "initial_ego_speed_mps",
        "oracle_enters_corridor_within_4s",
        "oracle_min_ttc_within_4s",
    }


def test_the_eligibility_document_is_written_beside_the_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The evidence has to travel with the cohort it explains."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=2)
    frozen = module.freeze_split(
        installation(), FAMILY_TYPES, split="development", protocol_sha256=PROTOCOL_SHA
    )

    path = module.write_eligibility(
        frozen.eligibility,
        tmp_path / "manifests" / "eligibility.json",
        frozen.scenarios_in_split_by_family,
    )

    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["examined"]


def test_the_eligibility_document_survives_having_no_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The totals come from a sweep; a caller that has records but no sweep is not lying."""

    module = load_module()
    patch_everything(monkeypatch, module, per_family=2)
    frozen = module.freeze_split(
        installation(), FAMILY_TYPES, split="development", protocol_sha256=PROTOCOL_SHA
    )

    document = json.loads(module.eligibility_json_bytes(frozen.eligibility))

    assert document["scenarios_in_split_by_family"] == {}
