"""Per-family intervention evidence stays on the validated common cohort."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from aebrisk.analysis.aggregate import FormalResults
from aebrisk.artifacts.results import AEBScenarioResultV2, ScenarioFamily
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import CohortManifestV1, membership_sha256
from aebrisk.simulation.orchestrate import TokenResultsV1


def _manifest() -> CohortManifestV1:
    return CohortManifestV1(
        schema_version="aeb-cohort-manifest/v1",
        split="evaluation",
        protocol_sha256="a" * 64,
        families={
            "lead_or_stopping": ("lead",),
            "cut_in_or_crossing": ("cut",),
            "pedestrian_or_crosswalk": ("ped",),
            "bicycle_or_vru": ("bike",),
        },
        log_names=("log.db",),
    )


def _loaded() -> tuple[FormalResults, CohortManifestV1]:
    manifest = _manifest()
    family_by_token = {
        token: family for family, tokens in manifest.families.items() for token in tokens
    }
    cohort_hash = membership_sha256(manifest)
    loaded = FormalResults(marker_tokens=tuple(family_by_token), cohort_manifest_sha256=cohort_hash)
    for configuration in formal_configurations():
        loaded[configuration.configuration_id] = {}
        for token, family in family_by_token.items():
            records = tuple(
                AEBScenarioResultV2(
                    schema_version="aeb-scenario-result/v2",
                    scenario_token=token,
                    family=cast(ScenarioFamily, family),
                    configuration_id=configuration.configuration_id,
                    replicate=replicate,
                    valid=True,
                    collision_vru=0,
                    collision_vehicle=0,
                    collision_object=0,
                    collision_energy=0.0,
                    contacts_not_at_fault=0,
                    min_ttc_s=None,
                    min_clearance_m=4.0,
                    missed_interventions=(
                        1 if configuration.configuration_id == "coalition-none" else 0
                    ),
                    false_interventions=(
                        2 if configuration.configuration_id == "oracle_aeb" else 0
                    ),
                    max_deceleration_mps2=2.0,
                    max_abs_jerk_mps3=3.0,
                    intervention_duration_s=1.0,
                    simulated_duration_s=2.0,
                )
                for replicate in range(configuration.replicate_count)
            )
            loaded[configuration.configuration_id][token] = TokenResultsV1(
                schema_version="aeb-token-results/v1",
                scenario_token=token,
                family=cast(ScenarioFamily, family),
                split="evaluation",
                configuration_id=configuration.configuration_id,
                protocol_sha256="a" * 64,
                cohort_manifest_sha256=cohort_hash,
                valid=True,
                results=records,
            )
    return loaded, manifest


def _protocol() -> dict[str, Any]:
    return {"cohort": {"evaluation_per_family": 100}}


def test_summary_uses_one_validated_common_cohort_and_event_denominator() -> None:
    from aebrisk.analysis.family_interventions import family_interventions

    loaded, manifest = _loaded()

    summary = family_interventions(loaded, manifest, _protocol())

    assert summary.schema_version == "aeb-family-interventions/v1"
    assert summary.protocol_sha256 == "a" * 64
    assert summary.cohort_manifest_sha256 == membership_sha256(manifest)
    assert summary.common_valid_tokens == 4
    assert summary.evaluation_per_family == 100
    assert len(summary.rows) == 12
    none_lead = next(
        row
        for row in summary.rows
        if row.family == "lead_or_stopping" and row.configuration_id == "coalition-none"
    )
    assert none_lead.valid_tokens == 1
    assert none_lead.replicate_count == 3
    assert none_lead.scenario_replicates == 3
    assert none_lead.missed_interventions == 3
    assert none_lead.missed_per_1000_scenario_replicates == 1000.0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda loaded, manifest: loaded.pop("no_aeb"), "missing configuration"),
        (
            lambda loaded, manifest: loaded["oracle_aeb"].__setitem__(
                "lead",
                loaded["oracle_aeb"]["lead"].model_copy(update={"family": "cut_in_or_crossing"}),
            ),
            "family",
        ),
        (
            lambda loaded, manifest: loaded["oracle_aeb"].__setitem__(
                "lead",
                loaded["oracle_aeb"]["lead"].model_copy(
                    update={"results": loaded["oracle_aeb"]["lead"].results[:2]}
                ),
            ),
            "replicates",
        ),
        (
            lambda loaded, manifest: loaded.marker_tokens.__add__(("other",)),
            "different cohort",
        ),
    ],
)
def test_summary_refuses_formal_set_drift(mutate, message: str) -> None:
    from aebrisk.analysis.family_interventions import family_interventions

    loaded, manifest = _loaded()
    if message == "different cohort":
        loaded.marker_tokens = (*loaded.marker_tokens, "other")
    else:
        mutate(loaded, manifest)

    with pytest.raises(ValueError, match=message):
        family_interventions(loaded, manifest, _protocol())


def test_summary_rejects_a_non_positive_protocol_target() -> None:
    from aebrisk.analysis.family_interventions import family_interventions

    loaded, manifest = _loaded()
    with pytest.raises(ValueError, match="evaluation_per_family"):
        family_interventions(loaded, manifest, {"cohort": {"evaluation_per_family": 0}})


def test_family_document_rejects_duplicate_missing_or_inconsistent_rows() -> None:
    from aebrisk.artifacts.family_interventions import FamilyInterventionsV1

    loaded, manifest = _loaded()
    from aebrisk.analysis.family_interventions import family_interventions

    payload = family_interventions(loaded, manifest, _protocol()).model_dump(mode="json")
    payload["rows"].append(payload["rows"][0])
    with pytest.raises(ValidationError, match="duplicate"):
        FamilyInterventionsV1.model_validate(payload)

    payload = family_interventions(loaded, manifest, _protocol()).model_dump(mode="json")
    payload["rows"].pop()
    with pytest.raises(ValidationError, match="exactly"):
        FamilyInterventionsV1.model_validate(payload)

    payload = family_interventions(loaded, manifest, _protocol()).model_dump(mode="json")
    payload["rows"][0]["scenario_replicates"] += 1
    with pytest.raises(ValidationError, match="scenario_replicates"):
        FamilyInterventionsV1.model_validate(payload)

    payload = family_interventions(loaded, manifest, _protocol()).model_dump(mode="json")
    payload["rows"][0]["false_per_1000_scenario_replicates"] = 1.0
    with pytest.raises(ValidationError, match="rate"):
        FamilyInterventionsV1.model_validate(payload)


def test_empty_family_has_null_rates_and_zero_denominator() -> None:
    from aebrisk.artifacts.family_interventions import FamilyInterventionRowV1

    row = FamilyInterventionRowV1(
        family="bicycle_or_vru",
        configuration_id="oracle_aeb",
        valid_tokens=0,
        replicate_count=3,
        scenario_replicates=0,
        missed_interventions=0,
        false_interventions=0,
        missed_per_1000_scenario_replicates=None,
        false_per_1000_scenario_replicates=None,
    )
    assert row.scenario_replicates == 0

    with pytest.raises(ValidationError, match="empty family requires zero events and null rates"):
        FamilyInterventionRowV1.model_validate(
            row.model_copy(update={"missed_interventions": 1}).model_dump(mode="json")
        )


def test_family_document_rejects_family_count_drift_and_wrong_global_sum() -> None:
    from aebrisk.analysis.family_interventions import family_interventions
    from aebrisk.artifacts.family_interventions import FamilyInterventionsV1

    loaded, manifest = _loaded()
    payload = family_interventions(loaded, manifest, _protocol()).model_dump(mode="json")
    payload["rows"][0]["valid_tokens"] += 1
    payload["rows"][0]["scenario_replicates"] += 3
    denominator = payload["rows"][0]["scenario_replicates"]
    payload["rows"][0]["missed_per_1000_scenario_replicates"] = (
        1000.0 * payload["rows"][0]["missed_interventions"] / denominator
    )
    payload["rows"][0]["false_per_1000_scenario_replicates"] = (
        1000.0 * payload["rows"][0]["false_interventions"] / denominator
    )
    with pytest.raises(ValidationError, match="constant across a family"):
        FamilyInterventionsV1.model_validate(payload)

    payload = family_interventions(loaded, manifest, _protocol()).model_dump(mode="json")
    payload["common_valid_tokens"] += 1
    with pytest.raises(ValidationError, match="sum to common_valid_tokens"):
        FamilyInterventionsV1.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("missed_interventions", -1),
        ("missed_per_1000_scenario_replicates", float("inf")),
    ],
)
def test_family_row_rejects_negative_counts_and_non_finite_rates(field: str, value: object) -> None:
    from aebrisk.artifacts.family_interventions import FamilyInterventionRowV1

    payload = {
        "family": "lead_or_stopping",
        "configuration_id": "oracle_aeb",
        "valid_tokens": 1,
        "replicate_count": 3,
        "scenario_replicates": 3,
        "missed_interventions": 0,
        "false_interventions": 0,
        "missed_per_1000_scenario_replicates": 0.0,
        "false_per_1000_scenario_replicates": 0.0,
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        FamilyInterventionRowV1.model_validate(payload)
