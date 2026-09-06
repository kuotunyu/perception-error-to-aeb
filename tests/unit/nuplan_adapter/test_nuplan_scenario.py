"""Contracts for the last piece of the bridge: a nuPlan token the runner can drive.

Two properties here are the study's control condition rather than conveniences,
and both are invisible in a result that has them wrong.

THE RECORDING IS READ ONCE. Every configuration and replicate of a token must be
driven over the same frames. Re-reading per configuration would leave a path in
which two cells of the matrix are driven over different readings of one
recording, and the difference would be reported as an effect of perception error.

THE ROUTE IS THE LOGGED EGO PATH. The lateral line is where the vehicle actually
went. A route invented here would make the ego drive a road the agents were not
recorded beside, and every clearance in the study would be a number about that
invention.

Nothing below opens a database. The fakes stand in for the query layer, whose
own reading of a real log is checked in `tests/integration/test_nuplan_mini_adapter.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any, Optional

import numpy as np
import pytest

PROTOCOL_HASH = "d" * 64
FIRST_TIMESTAMP_US = 1_600_000_000_000_000
STEP_US = 100_000
STEPS = 150
CHANNELS = ("dropout", "localization_shape", "latency", "track_instability")


def load_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.nuplan_adapter import nuplan_scenario
    except ImportError:  # pragma: no cover - names the absence during RED
        pytest.fail("aebrisk.nuplan_adapter.nuplan_scenario is missing", pytrace=False)
    return nuplan_scenario


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
    """Shaped like `nuplan.common.actor_state.agent.Agent`."""

    track_token: str
    tracked_object_type: FakeType
    box: FakeBox
    velocity: FakeVector


@dataclass(frozen=True)
class FakeDynamicCarState:
    speed: float


@dataclass(frozen=True)
class FakeFootprint:
    """Shaped like the devkit's `CarFootprint`, which is where the ego's size lives."""

    length: float
    width: float


@dataclass(frozen=True)
class FakeEgoState:
    center: FakeStateSE2
    dynamic_car_state: FakeDynamicCarState
    time_us: int
    car_footprint: FakeFootprint


class FakeQueryScenario:
    """A log's answers, with only the members the adapter is allowed to read."""

    def __init__(
        self,
        token: str = "t-0001",
        steps: int = STEPS,
        speed: float = 9.0,
        step_m: float = 1.0,
        lead_x: Optional[float] = None,
    ) -> None:
        self.token = token
        self._steps = steps
        self._speed = speed
        self._step_m = step_m
        self._lead_x = lead_x
        self.reads = 0

    def get_number_of_iterations(self) -> int:
        return self._steps

    def get_ego_state_at_iteration(self, iteration: int) -> FakeEgoState:
        self.reads += 1
        return FakeEgoState(
            center=FakeStateSE2(iteration * self._step_m, 0.0, 0.0),
            dynamic_car_state=FakeDynamicCarState(speed=self._speed),
            time_us=FIRST_TIMESTAMP_US + iteration * STEP_US,
            car_footprint=FakeFootprint(length=5.176, width=2.297),
        )

    def get_tracked_objects_at_iteration(self, iteration: int) -> Any:
        self.reads += 1
        if self._lead_x is None:
            return iter(())
        return iter(
            (
                FakeAgent(
                    track_token="lead-0001",
                    tracked_object_type=FakeType("VEHICLE"),
                    box=FakeBox(FakeStateSE2(self._lead_x, 0.0, 0.0), 5.0, 2.0),
                    velocity=FakeVector(0.0, 0.0),
                ),
            )
        )


def reference(module: ModuleType, token: str = "t-0001") -> Any:
    from aebrisk.nuplan_adapter.query_scenario import ScenarioReference

    return ScenarioReference(
        log_file="/data/nuplan/splits/val/2021.log.db",
        token=token,
        scenario_type="stopping_with_lead",
        timestamp_us=FIRST_TIMESTAMP_US,
    )


def configuration(identifier: str = "oracle_aeb", mode: str = "oracle") -> Any:
    from aebrisk.simulation.common_cohort import ExperimentConfiguration

    return ExperimentConfiguration(
        configuration_id=identifier,
        aeb_enabled=True,
        observation_mode=mode,
        severity_by_channel=dict.fromkeys(CHANNELS, "zero"),
        replicate_count=1,
    )


def build(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    scenario: Optional[FakeQueryScenario] = None,
) -> tuple[Any, FakeQueryScenario]:
    """A scenario whose log answers come from a fake, and the fake itself."""

    log = FakeQueryScenario() if scenario is None else scenario
    built: list[int] = []

    def builder(log_file: str, token: str, duration_s: float, frequency_hz: float) -> Any:
        built.append(1)
        return log

    monkeypatch.setattr(module, "BUILD_SCENARIO", builder)
    adapter = module.NuPlanScenario(reference(module), "lead_or_stopping", PROTOCOL_HASH)
    adapter.builds = built
    return adapter, log


def capture(monkeypatch: pytest.MonkeyPatch, module: ModuleType) -> list[dict[str, Any]]:
    """Record the keywords the step loop was driven with, without driving it."""

    calls: list[dict[str, Any]] = []

    def spy(**keywords: Any) -> Any:
        calls.append(keywords)
        return None

    monkeypatch.setattr(module, "RUN_STEPS", spy)
    return calls


def test_the_recording_is_read_once_however_many_configurations_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two cells driven over two readings of one log would differ for that reason."""

    module = load_module()
    adapter, log = build(monkeypatch, module)

    setup = adapter.build_setup(object())
    after_setup = log.reads
    for identifier in ("oracle_aeb", "dropout-high", "coalition-none"):
        adapter.simulate(setup, configuration(identifier, mode="corrupted"), 0)

    assert adapter.builds == [1]
    assert log.reads == after_setup


def test_the_route_is_the_ego_path_the_recording_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A route invented here would drive the ego past agents recorded somewhere else."""

    module = load_module()
    calls = capture(monkeypatch, module)
    adapter, _ = build(monkeypatch, module)

    setup = adapter.build_setup(object())
    adapter.simulate(setup, configuration(), 0)

    route = calls[0]["route_xy"]
    assert route.shape == (STEPS, 2)
    assert route[0].tolist() == [0.0, 0.0]
    assert route[-1].tolist() == [float(STEPS - 1), 0.0]


def test_the_route_signature_is_the_plan_built_from_that_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signature is what makes 'every configuration drove one route' checkable."""

    from aebrisk.simulation.route_follower import build_nominal_plan, plan_bytes

    module = load_module()
    adapter, _ = build(monkeypatch, module)

    setup = adapter.build_setup(object())

    route = np.array([[float(step), 0.0] for step in range(STEPS)], dtype=np.float64)
    expected = plan_bytes(build_nominal_plan(route, 9.0, 9.0, None)).hex()
    assert setup.route_signature == expected


def test_the_setup_pins_the_protocol_duration_and_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token measured over another horizon is not comparable with the rest."""

    from aebrisk.nuplan_adapter.simulation import (
        PROTOCOL_FREQUENCY_HZ,
        PROTOCOL_SCENARIO_DURATION_S,
    )

    module = load_module()
    adapter, _ = build(monkeypatch, module)

    setup = adapter.build_setup(object())

    assert setup.frequency_hz == PROTOCOL_FREQUENCY_HZ
    assert setup.termination_s == PROTOCOL_SCENARIO_DURATION_S
    assert setup.scenario_token == "t-0001"
    assert setup.family == "lead_or_stopping"
    assert setup.initial_speed_mps == 9.0
    assert setup.planner_id and setup.controller_id


def test_the_ego_is_sized_from_the_recording_rather_than_from_a_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guessed footprint would put every clearance in the study out by centimetres."""

    module = load_module()
    calls = capture(monkeypatch, module)
    adapter, _ = build(monkeypatch, module)

    adapter.simulate(adapter.build_setup(object()), configuration(), 0)

    assert calls[0]["ego_size_lw_m"] == (5.176, 2.297)


def test_the_frames_carry_the_recordings_own_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frame stamped on a synthetic grid would not share its tracks' clock."""

    module = load_module()
    calls = capture(monkeypatch, module)
    adapter, _ = build(monkeypatch, module, FakeQueryScenario(lead_x=60.0))

    adapter.simulate(adapter.build_setup(object()), configuration(), 0)
    frame_at_step = calls[0]["frame_at_step"]

    first_stamp, first_tracks = frame_at_step(0)
    later_stamp, _ = frame_at_step(7)
    assert first_stamp == FIRST_TIMESTAMP_US
    assert later_stamp == FIRST_TIMESTAMP_US + 7 * STEP_US
    assert [track.track_id for track in first_tracks] == ["lead-0001"]
    assert first_tracks[0].source_timestamp_us == FIRST_TIMESTAMP_US


def test_the_step_length_and_count_come_from_the_protocol_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stepping at the log's own rate would double every duration this study reports."""

    module = load_module()
    calls = capture(monkeypatch, module)
    adapter, _ = build(monkeypatch, module)

    adapter.simulate(adapter.build_setup(object()), configuration(), 0)

    assert calls[0]["steps"] == STEPS
    assert calls[0]["dt_s"] == pytest.approx(0.1)
    assert calls[0]["protocol_hash"] == PROTOCOL_HASH


def test_no_map_speed_limit_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The map stack is absent by design, and the absence must be stated, not guessed."""

    module = load_module()
    calls = capture(monkeypatch, module)
    adapter, _ = build(monkeypatch, module)

    adapter.simulate(adapter.build_setup(object()), configuration(), 0)

    assert calls[0]["map_speed_limit_mps"] is None


def test_a_scenario_whose_ego_never_moved_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recording with no route carries no heading either, and nothing to drive."""

    module = load_module()
    adapter, _ = build(monkeypatch, module, FakeQueryScenario(step_m=0.0, speed=0.0))

    with pytest.raises(ValueError, match=r"^the ego never moved in t-0001"):
        adapter.build_setup(object())


def test_simulating_before_the_recording_is_read_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading it in `simulate` instead would give each configuration its own reading."""

    module = load_module()
    adapter, _ = build(monkeypatch, module)

    from aebrisk.simulation.runner import ScenarioSetup

    setup = ScenarioSetup(
        scenario_token="t-0001",
        family="lead_or_stopping",
        initial_speed_mps=9.0,
        route_signature="a" * 64,
        planner_id="p",
        controller_id="c",
        frequency_hz=10.0,
        termination_s=15.0,
    )

    with pytest.raises(ValueError, match=r"^t-0001 was simulated before it was read"):
        adapter.simulate(setup, configuration(), 0)


def test_a_scenario_without_a_protocol_hash_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every error draw is keyed on it; two protocols would corrupt identically."""

    module = load_module()

    with pytest.raises(ValueError, match=r"^protocol_hash must name the frozen protocol"):
        module.NuPlanScenario(reference(module), "lead_or_stopping", "")


def test_the_scenario_drives_the_production_loop_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a spy: the real loop over fake log answers, which is what the runner does."""

    module = load_module()
    adapter, _ = build(monkeypatch, module, FakeQueryScenario(lead_x=70.0))

    setup = adapter.build_setup(object())
    outcome = adapter.simulate(setup, configuration(), 0)

    assert outcome.token == "t-0001"
    assert outcome.configuration_id == "oracle_aeb"
    assert len(outcome.states) == STEPS or outcome.ran_out_of_route
    assert outcome.min_ttc_s is not None
