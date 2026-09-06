"""The pieces, wired together, on a scenario simple enough to reason about by hand.

Every other test in this project checks one module. This one checks that the
modules compose into something that brakes: observation to error channels to
threat geometry to the state machine to the limiter to the vehicle's motion, and
back into a result record the cohort can read.

THIS IS SYNTHETIC VERIFICATION, NOT A RESULT. The scenario is a stationary lead
vehicle on a straight road, chosen because its outcome can be computed with
arithmetic rather than trusted from a simulator. Nothing here may be reported as
a finding about nuPlan, about real perception error, or about any real AEB; the
portfolio order gate forbids reading real nuPlan data at all until P2 is
released, and this file exists precisely so that the pipeline can be shown to
work before then.

The arithmetic: the ego starts at the origin at 10 m/s and the lead vehicle
stands at x = 60. Bodies are 4 m long, so their faces meet when the ego's centre
reaches x = 56. With no AEB the ego holds 10 m/s and arrives there at t = 5.6 s.
With the AEB on, the corridor's front edge is 2.5 m ahead of the ego's centre,
so a predicted overlap appears at 2.5 s of time to collision once the ego passes
x = 32.5, which is 23 m short of contact — more than the 17 m it needs to stop.
"""

from __future__ import annotations

from aebrisk.artifacts.results import AEBScenarioResultV1
from aebrisk.simulation.common_cohort import ExperimentConfiguration, common_valid_scenarios
from aebrisk.simulation.runner import run_common_scenario
from aebrisk.simulation.synthetic import TOKEN, SyntheticLeadScenario


def no_aeb() -> ExperimentConfiguration:
    return ExperimentConfiguration(
        configuration_id="no_aeb",
        aeb_enabled=False,
        observation_mode="oracle",
        severity_by_channel=dict.fromkeys(
            ("dropout", "localization_shape", "latency", "track_instability"), "zero"
        ),
        replicate_count=1,
    )


def oracle_aeb() -> ExperimentConfiguration:
    return ExperimentConfiguration(
        configuration_id="oracle_aeb",
        aeb_enabled=True,
        observation_mode="oracle",
        severity_by_channel=dict.fromkeys(
            ("dropout", "localization_shape", "latency", "track_instability"), "zero"
        ),
        replicate_count=1,
    )


def corrupted_zero() -> ExperimentConfiguration:
    return ExperimentConfiguration(
        configuration_id="corrupted_zero",
        aeb_enabled=True,
        observation_mode="corrupted",
        severity_by_channel=dict.fromkeys(
            ("dropout", "localization_shape", "latency", "track_instability"), "zero"
        ),
        replicate_count=1,
    )


def run(*configurations: ExperimentConfiguration) -> dict[str, AEBScenarioResultV1]:
    """Run the token, always including the oracle every other cell is measured against.

    The runner refuses a matrix without that reference, because a missed
    intervention is defined by the comparison and by nothing else.
    """

    named = {configuration.configuration_id for configuration in configurations}
    if "oracle_aeb" not in named:
        configurations = (oracle_aeb(), *configurations)
    results, invalid = run_common_scenario(
        SyntheticLeadScenario(), configurations, protocol=object()
    )
    assert invalid is None, invalid
    return {record.configuration_id: record for record in results}


def test_without_the_aeb_the_ego_hits_the_lead_vehicle() -> None:
    """The scenario has to be dangerous, or avoiding it would prove nothing."""

    outcome = run(no_aeb())["no_aeb"]

    assert outcome.collision_vehicle == 1
    assert outcome.collision_energy > 0.0


def test_with_the_aeb_the_ego_stops_short() -> None:
    """The payoff: the same scenario, the same controller, one thing different."""

    outcome = run(oracle_aeb())["oracle_aeb"]

    assert outcome.collision_vehicle == 0
    assert outcome.min_clearance_m > 0.0


def test_the_aeb_intervention_is_recorded() -> None:
    """An avoidance with no intervention would mean the geometry, not the AEB, saved it."""

    outcome = run(oracle_aeb())["oracle_aeb"]

    assert outcome.intervention_duration_s > 0.0
    assert outcome.max_deceleration_mps2 > 0.0


def test_the_braking_respects_the_actuator_envelope() -> None:
    """The limiter is in the loop, not bypassed by the state machine's target."""

    outcome = run(oracle_aeb())["oracle_aeb"]

    assert outcome.max_deceleration_mps2 <= 6.0
    assert outcome.max_abs_jerk_mps3 <= 5.0 + 1e-9


def test_the_all_zero_pipeline_avoids_the_collision_like_the_oracle() -> None:
    """Severity zero corrupts nothing, so the outcome must still be an avoidance.

    It is NOT asserted to equal the oracle in every measure. At severity zero
    the tracking channel still derives velocity by differencing observed
    positions, because differencing is how a real tracker obtains velocity: it
    is the estimator, not an error. The gap between `oracle_aeb` and the
    all-zero configuration is therefore a measurable quantity rather than a
    bug, and it is exactly why the experiment matrix carries both and why the
    Shapley baseline is `coalition-none` rather than the oracle.

    An earlier version of this test asserted the two were identical. That
    assertion passed only because `ChannelStages` was never bound to the
    channels, so the pipeline did nothing at all: it was vacuous, and the
    property it claimed is not one the design guarantees.
    """

    outcomes = run(oracle_aeb(), corrupted_zero())

    reference = outcomes["oracle_aeb"]
    through_pipeline = outcomes["corrupted_zero"]

    assert through_pipeline.collision_vehicle == 0 == reference.collision_vehicle
    assert through_pipeline.min_clearance_m > 0.0
    assert through_pipeline.intervention_duration_s > 0.0


def test_a_real_severity_reaches_the_outcome() -> None:
    """The end-to-end proof that the channels are connected to the controller.

    With every channel at high severity the observation the AEB acts on is not
    the world, so its behaviour must differ from the oracle's somewhere. If it
    did not, the whole pipeline would be decoration.
    """

    corrupted = ExperimentConfiguration(
        configuration_id="all-high",
        aeb_enabled=True,
        observation_mode="corrupted",
        severity_by_channel=dict.fromkeys(
            ("dropout", "localization_shape", "latency", "track_instability"), "high"
        ),
        replicate_count=1,
    )
    outcomes = run(oracle_aeb(), corrupted)

    reference = outcomes["oracle_aeb"]
    degraded = outcomes["all-high"]

    assert (
        degraded.collision_vehicle,
        round(degraded.min_clearance_m, 6),
        round(degraded.intervention_duration_s, 6),
    ) != (
        reference.collision_vehicle,
        round(reference.min_clearance_m, 6),
        round(reference.intervention_duration_s, 6),
    )


def test_the_configurations_share_a_cohort() -> None:
    """All three finish, so the token is comparable across all three."""

    results, _ = run_common_scenario(
        SyntheticLeadScenario(),
        (no_aeb(), oracle_aeb(), corrupted_zero()),
        protocol=object(),
    )
    by_config: dict[str, tuple[AEBScenarioResultV1, ...]] = {}
    for record in results:
        by_config.setdefault(record.configuration_id, ())
        by_config[record.configuration_id] += (record,)

    assert common_valid_scenarios(by_config) == (TOKEN,)


def test_the_run_is_deterministic() -> None:
    """Two runs of the same cell must agree, or nothing downstream means anything."""

    first = run(corrupted_zero())["corrupted_zero"]
    second = run(corrupted_zero())["corrupted_zero"]

    assert first == second
