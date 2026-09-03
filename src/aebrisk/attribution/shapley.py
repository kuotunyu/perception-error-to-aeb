"""Exact Shapley attribution over the four perception channels.

The study's headline question is which perception error contributes most to the
AEB's failures, and that question only has an answer if the channels interact.
If they did not, the single-channel runs would settle it and there would be no
coalitions to run.

Shapley is used rather than a table of main effects because it is the unique
attribution that is efficient, symmetric, null-player-respecting and additive.
With four channels the exact value is a sum over sixteen coalitions, which is
small enough that no sampling approximation is needed and none is used: an
approximate attribution would carry a sampling error no reader could
distinguish from an interaction effect.

THE SIGN CONVENTION IS FIXED: the value function measures HARM, so a positive
Shapley value means that channel INCREASES harm. Inverting it would flip the
study's conclusion while every test of the arithmetic still passed.

THE UNITS ARE NEVER MIXED. A collision indicator and a duration in seconds are
attributed separately and the two results are never combined, which is why the
metrics are named here rather than left to each caller.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping

CHANNELS: tuple[str, ...] = (
    "dropout",
    "localization_shape",
    "latency",
    "track_instability",
)

#: Attributed separately, always. Their units have nothing in common.
ATTRIBUTED_METRICS: tuple[str, ...] = ("collision_indicator", "intervention_duration_s")


def _validated_channels(channels: tuple[str, ...]) -> tuple[str, ...]:
    if not channels:
        raise ValueError("channels must not be empty; there is nothing to attribute")
    if len(set(channels)) != len(channels):
        raise ValueError(f"duplicate channels in {channels}; each is attributed once")
    return channels


def _validated_game(
    coalition_values: Mapping[frozenset[str], float],
    channels: tuple[str, ...],
) -> dict[frozenset[str], float]:
    expected = {
        frozenset(combination)
        for size in range(len(channels) + 1)
        for combination in itertools.combinations(channels, size)
    }

    unknown = sorted(
        "+".join(sorted(coalition)) or "<empty>"
        for coalition in coalition_values
        if coalition not in expected
    )
    if unknown:
        raise ValueError(
            f"coalition values name unknown coalitions: {unknown}; the game and the "
            "channels disagree about what was run"
        )
    missing = sorted(
        "+".join(sorted(coalition)) or "<empty>"
        for coalition in expected
        if coalition not in coalition_values
    )
    if missing:
        raise ValueError(
            f"coalition values are missing: {missing}; an exact Shapley value needs "
            "every coalition, and a gap would be silently filled with zero"
        )

    for coalition, value in coalition_values.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"coalition value for {sorted(coalition)} must be a number")
        if not math.isfinite(value):
            raise ValueError(
                f"coalition value for {sorted(coalition)} must be finite; one NaN would "
                "make every channel's attribution NaN with no indication why"
            )

    return dict(coalition_values)


def exact_shapley(
    coalition_values: Mapping[frozenset[str], float],
    channels: tuple[str, ...] = CHANNELS,
) -> dict[str, float]:
    """The exact Shapley value of each channel, summed over every coalition."""

    channels = _validated_channels(channels)
    game = _validated_game(coalition_values, channels)

    count = len(channels)
    factorial = math.factorial
    attribution: dict[str, float] = {}

    for channel in channels:
        others = [other for other in channels if other != channel]
        total = 0.0
        for size in range(count):
            weight = factorial(size) * factorial(count - size - 1) / factorial(count)
            for combination in itertools.combinations(others, size):
                without = frozenset(combination)
                total += weight * (game[without | {channel}] - game[without])
        attribution[channel] = total

    return attribution


def shapley_by_metric(
    games_by_metric: Mapping[str, Mapping[frozenset[str], float]],
    channels: tuple[str, ...] = CHANNELS,
) -> dict[str, dict[str, float]]:
    """Attribute each metric on its own.

    This function exists so that combining two metrics requires writing new
    code rather than passing a different argument. A collision count and a
    duration in seconds cannot be added, and an attribution that mixed them
    would still produce numbers.
    """

    unknown = sorted(set(games_by_metric) - set(ATTRIBUTED_METRICS))
    if unknown:
        raise ValueError(
            f"unknown metric to attribute: {unknown}; the attributed metrics are "
            f"{list(ATTRIBUTED_METRICS)}, and a third one needs its own units "
            "decision rather than a default"
        )
    return {metric: exact_shapley(game, channels) for metric, game in games_by_metric.items()}
