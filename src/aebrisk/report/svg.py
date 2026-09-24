"""Deterministic, dependency-free SVG figures derived from committed evidence.

Printed values are shortened with the project's one rounding rule
(`aebrisk.analysis.rounding`): seconds and event rates to one decimal,
collisions per hour to two, and Shapley contributions to three significant
figures. The exact value stays in a `data-*` attribute beside each printed one,
and in the evidence JSON the figure was drawn from.
"""

from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any, Optional

from aebrisk.analysis.rounding import fixed, scientific, significant
from aebrisk.artifacts.documents import AEBEvaluationV1, AEBShapleyV1
from aebrisk.artifacts.family_interventions import FamilyInterventionsV1
from aebrisk.attribution.factorial import SWEPT_SEVERITIES
from aebrisk.attribution.shapley import CHANNELS

INK = "#14213d"
MUTED = "#526078"
GRID = "#d7deea"
POSITIVE = "#d97706"
NEGATIVE = "#197278"
PAPER = "#f7f9fc"


def _attribute(value: object) -> str:
    return html.escape(str(value), quote=True)


def _text(x: float, y: float, value: object, *, size: int = 13, weight: int = 400) -> str:
    escaped = _attribute(value)
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
                    f'<g data-channel="{_attribute(channel)}" '
                    f'data-value="{_attribute(values[channel])}">',
                    _text(52, y + 12, channel),
                    _bar(480, y, value, maximum),
                    _text(754, y + 12, significant(value), size=12),
                    "</g>",
                ]
            )
        residual = scientific(metric["efficiency_max_abs_residual"], 2)
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
    return "not estimable" if rate is None else fixed(rate, 1)


def _exact(value: Optional[float]) -> str:
    """The machine-readable value kept beside a printed one; absence is not zero."""

    return "unavailable" if value is None else _attribute(value)


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
                f'<g data-family="{_attribute(row["family"])}" '
                f'data-configuration="{_attribute(row["configuration_id"])}" '
                f'data-missed-per-1000="{_exact(missed)}" '
                f'data-false-per-1000="{_exact(false)}">',
                _text(44, y + 13, row["family"], size=12, weight=600),
                _text(214, y + 13, row["configuration_id"], size=11),
                _bar(672, y, 0.0 if missed is None else float(missed), maximum, 150.0),
                _text(830, y + 12, f"missed {_rate_text(missed)}", size=11),
                _bar(672, y + 20, 0.0 if false is None else float(false), maximum, 150.0),
                _text(830, y + 32, f"false {_rate_text(false)}", size=11),
                _text(1040, y + 22, f"{row['scenario_replicates']} scenario-replicates", size=10),
                f'<line x1="44" y1="{y + 45}" x2="1156" y2="{y + 45}" stroke="{GRID}" />',
                "</g>",
            ]
        )
    parts.append("</svg>\n")
    return "\n".join(parts)


def severity_svg(evaluation: dict[str, Any]) -> str:
    """Plot the complete fixed severity sweep with separate units and denominators."""

    document = AEBEvaluationV1.model_validate(evaluation)
    configurations = {row.configuration_id: row for row in document.configurations}
    expected = [f"{channel}-{level}" for channel in CHANNELS for level in SWEPT_SEVERITIES]
    if len(configurations) != len(document.configurations) or any(
        name not in configurations or configurations[name].group != "single_channel"
        for name in expected
    ):
        raise ValueError("severity sweep must contain each fixed channel/level exactly once")
    values: dict[str, dict[str, Optional[float]]] = {}
    for name in expected:
        row = configurations[name]
        values[name] = {
            "collisions_per_hour": (
                row.collisions / row.simulated_seconds * 3600.0
                if row.simulated_seconds > 0.0
                else None
            ),
            "false_per_1000_replicates": (
                row.false_interventions / row.scenarios * 1000.0 if row.scenarios else None
            ),
            "missed_per_1000_replicates": (
                row.missed_interventions / row.scenarios * 1000.0 if row.scenarios else None
            ),
            "mean_intervention_duration_s": row.mean_intervention_duration_s,
        }
    panels = (
        ("collisions_per_hour", "Counted collisions / measured hour", 2),
        ("false_per_1000_replicates", "False events / 1,000 scenario-replicates", 1),
        ("missed_per_1000_replicates", "Missed events / 1,000 scenario-replicates", 1),
        ("mean_intervention_duration_s", "Mean intervention duration (s)", 1),
    )
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 1160" '
        'role="img" aria-labelledby="title desc">',
        '<title id="title">Observed single-channel severity sensitivity</title>',
        '<desc id="desc">Four channels, fixed low/medium/high severity; each metric row '
        "shares a scale across channels. Missing values are unavailable, not zero.</desc>",
        f'<rect width="1440" height="1160" fill="{PAPER}" />',
        _text(32, 36, "Observed single-channel severity sensitivity", size=25, weight=700),
        _text(
            32,
            61,
            "Lines connect fixed levels; not a confidence interval. No causal channel ranking.",
            size=13,
        ),
        _text(
            32,
            83,
            "Exposure is measured per configuration; events are not unique scenarios.",
            size=13,
        ),
    ]
    for index, (metric, label, places) in enumerate(panels):
        top = 106.0 + index * 255.0
        maximum = max([value[metric] or 0.0 for value in values.values()] + [1e-12])
        parts.append(_text(32, top + 14, label, size=18, weight=650))
        shared = fixed(maximum, places)
        parts.append(_text(930, top + 14, f"Shared scale: 0 to {shared}", size=12))
        for column, channel in enumerate(CHANNELS):
            left = 32.0 + column * 350.0
            parts.extend(
                [
                    f'<rect x="{left}" y="{top + 24}" width="332" height="219" '
                    f'rx="8" fill="#ffffff" stroke="{GRID}" />',
                    _text(left + 12, top + 46, channel, size=14, weight=600),
                    f'<line x1="{left + 30}" y1="{top + 144}" '
                    f'x2="{left + 302}" y2="{top + 144}" stroke="{GRID}" />',
                ]
            )
            previous: Optional[tuple[float, float]] = None
            for position, level in enumerate(SWEPT_SEVERITIES):
                name = f"{channel}-{level}"
                value = values[name][metric]
                x = left + 50 + position * 110
                raw = _exact(value)
                printed = "unavailable" if value is None else fixed(value, places)
                parts.append(
                    f'<g data-metric="{metric}" data-configuration="{name}" data-value="{raw}">'
                )
                if value is None:
                    previous = None
                else:
                    y = top + 144 - value / maximum * 80
                    if previous is not None:
                        parts.append(
                            f'<line x1="{previous[0]}" y1="{previous[1]}" x2="{x}" '
                            f'y2="{y}" stroke="{NEGATIVE}" stroke-width="2" />'
                        )
                    parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{NEGATIVE}" />')
                    previous = (x, y)
                parts.extend(
                    [
                        _text(x - 20, top + 160, level, size=11),
                        _text(left + 12, top + 184 + position * 17, f"{level}: {printed}", size=11),
                        "</g>",
                    ]
                )
    parts.append("</svg>\n")
    return "\n".join(parts)


#: The four configurations the braking figure names, with each label's offset
#: from its point and its text anchor. Every other point is unlabelled on
#: purpose: the figure makes one comparison, and a label per point would hide it.
BRAKING_FIGURE_LABELS: dict[str, tuple[str, float, float, str]] = {
    "no_aeb": ("no AEB", 14.0, 5.0, "start"),
    "oracle_aeb": ("oracle AEB", -16.0, -30.0, "end"),
    "coalition-none": ("coalition-none", 36.0, -52.0, "start"),
    "coalition-dropout+localization_shape+latency+track_instability": (
        "all four channels, medium",
        0.0,
        -46.0,
        "middle",
    ),
}
#: The two references every error configuration is read against.
BRAKING_FIGURE_REFERENCES: tuple[str, ...] = ("no_aeb", "oracle_aeb")
#: Plot area in viewBox units: left, right, top, bottom.
BRAKING_FIGURE_PLOT: tuple[float, float, float, float] = (110.0, 910.0, 150.0, 480.0)
#: The collision axis ends at the next multiple of this above the largest rate.
BRAKING_FIGURE_RATE_STEP = 50.0


def _braking_marker(x: float, y: float, kind: str) -> str:
    if kind == "reference":
        return (
            f'<path d="M{x:.2f} {y - 8:.2f} L{x + 8:.2f} {y:.2f} L{x:.2f} {y + 8:.2f} '
            f'L{x - 8:.2f} {y:.2f} Z" fill="{INK}" />'
        )
    if kind == "localization_shape":
        return (
            f'<rect x="{x - 6:.2f}" y="{y - 6:.2f}" width="12" height="12" '
            f'fill="{POSITIVE}" fill-opacity="0.85" stroke="#ffffff" />'
        )
    return (
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="6" fill="{NEGATIVE}" '
        'fill-opacity="0.85" stroke="#ffffff" />'
    )


def collisions_vs_braking_svg(evaluation: dict[str, Any]) -> str:
    """Plot counted collisions against the share of exposure spent braking.

    Braking share is ``mean_intervention_duration_s * scenarios /
    simulated_seconds``: the partial- or full-braking time a configuration
    accumulated, over its own measured exposure. Collisions are counted per
    1,000 scenario-replicates. Both are derived here from evaluation.json and
    neither is printed; each point carries its exact derived values in data
    attributes. Configurations whose error set includes localization_shape get
    their own marker, because that split is what the figure exists to show.
    """

    document = AEBEvaluationV1.model_validate(evaluation)
    points: list[tuple[str, float, float, str]] = []
    for row in sorted(document.configurations, key=lambda item: item.configuration_id):
        duration = row.mean_intervention_duration_s
        if row.scenarios == 0 or row.simulated_seconds == 0.0 or duration is None:
            raise ValueError(
                f"{row.configuration_id} lacks scenario-replicates, measured exposure or a "
                "mean intervention duration, so its braking share cannot be derived"
            )
        if row.configuration_id in BRAKING_FIGURE_REFERENCES:
            kind = "reference"
        elif "localization_shape" in row.configuration_id:
            kind = "localization_shape"
        else:
            kind = "other"
        points.append(
            (
                row.configuration_id,
                duration * row.scenarios / row.simulated_seconds,
                row.collisions * 1000.0 / row.scenarios,
                kind,
            )
        )

    left, right, top, bottom = BRAKING_FIGURE_PLOT
    largest = max(rate for _, _, rate, _ in points)
    steps = max(1, math.ceil(largest / BRAKING_FIGURE_RATE_STEP))
    ceiling = steps * BRAKING_FIGURE_RATE_STEP

    def x_at(share: float) -> float:
        return left + share * (right - left)

    def y_at(rate: float) -> float:
        return bottom - rate / ceiling * (bottom - top)

    middle = (top + bottom) / 2
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 640" role="img" '
        'aria-labelledby="title desc">',
        '<title id="title">Counted collisions against braking share</title>',
        '<desc id="desc">One point per configuration, derived from evaluation.json: counted '
        "collisions per 1,000 scenario-replicates against the share of measured exposure spent "
        "in partial or full braking. Descriptive only; scenario-replicates are not independent, "
        "there are no intervals and no channel ranking.</desc>",
        f'<rect width="960" height="640" fill="{PAPER}" />',
        _text(44, 42, "Counted collisions against braking share", size=24, weight=700),
        _text(44, 66, "One point per configuration, derived from evaluation.json", size=13),
    ]
    legend = (
        ("reference", "reference: no AEB, oracle AEB"),
        ("localization_shape", "error set includes localization_shape"),
        ("other", "other error configuration"),
    )
    for index, (kind, label) in enumerate(legend):
        x = 52.0 + index * 290.0
        parts.extend([_braking_marker(x, 100.0, kind), _text(x + 16, 105, label, size=13)])
    parts.append(
        f'<rect x="{left}" y="{top}" width="{right - left}" height="{bottom - top}" '
        f'fill="#ffffff" stroke="{GRID}" />'
    )
    for step in range(6):
        x = x_at(step / 5)
        parts.extend(
            [
                f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom}" stroke="{GRID}" />',
                _text(round(x - 14, 2), bottom + 22, f"{step * 20}%", size=12),
            ]
        )
    for step in range(steps + 1):
        rate = step * BRAKING_FIGURE_RATE_STEP
        y = y_at(rate)
        parts.extend(
            [
                f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="{GRID}" />',
                _text(left - 40, round(y + 4, 2), f"{rate:.0f}", size=12),
            ]
        )
    parts.extend(
        [
            _text(
                left,
                bottom + 50,
                "Share of measured exposure spent in partial or full braking",
                size=14,
                weight=600,
            ),
            f'<text x="30" y="{middle}" transform="rotate(-90 30 {middle})" '
            'text-anchor="middle" font-family="system-ui, sans-serif" font-size="14" '
            f'font-weight="600" fill="{INK}">Counted collisions per 1,000 '
            "scenario-replicates</text>",
        ]
    )
    # References last, so the two points every other point is read against sit on top.
    for configuration_id, share, rate, kind in sorted(
        points, key=lambda point: point[3] == "reference"
    ):
        parts.extend(
            [
                f'<g data-configuration="{_attribute(configuration_id)}" data-kind="{kind}" '
                f'data-braking-share="{share!r}" data-collisions-per-1000="{rate!r}">',
                _braking_marker(x_at(share), y_at(rate), kind),
                "</g>",
            ]
        )
    for configuration_id, share, rate, _ in points:
        if configuration_id in BRAKING_FIGURE_LABELS:
            label, dx, dy, anchor = BRAKING_FIGURE_LABELS[configuration_id]
            x, y = x_at(share), y_at(rate)
            parts.extend(
                [
                    f'<line x1="{x:.2f}" y1="{y:.2f}" x2="{x + dx:.2f}" y2="{y + dy:.2f}" '
                    f'stroke="{MUTED}" />',
                    f'<text x="{x + dx:.2f}" y="{y + dy - 4:.2f}" text-anchor="{anchor}" '
                    'font-family="system-ui, sans-serif" font-size="13" font-weight="600" '
                    f'fill="{INK}">{_attribute(label)}</text>',
                ]
            )
    parts.extend(
        [
            _text(
                44,
                600,
                "Descriptive and derived: braking share is mean intervention duration times "
                "scenario-replicates, divided by measured exposure.",
                size=12,
            ),
            _text(
                44,
                620,
                "Scenario-replicates are not independent; no intervals; no channel ranking.",
                size=12,
            ),
            "</svg>\n",
        ]
    )
    return "\n".join(parts)


def write_figures(evidence_dir: Path, output_dir: Path) -> tuple[Path, ...]:
    """Validate common source identity and write the four figures in fixed order."""

    shapley = AEBShapleyV1.model_validate_json((evidence_dir / "shapley.json").read_bytes())
    families = FamilyInterventionsV1.model_validate_json(
        (evidence_dir / "family-interventions.json").read_bytes()
    )
    evaluation = AEBEvaluationV1.model_validate_json(
        (evidence_dir / "evaluation.json").read_bytes()
    )
    identities = {
        (document.protocol_sha256, document.cohort_manifest_sha256, document.common_valid_tokens)
        for document in (shapley, families, evaluation)
    }
    if len(identities) != 1:
        raise ValueError("figure evidence identity differs across documents")
    documents = (
        (
            output_dir / "collisions-vs-braking.svg",
            collisions_vs_braking_svg(evaluation.model_dump(mode="json")),
        ),
        (output_dir / "shapley-contributions.svg", shapley_svg(shapley.model_dump(mode="json"))),
        (
            output_dir / "intervention-rates-by-family.svg",
            intervention_rates_svg(families.model_dump(mode="json")),
        ),
        (
            output_dir / "error-severity-sensitivity.svg",
            severity_svg(evaluation.model_dump(mode="json")),
        ),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, contents in documents:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(contents)
    return tuple(path for path, _ in documents)
