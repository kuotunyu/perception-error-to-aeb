"""Contracts for deciding which recordings are worth simulating, without reading them all.

The load-bearing property is the bounded walk. A family can hold tens of
thousands of scenarios and the cohort needs a hundred, so candidates are
examined in the order the freeze would take them and the walk stops when the cap
is full. That is the same cohort a full sweep would produce ONLY while the order
is decided before any scenario is looked at, so the order is a hash of the
protocol and the token and nothing else. A test below asserts exactly that.

The second is that a refusal says why. An eligibility record beside a frozen
cohort has to let a reader see that a thin family is thin because its recordings
were too short, or because nothing entered the corridor — not merely that some
scenarios did not make it.

Nothing here opens a database: the query layer is replaced and the threat
geometry is the real one.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any, Optional

import pytest

PROTOCOL_HASH = "a" * 64
FIRST_TIMESTAMP_US = 1_600_000_000_000_000
STEP_US = 100_000


def load_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.cohort import prefilter
    except ImportError:  # pragma: no cover - names the absence during RED
        pytest.fail("aebrisk.cohort.prefilter is missing", pytrace=False)
    return prefilter


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


@dataclass(frozen=True)
class FootprintlessEgoState:
    """Shaped like an ego state from a devkit that does not publish a footprint."""

    center: FakeStateSE2
    dynamic_car_state: FakeDynamicCarState
    time_us: int


class FakeScenario:
    """A recording with one stationary vehicle ahead of a moving ego."""

    def __init__(
        self,
        *,
        speed: float = 8.0,
        lead_xy: Optional[tuple[float, float]] = (45.0, 0.0),
        footprint: bool = True,
        iterations: int = 150,
        token: str = "t-0001",
    ) -> None:
        self.token = token
        self._speed = speed
        self._lead_xy = lead_xy
        self._footprint = footprint
        self._iterations = iterations
        self.iterations_read: list[int] = []

    def get_number_of_iterations(self) -> int:
        return self._iterations

    def get_ego_state_at_iteration(self, iteration: int) -> Any:
        self.iterations_read.append(iteration)
        center = FakeStateSE2(iteration * self._speed * 0.1, 0.0, 0.0)
        dynamics = FakeDynamicCarState(speed=self._speed)
        stamp = FIRST_TIMESTAMP_US + iteration * STEP_US
        if not self._footprint:
            return FootprintlessEgoState(center=center, dynamic_car_state=dynamics, time_us=stamp)
        return FakeEgoState(
            center=center,
            dynamic_car_state=dynamics,
            time_us=stamp,
            car_footprint=FakeFootprint(length=5.176, width=2.297),
        )

    def get_tracked_objects_at_iteration(self, iteration: int) -> Any:
        if self._lead_xy is None:
            return iter(())
        return iter(
            (
                FakeAgent(
                    track_token="lead-0001",
                    tracked_object_type=FakeType("VEHICLE"),
                    box=FakeBox(FakeStateSE2(self._lead_xy[0], self._lead_xy[1], 0.0), 5.0, 2.0),
                    velocity=FakeVector(0.0, 0.0),
                ),
            )
        )


def reference(module: ModuleType, token: str = "t-0001") -> Any:
    from aebrisk.nuplan_adapter.query_scenario import ScenarioReference

    return ScenarioReference(
        log_file="/data/nuplan/splits/val/2021.06.07.log.db",
        token=token,
        scenario_type="stopping_with_lead",
        timestamp_us=FIRST_TIMESTAMP_US,
    )


def patch_builder(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    scenario: Any = None,
    error: Optional[Exception] = None,
) -> list[str]:
    """Replace the scenario builder, and report which tokens it was asked for."""

    asked: list[str] = []

    def builder(log_file: str, token: str, duration_s: float, frequency_hz: float) -> Any:
        asked.append(token)
        if error is not None:
            raise error
        return FakeScenario() if scenario is None else scenario

    monkeypatch.setattr(module, "BUILD_SCENARIO", builder)
    return asked


def test_a_recording_with_a_lead_in_the_corridor_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The measurements, not a verdict alone: a reader has to see what decided it."""

    module = load_module()
    patch_builder(monkeypatch, module)

    eligibility = module.measure(reference(module), "lead_or_stopping", "val")

    assert eligibility.accepted is True
    assert eligibility.reason == ""
    assert eligibility.initial_ego_speed_mps == pytest.approx(8.0)
    assert eligibility.oracle_enters_corridor_within_4s is True
    assert eligibility.oracle_min_ttc_within_4s is not None
    assert 0.0 < eligibility.oracle_min_ttc_within_4s < 6.0
    assert eligibility.log_name == "2021.06.07.log.db"
    assert eligibility.official_split == "val"


def test_an_ego_too_slow_to_brake_is_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scenario with no braking decision available measures the channels against nothing."""

    module = load_module()
    patch_builder(monkeypatch, module, FakeScenario(speed=0.5))

    eligibility = module.measure(reference(module), "lead_or_stopping", "val")

    assert eligibility.accepted is False
    assert eligibility.reason.startswith("initial ego speed 0.500 m/s is below")
    assert eligibility.initial_ego_speed_mps == pytest.approx(0.5)


def test_a_recording_with_an_empty_corridor_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to brake for is a fact about the recording, and it is named."""

    module = load_module()
    patch_builder(monkeypatch, module, FakeScenario(lead_xy=None))

    eligibility = module.measure(reference(module), "lead_or_stopping", "val")

    assert eligibility.accepted is False
    assert eligibility.reason == "no observed object enters the ego corridor within 4 s"
    assert eligibility.oracle_enters_corridor_within_4s is False
    assert eligibility.oracle_min_ttc_within_4s is None


def test_a_recording_the_builder_refuses_is_recorded_rather_than_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silently skipped recording makes a thin family look like a thin population."""

    module = load_module()
    patch_builder(
        monkeypatch,
        module,
        error=ValueError("has 121 frames after the token and the protocol needs 150"),
    )

    eligibility = module.measure(reference(module), "lead_or_stopping", "val")

    assert eligibility.accepted is False
    assert "needs 150" in eligibility.reason
    assert eligibility.initial_ego_speed_mps is None
    assert eligibility.oracle_min_ttc_within_4s is None


def test_only_the_first_four_seconds_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading fifteen seconds of every candidate would cost most of the walk's time."""

    module = load_module()
    scenario = FakeScenario()
    patch_builder(monkeypatch, module, scenario)

    module.measure(reference(module), "lead_or_stopping", "val")

    assert max(scenario.iterations_read) == 39


def test_a_recording_without_a_footprint_still_measures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The devkit publishes one on every ego state; a fake that does not must not crash."""

    module = load_module()
    patch_builder(monkeypatch, module, FakeScenario(footprint=False))

    eligibility = module.measure(reference(module), "lead_or_stopping", "val")

    assert eligibility.accepted is True


# --------------------------------------------------------------------------
# The bounded walk
# --------------------------------------------------------------------------


def references(module: ModuleType, count: int) -> tuple[Any, ...]:
    return tuple(reference(module, f"t-{index:04d}") for index in range(count))


def test_the_walk_stops_once_the_cap_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Examining a family of seventy thousand to keep a hundred is the cost this avoids."""

    module = load_module()
    asked = patch_builder(monkeypatch, module)

    examined = module.examine_until_capped(
        references(module, 40), "lead_or_stopping", "val", protocol_hash=PROTOCOL_HASH, cap=5
    )

    assert len(examined) == 5
    assert len(asked) == 5
    assert all(record.accepted for record in examined)


def test_the_walk_takes_candidates_in_the_freeze_s_own_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is what makes the bounded walk the same cohort as a full sweep.

    If the order came from the filesystem, or from anything measured, the first
    hundred that passed would not be the hundred the freeze keeps.
    """

    from aebrisk.cohort.splits import selection_key

    module = load_module()
    patch_builder(monkeypatch, module)
    candidates = references(module, 12)

    examined = module.examine_until_capped(
        candidates, "lead_or_stopping", "val", protocol_hash=PROTOCOL_HASH, cap=4
    )

    expected = [
        item.token
        for item in sorted(
            candidates, key=lambda reference: selection_key(PROTOCOL_HASH, reference.token)
        )
    ][:4]
    assert [record.scenario_token for record in examined] == expected


def test_refused_scenarios_are_returned_beside_the_accepted_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusals are the evidence for why the cohort is the cohort."""

    module = load_module()
    patch_builder(monkeypatch, module, FakeScenario(lead_xy=None))

    examined = module.examine_until_capped(
        references(module, 6), "lead_or_stopping", "val", protocol_hash=PROTOCOL_HASH, cap=3
    )

    assert len(examined) == 6
    assert not any(record.accepted for record in examined)


def test_a_cap_of_nothing_is_refused() -> None:
    """A cap of zero would freeze an empty cohort and look like a thin family."""

    module = load_module()

    with pytest.raises(ValueError, match=r"^cap must be at least one"):
        module.examine_until_capped(
            (), "lead_or_stopping", "val", protocol_hash=PROTOCOL_HASH, cap=0
        )


# --------------------------------------------------------------------------
# Handing accepted scenarios to the freeze
# --------------------------------------------------------------------------


def test_an_accepted_record_becomes_the_candidate_the_freeze_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The freeze checks the prefilter again, so the two must describe one scenario."""

    from aebrisk.cohort.filters import passes_prefilter

    module = load_module()
    patch_builder(monkeypatch, module)

    candidate = module.candidate_of(module.measure(reference(module), "lead_or_stopping", "val"))

    assert passes_prefilter(candidate)
    assert candidate.scenario_token == "t-0001"
    assert candidate.official_split == "val"


def test_a_refused_record_is_not_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freezing a refused scenario would put an unmeasurable recording in the cohort."""

    module = load_module()
    patch_builder(monkeypatch, module, FakeScenario(lead_xy=None))

    eligibility = module.measure(reference(module), "lead_or_stopping", "val")

    with pytest.raises(ValueError, match=r"was refused \(no observed object enters"):
        module.candidate_of(eligibility)
