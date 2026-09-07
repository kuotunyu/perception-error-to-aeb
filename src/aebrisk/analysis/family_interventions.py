"""Aggregate intervention event rates by family after full formal-set validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from aebrisk.analysis.aggregate import FormalResults, common_cohort, validate_formal_set
from aebrisk.artifacts.family_interventions import (
    FAMILY_INTERVENTION_CONFIGURATION_IDS,
    FamilyInterventionRowV1,
    FamilyInterventionsV1,
)
from aebrisk.artifacts.results import SCENARIO_FAMILIES, ScenarioFamily
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import CohortManifestV1, membership_sha256


def _evaluation_target(protocol: Mapping[str, Any]) -> int:
    cohort = protocol.get("cohort")
    target = cohort.get("evaluation_per_family") if isinstance(cohort, Mapping) else None
    if isinstance(target, bool) or not isinstance(target, int) or target < 1:
        raise ValueError("protocol cohort.evaluation_per_family must be a positive integer")
    return target


def family_interventions(
    loaded: FormalResults,
    manifest: CohortManifestV1,
    protocol: Mapping[str, Any],
) -> FamilyInterventionsV1:
    """Summarize the three declared cells over one globally valid formal cohort."""

    tokens = tuple(token for family in SCENARIO_FAMILIES for token in manifest.families[family])
    configurations = formal_configurations()
    validate_formal_set(loaded, configurations, tokens, manifest=manifest)
    common = common_cohort(loaded)
    common_set = set(common)
    replicate_counts = {
        configuration.configuration_id: configuration.replicate_count
        for configuration in configurations
    }
    rows: list[FamilyInterventionRowV1] = []
    for family in SCENARIO_FAMILIES:
        family_tokens = tuple(token for token in manifest.families[family] if token in common_set)
        for configuration_id in FAMILY_INTERVENTION_CONFIGURATION_IDS:
            documents = loaded[configuration_id]
            records = tuple(
                record for token in family_tokens for record in documents[token].results
            )
            denominator = len(family_tokens) * replicate_counts[configuration_id]
            missed = sum(record.missed_interventions for record in records)
            false = sum(record.false_interventions for record in records)
            rows.append(
                FamilyInterventionRowV1(
                    family=cast(ScenarioFamily, family),
                    configuration_id=configuration_id,
                    valid_tokens=len(family_tokens),
                    replicate_count=replicate_counts[configuration_id],
                    scenario_replicates=denominator,
                    missed_interventions=missed,
                    false_interventions=false,
                    missed_per_1000_scenario_replicates=(
                        None if denominator == 0 else 1000.0 * missed / denominator
                    ),
                    false_per_1000_scenario_replicates=(
                        None if denominator == 0 else 1000.0 * false / denominator
                    ),
                )
            )
    return FamilyInterventionsV1(
        schema_version="aeb-family-interventions/v1",
        protocol_sha256=manifest.protocol_sha256,
        cohort_manifest_sha256=membership_sha256(manifest),
        common_valid_tokens=len(common),
        evaluation_per_family=_evaluation_target(protocol),
        rows=tuple(rows),
    )
