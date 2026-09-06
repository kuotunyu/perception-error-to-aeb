"""Aggregation keeps the common cohort, replicate means, and exposure explicit."""

from __future__ import annotations

from pathlib import Path

import pytest

from aebrisk.analysis.aggregate import (
    FormalResults,
    common_cohort,
    configuration_intervals,
    configuration_rows,
    exclusion_rows,
    load_formal_results,
)
from aebrisk.artifacts.results import AEBScenarioResultV2
from aebrisk.simulation.orchestrate import TokenResultsV1


def result(
    configuration: str,
    token: str,
    replicate: int,
    *,
    collision: int = 0,
    duration: float = 1.0,
    intervention: float = 0.0,
    missed: int = 0,
    false: int = 0,
) -> AEBScenarioResultV2:
    return AEBScenarioResultV2(
        schema_version="aeb-scenario-result/v2",
        scenario_token=token,
        family="lead_or_stopping",
        configuration_id=configuration,
        replicate=replicate,
        valid=True,
        collision_vru=collision,
        collision_vehicle=0,
        collision_object=0,
        collision_energy=float(collision) * 2.0,
        contacts_not_at_fault=0,
        min_ttc_s=1.5 if collision else None,
        min_clearance_m=0.0 if collision else 4.0,
        missed_interventions=missed,
        false_interventions=false,
        max_deceleration_mps2=2.0,
        max_abs_jerk_mps3=3.0,
        intervention_duration_s=intervention,
        simulated_duration_s=duration,
    )


def document(configuration: str, token: str, records) -> TokenResultsV1:
    return TokenResultsV1(
        schema_version="aeb-token-results/v1",
        scenario_token=token,
        family="lead_or_stopping",
        split="evaluation",
        configuration_id=configuration,
        protocol_sha256="a" * 64,
        cohort_manifest_sha256="b" * 64,
        valid=True,
        results=tuple(records),
    )


def loaded_two_by_two() -> FormalResults:
    loaded = FormalResults(marker_tokens=("t1", "t2"), cohort_manifest_sha256="b" * 64)
    loaded["no_aeb"] = {
        "t1": document("no_aeb", "t1", [result("no_aeb", "t1", index) for index in range(3)]),
        "t2": document(
            "no_aeb",
            "t2",
            [
                result(
                    "no_aeb",
                    "t2",
                    index,
                    collision=1,
                    duration=2.0,
                    intervention=3.0,
                    missed=2,
                    false=1,
                )
                for index in range(3)
            ],
        ),
    }
    loaded["oracle_aeb"] = {
        token: document(
            "oracle_aeb",
            token,
            [result("oracle_aeb", token, index, duration=4.0) for index in range(3)],
        )
        for token in ("t1", "t2")
    }
    return loaded


def test_loading_refuses_a_directory_without_the_completion_marker(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"has no run_complete.json$"):
        load_formal_results(tmp_path)


def test_common_cohort_uses_only_tokens_valid_in_every_configuration() -> None:
    loaded = loaded_two_by_two()
    invalid = loaded["oracle_aeb"]["t2"].model_copy(
        update={"valid": False, "invalid_reason": "failed", "invalid_phase": "step"}
    )
    loaded["oracle_aeb"]["t2"] = invalid.model_copy(update={"results": ()})

    assert common_cohort(loaded) == ("t1",)


def test_configuration_rows_sum_each_cells_measured_replicate_exposure() -> None:
    rows = configuration_rows(loaded_two_by_two(), ("t1", "t2"))

    no_aeb = rows[0]
    oracle = rows[1]
    assert no_aeb["configuration_id"] == "no_aeb"
    assert no_aeb["group"] == "baseline"
    assert no_aeb["scenarios"] == 6
    assert no_aeb["collisions"] == 3
    assert no_aeb["simulated_seconds"] == pytest.approx(9.0)
    assert no_aeb["collisions_per_hour"] == pytest.approx(1200.0)
    assert no_aeb["collisions_per_100km"] is None
    assert oracle["simulated_seconds"] == pytest.approx(24.0)


def test_bootstrap_uses_per_token_replicate_means_for_each_metric() -> None:
    computed = configuration_intervals(
        loaded_two_by_two(),
        ("t1", "t2"),
        {"t1": "lead_or_stopping", "t2": "lead_or_stopping"},
    )

    assert computed["no_aeb"]["collision_indicator"].estimate == pytest.approx(0.5)
    assert computed["no_aeb"]["intervention_duration_s"].estimate == pytest.approx(1.5)
    assert computed["no_aeb"]["missed_interventions"].estimate == pytest.approx(1.0)
    assert computed["no_aeb"]["false_interventions"].estimate == pytest.approx(0.5)
    interval = computed["oracle_aeb"]["collision_indicator"]
    assert interval.resamples == 5000
    assert interval.seed == 20260831


def test_bootstrap_refuses_a_common_token_without_three_replicates() -> None:
    loaded = loaded_two_by_two()
    loaded["no_aeb"]["t1"] = loaded["no_aeb"]["t1"].model_copy(
        update={"results": loaded["no_aeb"]["t1"].results[:2]}
    )

    with pytest.raises(ValueError, match=r"must have replicates \[0, 1, 2\]$"):
        configuration_intervals(
            loaded,
            ("t1", "t2"),
            {"t1": "lead_or_stopping", "t2": "lead_or_stopping"},
        )


def test_imported_configuration_is_grouped_and_empty_exclusions_are_empty() -> None:
    loaded = FormalResults(marker_tokens=("t1",), cohort_manifest_sha256="b" * 64)
    loaded["calibration_imported_abc"] = {
        "t1": document(
            "calibration_imported_abc",
            "t1",
            [result("calibration_imported_abc", "t1", index) for index in range(3)],
        )
    }

    assert configuration_rows(loaded, ("t1",))[0]["group"] == "imported"
    assert exclusion_rows({}, ()) == []
