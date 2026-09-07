"""Deterministic, dependency-free SVG figures derived from committed evidence."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Optional

from aebrisk.artifacts.documents import AEBShapleyV1
from aebrisk.artifacts.family_interventions import FamilyInterventionsV1

INK = "#14213d"
MUTED = "#526078"
GRID = "#d7deea"
POSITIVE = "#d97706"
NEGATIVE = "#197278"
PAPER = "#f7f9fc"


def _text(x: float, y: float, value: object, *, size: int = 13, weight: int = 400) -> str:
    escaped = html.escape(str(value), quote=True)
    return (
        f'<text x="{x}" y="{y}" font-family="system-ui, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{INK}">{escaped}</text>'
    )


def _bar(x: float, y: float, value: float, maximum: float, width: float = 250.0) -> str:
    scaled = width * abs(value) / max(maximum, 1e-12)
    left = x if value >= 0.0 else x - scaled
    color = POSITIVE if value >= 0.0 else NEGATIVE
    return f'<rect x="{left:.3f}" y="{y:.3f}" width="{scaled:.3f}" height="14" fill="{color}" />'


def shapley_svg(shapley: dict[str, Any]) -> str:
    """Render collision and duration contributions on separate labelled scales."""

    panels = (
        ("collision_indicator", "Collision indicator contribution", 84.0),
        ("intervention_duration_s", "Intervention duration contribution (s)", 386.0),
    )
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 680" role="img" '
        'aria-labelledby="title desc">',
        '<title id="title">Observed mean Shapley contributions</title>',
        '<desc id="desc">Two panels use independent scales for collision indicator and intervention duration.</desc>',
        f'<rect width="960" height="680" fill="{PAPER}" />',
        _text(44, 42, "Observed mean Shapley contributions", size=24, weight=700),
        _text(44, 66, "full minus empty coalition-none; oracle is a different baseline", size=13),
    ]
    for metric_name, label, top in panels:
        metric = shapley["metrics"][metric_name]
        values = metric["values"]
        maximum = max((abs(float(value)) for value in values.values()), default=0.0)
        parts.extend(
            [
                f'<rect x="32" y="{top}" width="896" height="268" rx="8" fill="#ffffff" stroke="{GRID}" />',
                _text(52, top + 34, label, size=18, weight=650),
                _text(700, top + 34, f"n = {metric['scenarios_attributed']} tokens", size=12),
                f'<line x1="480" y1="{top + 52}" x2="480" y2="{top + 226}" stroke="{MUTED}" />',
            ]
        )
        for index, channel in enumerate(sorted(values)):
            value = float(values[channel])
            y = top + 76 + index * 38
            parts.extend(
                [
                    _text(52, y + 12, channel),
                    _bar(480, y, value, maximum),
                    _text(754, y + 12, str(values[channel]), size=12),
                ]
            )
        residual = metric["efficiency_max_abs_residual"]
        parts.append(
            _text(
                52,
                top + 250,
                f"efficiency residual = {residual} (arithmetic check, not a confidence interval)",
                size=11,
            )
        )
    parts.append("</svg>\n")
    return "\n".join(parts)


def _rate_text(rate: Optional[float]) -> str:
    return "not estimable" if rate is None else str(rate)


def intervention_rates_svg(family_interventions: dict[str, Any]) -> str:
    """Render observed missed and false event rates with their denominators."""

    rows = family_interventions["rows"]
    rates = [
        float(rate)
        for row in rows
        for rate in (
            row["missed_per_1000_scenario_replicates"],
            row["false_per_1000_scenario_replicates"],
        )
        if rate is not None
    ]
    maximum = max(rates, default=0.0)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 790" role="img" '
        'aria-labelledby="title desc">',
        '<title id="title">Observed intervention event rates by family</title>',
        '<desc id="desc">Missed and false intervention events per 1,000 scenario-replicates; no uncertainty intervals are inferred.</desc>',
        f'<rect width="1200" height="790" fill="{PAPER}" />',
        _text(44, 42, "Observed intervention event rates by family", size=24, weight=700),
        _text(44, 68, "events per 1,000 scenario-replicates", size=13),
    ]
    for index, row in enumerate(rows):
        y = 96.0 + index * 54.0
        missed = row["missed_per_1000_scenario_replicates"]
        false = row["false_per_1000_scenario_replicates"]
        parts.extend(
            [
                _text(44, y + 13, row["family"], size=12, weight=600),
                _text(214, y + 13, row["configuration_id"], size=11),
                _bar(672, y, 0.0 if missed is None else float(missed), maximum, 150.0),
                _text(830, y + 12, f"missed {_rate_text(missed)}", size=11),
                _bar(672, y + 20, 0.0 if false is None else float(false), maximum, 150.0),
                _text(830, y + 32, f"false {_rate_text(false)}", size=11),
                _text(1040, y + 22, f"{row['scenario_replicates']} scenario-replicates", size=10),
                f'<line x1="44" y1="{y + 45}" x2="1156" y2="{y + 45}" stroke="{GRID}" />',
            ]
        )
    parts.append("</svg>\n")
    return "\n".join(parts)


def write_figures(evidence_dir: Path, output_dir: Path) -> tuple[Path, ...]:
    """Validate source documents and write both figures in a fixed order."""

    shapley = AEBShapleyV1.model_validate_json((evidence_dir / "shapley.json").read_bytes())
    families = FamilyInterventionsV1.model_validate_json(
        (evidence_dir / "family-interventions.json").read_bytes()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = (
        (output_dir / "shapley-contributions.svg", shapley_svg(shapley.model_dump(mode="json"))),
        (
            output_dir / "intervention-rates-by-family.svg",
            intervention_rates_svg(families.model_dump(mode="json")),
        ),
    )
    for path, contents in documents:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(contents)
    return tuple(path for path, _ in documents)
