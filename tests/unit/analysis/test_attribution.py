"""Exact attribution averages per-token games after checking efficiency."""

from __future__ import annotations

import pytest

from aebrisk.analysis.aggregate import FormalResults
from aebrisk.analysis.attribution import attribution_by_metric
from aebrisk.artifacts.results import AEBScenarioResultV2
from aebrisk.attribution.factorial import coalition_configurations
from aebrisk.simulation.orchestrate import TokenResultsV1


def _record(configuration: str, replicate: int, coalition: frozenset[str]):
    return AEBScenarioResultV2(
        schema_version="aeb-scenario-result/v2",
        scenario_token="token",
        family="lead_or_stopping",
        configuration_id=configuration,
        replicate=replicate,
        valid=True,
        collision_vru=int("dropout" in coalition),
        collision_vehicle=0,
        collision_object=0,
        collision_energy=0.0,
        contacts_not_at_fault=0,
        min_ttc_s=None,
        min_clearance_m=1.0,
        missed_interventions=0,
        false_interventions=0,
        max_deceleration_mps2=0.0,
        max_abs_jerk_mps3=0.0,
        intervention_duration_s=sum(
            {"dropout": 1.0, "localization_shape": 2.0, "latency": 3.0, "track_instability": 4.0}[
                channel
            ]
            for channel in coalition
        ),
        simulated_duration_s=1.0,
    )


def coalition_results() -> FormalResults:
    loaded = FormalResults(marker_tokens=("token",), cohort_manifest_sha256="b" * 64)
    for coalition, configuration in coalition_configurations().items():
        loaded[configuration] = {
            "token": TokenResultsV1(
                schema_version="aeb-token-results/v1",
                scenario_token="token",
                family="lead_or_stopping",
                split="evaluation",
                configuration_id=configuration,
                protocol_sha256="a" * 64,
                cohort_manifest_sha256="b" * 64,
                valid=True,
                results=tuple(_record(configuration, index, coalition) for index in range(3)),
            )
        }
    return loaded


def test_attribution_uses_one_complete_replicate_mean_game_per_token() -> None:
    attributed = attribution_by_metric(coalition_results(), ("token",))

    collisions = attributed["collision_indicator"]
    assert collisions["values"]["dropout"] == pytest.approx(1.0)
    assert collisions["values"]["latency"] == pytest.approx(0.0)
    durations = attributed["intervention_duration_s"]
    assert durations["values"] == pytest.approx(
        {"dropout": 1.0, "localization_shape": 2.0, "latency": 3.0, "track_instability": 4.0}
    )
    assert durations["efficiency_max_abs_residual"] <= 1e-9
    assert durations["scenarios_attributed"] == 1


def test_attribution_refuses_a_missing_coalition() -> None:
    loaded = coalition_results()
    del loaded["latency-medium"]

    with pytest.raises(ValueError, match=r"^missing coalition configuration 'latency-medium'$"):
        attribution_by_metric(loaded, ("token",))


def test_attribution_refuses_an_empty_cohort() -> None:
    with pytest.raises(ValueError, match=r"^there are no common scenarios to attribute$"):
        attribution_by_metric(coalition_results(), ())


def test_attribution_refuses_an_efficiency_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aebrisk.analysis.attribution.shapley_by_metric",
        lambda games: {
            metric: {
                "dropout": 2.0,
                "localization_shape": 0.0,
                "latency": 0.0,
                "track_instability": 0.0,
            }
            for metric in games
        },
    )

    with pytest.raises(ValueError, match=r"^Shapley efficiency residual "):
        attribution_by_metric(coalition_results(), ("token",))
