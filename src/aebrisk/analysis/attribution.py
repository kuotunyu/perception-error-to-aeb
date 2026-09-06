"""Exact per-token Shapley attribution over the sixteen medium coalitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from aebrisk.analysis.aggregate import _replicate_metric_mean
from aebrisk.attribution.factorial import coalition_configurations
from aebrisk.attribution.shapley import (
    ATTRIBUTED_METRICS,
    CHANNELS,
    shapley_by_metric,
)
from aebrisk.simulation.orchestrate import TokenResultsV1

EFFICIENCY_TOLERANCE = 1e-9


class AttributionMetric(TypedDict):
    values: dict[str, float]
    efficiency_max_abs_residual: float
    scenarios_attributed: int


def attribution_by_metric(
    loaded: Mapping[str, Mapping[str, TokenResultsV1]], cohort: tuple[str, ...]
) -> dict[str, AttributionMetric]:
    """Average exact Shapley values after checking efficiency on every token."""

    if not cohort:
        raise ValueError("there are no common scenarios to attribute")

    configurations = coalition_configurations()
    for identifier in configurations.values():
        if identifier not in loaded:
            raise ValueError(f"missing coalition configuration {identifier!r}")

    totals = {metric: dict.fromkeys(CHANNELS, 0.0) for metric in ATTRIBUTED_METRICS}
    max_residual = dict.fromkeys(ATTRIBUTED_METRICS, 0.0)
    for token in cohort:
        games = {
            metric: {
                coalition: _replicate_metric_mean(loaded[identifier][token], metric)
                for coalition, identifier in configurations.items()
            }
            for metric in ATTRIBUTED_METRICS
        }
        attributed = shapley_by_metric(games)
        full = frozenset(CHANNELS)
        for metric in ATTRIBUTED_METRICS:
            residual = abs(
                sum(attributed[metric].values())
                - (games[metric][full] - games[metric][frozenset()])
            )
            if residual > EFFICIENCY_TOLERANCE:
                raise ValueError(
                    f"Shapley efficiency residual {residual} exceeds "
                    f"{EFFICIENCY_TOLERANCE} for {token}/{metric}"
                )
            max_residual[metric] = max(max_residual[metric], residual)
            for channel in CHANNELS:
                totals[metric][channel] += attributed[metric][channel]

    return {
        metric: {
            "values": {channel: totals[metric][channel] / len(cohort) for channel in CHANNELS},
            "efficiency_max_abs_residual": max_residual[metric],
            "scenarios_attributed": len(cohort),
        }
        for metric in ATTRIBUTED_METRICS
    }
