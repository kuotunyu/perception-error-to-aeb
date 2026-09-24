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


def test_shapley_svg_prints_three_significant_figures_and_keeps_exact_values() -> None:
    """The label is short; the exact contribution stays in a data attribute."""

    import xml.etree.ElementTree as ET

    from aebrisk.report.svg import shapley_svg

    shapley = _shapley()
    duration = shapley["metrics"]["intervention_duration_s"]
    duration["values"]["localization_shape"] = 4.0670219638242875
    duration["efficiency_max_abs_residual"] = 2.6645352591003757e-15

    svg = shapley_svg(shapley)
    printed = [node.text for node in ET.fromstring(svg).iter() if node.text]
    exact = {
        node.attrib["data-channel"]: node.attrib["data-value"]
        for node in ET.fromstring(svg).iter()
        if "data-channel" in node.attrib
    }

    assert "4.07" in printed
    assert "4.0670219638242875" not in printed
    assert "0.0625" in printed
    assert "0.00" in printed
    assert exact["localization_shape"] == "4.0670219638242875"
    assert "efficiency residual = 2.7e-15 (arithmetic check, not a confidence interval)" in svg


def test_family_svg_prints_rates_to_one_decimal_and_keeps_exact_values() -> None:
    import xml.etree.ElementTree as ET

    from aebrisk.report.svg import intervention_rates_svg

    svg = intervention_rates_svg(_families())
    printed = [node.text for node in ET.fromstring(svg).iter() if node.text]
    rows = [node.attrib for node in ET.fromstring(svg).iter() if "data-family" in node.attrib]

    assert "missed 333.3" in printed
    assert "false 666.7" in printed
    assert all("333.3333333333333" not in text for text in printed)
    assert rows[0]["data-missed-per-1000"] == "333.3333333333333"
    assert rows[0]["data-false-per-1000"] == "666.6666666666666"


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

    from .test_severity_svg import evaluation

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "shapley.json").write_text(json.dumps(_shapley()), encoding="utf-8")
    (evidence / "family-interventions.json").write_text(json.dumps(_families()), encoding="utf-8")
    measured = evaluation()
    measured.update(cohort_size=4, common_valid_tokens=4)
    (evidence / "evaluation.json").write_text(json.dumps(measured), encoding="utf-8")

    written = write_figures(evidence, tmp_path / "figures")

    assert [path.name for path in written] == [
        "collisions-vs-braking.svg",
        "shapley-contributions.svg",
        "intervention-rates-by-family.svg",
        "error-severity-sensitivity.svg",
    ]
    assert all(path.read_bytes().endswith(b"</svg>\n") for path in written)


def test_figures_refuse_mixed_cohort_before_writing(tmp_path: Path) -> None:
    import pytest

    from aebrisk.report.svg import write_figures

    from .test_severity_svg import evaluation

    (tmp_path / "shapley.json").write_text(json.dumps(_shapley()), encoding="utf-8")
    (tmp_path / "family-interventions.json").write_text(json.dumps(_families()), encoding="utf-8")
    (tmp_path / "evaluation.json").write_text(json.dumps(evaluation()), encoding="utf-8")
    with pytest.raises(ValueError, match="figure evidence identity"):
        write_figures(tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
