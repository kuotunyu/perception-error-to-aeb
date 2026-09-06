"""Published evidence has strict models and one deterministic byte format."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aebrisk.artifacts import documents


def evaluation_payload() -> dict:
    return {
        "schema_version": "aeb-evaluation/v1",
        "protocol_sha256": "a" * 64,
        "cohort_manifest_sha256": "b" * 64,
        "cohort_size": 2,
        "common_valid_tokens": 2,
        "simulated_seconds": 18.0,
        "configurations": [
            {
                "configuration_id": "no_aeb",
                "group": "baseline",
                "scenarios": 6,
                "simulated_seconds": 18.0,
                "collisions": 1,
                "collisions_vru": 1,
                "collisions_vehicle": 0,
                "collisions_object": 0,
                "contacts_not_at_fault": 0,
                "collision_energy_total": 2.0,
                "missed_interventions": 0,
                "false_interventions": 0,
                "mean_intervention_duration_s": 0.0,
                "max_deceleration_mps2": 2.0,
                "max_abs_jerk_mps3": 3.0,
                "min_ttc_s": 1.0,
                "min_clearance_m": 0.0,
                "collisions_per_1000_scenarios": 1000.0 / 6.0,
                "collisions_per_hour": 200.0,
                "collisions_per_100km": None,
            }
        ],
    }


def test_every_published_version_has_one_registered_model() -> None:
    assert documents.SCHEMA_VERSIONS == (
        "aeb-evaluation/v1",
        "aeb-intervals/v1",
        "aeb-shapley/v1",
        "aeb-exclusions/v1",
    )
    assert tuple(documents.DOCUMENT_MODELS) == documents.SCHEMA_VERSIONS


def test_evaluation_contract_rejects_unknown_fields_and_non_integer_counts() -> None:
    payload = evaluation_payload()
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        documents.AEBEvaluationV1.model_validate(payload)

    payload = evaluation_payload()
    payload["cohort_size"] = 2.0
    with pytest.raises(ValidationError):
        documents.AEBEvaluationV1.model_validate(payload)


def test_interval_contract_rejects_an_estimate_outside_its_bounds() -> None:
    with pytest.raises(ValidationError, match="estimate"):
        documents.BootstrapIntervalV1(
            estimate=2.0, low=0.0, high=1.0, confidence=0.95, resamples=5000, seed=20260831
        )


def test_write_document_validates_and_writes_stable_lf_bytes(tmp_path: Path) -> None:
    model = documents.AEBEvaluationV1.model_validate(evaluation_payload())
    path = tmp_path / "nested" / "evaluation.json"

    documents.write_document(model, path)

    payload = path.read_bytes()
    assert payload.endswith(b"}\n")
    assert b"\r\n" not in payload
    parsed = json.loads(payload)
    assert list(parsed) == sorted(parsed)
    assert documents.AEBEvaluationV1.model_validate(parsed) == model


def test_write_document_refuses_an_unregistered_model(tmp_path: Path) -> None:
    from aebrisk.artifacts.run_record import RunRecordV1

    model = RunRecordV1.model_construct(schema_version="aeb-run-record/v1")
    with pytest.raises(ValueError, match=r"^unsupported published document model "):
        documents.write_document(model, tmp_path / "wrong.json")


def test_all_document_shapes_validate() -> None:
    common = {
        "protocol_sha256": "a" * 64,
        "cohort_manifest_sha256": "b" * 64,
        "cohort_size": 2,
        "common_valid_tokens": 2,
    }
    interval = {
        "estimate": 0.5,
        "low": 0.0,
        "high": 1.0,
        "confidence": 0.95,
        "resamples": 5000,
        "seed": 20260831,
    }
    metrics = {
        "collision_indicator": interval,
        "intervention_duration_s": interval,
        "missed_interventions": interval,
        "false_interventions": interval,
    }
    assert documents.AEBIntervalsV1.model_validate(
        {"schema_version": "aeb-intervals/v1", **common, "intervals": {"no_aeb": metrics}}
    )
    assert documents.AEBShapleyV1.model_validate(
        {
            "schema_version": "aeb-shapley/v1",
            **common,
            "metrics": {
                "collision_indicator": {
                    "values": {
                        "dropout": 0.0,
                        "localization_shape": 0.0,
                        "latency": 0.0,
                        "track_instability": 0.0,
                    },
                    "efficiency_max_abs_residual": 0.0,
                    "scenarios_attributed": 2,
                },
                "intervention_duration_s": {
                    "values": {
                        "dropout": 0.0,
                        "localization_shape": 0.0,
                        "latency": 0.0,
                        "track_instability": 0.0,
                    },
                    "efficiency_max_abs_residual": 0.0,
                    "scenarios_attributed": 2,
                },
            },
        }
    )
    assert documents.AEBExclusionsV1.model_validate(
        {
            "schema_version": "aeb-exclusions/v1",
            **{**common, "common_valid_tokens": 1},
            "excluded": [
                {
                    "scenario_token": "t3",
                    "phase": "step",
                    "reason": "failed",
                    "exception_type": None,
                }
            ],
        }
    )


def test_interval_and_shapley_documents_require_the_fixed_metric_sets() -> None:
    common = {
        "protocol_sha256": "a" * 64,
        "cohort_manifest_sha256": "b" * 64,
        "cohort_size": 2,
        "common_valid_tokens": 2,
    }
    with pytest.raises(ValidationError, match="interval metrics"):
        documents.AEBIntervalsV1.model_validate(
            {
                "schema_version": "aeb-intervals/v1",
                **common,
                "intervals": {"no_aeb": {}},
            }
        )
    with pytest.raises(ValidationError, match="attributed metrics"):
        documents.AEBShapleyV1.model_validate(
            {"schema_version": "aeb-shapley/v1", **common, "metrics": {}}
        )


def test_shapley_document_requires_all_four_channels() -> None:
    with pytest.raises(ValidationError, match="attributed channels"):
        documents.ShapleyMetricV1(
            values={"dropout": 0.0},
            efficiency_max_abs_residual=0.0,
            scenarios_attributed=2,
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda payload: payload.__setitem__("simulated_seconds", 17.0), "sum of configuration"),
        (
            lambda payload: payload["configurations"][0].__setitem__("collisions", 2),
            "collision total",
        ),
        (lambda payload: payload.__setitem__("common_valid_tokens", 3), "common-valid count"),
    ],
)
def test_evaluation_contract_checks_cross_field_totals(change, message: str) -> None:
    payload = evaluation_payload()
    change(payload)

    with pytest.raises(ValidationError, match=message):
        documents.AEBEvaluationV1.model_validate(payload)


def test_shapley_scenario_counts_match_the_common_cohort() -> None:
    common = {
        "protocol_sha256": "a" * 64,
        "cohort_manifest_sha256": "b" * 64,
        "cohort_size": 2,
        "common_valid_tokens": 2,
    }
    metric = {
        "values": dict.fromkeys(
            ("dropout", "localization_shape", "latency", "track_instability"), 0.0
        ),
        "efficiency_max_abs_residual": 0.0,
        "scenarios_attributed": 1,
    }
    with pytest.raises(ValidationError, match="scenarios_attributed"):
        documents.AEBShapleyV1.model_validate(
            {
                "schema_version": "aeb-shapley/v1",
                **common,
                "metrics": {
                    "collision_indicator": metric,
                    "intervention_duration_s": metric,
                },
            }
        )


def test_exclusion_count_matches_the_manifest_minus_common_cohort() -> None:
    with pytest.raises(ValidationError, match="excluded token count"):
        documents.AEBExclusionsV1.model_validate(
            {
                "schema_version": "aeb-exclusions/v1",
                "protocol_sha256": "a" * 64,
                "cohort_manifest_sha256": "b" * 64,
                "cohort_size": 2,
                "common_valid_tokens": 2,
                "excluded": [
                    {
                        "scenario_token": "t3",
                        "phase": "step",
                        "reason": "failed",
                        "exception_type": None,
                    }
                ],
            }
        )
