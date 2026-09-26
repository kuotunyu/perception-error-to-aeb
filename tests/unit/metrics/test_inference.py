"""Contracts for the tests, multiplicity rules and decision rules of the policy v2 study.

Each function here turns numbers into a sentence the study will print, so each
is pinned by values a reader can check by hand: sign-flip p-values that are
powers of two, a textbook Holm example, Clopper-Pearson bounds with a closed
form, and complete truth tables for the classifications and labels that the
analysis plan in ``docs/studies/aeb-policy-v2/analysis-plan.md`` fixes before
any result exists.
"""

from __future__ import annotations

import dataclasses
from types import ModuleType

import numpy as np
import pytest


def load_inference_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.metrics import inference
    except ImportError:
        pytest.fail("aebrisk.metrics.inference is missing", pytrace=False)
    return inference


# --------------------------------------------------------------------------
# The exact paired sign-flip test over logs
# --------------------------------------------------------------------------

#: Twenty-one logs with a non-zero difference, one more than the enumeration limit.
TWENTY_ONE_LOGS = [1, 2, 3, -1, -2, 3, 1, 1, -3, 2, 1, -1, 2, 3, -2, 1, 1, 2, -1, 3, 1]


@pytest.mark.parametrize("differences", [[], [0], [0, 0, 0]])
def test_no_log_with_a_difference_gives_a_p_value_of_one(differences: list[int]) -> None:
    """m = 0: both arms agree on every log, including when both have no collision."""

    inference = load_inference_module()

    assert inference.sign_flip_p_value(differences) == 1.0


@pytest.mark.parametrize(
    "differences, expected",
    [
        ([1, 1, 1, 1], 0.125),
        ([-3, -1, -2, -1], 0.125),
        ([1, 1, 1, 1, 0, 0], 0.125),
        ([1] * 6, 0.03125),
        ([-1] * 6, 0.03125),
        ([1] * 8, 0.0078125),
        ([2, 1, 3, 1, 1, 3, 1, 2], 0.0078125),
    ],
)
def test_same_sign_logs_give_two_patterns_out_of_all(
    differences: list[int], expected: float
) -> None:
    """Only all-plus and all-minus reach the observed |sum|: p = 2 / 2^m."""

    inference = load_inference_module()

    assert inference.sign_flip_p_value(differences) == expected


@pytest.mark.parametrize(
    "differences, expected",
    [
        # Observed |3 - 1 + 2| = 4. The eight sums are 4, 0, 6, 2, -2, -6, 0 and
        # -4, so four reach it. The zero log changes nothing.
        ([3, 0, -1, 2], 0.5),
        # Observed 4. The sums of +-2 +-1 +-1 are 4, 2, 2, 0, 0, -2, -2 and -4.
        ([2, 1, 1], 0.25),
        # Observed 5. The sums of +-5 +-1 +-1 are 7, 5, 5, 3, -3, -5, -5 and -7;
        # six reach it, because a tie counts.
        ([5, -1, 1], 0.75),
        # Observed 0, which every pattern reaches.
        ([1, -1], 1.0),
    ],
)
def test_a_mixed_sign_case_matches_the_hand_count(differences: list[int], expected: float) -> None:
    """The two-sided p is the share of patterns with |sum| at least the observed."""

    inference = load_inference_module()

    assert inference.sign_flip_p_value(differences) == expected


def test_twenty_one_logs_use_reproducible_random_flips_close_to_the_exact_value() -> None:
    """Above twenty logs, 100,000 patterns are drawn; below, all are enumerated."""

    inference = load_inference_module()

    exact = inference.sign_flip_p_value(TWENTY_ONE_LOGS, enumerate_up_to=21)
    drawn = inference.sign_flip_p_value(TWENTY_ONE_LOGS)
    again = inference.sign_flip_p_value(TWENTY_ONE_LOGS)

    assert drawn == again
    assert abs(drawn - exact) <= 0.01
    # The drawn p is (1 + count) / (1 + 100,000), which the exact share of 2^21
    # patterns is not.
    count = drawn * 100_001 - 1
    assert count == pytest.approx(round(count), abs=1e-6)
    assert drawn != exact


def test_the_enumeration_limit_is_inclusive() -> None:
    """m equal to the limit is enumerated; one more log goes to random flips."""

    inference = load_inference_module()

    assert inference.sign_flip_p_value([1, 1, 1, 1], enumerate_up_to=4) == 0.125
    drawn = inference.sign_flip_p_value([1, 1, 1, 1], enumerate_up_to=3, random_flips=1000)
    assert drawn != 0.125
    assert drawn == pytest.approx(0.125, abs=0.05)
    assert (drawn * 1001) == pytest.approx(round(drawn * 1001), abs=1e-9)


def test_the_random_flips_follow_their_seed() -> None:
    """The seed is cited with the p-value, so it has to be the one used."""

    inference = load_inference_module()

    first = inference.sign_flip_p_value(TWENTY_ONE_LOGS, random_flips=2000, seed=1)
    second = inference.sign_flip_p_value(TWENTY_ONE_LOGS, random_flips=2000, seed=2)
    by_default = inference.sign_flip_p_value(TWENTY_ONE_LOGS, random_flips=2000)
    protocol = inference.sign_flip_p_value(TWENTY_ONE_LOGS, random_flips=2000, seed=20260831)

    assert first != second
    assert by_default == protocol


def test_random_flips_include_the_observed_pattern() -> None:
    """With one flip that cannot reach the observed sum, p is 1 / 2, not 0."""

    inference = load_inference_module()

    p = inference.sign_flip_p_value([1] * 30, enumerate_up_to=0, random_flips=1)

    assert p == 0.5


def test_the_sign_flip_test_does_not_touch_the_global_random_state() -> None:
    """A shared stream would make the p-value depend on what ran before it."""

    inference = load_inference_module()
    np.random.seed(4242)
    before = np.random.random()
    np.random.seed(4242)

    inference.sign_flip_p_value(TWENTY_ONE_LOGS, random_flips=100)

    assert np.random.random() == before


def test_numpy_integer_differences_are_accepted() -> None:
    """Differences summed with numpy are still whole thirds."""

    inference = load_inference_module()

    differences = [np.int64(1), np.int32(1), 1, np.int8(1)]

    assert inference.sign_flip_p_value(differences) == 0.125


@pytest.mark.parametrize("bad_value", [0.5, 1.0, True, "1", None, np.float64(1.0), np.bool_(True)])
def test_a_difference_that_is_not_a_whole_number_of_thirds_is_refused(bad_value: object) -> None:
    """Differences are counted in thirds of a collision so that ties are exact."""

    inference = load_inference_module()

    with pytest.raises(
        ValueError, match=r"^log differences must be integers \(thirds of a collision\), got "
    ):
        inference.sign_flip_p_value([1, bad_value, -1])


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({"enumerate_up_to": -1}, r"^enumerate_up_to must be an integer of at least 0, got -1$"),
        ({"enumerate_up_to": 2.0}, r"^enumerate_up_to must be an integer of at least 0, got 2.0$"),
        (
            {"enumerate_up_to": True},
            r"^enumerate_up_to must be an integer of at least 0, got True$",
        ),
        ({"random_flips": 0}, r"^random_flips must be an integer of at least 1, got 0$"),
        ({"random_flips": False}, r"^random_flips must be an integer of at least 1, got False$"),
    ],
)
def test_impossible_sign_flip_settings_are_refused(
    arguments: dict[str, object], message: str
) -> None:
    """A negative limit or zero flips has no meaning."""

    inference = load_inference_module()

    with pytest.raises(ValueError, match=message):
        inference.sign_flip_p_value([1, 1], **arguments)


# --------------------------------------------------------------------------
# Holm
# --------------------------------------------------------------------------


def test_holm_on_a_textbook_set() -> None:
    """Four p-values at 0.05: the smallest two are rejected, then the third stops it.

    Sorted: 0.005 <= 0.05/4, 0.01 <= 0.05/3, and 0.03 > 0.05/2, so H3 and
    everything after it are not rejected.
    """

    inference = load_inference_module()

    decisions = inference.holm({"H1": 0.01, "H2": 0.04, "H3": 0.03, "H4": 0.005})

    assert decisions == {
        "H4": inference.HolmDecision(rejected=True, step=1),
        "H1": inference.HolmDecision(rejected=True, step=2),
        "H3": inference.HolmDecision(rejected=False, step=3),
        "H2": inference.HolmDecision(rejected=False, step=4),
    }


def test_holm_stops_at_the_first_p_value_it_does_not_reject() -> None:
    """0.04 is below 0.05/1, but it comes after a p-value that was not rejected."""

    inference = load_inference_module()

    decisions = inference.holm({"a": 0.02, "b": 0.03, "c": 0.04})

    assert [decisions[name].rejected for name in "abc"] == [False, False, False]
    assert [decisions[name].step for name in "abc"] == [1, 2, 3]


def test_holm_rejects_a_p_value_equal_to_its_threshold() -> None:
    """The rule is "at most 0.05 / (K - i + 1)"."""

    inference = load_inference_module()

    decisions = inference.holm({"a": 0.025, "b": 0.05})

    assert decisions["a"] == inference.HolmDecision(rejected=True, step=1)
    assert decisions["b"] == inference.HolmDecision(rejected=True, step=2)


def test_holm_orders_tied_p_values_by_name() -> None:
    """Ties cannot change which are rejected, but the steps must be reproducible."""

    inference = load_inference_module()

    decisions = inference.holm({"b": 0.01, "a": 0.01, "c": 0.5})

    assert decisions["a"] == inference.HolmDecision(rejected=True, step=1)
    assert decisions["b"] == inference.HolmDecision(rejected=True, step=2)
    assert decisions["c"] == inference.HolmDecision(rejected=False, step=3)


def test_holm_uses_the_requested_family_wise_level() -> None:
    """The default level is 0.05."""

    inference = load_inference_module()

    assert inference.holm({"a": 0.02})["a"].rejected is True
    assert inference.holm({"a": 0.02}, alpha=0.01)["a"].rejected is False
    assert inference.holm({"a": np.float64(0.02)})["a"].rejected is True


def test_holm_of_an_empty_family_is_empty() -> None:
    """There is nothing to decide."""

    inference = load_inference_module()

    assert inference.holm({}) == {}


@pytest.mark.parametrize("bad_value", [-0.01, 1.01, float("nan"), True, "0.01", None])
def test_holm_refuses_a_p_value_that_is_not_a_probability(bad_value: object) -> None:
    """A p-value outside [0, 1] is a bug upstream."""

    inference = load_inference_module()

    with pytest.raises(
        ValueError, match=r"^p-value of 'b' must be a probability in \[0, 1\], got "
    ):
        inference.holm({"a": 0.01, "b": bad_value})


@pytest.mark.parametrize("bad_value", [0.0, 1.0, float("nan"), True, "0.05"])
def test_holm_refuses_an_impossible_level(bad_value: object) -> None:
    """A level of zero rejects nothing; of one, everything."""

    inference = load_inference_module()

    with pytest.raises(ValueError, match=r"^alpha must be a number strictly between 0 and 1, got "):
        inference.holm({"a": 0.01}, alpha=bad_value)


def test_a_holm_decision_is_frozen() -> None:
    """It is a published decision."""

    inference = load_inference_module()
    decision = inference.HolmDecision(rejected=True, step=1)

    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.rejected = False


# --------------------------------------------------------------------------
# Interval levels
# --------------------------------------------------------------------------


def test_the_simultaneous_confidence_is_bonferroni() -> None:
    """1 - 0.05 / K: 99% for the five primary hypotheses, 99.17% for six."""

    inference = load_inference_module()

    assert inference.simultaneous_confidence(5) == pytest.approx(0.99, abs=1e-15)
    assert inference.simultaneous_confidence(6) == pytest.approx(1 - 0.05 / 6, abs=1e-15)
    assert inference.simultaneous_confidence(1) == pytest.approx(0.95, abs=1e-15)


def test_the_holm_step_confidence_follows_the_step() -> None:
    """1 - 0.05 / (K - i + 1): Bonferroni at step 1, 95% at the last step."""

    inference = load_inference_module()

    assert inference.holm_step_confidence(5, 1) == pytest.approx(0.99, abs=1e-15)
    assert inference.holm_step_confidence(5, 2) == pytest.approx(1 - 0.05 / 4, abs=1e-15)
    assert inference.holm_step_confidence(5, 3) == pytest.approx(1 - 0.05 / 3, abs=1e-15)
    assert inference.holm_step_confidence(5, 5) == pytest.approx(0.95, abs=1e-15)
    assert inference.holm_step_confidence(1, 1) == pytest.approx(0.95, abs=1e-15)


@pytest.mark.parametrize("bad_value", [0, -1, 2.0, True])
def test_an_impossible_family_size_is_refused(bad_value: object) -> None:
    """A family has at least one hypothesis."""

    inference = load_inference_module()

    with pytest.raises(ValueError, match=r"^k must be an integer of at least 1, got "):
        inference.simultaneous_confidence(bad_value)
    with pytest.raises(ValueError, match=r"^k must be an integer of at least 1, got "):
        inference.holm_step_confidence(bad_value, 1)


@pytest.mark.parametrize("bad_value", [0, 6, 2.0, True])
def test_a_step_outside_the_family_is_refused(bad_value: object) -> None:
    """Steps run from 1 to K."""

    inference = load_inference_module()

    with pytest.raises(ValueError, match=r"^step must be an integer from 1 to k = 5, got "):
        inference.holm_step_confidence(5, bad_value)


# --------------------------------------------------------------------------
# Clopper-Pearson
# --------------------------------------------------------------------------


def test_zero_events_in_344_tokens_give_the_closed_form_upper_bound() -> None:
    """For zero events the exact interval is [0, 1 - 0.025^(1/n)], [0, 0.0107] here."""

    inference = load_inference_module()

    low, high = inference.clopper_pearson(0, 344)

    assert low == 0.0
    assert abs(high - (1 - 0.025 ** (1 / 344))) <= 1e-15
    assert round(high, 4) == 0.0107


def test_344_events_in_344_tokens_mirror_the_zero_case() -> None:
    """For n events in n trials the interval is [0.025^(1/n), 1]."""

    inference = load_inference_module()

    low, high = inference.clopper_pearson(344, 344)

    assert abs(low - 0.025 ** (1 / 344)) <= 1e-15
    assert high == 1.0


def test_clopper_pearson_matches_a_literal_scipy_reference() -> None:
    """5 events in 100, computed once with scipy 1.13.1's beta distribution."""

    inference = load_inference_module()

    low, high = inference.clopper_pearson(5, 100)

    assert low == pytest.approx(0.016431879182052155, abs=1e-15)
    assert high == pytest.approx(0.11283491110546276, abs=1e-15)
    assert type(low) is float
    assert type(high) is float


def test_clopper_pearson_uses_the_requested_confidence() -> None:
    """At 90% the zero-event bound is 1 - 0.05^(1/n)."""

    inference = load_inference_module()

    low, high = inference.clopper_pearson(0, 10, confidence=0.9)

    assert low == 0.0
    assert high == pytest.approx(1 - 0.05 ** (1 / 10), abs=1e-15)


@pytest.mark.parametrize(
    "successes, trials, message",
    [
        (5, 4, r"^successes must be an integer from 0 to trials = 4, got 5$"),
        (-1, 4, r"^successes must be an integer from 0 to trials = 4, got -1$"),
        (1.0, 4, r"^successes must be an integer from 0 to trials = 4, got 1.0$"),
        (True, 4, r"^successes must be an integer from 0 to trials = 4, got True$"),
        (0, 0, r"^trials must be an integer of at least 1, got 0$"),
        (0, 4.0, r"^trials must be an integer of at least 1, got 4.0$"),
        (0, True, r"^trials must be an integer of at least 1, got True$"),
    ],
)
def test_clopper_pearson_refuses_impossible_counts(
    successes: object, trials: object, message: str
) -> None:
    """Counts are whole and the successes cannot exceed the trials."""

    inference = load_inference_module()

    with pytest.raises(ValueError, match=message):
        inference.clopper_pearson(successes, trials)


@pytest.mark.parametrize("bad_value", [0.0, 1.0, float("nan"), True, "0.95"])
def test_clopper_pearson_refuses_an_impossible_confidence(bad_value: object) -> None:
    """A confidence of one is [0, 1] whatever the data."""

    inference = load_inference_module()

    with pytest.raises(
        ValueError, match=r"^confidence must be a number strictly between 0 and 1, got "
    ):
        inference.clopper_pearson(0, 10, confidence=bad_value)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "estimate, rejected, predicted_sign, expected",
    [
        # Directional, predicted decrease.
        (-0.2, True, -1, "supported"),
        (-1e-300, True, -1, "supported"),
        (0.2, True, -1, "contradicted"),
        (1e-300, True, -1, "contradicted"),
        (0.0, True, -1, "not_established"),
        (-0.2, False, -1, "not_established"),
        (0.2, False, -1, "not_established"),
        (0.0, False, -1, "not_established"),
        # Directional, predicted increase.
        (0.2, True, 1, "supported"),
        (1e-300, True, 1, "supported"),
        (-0.2, True, 1, "contradicted"),
        (-1e-300, True, 1, "contradicted"),
        (0.0, True, 1, "not_established"),
        (0.2, False, 1, "not_established"),
        (-0.2, False, 1, "not_established"),
        # Two-sided.
        (0.1, True, 0, "increase"),
        (1e-300, True, 0, "increase"),
        (-0.1, True, 0, "decrease"),
        (-1e-300, True, 0, "decrease"),
        (0.0, True, 0, "not_established"),
        (0.1, False, 0, "not_established"),
        (-0.1, False, 0, "not_established"),
        (0.0, False, 0, "not_established"),
    ],
)
def test_the_classification_truth_table(
    estimate: float, rejected: bool, predicted_sign: int, expected: str
) -> None:
    """Supported or contradicted only when Holm rejects; otherwise not established."""

    inference = load_inference_module()

    assert inference.classify(estimate, rejected, predicted_sign) == expected


def test_a_numpy_rejection_is_accepted() -> None:
    """A decision computed with numpy is still a decision."""

    inference = load_inference_module()

    assert inference.classify(np.float64(-0.3), np.bool_(True), -1) == "supported"


@pytest.mark.parametrize(
    "estimate, rejected, predicted_sign, message",
    [
        (0.1, True, 2, r"^predicted_sign must be -1, 0 or 1, got 2$"),
        (0.1, True, True, r"^predicted_sign must be -1, 0 or 1, got True$"),
        (0.1, True, 1.0, r"^predicted_sign must be -1, 0 or 1, got 1.0$"),
        (0.1, True, "1", r"^predicted_sign must be -1, 0 or 1, got '1'$"),
        (float("nan"), True, 1, r"^estimate must be a finite number, got nan$"),
        (float("-inf"), True, 1, r"^estimate must be a finite number, got -inf$"),
        (True, True, 1, r"^estimate must be a finite number, got True$"),
        ("0.1", True, 1, r"^estimate must be a finite number, got '0.1'$"),
        (0.1, 1, 1, r"^rejected must be a bool, got 1$"),
        (0.1, None, 1, r"^rejected must be a bool, got None$"),
    ],
)
def test_classify_refuses_impossible_inputs(
    estimate: object, rejected: object, predicted_sign: object, message: str
) -> None:
    """A wrong type would silently fall through to "not established"."""

    inference = load_inference_module()

    with pytest.raises(ValueError, match=message):
        inference.classify(estimate, rejected, predicted_sign)


# --------------------------------------------------------------------------
# The Q1 label
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "h1, h2, estimate_h1, estimate_h2, expected",
    [
        ("supported", "supported", -0.2, -0.3, "support"),
        # The -0.10 boundary is inclusive.
        ("supported", "supported", -0.10, -0.10, "support"),
        ("supported", "supported", -0.10, -0.0999, "partial_support"),
        ("supported", "supported", -0.0999, -0.10, "partial_support"),
        ("supported", "supported", -0.05, -0.3, "partial_support"),
        ("supported", "not_established", -0.3, -0.3, "partial_support"),
        ("not_established", "supported", -0.3, -0.3, "partial_support"),
        ("supported", "contradicted", -0.3, 0.2, "no_support"),
        ("contradicted", "supported", 0.2, -0.3, "no_support"),
        ("not_established", "not_established", -0.3, -0.3, "no_support"),
        ("contradicted", "contradicted", 0.2, 0.2, "no_support"),
        ("not_established", "contradicted", -0.3, 0.2, "no_support"),
        ("contradicted", "not_established", 0.2, -0.3, "no_support"),
    ],
)
def test_the_q1_label_truth_table(
    h1: str, h2: str, estimate_h1: float, estimate_h2: float, expected: str
) -> None:
    """Support needs both supported at or below -0.10; partial needs one and no contradiction."""

    inference = load_inference_module()

    assert inference.q1_label(h1, h2, estimate_h1, estimate_h2) == expected


def test_the_q1_threshold_is_an_argument_with_the_plan_default() -> None:
    """Ten percentage points of simulated time unless stated otherwise."""

    inference = load_inference_module()

    assert inference.q1_label("supported", "supported", -0.15, -0.15) == "support"
    assert (
        inference.q1_label("supported", "supported", -0.15, -0.15, threshold=-0.2)
        == "partial_support"
    )
    assert inference.q1_label("supported", "supported", -0.05, -0.05, threshold=-0.05) == "support"


@pytest.mark.parametrize(
    "arguments, message",
    [
        (
            ("increase", "supported", -0.2, -0.2),
            r"^h1 must be one of \('supported', 'contradicted', 'not_established'\), got 'increase'$",
        ),
        (
            ("supported", "support", -0.2, -0.2),
            r"^h2 must be one of \('supported', 'contradicted', 'not_established'\), got 'support'$",
        ),
        (("supported", "supported", float("nan"), -0.2), r"^estimate_h1 must be a finite number"),
        (("supported", "supported", -0.2, None), r"^estimate_h2 must be a finite number"),
    ],
)
def test_the_q1_label_refuses_impossible_inputs(arguments: tuple, message: str) -> None:
    """Only directional classifications and finite estimates enter the label."""

    inference = load_inference_module()

    with pytest.raises(ValueError, match=message):
        inference.q1_label(*arguments)


def test_the_q1_label_refuses_an_impossible_threshold() -> None:
    """A NaN threshold would make Support unreachable without saying so."""

    inference = load_inference_module()

    with pytest.raises(ValueError, match=r"^threshold must be a finite number, got nan$"):
        inference.q1_label("supported", "supported", -0.2, -0.2, threshold=float("nan"))


# --------------------------------------------------------------------------
# The H5 qualifier
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "p_unadjusted, estimate, expected",
    [
        (0.049, 0.1, True),
        (0.0, 1e-300, True),
        (0.04999999, 0.5, True),
        # p = 0.05 is not below 0.05.
        (0.05, 0.1, False),
        # An estimate of zero is not above zero.
        (0.049, 0.0, False),
        (0.049, -0.1, False),
        (0.2, 0.1, False),
        (0.2, -0.1, False),
        (1.0, 0.0, False),
    ],
)
def test_the_h5_qualifier_truth_table(p_unadjusted: float, estimate: float, expected: bool) -> None:
    """More oracle collisions, flagged on the unadjusted p below 0.05."""

    inference = load_inference_module()

    assert inference.h5_qualifier(p_unadjusted, estimate) is expected


@pytest.mark.parametrize(
    "p_unadjusted, estimate, message",
    [
        (float("nan"), 0.1, r"^p_unadjusted must be a probability in \[0, 1\], got nan$"),
        (1.5, 0.1, r"^p_unadjusted must be a probability in \[0, 1\], got 1.5$"),
        (-0.1, 0.1, r"^p_unadjusted must be a probability in \[0, 1\], got -0.1$"),
        (0.01, float("inf"), r"^estimate must be a finite number, got inf$"),
    ],
)
def test_the_h5_qualifier_refuses_impossible_inputs(
    p_unadjusted: float, estimate: float, message: str
) -> None:
    """A NaN p would silently leave the qualifier off."""

    inference = load_inference_module()

    with pytest.raises(ValueError, match=message):
        inference.h5_qualifier(p_unadjusted, estimate)
