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

The third rule is that the two headline failures are produced here. A missed
intervention is not a property of a run: it is a property of a run compared with
the oracle's run of the same token, which no single simulation can see. So the
simulator returns what it measured, including its braking trace, and this module
assembles the record once every configuration has finished.
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


def make_outcome(token: str, configuration_id: str, replicate: int, **overrides: Any) -> Any:
    from aebrisk.simulation.step_loop import StepLoopOutcome

    fields: dict[str, Any] = {
        "token": token,
        "configuration_id": configuration_id,
        "replicate": replicate,
        "states": (),
        "commands": (),
        "nominal_accelerations_mps2": (),
        "collisions": {"vru": 0, "vehicle": 0, "object": 0},
        "collision_energy_j": 0.0,
        "min_ttc_s": 2.0,
        "min_clearance_m": 1.0,
        "max_deceleration_mps2": 3.0,
        "max_abs_jerk_mps3": 5.0,
        "intervention_duration_s": 1.0,
        "distance_travelled_m": 40.0,
        "final_speed_mps": 8.0,
        "final_pose_xy_m": (40.0, 0.0),
        "stop_distance_m": None,
        "ran_out_of_route": False,
    }
    fields.update(overrides)
    return StepLoopOutcome(**fields)


def trace(pattern: str) -> tuple[Any, ...]:
    """A braking trace written as a picture: `.` monitors and `b` brakes.

    The simulation steps on a 0.1 s grid, so `".....bbbbb"` is an intervention
    that began half a second in, which is how a reader of these tests needs to
    think about onset rather than in step indices.
    """

    from aebrisk.aeb.state_machine import AEBCommand, AEBState

    return tuple(
        AEBCommand(
            state=AEBState.PARTIAL if mark == "b" else AEBState.MONITOR,
            target_acceleration_mps2=-3.0 if mark == "b" else 0.0,
            selected_track_id="lead-0001" if mark == "b" else None,
        )
        for mark in pattern
    )


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
        return make_outcome(self.token, configuration.configuration_id, replicate)


class TracedScenario(SpyScenario):
    """A scenario whose braking trace is chosen per configuration, and per replicate.

    Nothing else about the runs differs, so any missed or false intervention in
    the records came from the comparison rather than from the simulation.
    """

    def __init__(self, traces: dict[str, Any], token: str = "s-0001") -> None:
        super().__init__(token=token)
        self.traces = traces

    def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
        self.seen.append((setup, configuration.configuration_id, replicate))
        pattern = self.traces[configuration.configuration_id]
        if not isinstance(pattern, str):
            pattern = pattern[replicate]
        return make_outcome(
            self.token,
            configuration.configuration_id,
            replicate,
            commands=trace(pattern),
        )


def oracle_configuration(configuration_id: str = "oracle_aeb", **overrides: Any) -> Any:
    return make_configuration(configuration_id, observation_mode="oracle", **overrides)


def two_configurations() -> tuple[Any, ...]:
    return (make_configuration("no_aeb", aeb_enabled=False), oracle_configuration())


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
        oracle_configuration(replicate_count=1),
        make_configuration("corrupted", replicate_count=3),
    )

    results, invalid = runner.run_common_scenario(scenario, configurations, protocol=object())

    assert invalid is None
    assert [(name, replicate) for _, name, replicate in scenario.seen] == [
        ("no_aeb", 0),
        ("oracle_aeb", 0),
        ("corrupted", 0),
        ("corrupted", 1),
        ("corrupted", 2),
    ]
    assert len(results) == 5


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
            return make_outcome(
                self.token,
                configuration.configuration_id,
                replicate,
                collisions={"vru": 0, "vehicle": 1, "object": 0},
                collision_energy_j=120_000.0,
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

    with pytest.raises(ValueError, match=r"^duplicate\ configuration_id\ in\ "):
        runner.run_common_scenario(SpyScenario(), configurations, protocol=object())


def test_a_result_for_the_wrong_token_is_refused() -> None:
    """A simulator returning another scenario's result would corrupt the cohort silently."""

    runner = load_runner_module()

    class Confused(SpyScenario):
        def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
            return make_outcome("s-9999", configuration.configuration_id, replicate)

    with pytest.raises(ValueError, match=r"^simulator returned scenario_token 's-9999' for token "):
        runner.run_common_scenario(Confused(), two_configurations(), protocol=object())


def test_a_result_for_the_wrong_configuration_is_refused() -> None:
    """The same guard on the other identifier every result is filed under."""

    runner = load_runner_module()

    class Confused(SpyScenario):
        def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
            return make_outcome(self.token, "somewhere-else", replicate)

    with pytest.raises(
        ValueError,
        match=r"^simulator returned configuration_id 'somewhere-else' for configuration ",
    ):
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

    with pytest.raises(ValueError, match=r"^scenario_token must not be empty$"):
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

    with pytest.raises(ValueError, match=r"^family\ must\ be\ one\ of\ "):
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


# --------------------------------------------------------------------------
# The comparison the records are assembled from
# --------------------------------------------------------------------------


def by_configuration(results: tuple[Any, ...]) -> dict[str, Any]:
    return {record.configuration_id: record for record in results}


def test_a_corrupted_run_that_braked_late_is_counted_as_a_miss() -> None:
    """Braking 0.4 s after the oracle is not the same protective action arriving later."""

    runner = load_runner_module()
    scenario = TracedScenario({"oracle_aeb": ".....bbbbb", "late": ".........bbbbb"})

    results, invalid = runner.run_common_scenario(
        scenario,
        (oracle_configuration(), make_configuration("late")),
        protocol=object(),
    )

    assert invalid is None
    late = by_configuration(results)["late"]
    assert late.missed_interventions == 1
    assert late.false_interventions == 0
    assert late.matched_delay_s == pytest.approx((0.4,))


def test_a_corrupted_run_that_braked_for_nothing_is_counted_as_false() -> None:
    """The cost side of the trade: braking for something the oracle never braked for."""

    runner = load_runner_module()
    scenario = TracedScenario({"oracle_aeb": "..........", "jumpy": "....bbb..."})

    results, _ = runner.run_common_scenario(
        scenario,
        (oracle_configuration(), make_configuration("jumpy")),
        protocol=object(),
    )

    jumpy = by_configuration(results)["jumpy"]
    assert jumpy.false_interventions == 1
    assert jumpy.missed_interventions == 0
    assert jumpy.matched_delay_s == ()


def test_a_run_that_braked_when_the_oracle_did_is_neither() -> None:
    """The control: an intervention at the same moment is not a failure of any kind."""

    runner = load_runner_module()
    scenario = TracedScenario({"oracle_aeb": ".....bbbbb", "agreeing": ".....bbbbb"})

    results, _ = runner.run_common_scenario(
        scenario,
        (oracle_configuration(), make_configuration("agreeing")),
        protocol=object(),
    )

    agreeing = by_configuration(results)["agreeing"]
    assert (agreeing.missed_interventions, agreeing.false_interventions) == (0, 0)
    assert agreeing.matched_delay_s == pytest.approx((0.0,))


def test_the_reference_run_is_scored_against_itself() -> None:
    """Its own record must show the zero, or a reader cannot see what the baseline was."""

    runner = load_runner_module()
    scenario = TracedScenario({"oracle_aeb": ".....bbbbb", "agreeing": ".....bbbbb"})

    results, _ = runner.run_common_scenario(
        scenario,
        (oracle_configuration(), make_configuration("agreeing")),
        protocol=object(),
    )

    oracle = by_configuration(results)["oracle_aeb"]
    assert (oracle.missed_interventions, oracle.false_interventions) == (0, 0)


def test_a_configuration_with_no_aeb_has_missed_nothing() -> None:
    """It has nothing to brake with, so the oracle's braking is not braking it MISSED.

    Counting it would make the baseline that exists to show the scenario was
    dangerous read as the study's worst perception failure.
    """

    runner = load_runner_module()
    scenario = TracedScenario({"no_aeb": "", "oracle_aeb": ".....bbbbb"})

    results, _ = runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    without = by_configuration(results)["no_aeb"]
    assert without.missed_interventions == 0
    assert without.false_interventions == 0
    assert without.matched_delay_s == ()


def test_the_record_carries_what_the_run_measured() -> None:
    """The record is assembled here now, so this mapping is a contract rather than glue."""

    runner = load_runner_module()

    class Measured(SpyScenario):
        def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
            return make_outcome(
                self.token,
                configuration.configuration_id,
                replicate,
                collisions={"vru": 2, "vehicle": 1, "object": 3},
                collision_energy_j=1234.0,
                min_ttc_s=0.7,
                min_clearance_m=0.25,
                max_deceleration_mps2=5.5,
                max_abs_jerk_mps3=4.25,
                intervention_duration_s=1.3,
                stop_distance_m=12.5,
            )

    results, _ = runner.run_common_scenario(Measured(), two_configurations(), protocol=object())
    record = by_configuration(results)["oracle_aeb"]

    assert (record.collision_vru, record.collision_vehicle, record.collision_object) == (2, 1, 3)
    assert record.collision_energy == 1234.0
    assert (record.min_ttc_s, record.min_clearance_m) == (0.7, 0.25)
    assert (record.max_deceleration_mps2, record.max_abs_jerk_mps3) == (5.5, 4.25)
    assert record.intervention_duration_s == 1.3
    assert record.stop_distance_m == 12.5
    assert record.family == "lead_or_stopping"
    assert record.valid is True


def test_a_matrix_without_an_oracle_reference_is_refused() -> None:
    """Without a reference the two headline metrics have nothing to be measured against."""

    runner = load_runner_module()

    with pytest.raises(ValueError, match=r"^no configuration is the oracle with its AEB enabled"):
        runner.run_common_scenario(
            SpyScenario(), (make_configuration("corrupted"),), protocol=object()
        )


def test_an_oracle_configuration_with_no_aeb_is_not_a_reference() -> None:
    """A reference that never brakes would make every corrupted intervention false."""

    runner = load_runner_module()
    configurations = (
        make_configuration("oracle_no_aeb", observation_mode="oracle", aeb_enabled=False),
        make_configuration("corrupted"),
    )

    with pytest.raises(ValueError, match=r"^no configuration is the oracle with its AEB enabled"):
        runner.run_common_scenario(SpyScenario(), configurations, protocol=object())


def test_two_oracle_references_are_refused() -> None:
    """Which one was chosen would decide every missed intervention in the study."""

    runner = load_runner_module()
    configurations = (oracle_configuration(), oracle_configuration("oracle_again"))

    with pytest.raises(
        ValueError, match=r"^more than one oracle configuration has its AEB enabled"
    ):
        runner.run_common_scenario(SpyScenario(), configurations, protocol=object())


def test_the_reference_is_checked_before_anything_is_simulated() -> None:
    """A matrix that cannot define its own metrics must cost a second, not a cohort."""

    runner = load_runner_module()
    scenario = SpyScenario()

    with pytest.raises(ValueError, match=r"^no configuration is the oracle"):
        runner.run_common_scenario(scenario, (make_configuration("corrupted"),), protocol=object())

    assert scenario.setup_calls == 0
    assert scenario.seen == []


def test_oracle_replicates_that_disagree_are_refused() -> None:
    """The oracle passes through no error channel, so its replicates must be identical."""

    runner = load_runner_module()
    scenario = TracedScenario({"oracle_aeb": (".....bbbbb", "......bbbbb")})

    with pytest.raises(ValueError, match=r"produced 2 different braking traces"):
        runner.run_common_scenario(
            scenario, (oracle_configuration(replicate_count=2),), protocol=object()
        )


def test_a_result_for_the_wrong_replicate_is_refused() -> None:
    """Two runs filed under one number overwrite each other and a third goes missing."""

    runner = load_runner_module()

    class Confused(SpyScenario):
        def simulate(self, setup: Any, configuration: Any, replicate: int) -> Any:
            return make_outcome(self.token, configuration.configuration_id, 7)

    with pytest.raises(ValueError, match=r"^simulator returned replicate 7 for replicate 0"):
        runner.run_common_scenario(Confused(), two_configurations(), protocol=object())


def test_a_failed_token_returns_only_the_failure() -> None:
    """The finished runs were measured, but their comparison will now never happen.

    Their missed and false interventions are defined by a comparison against an
    oracle run this token never completed, so a record carrying zeros for them
    would be a fabricated result rather than a partial one.
    """

    runner = load_runner_module()
    scenario = SpyScenario(fail_on="oracle_aeb")

    results, invalid = runner.run_common_scenario(scenario, two_configurations(), protocol=object())

    assert invalid is not None
    assert [record.configuration_id for record in results] == ["oracle_aeb"]
    assert results[0].valid is False
