"""The paired, family-stratified bootstrap over scenario tokens.

This is what turns "corrupted collided more often" into a statement with a
magnitude and a width. Two properties make it the right interval for this study.

IT IS PAIRED. Every configuration is resampled over the SAME scenario tokens in
the same draw. The configurations were run on identical scenarios by
construction, so the between-configuration difference carries no
scenario-selection variance; resampling each configuration independently would
put that variance back and widen every interval for no reason. The test for this
asserts the consequence rather than the mechanism: two configurations whose
values differ by a constant must have intervals differing by the same constant.

IT IS STRATIFIED BY FAMILY. The cohort is built with a fixed number of scenarios
per family, so an unstratified resample would sometimes draw a sample that was
three quarters pedestrian crossings, and the interval would describe a cohort
the study never ran.

The generator is built from the seed and never shares the global stream, for the
same reason every error channel derives its own: a shared stream would make the
interval depend on how many draws happened to run before it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

import numpy as np

#: Cited in every published interval, so they are values rather than literals.
DEFAULT_RESAMPLES = 5000
DEFAULT_SEED = 20260831
DEFAULT_CONFIDENCE = 0.95


@dataclass(frozen=True)
class BootstrapInterval:
    """A published number and everything needed to reproduce it."""

    estimate: float
    low: float
    high: float
    confidence: float
    resamples: int
    seed: int

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(
                f"low {self.low} exceeds high {self.high}; an inverted interval would still print"
            )


def _validated(
    metric_by_scenario_config: Mapping[str, Mapping[str, float]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not metric_by_scenario_config:
        raise ValueError("there are no scenarios to resample")

    tokens = tuple(sorted(metric_by_scenario_config))
    configurations = tuple(sorted(metric_by_scenario_config[tokens[0]]))

    for token in tokens:
        row = metric_by_scenario_config[token]
        if tuple(sorted(row)) != configurations:
            raise ValueError(
                f"scenario {token!r} does not carry every configuration; the pairing "
                "requires all of them on every scenario, and the common cohort "
                "guarantees it, so a gap means the cohort was not applied"
            )
        for name, value in row.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"metric for {token!r}/{name!r} must be a number")
            if not math.isfinite(value):
                raise ValueError(
                    f"metric for {token!r}/{name!r} must be finite; one NaN would make "
                    "every percentile NaN with no indication why"
                )

    return tokens, configurations


def _strata(
    tokens: tuple[str, ...],
    family_by_scenario: Optional[Mapping[str, str]],
) -> tuple[tuple[int, ...], ...]:
    """Group token positions into the strata each resample draws within."""

    if family_by_scenario is None:
        return (tuple(range(len(tokens))),)

    missing = sorted(token for token in tokens if token not in family_by_scenario)
    if missing:
        raise ValueError(
            f"no family for scenarios {missing}; putting them in a default stratum "
            "would silently unbalance every resample"
        )

    by_family: dict[str, list[int]] = {}
    for position, token in enumerate(tokens):
        by_family.setdefault(family_by_scenario[token], []).append(position)
    return tuple(tuple(positions) for _, positions in sorted(by_family.items()))


def paired_scenario_bootstrap(
    metric_by_scenario_config: Mapping[str, Mapping[str, float]],
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
    family_by_scenario: Optional[Mapping[str, str]] = None,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict[str, BootstrapInterval]:
    """A percentile interval per configuration, over the same resampled tokens."""

    if isinstance(resamples, bool) or not isinstance(resamples, int) or resamples < 1:
        raise ValueError(f"resamples must be a positive integer, got {resamples!r}")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError("confidence must be a number")
    if not math.isfinite(confidence) or not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must lie strictly between 0 and 1, got {confidence!r}")

    tokens, configurations = _validated(metric_by_scenario_config)
    strata = _strata(tokens, family_by_scenario)

    values = np.array(
        [[metric_by_scenario_config[token][name] for name in configurations] for token in tokens],
        dtype=np.float64,
    )

    generator = np.random.Generator(np.random.PCG64(seed))
    means = np.empty((resamples, len(configurations)), dtype=np.float64)
    for draw in range(resamples):
        # One index vector per draw, used for every configuration. That is the
        # pairing, and it is why this is a loop over draws rather than over
        # configurations.
        picked = np.concatenate(
            [
                generator.choice(np.asarray(stratum), size=len(stratum), replace=True)
                for stratum in strata
            ]
        )
        means[draw] = values[picked].mean(axis=0)

    tail = 100.0 * (1.0 - confidence) / 2.0
    low = np.percentile(means, tail, axis=0)
    high = np.percentile(means, 100.0 - tail, axis=0)
    estimate = values.mean(axis=0)

    return {
        name: BootstrapInterval(
            estimate=float(estimate[index]),
            low=float(low[index]),
            high=float(high[index]),
            confidence=float(confidence),
            resamples=resamples,
            seed=seed,
        )
        for index, name in enumerate(configurations)
    }
