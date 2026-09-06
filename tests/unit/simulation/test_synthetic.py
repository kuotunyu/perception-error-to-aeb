"""The licensed-data-free source is a real production cohort entry."""


def test_synthetic_cohort_builds_a_scenario_with_matching_identity() -> None:
    from aebrisk.simulation.orchestrate import BUILD_TOKEN, CohortScenario
    from aebrisk.simulation.synthetic import SyntheticLeadScenario, synthetic_cohort

    cohort = synthetic_cohort()
    assert len(cohort) == 1
    scenario = cohort[0]
    assert isinstance(scenario, CohortScenario)
    assert scenario.reference.log_file == "synthetic"
    assert scenario.family == "lead_or_stopping"
    token = BUILD_TOKEN(scenario.reference, scenario.family, "a" * 64)
    assert isinstance(token, SyntheticLeadScenario)
    assert token.token == scenario.reference.token == "synthetic-lead-0001"
    assert token.build_setup(object()).scenario_token == token.token


def test_recording_cohort_still_builds_the_nuplan_adapter() -> None:
    from aebrisk.nuplan_adapter.nuplan_scenario import NuPlanScenario
    from aebrisk.nuplan_adapter.query_scenario import ScenarioReference
    from aebrisk.simulation.orchestrate import BUILD_TOKEN

    reference = ScenarioReference("one.db", "t-lead", "stopping_with_lead", 123)
    assert isinstance(BUILD_TOKEN(reference, "lead_or_stopping", "a" * 64), NuPlanScenario)
