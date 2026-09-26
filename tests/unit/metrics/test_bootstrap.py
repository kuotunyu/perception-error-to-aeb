"""Contracts for the paired bootstrap over scenario tokens.

The interval this produces is what turns "corrupted collided more often" into a
statement with a magnitude and a width. Two properties make it the right
interval for this study, and both are testable.

IT IS PAIRED. Every configuration is resampled over the SAME scenario tokens in
the same draw. The configurations were run on identical scenarios, so the
between-configuration difference has no scenario-selection variance in it, and
resampling them independently would put that variance back and widen every
interval for no reason.

IT IS STRATIFIED BY FAMILY. The cohort is built with a fixed number of scenarios
per family, so a resample that ignored family would sometimes draw a sample that
was three quarters pedestrian crossings, and the interval would describe a
cohort the study never ran.

The seed and the resample count are arguments with fixed defaults rather than
constants, because a run record cites them; two runs quoting the same seed must
produce the same interval, and a test asserts it.
"""

from __future__ import annotations

from types import ModuleType

import pytest


def load_bootstrap_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.metrics import bootstrap
    except ImportError:
        pytest.fail("aebrisk.metrics.bootstrap is missing", pytrace=False)
    return bootstrap


def paired_metric(count: int = 40) -> dict[str, dict[str, float]]:
    """A cohort where the corrupted configuration is worse by exactly 0.2."""

    return {
        f"s-{index:03d}": {
            "oracle_aeb": float(index % 5) / 10.0,
            "corrupted": float(index % 5) / 10.0 + 0.2,
        }
        for index in range(count)
    }


def families(count: int = 40) -> dict[str, str]:
    names = (
        "lead_or_stopping",
        "cut_in_or_crossing",
        "pedestrian_or_crosswalk",
        "bicycle_or_vru",
    )
    return {f"s-{index:03d}": names[index % 4] for index in range(count)}


# --------------------------------------------------------------------------
# The interval
# --------------------------------------------------------------------------


def test_every_configuration_gets_an_interval() -> None:
    """One per cell, because each is reported on its own."""

    bootstrap = load_bootstrap_module()

    intervals = bootstrap.paired_scenario_bootstrap(paired_metric(), resamples=200)

    assert set(intervals) == {"oracle_aeb", "corrupted"}


def test_the_estimate_is_the_cohort_mean() -> None:
    """The point estimate is the data, not a resample average."""

    bootstrap = load_bootstrap_module()
    data = paired_metric()

    intervals = bootstrap.paired_scenario_bootstrap(data, resamples=200)

    expected = sum(row["corrupted"] for row in data.values()) / len(data)
    assert intervals["corrupted"].estimate == pytest.approx(expected)


def test_the_interval_contains_the_estimate() -> None:
    """A percentile interval that excluded its own estimate would be a bug."""

    bootstrap = load_bootstrap_module()

    interval = bootstrap.paired_scenario_bootstrap(paired_metric(), resamples=500)["corrupted"]

    assert interval.low <= interval.estimate <= interval.high


def test_a_constant_metric_has_a_degenerate_interval() -> None:
    """If every scenario gives the same value, no resample can give another."""

    bootstrap = load_bootstrap_module()
    data = {f"s-{index}": {"only": 1.5} for index in range(20)}

    interval = bootstrap.paired_scenario_bootstrap(data, resamples=200)["only"]

    assert interval.low == pytest.approx(1.5)
    assert interval.high == pytest.approx(1.5)


def test_one_scenario_and_one_resample_are_valid() -> None:
    """The smallest defined bootstrap is useful for smoke and boundary checks."""

    bootstrap = load_bootstrap_module()
    interval = bootstrap.paired_scenario_bootstrap(
        {"only-token": {"only-config": 2.5}}, resamples=1, seed=7
    )["only-config"]

    assert interval.estimate == pytest.approx(2.5)
    assert interval.low == pytest.approx(2.5)
    assert interval.high == pytest.approx(2.5)
    assert interval.resamples == 1


def test_non_degenerate_percentile_endpoints_are_hand_calculated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both tails implement the requested central confidence interval."""

    bootstrap = load_bootstrap_module()
    draws = iter(([0, 0, 0, 0], [1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3], [0, 1, 2, 3]))

    class FixedGenerator:
        def __init__(self, _bit_generator: object) -> None:
            pass

        def choice(self, _source: object, *, size: int, replace: bool = True) -> object:
            assert size == 4
            assert replace is True
            return bootstrap.np.asarray(next(draws))

    generator = FixedGenerator(None)
    draws = iter(([0, 1, 2, 3], [0, 1, 2, 3]))
    assert bootstrap.np.array_equal(
        generator.choice(range(4), size=4),
        generator.choice(range(4), size=4, replace=True),
    )
    draws = iter(([0, 0, 0, 0], [1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3], [0, 1, 2, 3]))
    monkeypatch.setattr(bootstrap.np.random, "Generator", FixedGenerator)
    data = {
        "a": {"only": 0.0},
        "b": {"only": 1.0},
        "c": {"only": 4.0},
        "d": {"only": 9.0},
    }

    interval = bootstrap.paired_scenario_bootstrap(data, resamples=5, seed=7, confidence=0.6)[
        "only"
    ]

    # The five controlled means sort to [0, 1, 3.5, 4, 9]. At the 20th and
    # 80th percentiles, linear interpolation lands at 0.8 and 5.0.
    assert interval.estimate == pytest.approx(3.5)
    assert interval.low == pytest.approx(0.8)
    assert interval.high == pytest.approx(5.0)


def test_more_variance_gives_a_wider_interval() -> None:
    """The interval has to respond to the data, or it is decoration."""

    bootstrap = load_bootstrap_module()
    tight = {f"s-{index}": {"m": 1.0 + 0.01 * (index % 2)} for index in range(40)}
    loose = {f"s-{index}": {"m": 1.0 + 5.0 * (index % 2)} for index in range(40)}

    narrow = bootstrap.paired_scenario_bootstrap(tight, resamples=400)["m"]
    wide = bootstrap.paired_scenario_bootstrap(loose, resamples=400)["m"]

    assert (wide.high - wide.low) > (narrow.high - narrow.low)


def test_the_interval_records_its_own_provenance() -> None:
    """A run record cites the seed and the resample count, so they travel with it."""

    bootstrap = load_bootstrap_module()

    interval = bootstrap.paired_scenario_bootstrap(paired_metric(), resamples=300, seed=12345)[
        "corrupted"
    ]

    assert interval.resamples == 300
    assert interval.seed == 12345
    assert interval.confidence == pytest.approx(0.95)


# --------------------------------------------------------------------------
# Determinism and pairing
# --------------------------------------------------------------------------


def test_the_same_seed_gives_the_same_interval() -> None:
    """Two runs quoting the same seed must agree, or the citation means nothing."""

    bootstrap = load_bootstrap_module()
    data = paired_metric()

    first = bootstrap.paired_scenario_bootstrap(data, resamples=200, seed=7)
    second = bootstrap.paired_scenario_bootstrap(data, resamples=200, seed=7)

    assert first == second


def test_a_different_seed_gives_a_different_interval() -> None:
    """The pair to the test above; a constant interval would satisfy it alone."""

    bootstrap = load_bootstrap_module()
    data = paired_metric()

    first = bootstrap.paired_scenario_bootstrap(data, resamples=200, seed=7)
    second = bootstrap.paired_scenario_bootstrap(data, resamples=200, seed=8)

    assert first != second


def test_the_global_random_state_is_not_touched() -> None:
    """A shared stream would make this depend on how many draws ran before it."""

    import numpy as np

    bootstrap = load_bootstrap_module()
    np.random.seed(4242)
    before = np.random.random()
    np.random.seed(4242)

    bootstrap.paired_scenario_bootstrap(paired_metric(), resamples=100)

    assert np.random.random() == before


def test_the_configurations_are_resampled_over_the_same_tokens() -> None:
    """The pairing, asserted through its consequence.

    Two configurations whose values differ by a constant must have intervals
    that differ by that same constant, because every resample sees the same
    scenarios in both. Resampling independently would break this.
    """

    bootstrap = load_bootstrap_module()

    intervals = bootstrap.paired_scenario_bootstrap(paired_metric(), resamples=400)

    assert intervals["corrupted"].low - intervals["oracle_aeb"].low == pytest.approx(0.2)
    assert intervals["corrupted"].high - intervals["oracle_aeb"].high == pytest.approx(0.2)


# --------------------------------------------------------------------------
# Stratification
# --------------------------------------------------------------------------


def test_stratifying_by_family_changes_the_interval() -> None:
    """If it did not, the stratification would be doing nothing."""

    bootstrap = load_bootstrap_module()
    data = paired_metric()

    plain = bootstrap.paired_scenario_bootstrap(data, resamples=400, seed=11)
    stratified = bootstrap.paired_scenario_bootstrap(
        data, resamples=400, seed=11, family_by_scenario=families()
    )

    assert plain["corrupted"] != stratified["corrupted"]


def test_stratification_is_deterministic_too() -> None:
    """The stratified path needs the same guarantee as the plain one."""

    bootstrap = load_bootstrap_module()
    data = paired_metric()

    first = bootstrap.paired_scenario_bootstrap(
        data, resamples=200, seed=3, family_by_scenario=families()
    )
    second = bootstrap.paired_scenario_bootstrap(
        data, resamples=200, seed=3, family_by_scenario=families()
    )

    assert first == second


def test_a_scenario_with_no_family_is_refused() -> None:
    """Silently putting it in a default stratum would unbalance the resample."""

    bootstrap = load_bootstrap_module()
    incomplete = dict(families())
    del incomplete["s-000"]

    with pytest.raises(ValueError, match=r"^no\ family\ for\ scenarios\ "):
        bootstrap.paired_scenario_bootstrap(
            paired_metric(), resamples=50, family_by_scenario=incomplete
        )


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_an_empty_cohort_is_refused() -> None:
    """There is nothing to resample, and the mean would divide by zero."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(
        ValueError,
        match=r"^(no\ family\ for\ scenarios\ |scenario\ |there\ are\ no\ scenarios\ to\ resample)",
    ):
        bootstrap.paired_scenario_bootstrap({}, resamples=100)


def test_a_scenario_missing_a_configuration_is_refused() -> None:
    """The pairing requires every configuration on every scenario.

    The common cohort guarantees it, so a gap here means the cohort was not
    applied and the comparison is no longer paired.
    """

    bootstrap = load_bootstrap_module()
    data = paired_metric(4)
    del data["s-002"]["oracle_aeb"]

    with pytest.raises(ValueError, match=r"^scenario\ "):
        bootstrap.paired_scenario_bootstrap(data, resamples=50)


def test_a_non_finite_metric_is_refused() -> None:
    """One NaN would make every percentile NaN with no indication why."""

    bootstrap = load_bootstrap_module()
    data = paired_metric(4)
    data["s-001"]["corrupted"] = float("nan")

    with pytest.raises(ValueError, match=r"^metric\ for\ "):
        bootstrap.paired_scenario_bootstrap(data, resamples=50)


@pytest.mark.parametrize("bad_value", [0, -1, 2.5, True])
def test_an_impossible_resample_count_is_refused(bad_value: object) -> None:
    """Zero resamples produce no percentiles at all."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=r"^resamples\ must\ be\ a\ positive\ integer,\ got\ "):
        bootstrap.paired_scenario_bootstrap(paired_metric(4), resamples=bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", [0.0, 1.0, -0.1, 1.5])
def test_an_impossible_confidence_is_refused(bad_value: float) -> None:
    """A confidence of one is the whole real line; of zero, a point."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(
        ValueError,
        match=r"^(confidence\ must\ be\ a\ number|confidence\ must\ lie\ strictly\ between\ 0\ and\ 1,\ got\ )",
    ):
        bootstrap.paired_scenario_bootstrap(paired_metric(4), resamples=50, confidence=bad_value)


def test_the_defaults_are_the_protocol_values() -> None:
    """5,000 resamples and seed 20260831, cited in every published interval."""

    bootstrap = load_bootstrap_module()

    assert bootstrap.DEFAULT_RESAMPLES == 5000
    assert bootstrap.DEFAULT_SEED == 20260831


def test_the_interval_is_frozen() -> None:
    """It is a published number and its provenance."""

    import dataclasses

    bootstrap = load_bootstrap_module()
    interval = bootstrap.paired_scenario_bootstrap(paired_metric(8), resamples=50)["corrupted"]

    with pytest.raises(dataclasses.FrozenInstanceError):
        interval.low = 0.0  # type: ignore[misc]


def test_an_interval_whose_bounds_are_inverted_is_refused() -> None:
    """Constructed directly elsewhere, so the type defends itself."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=r"^low\ "):
        bootstrap.BootstrapInterval(
            estimate=1.0, low=2.0, high=0.5, confidence=0.95, resamples=100, seed=1
        )


@pytest.mark.parametrize("bad_value", ["0.5", True, None])
def test_a_metric_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently be a metric of one."""

    bootstrap = load_bootstrap_module()
    data: dict = paired_metric(4)
    data["s-001"]["corrupted"] = bad_value

    with pytest.raises(ValueError, match=r"^metric for 's-001'/'corrupted' must be a number$"):
        bootstrap.paired_scenario_bootstrap(data, resamples=50)


@pytest.mark.parametrize("bad_value", ["0.95", True, None])
def test_a_confidence_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` is one, which is the whole real line."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=r"^confidence\ must\ be\ a\ number"):
        bootstrap.paired_scenario_bootstrap(
            paired_metric(4),
            resamples=50,
            confidence=bad_value,  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------
# The family-stratified log-cluster bootstrap
#
# The study resamples (family, log) clusters within each family, and every
# contrast reads the same draws. The draws are therefore returned once, as a
# draws x tokens array of multiplicities, so that any mean or ratio of sums
# over a draw is a weighted sum over the tokens.
# --------------------------------------------------------------------------


#: token: (family, log). Three clusters in "lead" and four in "crossing";
#: log-c has tokens in both families, so it forms one cluster in each.
CLUSTERED_LAYOUT = {
    "t-01": ("lead", "log-a"),
    "t-02": ("lead", "log-a"),
    "t-03": ("lead", "log-a"),
    "t-04": ("lead", "log-b"),
    "t-05": ("lead", "log-c"),
    "t-06": ("lead", "log-c"),
    "t-07": ("crossing", "log-c"),
    "t-08": ("crossing", "log-d"),
    "t-09": ("crossing", "log-e"),
    "t-10": ("crossing", "log-e"),
    "t-11": ("crossing", "log-f"),
}


def clustered_family() -> dict[str, str]:
    return {token: family for token, (family, _) in CLUSTERED_LAYOUT.items()}


def clustered_log() -> dict[str, str]:
    return {token: log for token, (_, log) in CLUSTERED_LAYOUT.items()}


def cluster_multiplicities(
    tokens: tuple[str, ...], weights: object, cluster_of: dict
) -> list[dict]:
    """Per draw, how often each cluster was drawn, read from its first token's weight."""

    import numpy as np

    by_draw: list[dict] = []
    for row in np.asarray(weights):
        seen: dict = {}
        for position, token in enumerate(tokens):
            seen.setdefault(cluster_of[token], int(row[position]))
        by_draw.append(seen)
    return by_draw


def test_each_draw_resamples_as_many_clusters_per_family_as_the_family_has() -> None:
    """Within each family the draw has the family's own number of clusters."""

    bootstrap = load_bootstrap_module()

    drawn = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log(), resamples=300, seed=5
    )

    for row in cluster_multiplicities(drawn.tokens, drawn.weights, CLUSTERED_LAYOUT):
        per_family = {"lead": 0, "crossing": 0}
        for (family, _), multiplicity in row.items():
            per_family[family] += multiplicity
        assert per_family == {"lead": 3, "crossing": 4}


def test_the_draws_actually_resample() -> None:
    """Identity weights would pass every count above, so the draws must vary."""

    bootstrap = load_bootstrap_module()

    drawn = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log(), resamples=300, seed=5
    )

    assert int(drawn.weights.max()) > 1
    assert int(drawn.weights.min()) == 0
    assert len({tuple(row) for row in drawn.weights.tolist()}) > 1


def test_with_a_single_stratum_each_draw_has_as_many_clusters_as_there_are() -> None:
    """One stratum and logs as clusters gives the unstratified whole-log bootstrap.

    log-c is then one cluster of three tokens, whatever their families.
    """

    bootstrap = load_bootstrap_module()
    one_stratum = dict.fromkeys(CLUSTERED_LAYOUT, "all")
    logs = clustered_log()

    drawn = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), one_stratum, logs, resamples=300, seed=5
    )

    for row in cluster_multiplicities(drawn.tokens, drawn.weights, logs):
        assert sum(row.values()) == 6
    position = {token: index for index, token in enumerate(drawn.tokens)}
    for row in drawn.weights:
        assert row[position["t-05"]] == row[position["t-06"]] == row[position["t-07"]]


def test_a_clusters_tokens_always_move_together() -> None:
    """A drawn cluster carries all its tokens; a token never moves alone."""

    bootstrap = load_bootstrap_module()

    drawn = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log(), resamples=300, seed=5
    )

    position = {token: index for index, token in enumerate(drawn.tokens)}
    for row in drawn.weights:
        assert row[position["t-01"]] == row[position["t-02"]] == row[position["t-03"]]
        assert row[position["t-05"]] == row[position["t-06"]]
        assert row[position["t-09"]] == row[position["t-10"]]
    # log-c is two clusters, one per family, so its tokens in different
    # families are drawn independently.
    assert any(row[position["t-05"]] != row[position["t-07"]] for row in drawn.weights)


def test_hand_controlled_cluster_draws_give_hand_calculated_weights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Families are drawn in sorted order, and clusters in sorted order within each."""

    bootstrap = load_bootstrap_module()
    picks = iter(([0, 0, 2, 2], [2, 2, 1]))

    class FixedGenerator:
        def __init__(self, _bit_generator: object) -> None:
            pass

        def choice(self, source: object, *, size: int, replace: bool = True) -> object:
            array = bootstrap.np.asarray(source)
            assert size == len(array)
            assert replace is True
            return array[next(picks)]

    monkeypatch.setattr(bootstrap.np.random, "Generator", FixedGenerator)

    drawn = bootstrap.cluster_bootstrap_weights(
        list(reversed(CLUSTERED_LAYOUT)), clustered_family(), clustered_log(), resamples=1
    )

    # "crossing" sorts first: its log-c twice and log-e twice. Then "lead": its
    # log-c twice and log-b once. log-a, log-d and log-f are not drawn.
    assert drawn.tokens == tuple(sorted(CLUSTERED_LAYOUT))
    assert drawn.weights.tolist() == [[0, 0, 0, 1, 2, 2, 2, 0, 2, 2, 0]]


def test_the_same_seed_gives_the_same_weights() -> None:
    """The draws are cited by their seed, as the released intervals are."""

    import numpy as np

    bootstrap = load_bootstrap_module()

    first = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log(), resamples=200, seed=7
    )
    second = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log(), resamples=200, seed=7
    )

    assert first.tokens == second.tokens
    assert np.array_equal(first.weights, second.weights)


def test_a_different_seed_gives_different_weights() -> None:
    """The pair to the test above; constant weights would satisfy it alone."""

    import numpy as np

    bootstrap = load_bootstrap_module()

    first = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log(), resamples=200, seed=7
    )
    second = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log(), resamples=200, seed=8
    )

    assert not np.array_equal(first.weights, second.weights)


def test_the_order_of_the_tokens_does_not_change_the_draws() -> None:
    """Tokens are sorted first, so a reordered cohort cites the same draws."""

    import numpy as np

    bootstrap = load_bootstrap_module()

    forward = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log(), resamples=100
    )
    backward = bootstrap.cluster_bootstrap_weights(
        list(reversed(CLUSTERED_LAYOUT)), clustered_family(), clustered_log(), resamples=100
    )

    assert forward.tokens == tuple(sorted(CLUSTERED_LAYOUT))
    assert backward.tokens == forward.tokens
    assert np.array_equal(forward.weights, backward.weights)
    assert forward.weights.shape == (100, len(CLUSTERED_LAYOUT))
    assert np.issubdtype(forward.weights.dtype, np.integer)


def test_cluster_weights_default_to_the_protocol_values() -> None:
    """5,000 draws from seed 20260831, as the study's statistics section states."""

    import numpy as np

    bootstrap = load_bootstrap_module()

    by_default = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log()
    )
    explicit = bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT),
        clustered_family(),
        clustered_log(),
        resamples=5000,
        seed=20260831,
    )

    assert by_default.weights.shape == (5000, len(CLUSTERED_LAYOUT))
    assert np.array_equal(by_default.weights, explicit.weights)


def test_cluster_weights_do_not_touch_the_global_random_state() -> None:
    """A shared stream would make the draws depend on what ran before them."""

    import numpy as np

    bootstrap = load_bootstrap_module()
    np.random.seed(4242)
    before = np.random.random()
    np.random.seed(4242)

    bootstrap.cluster_bootstrap_weights(
        list(CLUSTERED_LAYOUT), clustered_family(), clustered_log(), resamples=50
    )

    assert np.random.random() == before


@pytest.mark.parametrize("mapping", ["family", "cluster"])
def test_a_token_with_no_family_or_cluster_is_refused(mapping: str) -> None:
    """A default stratum or cluster would silently change every draw."""

    bootstrap = load_bootstrap_module()
    family = clustered_family()
    log = clustered_log()
    if mapping == "family":
        del family["t-04"]
    else:
        del log["t-04"]

    with pytest.raises(ValueError, match=r"^no family or cluster for tokens \['t-04'\]; "):
        bootstrap.cluster_bootstrap_weights(list(CLUSTERED_LAYOUT), family, log, resamples=10)


def test_cluster_weights_refuse_an_empty_cohort() -> None:
    """There is nothing to resample."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=r"^there are no tokens to resample$"):
        bootstrap.cluster_bootstrap_weights([], {}, {}, resamples=10)


def test_cluster_weights_refuse_a_repeated_token() -> None:
    """A token listed twice would weigh double in every draw."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=r"^tokens repeat: \['t-01'\]; "):
        bootstrap.cluster_bootstrap_weights(
            [*CLUSTERED_LAYOUT, "t-01"], clustered_family(), clustered_log(), resamples=10
        )


@pytest.mark.parametrize("bad_value", [0, -1, 2.5, True])
def test_cluster_weights_refuse_an_impossible_resample_count(bad_value: object) -> None:
    """Zero draws give no interval and no p-value."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=r"^resamples must be a positive integer, got "):
        bootstrap.cluster_bootstrap_weights(
            list(CLUSTERED_LAYOUT),
            clustered_family(),
            clustered_log(),
            resamples=bad_value,
        )


# --------------------------------------------------------------------------
# The weights as a type
# --------------------------------------------------------------------------


def test_the_weights_are_frozen_and_read_only() -> None:
    """They are shared by every contrast, so no contrast may change them."""

    import dataclasses

    import numpy as np

    bootstrap = load_bootstrap_module()
    source = np.array([[1, 2], [3, 0]])
    weights = bootstrap.BootstrapWeights(tokens=("a", "b"), weights=source)

    with pytest.raises(dataclasses.FrozenInstanceError):
        weights.tokens = ("c", "d")
    with pytest.raises(ValueError, match=r"read-only"):
        weights.weights[0, 0] = 5
    source[0, 0] = 5
    assert weights.weights.tolist() == [[1, 2], [3, 0]]


@pytest.mark.parametrize("shape", [(2,), (1, 3), (2, 0), (1, 1, 2), (0, 2)])
def test_weights_of_the_wrong_shape_are_refused(shape: tuple[int, ...]) -> None:
    """One row per draw and one column per token, with at least one draw."""

    bootstrap = load_bootstrap_module()
    weights = bootstrap.np.ones(shape, dtype=int)

    with pytest.raises(
        ValueError, match=r"^weights must be a draws x tokens array with 2 columns and at least "
    ):
        bootstrap.BootstrapWeights(tokens=("a", "b"), weights=weights)


def test_weights_with_no_token_are_refused() -> None:
    """There is nothing to average."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=r"^there are no tokens to resample$"):
        bootstrap.BootstrapWeights(tokens=(), weights=bootstrap.np.zeros((1, 0), dtype=int))


@pytest.mark.parametrize("array", [[[1.0, 1.0]], [[2, -1]], [[True, True]]])
def test_weights_that_are_not_multiplicities_are_refused(array: list[list[object]]) -> None:
    """A multiplicity counts how often a token was drawn."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=r"^weights must be non-negative integer multiplicities$"):
        bootstrap.BootstrapWeights(tokens=("a", "b"), weights=bootstrap.np.array(array))


def test_a_draw_that_carries_no_token_is_refused() -> None:
    """Its mean would divide by zero."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=r"^every draw must carry at least one token$"):
        bootstrap.BootstrapWeights(tokens=("a", "b"), weights=bootstrap.np.array([[1, 1], [0, 0]]))


# --------------------------------------------------------------------------
# Means and ratios of sums over the draws
# --------------------------------------------------------------------------


def hand_weights() -> object:
    """Draw 1 is the cohort, draw 2 is token a three times, draw 3 is b, b and c."""

    bootstrap = load_bootstrap_module()
    return bootstrap.BootstrapWeights(
        tokens=("a", "b", "c"),
        weights=bootstrap.np.array([[1, 1, 1], [3, 0, 0], [0, 2, 1]]),
    )


def test_a_weighted_mean_is_the_mean_over_each_draws_tokens() -> None:
    """Every token of the draw counts once per time it was drawn."""

    bootstrap = load_bootstrap_module()

    means = bootstrap.weighted_mean(hand_weights(), {"a": 0.0, "b": 3.0, "c": 6})

    assert means.tolist() == pytest.approx([3.0, 0.0, 4.0])


def test_a_weighted_ratio_is_a_ratio_of_sums_not_a_mean_of_ratios() -> None:
    """Rates are normalised by the drawn exposure, as the released rates are."""

    bootstrap = load_bootstrap_module()
    numerator = {"a": 1.0, "b": 2.0, "c": 3.0}
    denominator = {"a": 1.0, "b": 1.0, "c": 4.0}

    ratios = bootstrap.weighted_ratio(hand_weights(), numerator, denominator)

    # Draw 1: 6 / 6. Draw 2: 3 / 3. Draw 3: (2 + 2 + 3) / (1 + 1 + 4). The mean
    # of the per-token ratios in draw 1 would be 1.25.
    assert ratios.tolist() == pytest.approx([1.0, 1.0, 7.0 / 6.0])


@pytest.mark.parametrize(
    "values, message",
    [
        (
            {"a": 1.0, "b": 2.0},
            r"^values must hold exactly the resampled tokens; missing \['c'\], unexpected \[\]$",
        ),
        (
            {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0},
            r"^values must hold exactly the resampled tokens; missing \[\], unexpected \['d'\]$",
        ),
        ({"a": 1.0, "b": float("nan"), "c": 3.0}, r"^values for 'b' must be a finite number$"),
        ({"a": 1.0, "b": float("inf"), "c": 3.0}, r"^values for 'b' must be a finite number$"),
        ({"a": 1.0, "b": True, "c": 3.0}, r"^values for 'b' must be a finite number$"),
        ({"a": 1.0, "b": "2", "c": 3.0}, r"^values for 'b' must be a finite number$"),
    ],
)
def test_a_weighted_mean_refuses_values_that_do_not_fit_the_draws(
    values: dict[str, object], message: str
) -> None:
    """A gap or an extra token means the cohort was not the one resampled."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match=message):
        bootstrap.weighted_mean(hand_weights(), values)


def test_a_weighted_ratio_checks_both_of_its_terms() -> None:
    """Each term is named in the refusal."""

    bootstrap = load_bootstrap_module()
    complete = {"a": 1.0, "b": 1.0, "c": 1.0}

    with pytest.raises(ValueError, match=r"^numerator for 'a' must be a finite number$"):
        bootstrap.weighted_ratio(hand_weights(), {**complete, "a": float("nan")}, complete)
    with pytest.raises(ValueError, match=r"^denominator must hold exactly the resampled tokens"):
        bootstrap.weighted_ratio(hand_weights(), complete, {"a": 1.0})


def test_a_ratio_whose_denominator_sums_to_zero_in_a_draw_is_refused() -> None:
    """Draw 2 holds only token a, whose exposure here is zero."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(
        ValueError, match=r"^the denominator must sum to a positive value in every draw$"
    ):
        bootstrap.weighted_ratio(
            hand_weights(), {"a": 1.0, "b": 1.0, "c": 1.0}, {"a": 0.0, "b": 1.0, "c": 1.0}
        )


# --------------------------------------------------------------------------
# Percentile intervals and bootstrap p-values over draws
# --------------------------------------------------------------------------


def test_a_percentile_interval_matches_the_released_endpoints() -> None:
    """The five means of the released hand-calculated case, in another order."""

    bootstrap = load_bootstrap_module()

    low, high = bootstrap.percentile_interval(bootstrap.np.array([9.0, 3.5, 0.0, 4.0, 1.0]), 0.6)

    assert low == pytest.approx(0.8)
    assert high == pytest.approx(5.0)
    assert type(low) is float
    assert type(high) is float


def test_a_percentile_interval_uses_the_requested_confidence() -> None:
    """At 90% the tails are the 5th and 95th percentiles of 0..100."""

    bootstrap = load_bootstrap_module()
    draws = bootstrap.np.arange(101, dtype=float)

    assert bootstrap.percentile_interval(draws, 0.9) == pytest.approx((5.0, 95.0))
    assert bootstrap.percentile_interval(draws, 0.99) == pytest.approx((0.5, 99.5))


@pytest.mark.parametrize("bad_value", [0.0, 1.0, -0.1, 1.5, float("nan"), "0.95", True, None])
def test_a_percentile_interval_refuses_an_impossible_confidence(bad_value: object) -> None:
    """A confidence of one is the whole real line; of zero, a point."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(
        ValueError, match=r"^confidence must be a number strictly between 0 and 1, got "
    ):
        bootstrap.percentile_interval(bootstrap.np.array([1.0, 2.0]), bad_value)


@pytest.mark.parametrize("draws", [[], [[1.0, 2.0]], [1.0, float("nan")], [float("inf")]])
def test_draws_that_are_empty_or_not_finite_are_refused(draws: list[object]) -> None:
    """One NaN would make every percentile NaN with no indication why."""

    bootstrap = load_bootstrap_module()
    array = bootstrap.np.array(draws, dtype=float)

    with pytest.raises(
        ValueError, match=r"^draws must be a non-empty one-dimensional array of finite numbers$"
    ):
        bootstrap.percentile_interval(array, 0.95)
    with pytest.raises(
        ValueError, match=r"^draws must be a non-empty one-dimensional array of finite numbers$"
    ):
        bootstrap.bootstrap_p_value(array)


def test_the_smallest_bootstrap_p_value_is_two_in_five_thousand_and_one() -> None:
    """Every draw on one side gives 2 / (5,000 + 1), whichever side it is."""

    bootstrap = load_bootstrap_module()

    assert bootstrap.bootstrap_p_value(bootstrap.np.full(5000, 0.3)) == 2 / 5001
    assert bootstrap.bootstrap_p_value(bootstrap.np.full(5000, -0.3)) == 2 / 5001


def test_the_bootstrap_p_value_counts_draws_at_zero_on_both_sides() -> None:
    """p = min(1, 2 min(#{d <= 0} + 1, #{d >= 0} + 1) / (B + 1)), by hand."""

    bootstrap = load_bootstrap_module()
    np = bootstrap.np

    one_below = np.array([-1.0] + [1.0] * 9)
    two_at_zero = np.array([0.0, 0.0] + [1.0] * 8)
    two_at_zero_below = np.array([0.0, 0.0] + [-1.0] * 8)

    assert bootstrap.bootstrap_p_value(one_below) == 4 / 11
    assert bootstrap.bootstrap_p_value(two_at_zero) == 6 / 11
    assert bootstrap.bootstrap_p_value(two_at_zero_below) == 6 / 11
    assert type(bootstrap.bootstrap_p_value(one_below)) is float


def test_the_bootstrap_p_value_is_at_most_one() -> None:
    """Balanced draws would give more than one without the cap."""

    bootstrap = load_bootstrap_module()

    assert bootstrap.bootstrap_p_value(bootstrap.np.array([-2.0, -1.0, 0.0, 1.0, 2.0])) == 1.0
    assert bootstrap.bootstrap_p_value(bootstrap.np.array([-1.0, 1.0, 1.0])) == 1.0
