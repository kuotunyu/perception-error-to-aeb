"""Contracts for aggregating one configuration's results into reportable numbers.

Two rules run through all of this, and both are about what a reader is allowed
to conclude.

EVERY RATE CARRIES ITS NUMERATOR, ITS DENOMINATOR AND THE COHORT IT WAS TAKEN
OVER. "Three collisions per thousand scenarios" says nothing on its own; over
forty scenarios it is one collision and noise, over four thousand it is a
finding. A rate that hid its denominator would let the same number mean either.

AN UNDEFINED VALUE IS ``None``, NEVER ZERO. A cohort with no stops has no mean
stopping distance; reporting zero would say the vehicle stopped instantly. The
same for a minimum time to collision when nothing was ever on a collision
course, which is the opposite claim to zero.

The per-distance rate is refused below 100 km of simulated driving. Collisions
per 100 km computed from 4 km of driving is a number with two significant
figures of noise and an authoritative unit, which is worse than no number.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest


def load_safety_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.metrics import safety
    except ImportError:
        pytest.fail("aebrisk.metrics.safety is missing", pytrace=False)
    return safety


def result(token: str, **overrides: Any) -> Any:
    from aebrisk.artifacts.results import AEBScenarioResultV1

    fields: dict[str, Any] = {
        "schema_version": "aeb-scenario-result/v1",
        "scenario_token": token,
        "family": "lead_or_stopping",
        "configuration_id": "oracle_aeb",
        "replicate": 0,
        "valid": True,
        "invalid_reason": None,
        "collision_vru": 0,
        "collision_vehicle": 0,
        "collision_object": 0,
        "collision_energy": 0.0,
        "min_ttc_s": 2.0,
        "min_clearance_m": 1.5,
        "missed_interventions": 0,
        "false_interventions": 0,
        "matched_delay_s": (),
        "stop_distance_m": None,
        "max_deceleration_mps2": 3.0,
        "max_abs_jerk_mps3": 4.0,
        "intervention_duration_s": 1.0,
    }
    fields.update(overrides)
    return AEBScenarioResultV1(**fields)


def summarize(results: tuple, **kwargs: Any) -> Any:
    safety = load_safety_module()
    settings: dict[str, Any] = {
        "cohort": tuple(sorted({record.scenario_token for record in results})),
        "simulated_seconds": 3600.0,
    }
    settings.update(kwargs)
    return safety.summarize_configuration(results, **settings)


# --------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------


def test_collisions_are_counted_by_group() -> None:
    """A pedestrian and a bollard are not the same outcome, and never summed."""

    summary = summarize(
        (
            result("s-1", collision_vru=1, collision_energy=50_000.0),
            result("s-2", collision_vehicle=1, collision_energy=90_000.0),
            result("s-3", collision_object=1, collision_energy=10_000.0),
        )
    )

    assert summary.collisions_vru == 1
    assert summary.collisions_vehicle == 1
    assert summary.collisions_object == 1


def test_collision_energy_is_totalled() -> None:
    """Severity, not just incidence: two grazes are not one impact."""

    summary = summarize(
        (
            result("s-1", collision_vehicle=1, collision_energy=50_000.0),
            result("s-2", collision_vehicle=1, collision_energy=90_000.0),
        )
    )

    assert summary.collision_energy_total == pytest.approx(140_000.0)


def test_the_worst_case_measures_are_extremes_not_means() -> None:
    """A mean deceleration would hide the one scenario that braked hardest."""

    summary = summarize(
        (
            result("s-1", max_deceleration_mps2=2.0, max_abs_jerk_mps3=1.0),
            result("s-2", max_deceleration_mps2=5.5, max_abs_jerk_mps3=4.9),
        )
    )

    assert summary.max_deceleration_mps2 == pytest.approx(5.5)
    assert summary.max_abs_jerk_mps3 == pytest.approx(4.9)


def test_the_minimum_time_to_collision_is_the_smallest_seen() -> None:
    """The closest the vehicle came, over the whole cohort."""

    summary = summarize((result("s-1", min_ttc_s=2.0), result("s-2", min_ttc_s=0.4)))

    assert summary.min_ttc_s == pytest.approx(0.4)


def test_missed_and_false_interventions_are_totalled() -> None:
    """The study's two headline failures, counted over the cohort."""

    summary = summarize(
        (
            result("s-1", missed_interventions=2, false_interventions=1),
            result("s-2", missed_interventions=1, false_interventions=0),
        )
    )

    assert summary.missed_interventions == 3
    assert summary.false_interventions == 1


# --------------------------------------------------------------------------
# Undefined values
# --------------------------------------------------------------------------


def test_a_cohort_that_never_stopped_has_no_mean_stopping_distance() -> None:
    """Zero would say the vehicle stopped instantly, which is the opposite claim."""

    summary = summarize((result("s-1", stop_distance_m=None),))

    assert summary.mean_stop_distance_m is None


def test_a_cohort_with_stops_averages_only_those() -> None:
    """Scenarios where the vehicle never stopped contribute no distance, not zero."""

    summary = summarize(
        (
            result("s-1", stop_distance_m=10.0),
            result("s-2", stop_distance_m=None),
            result("s-3", stop_distance_m=20.0),
        )
    )

    assert summary.mean_stop_distance_m == pytest.approx(15.0)


def test_a_cohort_never_on_a_collision_course_has_no_minimum_time_to_collision() -> None:
    """``None`` and zero are opposite statements about the same cohort."""

    summary = summarize((result("s-1", min_ttc_s=None), result("s-2", min_ttc_s=None)))

    assert summary.min_ttc_s is None


def test_an_empty_cohort_produces_no_rates() -> None:
    """Dividing by zero scenarios is undefined, and undefined is not zero."""

    summary = summarize((), cohort=())

    assert summary.collisions_per_1000_scenarios.value is None
    assert summary.scenarios == 0


# --------------------------------------------------------------------------
# Rates
# --------------------------------------------------------------------------


def test_a_rate_carries_its_numerator_and_denominator() -> None:
    """ "Three per thousand" means nothing without the cohort it was taken over."""

    summary = summarize(
        tuple(result(f"s-{index}", collision_vehicle=1 if index < 3 else 0) for index in range(40))
    )
    rate = summary.collisions_per_1000_scenarios

    assert rate.numerator == 3
    assert rate.denominator == 40
    assert rate.value == pytest.approx(75.0)


def test_the_per_hour_rate_uses_the_simulated_time() -> None:
    """Two collisions in half an hour is four per hour."""

    summary = summarize(
        (result("s-1", collision_vehicle=1), result("s-2", collision_vehicle=1)),
        simulated_seconds=1800.0,
    )

    assert summary.collisions_per_hour.value == pytest.approx(4.0)


def test_the_per_distance_rate_is_refused_below_a_hundred_kilometres() -> None:
    """A rate with two significant figures of noise and an authoritative unit.

    That is worse than no number, because a reader cannot tell it apart from a
    measurement.
    """

    summary = summarize((result("s-1", collision_vehicle=1),), simulated_metres=4_000.0)

    assert summary.collisions_per_100km is None


def test_the_per_distance_rate_appears_above_the_threshold() -> None:
    """The pair to the test above, so the rate is not simply never produced."""

    summary = summarize((result("s-1", collision_vehicle=1),), simulated_metres=200_000.0)

    assert summary.collisions_per_100km is not None
    assert summary.collisions_per_100km.value == pytest.approx(0.5)


def test_no_distance_at_all_produces_no_per_distance_rate() -> None:
    """Not every caller knows how far the cohort drove."""

    summary = summarize((result("s-1"),))

    assert summary.collisions_per_100km is None


# --------------------------------------------------------------------------
# The cohort
# --------------------------------------------------------------------------


def test_only_scenarios_in_the_common_cohort_are_counted() -> None:
    """The comparison is over one set of roads, decided elsewhere."""

    summary = summarize(
        (result("s-1", collision_vehicle=1), result("s-2", collision_vehicle=1)),
        cohort=("s-1",),
    )

    assert summary.scenarios == 1
    assert summary.collisions_vehicle == 1


def test_an_invalid_result_is_not_counted() -> None:
    """It carries no measurement, so averaging it would average a zero."""

    summary = summarize(
        (
            result("s-1", collision_vehicle=1),
            result(
                "s-2",
                valid=False,
                invalid_reason="the simulator raised during the step phase",
                max_deceleration_mps2=0.0,
            ),
        ),
        cohort=("s-1", "s-2"),
    )

    assert summary.scenarios == 1


def test_the_cohort_count_is_reported_alongside_the_rates() -> None:
    """A rate taken over fewer scenarios than the cohort is a different rate."""

    summary = summarize(
        (result("s-1"),),
        cohort=("s-1", "s-2", "s-3"),
    )

    assert summary.cohort_size == 3
    assert summary.scenarios == 1


def test_several_replicates_of_one_scenario_are_all_counted() -> None:
    """Replicates are repeated measurements, not duplicates to be discarded."""

    summary = summarize(
        (
            result("s-1", replicate=0, collision_vehicle=1),
            result("s-1", replicate=1, collision_vehicle=0),
        ),
        cohort=("s-1",),
    )

    assert summary.scenarios == 2


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_results_from_two_configurations_are_refused() -> None:
    """A summary is of one cell; mixing two would report neither."""

    safety = load_safety_module()

    with pytest.raises(
        ValueError, match=r"^results span several configuration_id values .*; a summary describes"
    ):
        safety.summarize_configuration(
            (result("s-1"), result("s-2", configuration_id="no_aeb")),
            cohort=("s-1", "s-2"),
            simulated_seconds=3600.0,
        )


@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan")])
def test_an_impossible_simulated_duration_is_refused(bad_value: float) -> None:
    """The per-hour rate divides by it."""

    safety = load_safety_module()

    with pytest.raises(ValueError, match=r"^simulated_seconds must "):
        safety.summarize_configuration(
            (result("s-1"),), cohort=("s-1",), simulated_seconds=bad_value
        )


@pytest.mark.parametrize("bad_value", [-1.0, float("nan")])
def test_an_impossible_simulated_distance_is_refused(bad_value: float) -> None:
    """A negative distance means the odometry was accumulated wrongly."""

    safety = load_safety_module()

    with pytest.raises(
        ValueError,
        match=r"^(simulated_metres\ must\ be\ a\ number|simulated_metres\ must\ be\ finite\ and\ non\-negative,\ got\ )",
    ):
        safety.summarize_configuration(
            (result("s-1"),),
            cohort=("s-1",),
            simulated_seconds=3600.0,
            simulated_metres=bad_value,
        )


def test_the_summary_is_frozen() -> None:
    """It is what gets written into an artifact and read back."""

    import dataclasses

    summary = summarize((result("s-1"),))

    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.collisions_vehicle = 99  # type: ignore[misc]


def test_the_rate_is_frozen() -> None:
    """The same, for the part a reader is most likely to quote alone."""

    import dataclasses

    summary = summarize((result("s-1"),))

    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.collisions_per_hour.value = 0.0  # type: ignore[misc]


@pytest.mark.parametrize("bad_value", ["3600", True, None])
def test_a_simulated_duration_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """``True`` would silently mean one second of driving."""

    safety = load_safety_module()

    with pytest.raises(ValueError, match="simulated_seconds must be a number"):
        safety.summarize_configuration(
            (result("s-1"),),
            cohort=("s-1",),
            simulated_seconds=bad_value,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("bad_value", ["1000", True])
def test_a_simulated_distance_that_is_not_a_number_is_refused(bad_value: object) -> None:
    """The same guard on the odometry the per-distance rate divides by."""

    safety = load_safety_module()

    with pytest.raises(ValueError, match=r"^simulated_metres\ must\ be\ a\ number"):
        safety.summarize_configuration(
            (result("s-1"),),
            cohort=("s-1",),
            simulated_seconds=3600.0,
            simulated_metres=bad_value,  # type: ignore[arg-type]
        )
