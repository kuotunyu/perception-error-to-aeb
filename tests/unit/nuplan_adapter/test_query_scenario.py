"""Contracts for reading one nuPlan scenario through raw SQL and nothing else.

The devkit's own scenario builder cannot be imported in this container: it pulls
in the map stack, which pulls in rasterio, and the observation types pull in
OpenCV. That is deliberate — the container has neither, so an edit that reaches
for the sensor or map API cannot even import, and the "world state, database and
maps only" boundary is enforced by the environment rather than by discipline.

What remains importable is the raw-SQL query module and the actor-state types,
and they carry everything `oracle_world_frame` reads. This module is the bridge:
it answers the four members the adapter is allowed to touch, and it obtains them
by querying the log database directly.

The tests below use fakes for every query function, so none of them opens a
database. The one test that reads real data lives in
`tests/integration/test_nuplan_mini_adapter.py` and skips when nothing is mounted.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any, Optional

import pytest


def load_query_scenario_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.nuplan_adapter import query_scenario
    except ImportError:  # pragma: no cover - names the absence during RED
        pytest.fail("aebrisk.nuplan_adapter.query_scenario is missing", pytrace=False)
    return query_scenario


class FakeLidarPc:
    """The two fields this module reads off a sampled lidar_pc."""

    def __init__(self, token: str, timestamp: int) -> None:
        self.token = token
        self.timestamp = timestamp


def sampled(count: int, period_us: int = 50_000, start: int = 1_600_000_000_000_000) -> list:
    return [FakeLidarPc(f"t{index:04d}", start + index * period_us) for index in range(count)]


def patch_queries(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    *,
    lidarpcs: Optional[list] = None,
    ego: Any = "EGO",
    objects: Optional[list] = None,
    scenarios: Optional[list] = None,
    record: Optional[list] = None,
) -> None:
    """Replace every database call with a fake that records what it was asked."""

    pcs = sampled(200) if lidarpcs is None else lidarpcs

    def query_sampled(log_file: str, token: str, source: Any, indexes: Any, future: bool) -> Any:
        wanted = list(indexes)
        if record is not None:
            record.append(("sampled", log_file, token, tuple(wanted), future))
        return iter([pcs[index] for index in wanted if index < len(pcs)])

    def query_ego(log_file: str, token: str) -> Any:
        if record is not None:
            record.append(("ego", log_file, token))
        return ego

    def query_objects(log_file: str, token: str) -> Any:
        if record is not None:
            record.append(("objects", log_file, token))
        return iter([] if objects is None else objects)

    def query_scenarios(log_file: str, tokens: Any, types: Any, maps: Any) -> Any:
        if record is not None:
            record.append(("scenarios", log_file, None if types is None else tuple(types)))
        return iter([] if scenarios is None else scenarios)

    monkeypatch.setattr(module, "QUERY_SAMPLED_LIDARPCS", query_sampled)
    monkeypatch.setattr(module, "QUERY_EGO_STATE", query_ego)
    monkeypatch.setattr(module, "QUERY_TRACKED_OBJECTS", query_objects)
    monkeypatch.setattr(module, "QUERY_SCENARIOS", query_scenarios)


def test_the_scenario_answers_exactly_the_members_the_adapter_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`oracle_world_frame` reads four members; a bridge that misses one is useless."""

    module = load_query_scenario_module()
    patch_queries(monkeypatch, module)

    scenario = module.build_scenario("log.db", "anchor", duration_s=1.0, frequency_hz=10.0)

    assert scenario.token == "anchor"
    assert scenario.get_number_of_iterations() == 10
    assert scenario.get_ego_state_at_iteration(0) == "EGO"
    assert list(scenario.get_tracked_objects_at_iteration(0)) == []


def test_the_iteration_grid_is_the_protocol_rate_not_the_log_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 20 Hz log simulated at 20 Hz would double every duration the study reports."""

    module = load_query_scenario_module()
    record: list = []
    patch_queries(monkeypatch, module, record=record)

    module.build_scenario("log.db", "anchor", duration_s=1.0, frequency_hz=10.0)

    sampled_calls = [entry for entry in record if entry[0] == "sampled"]
    # One call measures the native period, the second builds the grid at stride 2.
    assert sampled_calls[-1][3] == (0, 2, 4, 6, 8, 10, 12, 14, 16, 18)


def test_each_iteration_queries_its_own_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading iteration i from the anchor's token would return the same frame every time."""

    module = load_query_scenario_module()
    record: list = []
    patch_queries(monkeypatch, module, record=record)

    scenario = module.build_scenario("log.db", "anchor", duration_s=1.0, frequency_hz=10.0)
    record.clear()
    scenario.get_ego_state_at_iteration(3)
    scenario.get_tracked_objects_at_iteration(3)

    assert record == [("ego", "log.db", "t0006"), ("objects", "log.db", "t0006")]


def test_an_iteration_outside_the_scenario_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clamping would silently score a different frame than the one asked for."""

    module = load_query_scenario_module()
    patch_queries(monkeypatch, module)
    scenario = module.build_scenario("log.db", "anchor", duration_s=1.0, frequency_hz=10.0)

    for index in (-1, 10):
        with pytest.raises(IndexError, match=r"outside the scenario's 0\.\."):
            scenario.get_ego_state_at_iteration(index)
    with pytest.raises(IndexError, match=r"outside the scenario's 0\.\."):
        scenario.get_tracked_objects_at_iteration(10)


def test_a_log_whose_rate_is_not_a_multiple_of_the_protocol_rate_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resampling a 15 Hz log to 10 Hz would put the study's steps at uneven times."""

    module = load_query_scenario_module()
    patch_queries(monkeypatch, module, lidarpcs=sampled(200, period_us=66_667))

    with pytest.raises(ValueError, match=r"^log\.db samples at "):
        module.build_scenario("log.db", "anchor", duration_s=1.0, frequency_hz=10.0)


def test_a_log_too_short_for_the_full_duration_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scenario cut short is not the same experiment as one that ran to the end."""

    module = load_query_scenario_module()
    patch_queries(monkeypatch, module, lidarpcs=sampled(9))

    with pytest.raises(ValueError, match=r"^log\.db has 5 frames after "):
        module.build_scenario("log.db", "anchor", duration_s=1.0, frequency_hz=10.0)


def test_a_log_with_one_frame_cannot_have_its_rate_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One timestamp gives no interval, and assuming 20 Hz is what this refuses to do."""

    module = load_query_scenario_module()
    patch_queries(monkeypatch, module, lidarpcs=sampled(1))

    with pytest.raises(ValueError, match=r"^log\.db has too few frames after "):
        module.build_scenario("log.db", "anchor", duration_s=1.0, frequency_hz=10.0)


def test_the_native_period_is_measured_from_the_median_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real timestamps jitter by microseconds; a mean would drift with one outlier."""

    module = load_query_scenario_module()
    jittered = [
        FakeLidarPc("t0", 0),
        FakeLidarPc("t1", 49_992),
        FakeLidarPc("t2", 99_998),
        FakeLidarPc("t3", 150_002),
        FakeLidarPc("t4", 5_000_000),
    ]

    assert module.native_period_us(jittered) == 50_004


def test_measuring_a_rate_from_one_frame_is_refused_rather_than_assumed() -> None:
    """`build_scenario` guards this too; the function must not be usable alone either.

    One timestamp gives no interval. Returning a default here — 20 Hz, say —
    is exactly the assumption this module exists to avoid, and a caller other
    than `build_scenario` would inherit it silently.
    """

    module = load_query_scenario_module()

    with pytest.raises(ValueError, match=r"^a sampling interval needs at least two frames$"):
        module.native_period_us(sampled(1))


def test_scenarios_of_type_returns_references_in_a_stable_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two runs of the same query must offer the same cohort in the same order."""

    module = load_query_scenario_module()
    rows = [
        {"token": b"\xbb\x02", "timestamp": 200, "scenario_type": "behind_bike"},
        {"token": "aa01", "timestamp": 100, "scenario_type": "behind_bike"},
    ]
    patch_queries(monkeypatch, module, scenarios=rows)

    references = module.scenarios_of_type("log.db", ("behind_bike",))

    assert [reference.token for reference in references] == ["aa01", "bb02"]
    assert references[0].scenario_type == "behind_bike"
    assert references[0].timestamp_us == 100
    assert references[0].log_file == "log.db"


def test_the_query_module_pulls_in_no_sensor_or_map_reader() -> None:
    """The boundary is enforced by what the container lacks; this proves it holds.

    `nuplan.common.maps.maps_datatypes` IS imported, and that is stated rather
    than hidden: it is an enum module carrying traffic-light status types, it
    reads nothing, and the actor-state package pulls it in. What must never
    appear is a map reader, the ORM, the devkit's scenario builder, or any of
    the image libraries those need.
    """

    load_query_scenario_module()

    forbidden = (
        "nuplan.planning.scenario_builder",
        "nuplan.common.maps.nuplan_map",
        "nuplan.common.maps.abstract_map",
        "nuplan.database.nuplan_db_orm",
    )
    loaded = sorted(name for name in sys.modules if name.startswith(forbidden))

    assert loaded == []
    assert [name for name in ("cv2", "rasterio", "matplotlib") if name in sys.modules] == []
