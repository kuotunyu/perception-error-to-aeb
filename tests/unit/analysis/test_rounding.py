"""One rounding rule for displayed values, shared by the audit, the report and the figures."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from aebrisk.analysis.rounding import (
    MAX_DECIMAL_PLACES,
    fixed,
    scientific,
    significant,
    significant_places,
)


@pytest.mark.parametrize(
    ("value", "places", "expected"),
    [
        (14901.900000000001, 1, "14901.9"),
        (9.150872093023263, 1, "9.2"),
        (13.265503875969005, 1, "13.3"),
        (1032, 0, "1032"),
        (2, 2, "2.00"),
        (Decimal("2.5"), 0, "2"),
        (0.25, 1, "0.2"),
        # The decimal text of 0.35 is "0.35", so half to even gives 0.4, where
        # formatting the binary float would give 0.3. The rule is the text.
        (0.35, 1, "0.4"),
        (-0.0001, 2, "0.00"),
        (-0.027374031007751935, 4, "-0.0274"),
    ],
)
def test_fixed_rounds_the_decimal_text_half_to_even(value: Any, places: int, expected: str) -> None:
    assert fixed(value, places) == expected


@pytest.mark.parametrize("places", [-1, MAX_DECIMAL_PLACES + 1, True, 1.0])
def test_fixed_refuses_a_precision_no_marker_can_declare(places: Any) -> None:
    with pytest.raises(ValueError, match="places must be an integer from 0 to 9"):
        fixed(1.0, places)


@pytest.mark.parametrize("value", [True, "1.0", None, float("nan"), float("inf")])
def test_a_displayed_value_must_be_a_finite_number(value: Any) -> None:
    with pytest.raises(ValueError, match="a displayed value must be"):
        fixed(value, 1)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-0.027374031007751935, "-0.0274"),
        (4.0670219638242875, "4.07"),
        (-0.0021802325581395357, "-0.00218"),
        (0.0, "0.00"),
        (1234.5, "1234"),
        (1e-12, "0.000000000"),
    ],
)
def test_significant_shows_three_figures_without_dropping_integer_digits(
    value: float, expected: str
) -> None:
    assert significant(value) == expected


def test_significant_places_is_what_a_rounded_marker_declares() -> None:
    assert significant_places(4.0670219638242875, 3) == 2
    assert significant_places(-0.027374031007751935, 3) == 4
    assert significant_places(0.0, 2) == 1


@pytest.mark.parametrize("figures", [0, True, 2.0])
def test_significant_figures_must_be_a_positive_integer(figures: Any) -> None:
    with pytest.raises(ValueError, match="figures must be a positive integer"):
        significant_places(1.0, figures)
    with pytest.raises(ValueError, match="figures must be a positive integer"):
        scientific(1.0, figures)


def test_scientific_spells_an_arithmetic_residual_compactly() -> None:
    assert scientific(2.220446049250313e-16, 2) == "2.2e-16"
    assert scientific(2.6645352591003757e-15) == "2.66e-15"
