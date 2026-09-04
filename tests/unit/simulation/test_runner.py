"""Contracts for running one scenario token through every configuration.

The claim this study makes is that the CONTROLLER, the ROUTE, the INITIAL STATE,
the RATE and the TERMINATION were identical across configurations and only the
perception differed. That claim is made true here or nowhere: the setup is built
once per token and handed to every configuration, so there is no code path in
which one configuration could receive a different one.

The second rule is the guarded transaction. A token is run for all its
configurations or it is used in none of them. Keeping the configurations that
finished would average them over a different set of roads than the one that
failed, and that difference would be reported as an effect of perception error.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest


def load_runner_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.simulation import runner
    except ImportError:
        pytest.fail("aebrisk.simulation.runner is missing", pytrace=False)
    return runner


def make_configuration(configuration_id: str, **overrides: Any) -> Any:
    from aebrisk.simulation.common_cohort import ExperimentConfiguration

    fields: dict[str, Any] = {
        "configuration_id": configuration_id,
        "aeb_enabled": True,
        "observation_mode": "corrupted",
        "severity_by_channel": {
            "dropout": "zero",
            "localization_shape": "zero",
            "latency": "zero",
            "track_instability": "zero",
        },
        "replicate_count": 1,
    }
    fields.update(overrides)
    return ExperimentConfiguration(**fields)


def make_result(token: str, configuration_id: str, replicate: int, **overrides: Any) -> Any:
    from aebrisk.artifacts.results import AEBScenarioResultV1

    fields: dict[str, Any] = {
        "schema_version": "aeb-scenario-result/v1",
        "scenario_token": token,
        "family": "lead_or_stopping",
        "configuration_id": configuration_id,
        "replicate": replicate,
        "valid": True,
        "invalid_reason": None,
        "collision_vru": 0,
        "collision_vehicle": 0,
        "collision_object": 0,
        "collision_energy": 0.0,
        "min_ttc_s": 2.0,
        "min_clearance_m": 1.0,
        "missed_interventions": 0,
        "false_interventions": 0,
        "matched_delay_s": (),
        "stop_distance_m": None,
        "max_deceleration_mps2": 3.0,
        "max_abs_jerk_mps3": 5.0,
        "intervention_duration_s": 1.0,
    }
    fields.update(overrides)
    return AEBScenarioResultV1(**fields)


class SpyScenario:
    """Records the setup every configuration was handed, and what it was asked to run."""

    def __init__(self, token: str = "s-0001", fail_on: str = "") -> None:
        self.token = token
        self.fail_on = fail_on
        self.setup_calls = 0
        self.seen: list[tuple[Any, str, int]] = []

    def build_setup(self, protocol: Any) -> Any:
        from aebrisk.simulation.runner import ScenarioSetup

        self.setup_calls += 1
        return ScenarioSetup(
            scenario_token=self.token,
            family="lead_or_stopping",
            initial_speed_mps=8.0,
            route_signature="a" * 64,
            planner_id="aebrisk-closed-loop/v1",
            controller_id="aebrisk-jerk-limited/v1",
            frequency_hz=10.0,
            termination_s=15.0,
        )

    def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
        self.seen.append((setup, configuration.configuration_id, replicate))
        if configuration.configuration_id == self.fail_on:
            raise RuntimeError("the log database went away")
        return make_result(self.token, configuration.configuration_id, replicate)


def two_configurations() -> tuple[Any, ...]:
    return (make_configuration("no_aeb", aeb_enabled=False), make_configuration("oracle_aeb"))


# --------------------------------------------------------------------------
# The controlled comparison
# --------------------------------------------------------------------------


def test_the_setup_is_built_once_for_the_token() -> None:
    """Building it per configuration is how the two could ever differ."""

    runner = load_runner_module()
    scenario = SpyScenario()

    runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    assert scenario.setup_calls == 1


def test_every_configuration_receives_the_identical_setup() -> None:
    """The study's controlled comparison, asserted on object identity.

    Equality would pass for two objects that merely happened to agree today;
    identity says there is only one, so no future edit can make them diverge.
    """

    runner = load_runner_module()
    scenario = SpyScenario()

    runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    setups = {id(setup) for setup, _, _ in scenario.seen}
    assert len(setups) == 1


def test_the_setup_pins_the_rate_the_planner_and_the_termination() -> None:
    """Named fields, because these are exactly what a reader must be able to check."""

    runner = load_runner_module()
    scenario = SpyScenario()

    runner.run_common_scenario(scenario, two_configurations(), protocol=object())
    setup = scenario.seen[0][0]

    assert setup.frequency_hz == 10.0
    assert setup.planner_id
    assert setup.controller_id
    assert setup.termination_s > 0.0


def test_every_configuration_and_replicate_is_run() -> None:
    """A configuration silently skipped would leave an unbalanced cohort."""

    runner = load_runner_module()
    scenario = SpyScenario()
    configurations = (
        make_configuration("no_aeb", aeb_enabled=False, replicate_count=1),
        make_configuration("corrupted", replicate_count=3),
    )

    results, invalid = runner.run_common_scenario(scenario, configurations, protocol=object())

    assert invalid is None
    assert [(name, replicate) for _, name, replicate in scenario.seen] == [
        ("no_aeb", 0),
        ("corrupted", 0),
        ("corrupted", 1),
        ("corrupted", 2),
    ]
    assert len(results) == 4


def test_the_configuration_order_is_preserved() -> None:
    """Results are compared pairwise downstream, so their order is not incidental."""

    runner = load_runner_module()
    scenario = SpyScenario()

    results, _ = runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    assert [record.configuration_id for record in results] == ["no_aeb", "oracle_aeb"]


# --------------------------------------------------------------------------
# The guarded transaction
# --------------------------------------------------------------------------


def test_a_failure_in_one_configuration_invalidates_the_token() -> None:
    """The rule the whole comparison rests on."""

    runner = load_runner_module()
    scenario = SpyScenario(fail_on="oracle_aeb")

    _, invalid = runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    assert invalid is not None
    assert invalid.scenario_token == "s-0001"


def test_a_failure_stops_the_remaining_configurations() -> None:
    """A token already doomed must not consume compute proving it again."""

    runner = load_runner_module()
    scenario = SpyScenario(fail_on="no_aeb")

    runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    assert [name for _, name, _ in scenario.seen] == ["no_aeb"]


def test_the_failed_configuration_is_recorded_as_an_invalid_result() -> None:
    """The raw per-configuration failure is kept, not only the exclusion.

    An exclusion manifest says a token left the cohort; this says which
    configuration it left from, which is what makes the failure diagnosable.
    """

    runner = load_runner_module()
    scenario = SpyScenario(fail_on="no_aeb")

    results, _ = runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    assert len(results) == 1
    assert results[0].configuration_id == "no_aeb"
    assert results[0].valid is False
    assert results[0].invalid_reason


def test_a_failure_records_the_phase_and_the_exception() -> None:
    """The same evidence any other infrastructure failure carries."""

    runner = load_runner_module()
    scenario = SpyScenario(fail_on="no_aeb")

    _, invalid = runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    assert invalid.exception_type == "RuntimeError"
    assert invalid.phase == "step"
    assert invalid.stack_sha256 is not None


def test_a_failure_to_build_the_setup_invalidates_before_any_configuration_runs() -> None:
    """Nothing can be compared if the token could not be initialized at all."""

    runner = load_runner_module()

    class Broken(SpyScenario):
        def build_setup(self, protocol: Any) -> Any:
            raise FileNotFoundError("the map is missing")

    scenario = Broken()

    results, invalid = runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    assert results == ()
    assert invalid.phase == "initialization"
    assert invalid.exception_type == "FileNotFoundError"


def test_a_collision_is_not_an_infrastructure_failure() -> None:
    """The measurement this study exists to make must survive its own runner."""

    runner = load_runner_module()

    class Colliding(SpyScenario):
        def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
            return make_result(
                self.token,
                configuration.configuration_id,
                replicate,
                collision_vehicle=1,
                collision_energy=120_000.0,
                min_ttc_s=0.0,
                min_clearance_m=0.0,
            )

    results, invalid = runner.run_common_scenario(
        Colliding(), two_configurations(), protocol=object()
    )

    assert invalid is None
    assert all(record.valid for record in results)
    assert all(record.collision_vehicle == 1 for record in results)


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_no_configurations_is_refused() -> None:
    """A token run against nothing produces no comparison and no failure either."""

    runner = load_runner_module()

    with pytest.raises(
        ValueError, match=r"^configurations\ must\ not\ be\ empty;\ a\ token\ run\ against\ nothing"
    ):
        runner.run_common_scenario(SpyScenario(), (), protocol=object())


def test_duplicate_configuration_ids_are_refused() -> None:
    """Two cells with the same name would overwrite each other in every artifact."""

    runner = load_runner_module()
    configurations = (make_configuration("oracle_aeb"), make_configuration("oracle_aeb"))

    with pytest.raises(ValueError, match="duplicate"):
        runner.run_common_scenario(SpyScenario(), configurations, protocol=object())


def test_a_result_for_the_wrong_token_is_refused() -> None:
    """A simulator returning another scenario's result would corrupt the cohort silently."""

    runner = load_runner_module()

    class Confused(SpyScenario):
        def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
            return make_result("s-9999", configuration.configuration_id, replicate)

    with pytest.raises(ValueError, match="scenario_token"):
        runner.run_common_scenario(Confused(), two_configurations(), protocol=object())


def test_a_result_for_the_wrong_configuration_is_refused() -> None:
    """The same guard on the other identifier every result is filed under."""

    runner = load_runner_module()

    class Confused(SpyScenario):
        def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
            return make_result(self.token, "somewhere-else", replicate)

    with pytest.raises(ValueError, match="configuration_id"):
        runner.run_common_scenario(Confused(), two_configurations(), protocol=object())


def test_the_setup_is_frozen() -> None:
    """It is the evidence that the comparison was controlled."""

    import dataclasses

    load_runner_module()
    scenario = SpyScenario()
    setup = scenario.build_setup(object())

    with pytest.raises(dataclasses.FrozenInstanceError):
        setup.frequency_hz = 20.0  # type: ignore[misc]


def test_a_setup_at_the_wrong_rate_is_refused() -> None:
    """The protocol fixes 10 Hz; another rate is a different experiment."""

    runner = load_runner_module()

    with pytest.raises(ValueError, match=r"^frequency_hz must "):
        runner.ScenarioSetup(
            scenario_token="s-0001",
            family="lead_or_stopping",
            initial_speed_mps=8.0,
            route_signature="a" * 64,
            planner_id="p",
            controller_id="c",
            frequency_hz=0.0,
            termination_s=15.0,
        )


def setup_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "scenario_token": "s-0001",
        "family": "lead_or_stopping",
        "initial_speed_mps": 8.0,
        "route_signature": "a" * 64,
        "planner_id": "aebrisk-closed-loop/v1",
        "controller_id": "aebrisk-jerk-limited/v1",
        "frequency_hz": 10.0,
        "termination_s": 15.0,
    }
    fields.update(overrides)
    return fields


def test_a_setup_without_a_token_is_refused() -> None:
    """Every result and every exclusion is filed under it."""

    runner = load_runner_module()

    with pytest.raises(ValueError, match="scenario_token"):
        runner.ScenarioSetup(**setup_fields(scenario_token=""))


@pytest.mark.parametrize("bad_value", [-1.0, float("nan")])
def test_a_setup_with_an_impossible_initial_speed_is_refused(bad_value: float) -> None:
    """A negative logged speed means the log was parsed wrongly."""

    runner = load_runner_module()

    with pytest.raises(
        ValueError, match=r"^initial_speed_mps\ must\ be\ finite\ and\ non\-negative"
    ):
        runner.ScenarioSetup(**setup_fields(initial_speed_mps=bad_value))


def test_a_setup_without_a_route_signature_is_refused() -> None:
    """It is the evidence that every configuration drove the same route.

    An empty one would make that claim unverifiable while still looking
    recorded, which is worse than not recording it.
    """

    runner = load_runner_module()

    with pytest.raises(ValueError, match=r"^route_signature\ must\ not\ be\ empty"):
        runner.ScenarioSetup(**setup_fields(route_signature=""))


@pytest.mark.parametrize("field", ["planner_id", "controller_id"])
def test_a_setup_that_does_not_name_its_controller_is_refused(field: str) -> None:
    """Two runs naming the same planner must have used the same one."""

    runner = load_runner_module()

    with pytest.raises(
        ValueError, match=r"^planner_id\ and\ controller_id\ must\ both\ name\ something"
    ):
        runner.ScenarioSetup(**setup_fields(**{field: ""}))


def test_a_setup_with_an_unknown_family_is_refused() -> None:
    """The four families are the stratification; a fifth is a different cohort."""

    runner = load_runner_module()

    with pytest.raises(ValueError, match="family"):
        runner.ScenarioSetup(
            scenario_token="s-0001",
            family="roundabout",  # type: ignore[arg-type]
            initial_speed_mps=8.0,
            route_signature="a" * 64,
            planner_id="p",
            controller_id="c",
            frequency_hz=10.0,
            termination_s=15.0,
        )


def test_a_setup_that_never_terminates_is_refused() -> None:
    """A scenario with no horizon would run until the process was killed."""

    runner = load_runner_module()

    with pytest.raises(
        ValueError, match=r"^termination_s\ must\ be\ finite\ and\ positive,\ got\ "
    ):
        runner.ScenarioSetup(
            scenario_token="s-0001",
            family="lead_or_stopping",
            initial_speed_mps=8.0,
            route_signature="a" * 64,
            planner_id="p",
            controller_id="c",
            frequency_hz=10.0,
            termination_s=0.0,
        )
