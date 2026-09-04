"""Contracts for turning one nuPlan iteration into one WorldFrame.

The adapter is the only place in this project that knows what nuPlan calls
things, and it is deliberately structural: it reads attributes off whatever
scenario object it is handed rather than importing `AbstractScenario`. That is
not a style preference. `AbstractScenario` imports the devkit's observation
types, which import its image stack, which needs OpenCV; a project that reads
world state only would have to install the entire sensor pipeline just to name
the type it never uses. Depending on the shape instead keeps the boundary the
study claims to have.

The fakes below therefore stand in for real scenarios, and a spy test asserts
that no sensor or camera method is ever reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any, Optional

import pytest


def load_scenario_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.nuplan_adapter import scenario
    except ImportError:
        pytest.fail("aebrisk.nuplan_adapter.scenario is missing", pytrace=False)
    return scenario


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
class FakeStaticObject:
    """Shaped like `nuplan.common.actor_state.static_object.StaticObject`; no velocity."""

    track_token: str
    tracked_object_type: FakeType
    box: FakeBox


@dataclass(frozen=True)
class FakeDynamicCarState:
    speed: float


@dataclass(frozen=True)
class FakeEgoState:
    center: FakeStateSE2
    dynamic_car_state: FakeDynamicCarState
    time_us: int


class FakeTrackedObjects:
    def __init__(self, objects: list[Any]) -> None:
        self._objects = objects

    def __iter__(self) -> Any:
        return iter(self._objects)


class FakeScenario:
    """A scenario with only the members the adapter is allowed to read."""

    def __init__(
        self,
        token: str = "s-0001",
        objects: Optional[list[Any]] = None,
        iterations: int = 3,
        ego_time_us: int = 1_600_000_000_000_000,
    ) -> None:
        self.token = token
        self._objects = [] if objects is None else objects
        self._iterations = iterations
        self._ego_time_us = ego_time_us
        self.touched: list[str] = []

    def get_number_of_iterations(self) -> int:
        self.touched.append("get_number_of_iterations")
        return self._iterations

    def get_tracked_objects_at_iteration(self, iteration: int) -> FakeTrackedObjects:
        self.touched.append(f"get_tracked_objects_at_iteration({iteration})")
        return FakeTrackedObjects(self._objects)

    def get_ego_state_at_iteration(self, iteration: int) -> FakeEgoState:
        self.touched.append(f"get_ego_state_at_iteration({iteration})")
        return FakeEgoState(
            center=FakeStateSE2(0.0, 0.0, 0.0),
            dynamic_car_state=FakeDynamicCarState(speed=8.0),
            time_us=self._ego_time_us + iteration * 100_000,
        )


class SensorTrippingScenario(FakeScenario):
    """Fails the test the moment anything reaches for sensor or camera data."""

    def get_sensors_at_iteration(self, iteration: int, channels: Any = None) -> Any:
        raise AssertionError("the adapter reached for sensor data")

    def get_sensor_data_token_timestamp(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the adapter reached for sensor data")

    def get_images_at_iteration(self, iteration: int) -> Any:
        raise AssertionError("the adapter reached for camera images")

    @property
    def sensor_root(self) -> Any:
        raise AssertionError("the adapter reached for the sensor root")


def agent(
    track_token: str = "a-1",
    category: str = "VEHICLE",
    center: tuple[float, float] = (10.0, 2.0),
    heading: float = 0.0,
    size_lw: tuple[float, float] = (4.5, 1.9),
    velocity: tuple[float, float] = (5.0, 0.0),
) -> FakeAgent:
    return FakeAgent(
        track_token=track_token,
        tracked_object_type=FakeType(category),
        box=FakeBox(FakeStateSE2(center[0], center[1], heading), size_lw[0], size_lw[1]),
        velocity=FakeVector(velocity[0], velocity[1]),
    )


def static_object(track_token: str = "o-1", category: str = "BARRIER") -> FakeStaticObject:
    return FakeStaticObject(
        track_token=track_token,
        tracked_object_type=FakeType(category),
        box=FakeBox(FakeStateSE2(20.0, 1.0, 0.0), 1.0, 1.0),
    )


@pytest.mark.parametrize(
    ("nuplan_category", "expected"),
    [
        ("VEHICLE", "vehicle"),
        ("PEDESTRIAN", "pedestrian"),
        ("BICYCLE", "bicycle"),
        ("TRAFFIC_CONE", "object"),
        ("BARRIER", "object"),
        ("CZONE_SIGN", "object"),
        ("GENERIC_OBJECT", "object"),
    ],
)
def test_every_nuplan_category_maps_to_a_study_group(
    nuplan_category: str,
    expected: str,
) -> None:
    """The mapping decides risk weighting and AEB priority, so it is fixed here."""

    scenario = load_scenario_module()

    assert scenario.category_to_group(nuplan_category) == expected


def test_the_ego_category_is_refused() -> None:
    """The ego is not one of its own observations; treating it as one is a bug."""

    scenario = load_scenario_module()

    with pytest.raises(
        ValueError, match=r"^EGO\ is\ the\ observer,\ not\ one\ of\ its\ own\ observations$"
    ):
        scenario.category_to_group("EGO")


def test_an_unknown_category_is_refused_rather_than_grouped_as_an_object() -> None:
    """Silently grouping a new devkit category as inert would understate its risk."""

    scenario = load_scenario_module()

    with pytest.raises(ValueError, match=r"^unknown\ nuPlan\ category:\ "):
        scenario.category_to_group("SPACESHIP")


def test_one_iteration_becomes_one_world_frame() -> None:
    """This is the only conversion; everything downstream sees WorldFrame alone."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[agent()])

    frame = scenario.oracle_world_frame(fake, 0)

    assert frame.scenario_token == "s-0001"
    assert frame.ego_center_xy_m == (0.0, 0.0)
    assert frame.ego_speed_mps == 8.0
    assert len(frame.tracks) == 1


def test_the_box_centre_yaw_and_size_are_carried_across() -> None:
    """A transposed length and width would silently change every overlap test."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[agent(center=(12.5, -3.0), heading=1.25, size_lw=(4.8, 2.1))])

    [track] = scenario.oracle_world_frame(fake, 0).tracks

    assert track.center_xy_m == (12.5, -3.0)
    assert track.yaw_rad == 1.25
    assert track.size_lw_m == (4.8, 2.1)


def test_agent_velocity_is_carried_in_metres_per_second() -> None:
    """nuPlan reports m/s; a unit slip here would scale every time-to-collision."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[agent(velocity=(3.5, -1.5))])

    [track] = scenario.oracle_world_frame(fake, 0).tracks

    assert track.velocity_xy_mps == (3.5, -1.5)


def test_a_static_object_is_observed_as_stationary() -> None:
    """Static objects carry no velocity in the devkit; assuming one would be invented."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[static_object()])

    [track] = scenario.oracle_world_frame(fake, 0).tracks

    assert track.category == "object"
    assert track.velocity_xy_mps == (0.0, 0.0)


def test_the_frame_timestamp_comes_from_the_ego_state() -> None:
    """One clock per frame; taking it per track would make frames non-atomic."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[agent()], ego_time_us=1_700_000_000_000_000)

    frame = scenario.oracle_world_frame(fake, 2)

    assert frame.timestamp_us == 1_700_000_000_000_000 + 2 * 100_000
    assert frame.tracks[0].source_timestamp_us == frame.timestamp_us


def test_the_oracle_observation_is_fully_visible_and_certain() -> None:
    """The oracle is the zero-error reference every channel is measured against."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[agent()])

    [track] = scenario.oracle_world_frame(fake, 0).tracks

    assert track.visible is True
    assert track.covariance_xy == (0.0, 0.0, 0.0, 0.0)


def test_tracks_are_ordered_by_identity_not_by_devkit_order() -> None:
    """Devkit ordering is not part of the contract, and it must not reach the AEB."""

    scenario = load_scenario_module()
    fake = FakeScenario(
        objects=[agent(track_token="a-3"), agent(track_token="a-1"), agent(track_token="a-2")]
    )

    frame = scenario.oracle_world_frame(fake, 0)

    assert [track.track_id for track in frame.tracks] == ["a-1", "a-2", "a-3"]


def test_duplicate_track_tokens_are_refused() -> None:
    """One identity naming two objects would corrupt every cross-frame match."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[agent(track_token="a-1"), agent(track_token="a-1")])

    with pytest.raises(ValueError, match=r"^duplicate track token in scenario: "):
        scenario.oracle_world_frame(fake, 0)


def test_an_iteration_past_the_end_is_refused() -> None:
    """Reading past the horizon would silently repeat the last frame forever."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[agent()], iterations=3)

    with pytest.raises(IndexError, match=r"^iteration\ "):
        scenario.oracle_world_frame(fake, 3)


def test_a_negative_iteration_is_refused() -> None:
    """Negative indexing would read from the end of the scenario without saying so."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[agent()])

    with pytest.raises(IndexError, match=r"^iteration\ "):
        scenario.oracle_world_frame(fake, -1)


def test_no_sensor_or_camera_method_is_ever_reached() -> None:
    """The study's central boundary: world state only, no sensor replay.

    The scenario handed in raises on every sensor and camera accessor, so this
    passes only if the adapter never touches one.
    """

    scenario = load_scenario_module()
    fake = SensorTrippingScenario(objects=[agent()])

    frame = scenario.oracle_world_frame(fake, 0)

    assert len(frame.tracks) == 1
    assert not any("sensor" in call or "image" in call for call in fake.touched)


def test_only_the_declared_scenario_members_are_read() -> None:
    """A member read here but absent from a real scenario would fail only on real data."""

    scenario = load_scenario_module()
    fake = FakeScenario(objects=[agent()])

    scenario.oracle_world_frame(fake, 1)

    assert fake.touched == [
        "get_number_of_iterations",
        "get_ego_state_at_iteration(1)",
        "get_tracked_objects_at_iteration(1)",
    ]


def test_the_ego_is_not_observed_as_one_of_its_own_tracks() -> None:
    """The devkit's tracked objects can include the ego; the car is not a threat to itself."""

    scenario = load_scenario_module()
    ego_entry = FakeStaticObject(
        track_token="ego",
        tracked_object_type=FakeType("EGO"),
        box=FakeBox(FakeStateSE2(0.0, 0.0, 0.0), 4.9, 2.0),
    )
    fake = FakeScenario(objects=[ego_entry, agent(track_token="a-1")])

    frame = scenario.oracle_world_frame(fake, 0)

    assert [track.track_id for track in frame.tracks] == ["a-1"]
