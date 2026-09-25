"""The collisions-vs-braking figure: one derived comparison, drawn from evaluation.json."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import pytest

FULL = "coalition-dropout+localization_shape+latency+track_instability"


def row(configuration_id: str, group: str, collisions: int, duration: float) -> dict[str, Any]:
    return {
        "configuration_id": configuration_id,
        "group": group,
        "scenarios": 40,
        "simulated_seconds": 400.0,
        "collisions": collisions,
        "collisions_vru": 0,
        "collisions_vehicle": collisions,
        "collisions_object": 0,
        "contacts_not_at_fault": 0,
        "collision_energy_total": 0.0,
        "missed_interventions": 0,
        "false_interventions": 0,
        "mean_intervention_duration_s": duration,
        "max_deceleration_mps2": None,
        "max_abs_jerk_mps3": None,
        "min_ttc_s": None,
        "min_clearance_m": None,
        "collisions_per_1000_scenarios": collisions * 25.0,
        "collisions_per_hour": None,
        "collisions_per_100km": None,
    }


def evaluation() -> dict[str, Any]:
    rows = [
        row("no_aeb", "baseline", 8, 0.0),
        row("oracle_aeb", "baseline", 2, 6.0),
        row("coalition-none", "coalition", 1, 6.5),
        row("latency-medium", "single_channel", 1, 6.4),
        row("localization_shape-medium", "single_channel", 0, 9.0),
        row(FULL, "coalition", 0, 8.8),
    ]
    return {
        "schema_version": "aeb-evaluation/v1",
        "protocol_sha256": "a" * 64,
        "cohort_manifest_sha256": "b" * 64,
        "cohort_size": 40,
        "common_valid_tokens": 40,
        "simulated_seconds": 400.0 * len(rows),
        "configurations": rows,
    }


def points(svg: str) -> dict[str, dict[str, str]]:
    return {
        node.attrib["data-configuration"]: node.attrib
        for node in ET.fromstring(svg).iter()
        if "data-configuration" in node.attrib
    }


def test_every_configuration_is_one_point_with_its_derived_inputs() -> None:
    from aebrisk.report.svg import collisions_vs_braking_svg

    svg = collisions_vs_braking_svg(evaluation())
    plotted = points(svg)

    assert sorted(plotted) == sorted(
        row["configuration_id"] for row in evaluation()["configurations"]
    )
    # Braking share is mean duration x scenario-replicates / measured exposure.
    assert plotted["oracle_aeb"]["data-braking-share"] == str(6.0 * 40 / 400.0)
    assert plotted["no_aeb"]["data-braking-share"] == "0.0"
    assert plotted["no_aeb"]["data-collisions-per-1000"] == str(8 * 1000.0 / 40)
    assert plotted[FULL]["data-collisions-per-1000"] == "0.0"
    assert {name: attributes["data-kind"] for name, attributes in plotted.items()} == {
        "no_aeb": "reference",
        "oracle_aeb": "reference",
        "coalition-none": "other",
        "latency-medium": "other",
        "localization_shape-medium": "localization_shape",
        FULL: "localization_shape",
    }


def test_the_figure_names_its_references_and_its_limits() -> None:
    from aebrisk.report.svg import collisions_vs_braking_svg

    svg = collisions_vs_braking_svg(evaluation())

    for label in (
        "no AEB",
        "oracle AEB",
        "coalition-none",
        "all four channels, medium",
        "Counted collisions per 1,000 scenario-replicates",
        "Share of measured exposure spent in partial or full braking",
        "includes localization_shape",
    ):
        assert label in svg
    for limit in ("Descriptive", "not independent", "no intervals", "no channel ranking"):
        assert limit in svg
    # The y axis ends at the next multiple of 50 above the largest rate, 200.
    assert ">200<" in svg
    assert ">250<" not in svg


def test_the_figure_does_not_depend_on_row_order_and_is_byte_stable() -> None:
    from aebrisk.report.svg import collisions_vs_braking_svg

    data = evaluation()
    first = collisions_vs_braking_svg(data)
    data["configurations"].reverse()

    assert collisions_vs_braking_svg(data) == first
    assert first.endswith("</svg>\n")


@pytest.mark.parametrize(
    "change",
    [{"scenarios": 0}, {"simulated_seconds": 0.0}, {"mean_intervention_duration_s": None}],
)
def test_a_configuration_without_braking_inputs_is_refused(change: dict[str, Any]) -> None:
    from aebrisk.report.svg import collisions_vs_braking_svg

    data = evaluation()
    data["configurations"][2].update(change)
    data["simulated_seconds"] = sum(item["simulated_seconds"] for item in data["configurations"])

    with pytest.raises(ValueError, match="braking share cannot be derived"):
        collisions_vs_braking_svg(data)
