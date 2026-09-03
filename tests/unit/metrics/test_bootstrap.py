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

    with pytest.raises(ValueError, match="family"):
        bootstrap.paired_scenario_bootstrap(
            paired_metric(), resamples=50, family_by_scenario=incomplete
        )


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_an_empty_cohort_is_refused() -> None:
    """There is nothing to resample, and the mean would divide by zero."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match="scenario"):
        bootstrap.paired_scenario_bootstrap({}, resamples=100)


def test_a_scenario_missing_a_configuration_is_refused() -> None:
    """The pairing requires every configuration on every scenario.

    The common cohort guarantees it, so a gap here means the cohort was not
    applied and the comparison is no longer paired.
    """

    bootstrap = load_bootstrap_module()
    data = paired_metric(4)
    del data["s-002"]["oracle_aeb"]

    with pytest.raises(ValueError, match="configuration"):
        bootstrap.paired_scenario_bootstrap(data, resamples=50)


def test_a_non_finite_metric_is_refused() -> None:
    """One NaN would make every percentile NaN with no indication why."""

    bootstrap = load_bootstrap_module()
    data = paired_metric(4)
    data["s-001"]["corrupted"] = float("nan")

    with pytest.raises(ValueError, match="finite"):
        bootstrap.paired_scenario_bootstrap(data, resamples=50)


@pytest.mark.parametrize("bad_value", [0, -1, 2.5, True])
def test_an_impossible_resample_count_is_refused(bad_value: object) -> None:
    """Zero resamples produce no percentiles at all."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match="resamples"):
        bootstrap.paired_scenario_bootstrap(paired_metric(4), resamples=bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", [0.0, 1.0, -0.1, 1.5])
def test_an_impossible_confidence_is_refused(bad_value: float) -> None:
    """A confidence of one is the whole real line; of zero, a point."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match="confidence"):
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

    with pytest.raises(ValueError, match="low"):
        bootstrap.BootstrapInterval(
            estimate=1.0, low=2.0, high=0.5, confidence=0.95, resamples=100, seed=1
        )


@pytest.mark.parametrize("bad_value", ["0.5", True, None])
def test_a_metric_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently be a metric of one."""

    bootstrap = load_bootstrap_module()
    data: dict = paired_metric(4)
    data["s-001"]["corrupted"] = bad_value

    with pytest.raises(ValueError, match="must be a number"):
        bootstrap.paired_scenario_bootstrap(data, resamples=50)


@pytest.mark.parametrize("bad_value", ["0.95", True, None])
def test_a_confidence_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` is one, which is the whole real line."""

    bootstrap = load_bootstrap_module()

    with pytest.raises(ValueError, match="confidence must be a number"):
        bootstrap.paired_scenario_bootstrap(
            paired_metric(4),
            resamples=50,
            confidence=bad_value,  # type: ignore[arg-type]
        )
