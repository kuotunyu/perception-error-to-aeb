"""Evidence-derived SVGs keep units, denominators, and source values explicit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _shapley() -> dict:
    metric: dict[str, Any] = {
        "values": {
            "dropout": -0.125,
            "localization_shape": 0.25,
            "latency": 0.0,
            "track_instability": 0.0625,
        },
        "efficiency_max_abs_residual": 2.2e-16,
        "scenarios_attributed": 4,
    }
    return {
        "schema_version": "aeb-shapley/v1",
        "protocol_sha256": "a" * 64,
        "cohort_manifest_sha256": "b" * 64,
        "cohort_size": 4,
        "common_valid_tokens": 4,
        "metrics": {
            "collision_indicator": metric,
            "intervention_duration_s": {
                **metric,
                "values": {**metric["values"], "localization_shape": 4.5},
            },
        },
    }


def _families() -> dict:
    rows = []
    for family in (
        "lead_or_stopping",
        "cut_in_or_crossing",
        "pedestrian_or_crosswalk",
        "bicycle_or_vru",
    ):
        for configuration in (
            "oracle_aeb",
            "coalition-none",
            "coalition-dropout+localization_shape+latency+track_instability",
        ):
            rows.append(
                {
                    "family": family,
                    "configuration_id": configuration,
                    "valid_tokens": 1,
                    "replicate_count": 3,
                    "scenario_replicates": 3,
                    "missed_interventions": 1,
                    "false_interventions": 2,
                    "missed_per_1000_scenario_replicates": 333.3333333333333,
                    "false_per_1000_scenario_replicates": 666.6666666666666,
                }
            )
    return {
        "schema_version": "aeb-family-interventions/v1",
        "protocol_sha256": "a" * 64,
        "cohort_manifest_sha256": "b" * 64,
        "common_valid_tokens": 4,
        "evaluation_per_family": 100,
        "rows": rows,
    }


def test_shapley_svg_separates_incompatible_units_and_preserves_values() -> None:
    from aebrisk.report.svg import shapley_svg

    svg = shapley_svg(_shapley())

    assert svg.startswith("<svg")
    assert "Collision indicator contribution" in svg
    assert "Intervention duration contribution (s)" in svg
    assert "-0.125" in svg
    assert "4.5" in svg
    assert "full minus empty coalition-none" in svg
    assert "not a confidence interval" in svg


def test_family_svg_states_event_rate_denominator_without_error_bars() -> None:
    from aebrisk.report.svg import intervention_rates_svg

    svg = intervention_rates_svg(_families())

    assert "events per 1,000 scenario-replicates" in svg
    assert "333.3333333333333" in svg
    assert "3 scenario-replicates" in svg
    assert "confidence interval" not in svg.lower()
    assert "error bar" not in svg.lower()


def test_svg_generation_is_byte_stable_and_escapes_labels() -> None:
    from aebrisk.report.svg import intervention_rates_svg, shapley_svg

    shapley = _shapley()
    shapley["metrics"]["collision_indicator"]["values"]["dropout&x"] = shapley["metrics"][
        "collision_indicator"
    ]["values"].pop("dropout")
    first = shapley_svg(shapley)
    assert first == shapley_svg(shapley)
    assert "dropout&amp;x" in first
    assert intervention_rates_svg(_families()) == intervention_rates_svg(_families())


def test_write_figures_reads_strict_evidence_and_writes_fixed_names(tmp_path: Path) -> None:
    from aebrisk.report.svg import write_figures

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "shapley.json").write_text(json.dumps(_shapley()), encoding="utf-8")
    (evidence / "family-interventions.json").write_text(json.dumps(_families()), encoding="utf-8")

    written = write_figures(evidence, tmp_path / "figures")

    assert [path.name for path in written] == [
        "shapley-contributions.svg",
        "intervention-rates-by-family.svg",
    ]
    assert all(path.read_bytes().endswith(b"</svg>\n") for path in written)
