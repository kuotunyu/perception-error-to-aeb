"""Severity plots preserve the fixed sweep and configuration-specific exposure."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import pytest


def evaluation() -> dict[str, Any]:
    rows = []
    for channel in ("dropout", "localization_shape", "latency", "track_instability"):
        for level in ("low", "medium", "high"):
            rows.append(
                {
                    "configuration_id": f"{channel}-{level}",
                    "group": "single_channel",
                    "scenarios": 2,
                    "simulated_seconds": 3600.0,
                    "collisions": 1,
                    "collisions_vru": 0,
                    "collisions_vehicle": 1,
                    "collisions_object": 0,
                    "contacts_not_at_fault": 0,
                    "collision_energy_total": 0.0,
                    "missed_interventions": 3,
                    "false_interventions": 4,
                    "mean_intervention_duration_s": 1.25,
                    "max_deceleration_mps2": None,
                    "max_abs_jerk_mps3": None,
                    "min_ttc_s": None,
                    "min_clearance_m": None,
                    "collisions_per_1000_scenarios": 500.0,
                    "collisions_per_hour": 1.0,
                    "collisions_per_100km": None,
                }
            )
    return {
        "schema_version": "aeb-evaluation/v1",
        "protocol_sha256": "a" * 64,
        "cohort_manifest_sha256": "b" * 64,
        "cohort_size": 2,
        "common_valid_tokens": 2,
        "simulated_seconds": 43200.0,
        "configurations": rows,
    }


def test_severity_plot_uses_own_exposure_and_is_order_independent() -> None:
    from aebrisk.report.svg import severity_svg

    data = evaluation()
    data["configurations"][1]["simulated_seconds"] = 7200.0
    data["configurations"][1]["collisions_per_hour"] = 0.5
    data["simulated_seconds"] += 3600.0
    first = severity_svg(data)
    data["configurations"].reverse()
    assert severity_svg(data) == first
    svg = ET.fromstring(first)
    points = {
        (node.attrib["data-metric"], node.attrib["data-configuration"]): node.attrib["data-value"]
        for node in svg.iter()
        if "data-value" in node.attrib
    }
    assert points[("collisions_per_hour", "dropout-medium")] == "0.5"
    assert points[("false_per_1000_replicates", "dropout-medium")] == "2000.0"
    assert points[("missed_per_1000_replicates", "dropout-low")] == "1500.0"
    assert points[("mean_intervention_duration_s", "dropout-high")] == "1.25"
    assert len(points) == 48
    assert "not a confidence interval" in first


@pytest.mark.parametrize("problem", ["missing", "duplicate", "wrong_group"])
def test_incomplete_or_ambiguous_sweep_is_refused(problem: str) -> None:
    from aebrisk.report.svg import severity_svg

    data = evaluation()
    if problem == "missing":
        data["configurations"].pop()
        data["simulated_seconds"] -= 3600.0
    elif problem == "duplicate":
        data["configurations"].append(dict(data["configurations"][0]))
        data["simulated_seconds"] += 3600.0
    else:
        data["configurations"][0]["group"] = "baseline"
    with pytest.raises(ValueError, match="severity sweep"):
        severity_svg(data)


def test_nulls_remain_unavailable_and_true_zero_is_plotted() -> None:
    from aebrisk.report.svg import severity_svg

    data = evaluation()
    row = data["configurations"][0]
    row.update(scenarios=0, simulated_seconds=0.0, mean_intervention_duration_s=None)
    data["simulated_seconds"] -= 3600.0
    data["configurations"][1]["mean_intervention_duration_s"] = 0.0
    svg = ET.fromstring(severity_svg(data))
    points = [node.attrib for node in svg.iter() if "data-value" in node.attrib]
    low = [point for point in points if point["data-configuration"] == "dropout-low"]
    assert all(point["data-value"] == "unavailable" for point in low)
    assert any(
        point["data-configuration"] == "dropout-medium"
        and point["data-metric"] == "mean_intervention_duration_s"
        and point["data-value"] == "0.0"
        for point in points
    )
