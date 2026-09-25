"""One rounding rule for every shortened number this project displays.

The published evidence keeps each value at full precision, and it stays that
way: the JSON documents, the claims registry and the report's claim appendix
all carry the exact number. A README table, the report's summary tables and the
figures show a shorter spelling, and that spelling is produced here so that it
can be recomputed. The attribution audit applies the same rule to a
``<!-- claim: <id>; rounded: N -->`` marker, which is what keeps a rounded
README value traceable to its artifact.

The rule: take the value's shortest decimal text (``str`` of the JSON number),
round it half to even to ``N`` decimal places, and spell exactly ``N`` decimals.
Rounding the decimal text rather than the binary float means a reader can
reproduce a displayed value from the JSON by hand. A negative zero is spelled
as zero.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Union

#: The widest fixed spelling a marker may declare. Nine places already exceeds
#: anything a reader can use, and a single digit keeps the marker unambiguous.
MAX_DECIMAL_PLACES = 9

Number = Union[int, float, Decimal]


def _decimal(value: Number) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"a displayed value must be a number, got {value!r}")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError(f"a displayed value must be finite, got {value!r}")
    return number


def _require_figures(figures: int) -> None:
    if isinstance(figures, bool) or not isinstance(figures, int) or figures < 1:
        raise ValueError(f"figures must be a positive integer, got {figures!r}")


def fixed(value: Number, places: int) -> str:
    """Round half to even on the decimal text and spell exactly ``places`` decimals."""

    if (
        isinstance(places, bool)
        or not isinstance(places, int)
        or not 0 <= places <= MAX_DECIMAL_PLACES
    ):
        raise ValueError(
            f"places must be an integer from 0 to {MAX_DECIMAL_PLACES}, got {places!r}"
        )
    rounded = _decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    if rounded.is_zero():
        rounded = rounded.copy_abs()
    return f"{rounded:f}"


def significant_places(value: Number, figures: int) -> int:
    """How many decimal places show ``figures`` significant figures of ``value``.

    Zero shows ``figures - 1`` places. When rounding carries into a new leading
    digit (9.996 to three figures is 10.0, not 10.00), one place fewer keeps the
    count exact. Integer digits are never rounded away, and the answer is
    clamped to what a marker can declare, so a value too small to show at this
    precision is spelled as zero rather than refused.
    """

    _require_figures(figures)
    number = _decimal(value)
    if number.is_zero():
        places = figures - 1
    else:
        places = figures - 1 - number.adjusted()
        # The rounded coefficient has at most ``figures + 1`` digits, the extra
        # one being the carry, so this precision always holds it.
        rounded = number.quantize(
            Decimal(1).scaleb(-places),
            rounding=ROUND_HALF_EVEN,
            context=Context(prec=figures + 1),
        )
        if rounded.adjusted() > number.adjusted():
            places -= 1
    return max(0, min(MAX_DECIMAL_PLACES, places))


def significant(value: Number, figures: int = 3) -> str:
    """Spell ``value`` to ``figures`` significant figures under the fixed rule."""

    return fixed(value, significant_places(value, figures))


def scientific(value: Number, figures: int = 3) -> str:
    """Spell a tiny arithmetic check, such as an efficiency residual, in E notation.

    Every zero is spelled with exponent zero (``0.0e+0`` at two figures): a zero
    has no leading digit to scale by, and ``Decimal`` would otherwise take the
    exponent from how the zero happened to be written.
    """

    _require_figures(figures)
    number = _decimal(value)
    if number.is_zero():
        return f"{Decimal(0):.{figures - 1}f}e+0"
    return f"{number:.{figures - 1}e}"
