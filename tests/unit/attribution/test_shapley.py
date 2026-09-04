"""Contracts for the exact Shapley attribution.

The study's headline question is which perception error contributes most to the
AEB's failures, and that question only has an answer if the channels interact.
If they did not, the single-channel runs would settle it and there would be no
coalitions to run.

Shapley is used rather than a main-effects table because it is the unique
attribution that is efficient, symmetric, null-player-respecting and additive,
and with four channels the exact value is a sum over sixteen coalitions — small
enough that no sampling approximation is needed and none is used. An approximate
attribution would carry a sampling error that no reader could distinguish from
an interaction effect.

The sign convention is fixed here and everywhere downstream: the value function
measures HARM, so a positive Shapley value means that channel INCREASES harm.
Getting this backwards would invert the study's conclusion while every test of
the arithmetic still passed, which is why it has its own test rather than a
sentence in a document.
"""

from __future__ import annotations

import itertools
from types import ModuleType

import pytest


def load_shapley_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.attribution import shapley
    except ImportError:
        pytest.fail("aebrisk.attribution.shapley is missing", pytrace=False)
    return shapley


CHANNELS = ("dropout", "localization_shape", "latency", "track_instability")


def all_coalitions(channels: tuple[str, ...] = CHANNELS) -> list[frozenset[str]]:
    return [
        frozenset(combination)
        for size in range(len(channels) + 1)
        for combination in itertools.combinations(channels, size)
    ]


def additive_game(weights: dict[str, float]) -> dict[frozenset[str], float]:
    return {
        coalition: sum(weights[channel] for channel in coalition) for coalition in all_coalitions()
    }


# --------------------------------------------------------------------------
# The known-answer games
# --------------------------------------------------------------------------


def test_an_additive_game_gives_each_channel_its_own_weight() -> None:
    """With no interaction, Shapley must reduce to the main effect exactly."""

    shapley = load_shapley_module()
    weights = {
        "dropout": 0.4,
        "localization_shape": 0.25,
        "latency": 0.1,
        "track_instability": 0.05,
    }

    values = shapley.exact_shapley(additive_game(weights))

    for channel, weight in weights.items():
        assert values[channel] == pytest.approx(weight, abs=1e-12)


def test_a_pure_interaction_game_splits_the_value_evenly() -> None:
    """Two channels that only matter together each get half.

    This is the case a main-effects table reports as zero for both, which is
    the reason this project computes Shapley at all.
    """

    shapley = load_shapley_module()
    pair = {"dropout", "latency"}
    game = {coalition: 1.0 if pair <= coalition else 0.0 for coalition in all_coalitions()}

    values = shapley.exact_shapley(game)

    assert values["dropout"] == pytest.approx(0.5, abs=1e-12)
    assert values["latency"] == pytest.approx(0.5, abs=1e-12)
    assert values["localization_shape"] == pytest.approx(0.0, abs=1e-12)
    assert values["track_instability"] == pytest.approx(0.0, abs=1e-12)


def test_a_constant_game_attributes_nothing() -> None:
    """If no channel changes the outcome, no channel is responsible for it."""

    shapley = load_shapley_module()
    game = dict.fromkeys(all_coalitions(), 3.7)

    values = shapley.exact_shapley(game)

    assert all(value == pytest.approx(0.0, abs=1e-12) for value in values.values())


def test_a_null_channel_receives_nothing() -> None:
    """A channel that never changes any coalition's value gets exactly zero."""

    shapley = load_shapley_module()
    game = {
        coalition: float(len(coalition - {"track_instability"})) for coalition in all_coalitions()
    }

    values = shapley.exact_shapley(game)

    assert values["track_instability"] == pytest.approx(0.0, abs=1e-12)


def test_two_symmetric_channels_receive_the_same_value() -> None:
    """Symmetry is one of the four axioms that make Shapley the only answer."""

    shapley = load_shapley_module()
    game = {
        coalition: float(len(coalition & {"dropout", "latency"})) for coalition in all_coalitions()
    }

    values = shapley.exact_shapley(game)

    assert values["dropout"] == pytest.approx(values["latency"], abs=1e-12)


# --------------------------------------------------------------------------
# Efficiency and sign
# --------------------------------------------------------------------------


def test_the_values_sum_to_the_grand_coalition_minus_the_empty_one() -> None:
    """Efficiency: the whole effect is distributed, and nothing is invented.

    Checked to 1e-12 rather than to a loose tolerance, because a coefficient
    error would show up here as a small residue rather than a large one.
    """

    shapley = load_shapley_module()
    game = {
        coalition: len(coalition) ** 1.7 + 0.3 * len(coalition & {"latency"})
        for coalition in all_coalitions()
    }

    values = shapley.exact_shapley(game)

    total = game[frozenset(CHANNELS)] - game[frozenset()]
    assert sum(values.values()) == pytest.approx(total, abs=1e-12)


def test_efficiency_holds_when_the_empty_coalition_is_not_zero() -> None:
    """The all-zero corrupted run is the baseline and it is not always exactly zero.

    An implementation that assumed ``v(empty) == 0`` would pass every other
    test here and be wrong on real data by exactly that baseline.
    """

    shapley = load_shapley_module()
    game = {coalition: 5.0 + len(coalition) for coalition in all_coalitions()}

    values = shapley.exact_shapley(game)

    assert sum(values.values()) == pytest.approx(4.0, abs=1e-12)


def test_a_channel_that_increases_harm_gets_a_positive_value() -> None:
    """The sign convention, stated as a test because a document cannot enforce it.

    Inverting it would flip the study's conclusion while every arithmetic test
    still passed.
    """

    shapley = load_shapley_module()
    game = {coalition: float("dropout" in coalition) for coalition in all_coalitions()}

    assert shapley.exact_shapley(game)["dropout"] > 0.0


def test_a_channel_that_reduces_harm_gets_a_negative_value() -> None:
    """The other side of the convention. It should not happen, and it must be visible."""

    shapley = load_shapley_module()
    game = {coalition: -float("dropout" in coalition) for coalition in all_coalitions()}

    assert shapley.exact_shapley(game)["dropout"] < 0.0


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_missing_coalition_is_refused() -> None:
    """An exact value needs every coalition; a gap would be silently filled with zero."""

    shapley = load_shapley_module()
    game = additive_game(dict.fromkeys(CHANNELS, 1.0))
    del game[frozenset({"latency"})]

    with pytest.raises(ValueError, match="missing"):
        shapley.exact_shapley(game)


def test_an_extra_coalition_is_refused() -> None:
    """A coalition over an unknown channel means the game and the channels disagree."""

    shapley = load_shapley_module()
    game = additive_game(dict.fromkeys(CHANNELS, 1.0))
    game[frozenset({"blur"})] = 1.0

    with pytest.raises(ValueError, match="unknown"):
        shapley.exact_shapley(game)


@pytest.mark.parametrize("bad_value", ["1.0", True, None])
def test_a_coalition_value_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean a coalition worth one collision."""

    shapley = load_shapley_module()
    game: dict = dict(additive_game(dict.fromkeys(CHANNELS, 1.0)))
    game[frozenset({"latency"})] = bad_value

    with pytest.raises(ValueError, match="must be a number"):
        shapley.exact_shapley(game)


def test_a_non_finite_coalition_value_is_refused() -> None:
    """One NaN would make every channel's attribution NaN with no indication why."""

    shapley = load_shapley_module()
    game = additive_game(dict.fromkeys(CHANNELS, 1.0))
    game[frozenset({"latency"})] = float("nan")

    with pytest.raises(ValueError, match="finite"):
        shapley.exact_shapley(game)


def test_no_channels_is_refused() -> None:
    """There is nothing to attribute, and the coefficient would divide by zero."""

    shapley = load_shapley_module()

    with pytest.raises(ValueError, match="channels"):
        shapley.exact_shapley({frozenset(): 0.0}, channels=())


def test_duplicate_channels_are_refused() -> None:
    """A repeated channel would be attributed twice and break efficiency."""

    shapley = load_shapley_module()

    with pytest.raises(ValueError, match=r"^duplicate channels in .*; each is attributed once$"):
        shapley.exact_shapley({}, channels=("dropout", "dropout"))


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


def test_the_attributed_metrics_are_named_and_separate() -> None:
    """A collision count and a duration in seconds cannot be added.

    They are attributed separately and the two results are never combined, so
    the metrics are named here rather than left to each caller.
    """

    shapley = load_shapley_module()

    assert set(shapley.ATTRIBUTED_METRICS) == {"collision_indicator", "intervention_duration_s"}


def test_each_metric_is_attributed_on_its_own() -> None:
    """The helper exists so that combining them requires writing new code."""

    shapley = load_shapley_module()
    games = {
        "collision_indicator": {
            coalition: float("dropout" in coalition) for coalition in all_coalitions()
        },
        "intervention_duration_s": {
            coalition: 2.0 * len(coalition) for coalition in all_coalitions()
        },
    }

    attributed = shapley.shapley_by_metric(games)

    assert set(attributed) == set(games)
    assert attributed["collision_indicator"]["dropout"] > 0.0
    assert attributed["intervention_duration_s"]["latency"] == pytest.approx(2.0, abs=1e-12)


def test_an_unknown_metric_is_refused() -> None:
    """A third metric would need its own units decision, not a default one."""

    shapley = load_shapley_module()

    with pytest.raises(ValueError, match="metric"):
        shapley.shapley_by_metric({"comfort": {frozenset(): 0.0}})


# --------------------------------------------------------------------------
# A smaller game, computed by hand
# --------------------------------------------------------------------------


def test_a_two_channel_game_matches_the_hand_computed_value() -> None:
    """Two channels is small enough to write the whole sum out.

    v({}) = 0, v({a}) = 1, v({b}) = 4, v({a,b}) = 9.
    phi_a = 1/2 * (v({a}) - v({})) + 1/2 * (v({a,b}) - v({b})) = 1/2 + 5/2 = 3.
    phi_b = 1/2 * (v({b}) - v({})) + 1/2 * (v({a,b}) - v({a})) = 2 + 4 = 6.
    """

    shapley = load_shapley_module()
    game = {
        frozenset(): 0.0,
        frozenset({"a"}): 1.0,
        frozenset({"b"}): 4.0,
        frozenset({"a", "b"}): 9.0,
    }

    values = shapley.exact_shapley(game, channels=("a", "b"))

    assert values["a"] == pytest.approx(3.0, abs=1e-12)
    assert values["b"] == pytest.approx(6.0, abs=1e-12)
