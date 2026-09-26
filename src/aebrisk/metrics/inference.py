"""Tests, multiplicity rules and decision rules for the policy v2 study.

The study's analysis plan (``docs/studies/aeb-policy-v2/analysis-plan.md``)
fixed every rule here before any result existed. Each function is one of its
sentences in code, and none of them looks at the data it decides on:

- a collision-indicator contrast gets an exact paired sign-flip test over logs.
  For sparse paired binary outcomes the bootstrap p is anti-conservative: when
  one arm has no collision, every draw has the same sign;
- each family of hypotheses is decided by Holm at a family-wise 0.05, and the
  interval levels follow the family size and the Holm step;
- a zero-event cell gets exact Clopper-Pearson bounds. They are descriptive:
  they count tokens, which are correlated within logs, so they are too narrow;
- the classifications, the Q1 label and the H5 qualifier are the plan's
  decision rules, so a result is worded by rule rather than after reading it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.stats import beta as beta_distribution

from aebrisk.metrics.bootstrap import DEFAULT_CONFIDENCE, DEFAULT_SEED, _check_open_unit

#: The study's level: family-wise for Holm and Bonferroni, and unadjusted for
#: the H5 qualifier, a harm check for which a missed signal costs more than a
#: false alarm.
ALPHA = 0.05

#: What a directional hypothesis can be classified as.
DIRECTIONAL_CLASSES = ("supported", "contradicted", "not_established")

Classification = Literal["supported", "contradicted", "not_established", "increase", "decrease"]
Q1Label = Literal["support", "partial_support", "no_support"]


def _check_count(name: str, value: int, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}, got {value!r}")


def _check_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")


def _check_probability(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a probability in [0, 1], got {value!r}")


def sign_flip_p_value(
    log_differences_in_thirds: Sequence[int],
    enumerate_up_to: int = 20,
    random_flips: int = 100_000,
    seed: int = DEFAULT_SEED,
) -> float:
    """The two-sided exact paired sign-flip p over logs.

    Each entry is one log's summed difference in collision indicator, counted in
    thirds of a collision (three replicates per token), so ties are exact. With
    m logs whose difference is not zero, p is the share of the 2^m sign
    patterns whose |sum| is at least the observed |sum|; logs with no difference
    change no sum. Up to ``enumerate_up_to`` logs every pattern is enumerated.
    Above it, ``random_flips`` patterns are drawn from ``seed`` and
    p = (1 + count) / (1 + random_flips). With m = 0, p = 1.
    """

    _check_count("enumerate_up_to", enumerate_up_to, 0)
    _check_count("random_flips", random_flips, 1)
    for value in log_differences_in_thirds:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError(
                f"log differences must be integers (thirds of a collision), got {value!r}"
            )

    differences = np.array(
        [int(value) for value in log_differences_in_thirds if value != 0], dtype=np.int64
    )
    if differences.size == 0:
        return 1.0
    observed = abs(int(differences.sum()))

    if differences.size <= enumerate_up_to:
        totals = np.zeros(1, dtype=np.int64)
        for difference in differences:
            totals = np.concatenate((totals + difference, totals - difference))
        return int(np.count_nonzero(np.abs(totals) >= observed)) / totals.size

    generator = np.random.Generator(np.random.PCG64(seed))
    signs = 2 * generator.integers(0, 2, size=(random_flips, differences.size), dtype=np.int8) - 1
    count = int(np.count_nonzero(np.abs(signs @ differences) >= observed))
    return (1 + count) / (1 + random_flips)


@dataclass(frozen=True)
class HolmDecision:
    """Whether Holm rejected a hypothesis, and at which step.

    The step is the hypothesis's rank among its family's p-values, from 1 for
    the smallest; it sets the level of its Holm-step interval.
    """

    rejected: bool
    step: int


def holm(p_values: Mapping[str, float], alpha: float = ALPHA) -> dict[str, HolmDecision]:
    """Holm's step-down procedure over one family of hypotheses.

    The p-values are sorted in ascending order, ties by name. The i-th smallest
    is rejected while it is at most alpha / (K - i + 1); the first that is not
    rejected stops the procedure, and every later one is not rejected either.
    """

    _check_open_unit("alpha", alpha)
    for name, p_value in p_values.items():
        _check_probability(f"p-value of {name!r}", p_value)

    ordered = sorted(p_values, key=lambda name: (p_values[name], name))
    family_size = len(ordered)
    decisions: dict[str, HolmDecision] = {}
    rejecting = True
    for step, name in enumerate(ordered, start=1):
        rejecting = rejecting and bool(p_values[name] <= alpha / (family_size - step + 1))
        decisions[name] = HolmDecision(rejected=rejecting, step=step)
    return decisions


def holm_step_confidence(k: int, step: int) -> float:
    """The interval level for Holm step ``step`` of ``k``: 1 - 0.05 / (k - step + 1)."""

    _check_count("k", k, 1)
    if isinstance(step, bool) or not isinstance(step, int) or not 1 <= step <= k:
        raise ValueError(f"step must be an integer from 1 to k = {k}, got {step!r}")
    return 1.0 - ALPHA / (k - step + 1)


def simultaneous_confidence(k: int) -> float:
    """The simultaneous Bonferroni interval level for ``k`` hypotheses: 1 - 0.05 / k."""

    _check_count("k", k, 1)
    return 1.0 - ALPHA / k


def clopper_pearson(
    successes: int, trials: int, confidence: float = DEFAULT_CONFIDENCE
) -> tuple[float, float]:
    """The exact two-sided Clopper-Pearson interval for a binomial proportion.

    With zero successes the lower bound is 0 and the upper is
    1 - ((1 - confidence) / 2)^(1 / trials); with all successes, the mirror.
    """

    _check_count("trials", trials, 1)
    if (
        isinstance(successes, bool)
        or not isinstance(successes, int)
        or not 0 <= successes <= trials
    ):
        raise ValueError(
            f"successes must be an integer from 0 to trials = {trials}, got {successes!r}"
        )
    _check_open_unit("confidence", confidence)

    tail = (1.0 - confidence) / 2.0
    low = 0.0
    if successes > 0:
        low = float(beta_distribution.ppf(tail, successes, trials - successes + 1))
    high = 1.0
    if successes < trials:
        high = float(beta_distribution.isf(tail, successes + 1, trials - successes))
    return low, high


def classify(estimate: float, rejected: bool, predicted_sign: Literal[-1, 0, 1]) -> Classification:
    """Classify one hypothesis from its Holm decision and its point estimate.

    A directional hypothesis (``predicted_sign`` -1 or 1) is supported when Holm
    rejects it and the estimate has the predicted sign, contradicted when Holm
    rejects it and the sign is the opposite, and not established otherwise. A
    two-sided hypothesis (``predicted_sign`` 0) is an increase, a decrease or not
    established. Not established is not evidence of no effect.
    """

    _check_finite("estimate", estimate)
    if not isinstance(rejected, (bool, np.bool_)):
        raise ValueError(f"rejected must be a bool, got {rejected!r}")
    if (
        isinstance(predicted_sign, bool)
        or not isinstance(predicted_sign, int)
        or predicted_sign not in (-1, 0, 1)
    ):
        raise ValueError(f"predicted_sign must be -1, 0 or 1, got {predicted_sign!r}")

    if not rejected or estimate == 0:
        return "not_established"
    increased = estimate > 0
    if predicted_sign == 0:
        return "increase" if increased else "decrease"
    return "supported" if increased == (predicted_sign > 0) else "contradicted"


def q1_label(
    h1: str,
    h2: str,
    estimate_h1: float,
    estimate_h2: float,
    threshold: float = -0.10,
) -> Q1Label:
    """The Q1 label, from H1 and H2 only.

    Support: both supported, each with an estimate at or below ``threshold``
    (ten percentage points of simulated time by default). Partial support: at
    least one supported and neither contradicted, but not Support. No support
    otherwise.
    """

    for name, label in (("h1", h1), ("h2", h2)):
        if label not in DIRECTIONAL_CLASSES:
            raise ValueError(f"{name} must be one of {DIRECTIONAL_CLASSES}, got {label!r}")
    _check_finite("estimate_h1", estimate_h1)
    _check_finite("estimate_h2", estimate_h2)
    _check_finite("threshold", threshold)

    labels = (h1, h2)
    if (
        labels == ("supported", "supported")
        and estimate_h1 <= threshold
        and estimate_h2 <= threshold
    ):
        return "support"
    if "supported" in labels and "contradicted" not in labels:
        return "partial_support"
    return "no_support"


def h5_qualifier(p_unadjusted: float, estimate: float) -> bool:
    """Whether the Q1 label is printed "with more oracle collisions".

    True when H5's unadjusted sign-flip p is below 0.05 and its estimate is
    above zero.
    """

    _check_probability("p_unadjusted", p_unadjusted)
    _check_finite("estimate", estimate)
    return bool(p_unadjusted < ALPHA and estimate > 0)
