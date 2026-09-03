"""Contracts for one scenario's AEB outcome record.

Every number this project publishes is an aggregate over these records, so the
model is where a physically impossible result has to be refused: a negative
collision count, a stop distance on a scenario that never stopped, or a run
marked invalid that still reports outcomes. Catching those here means the
analysis never has to ask whether its inputs made sense.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

FAMILIES = (
    "lead_or_stopping",
    "cut_in_or_crossing",
    "pedestrian_or_crosswalk",
    "bicycle_or_vru",
)


def load_results_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.artifacts import results
    except ImportError:
        pytest.fail("aebrisk.artifacts.results is missing", pytrace=False)
    return results


def valid_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "schema_version": "aeb-scenario-result/v1",
        "scenario_token": "b1c2d3e4f5a60718",
        "family": "lead_or_stopping",
        "configuration_id": "dropout-medium",
        "replicate": 0,
        "valid": True,
        "invalid_reason": None,
        "collision_vru": 0,
        "collision_vehicle": 0,
        "collision_object": 0,
        "collision_energy": 0.0,
        "min_ttc_s": 2.5,
        "min_clearance_m": 1.75,
        "missed_interventions": 0,
        "false_interventions": 1,
        "matched_delay_s": [0.2, 0.4],
        "stop_distance_m": 3.5,
        "max_deceleration_mps2": 4.5,
        "max_abs_jerk_mps3": 3.0,
        "intervention_duration_s": 1.2,
    }
    values.update(overrides)
    return values


def test_a_complete_result_validates() -> None:
    """The success path must pass or no simulation could record its outcome."""

    results = load_results_module()

    record = results.AEBScenarioResultV1.model_validate(valid_values())

    assert record.scenario_token == "b1c2d3e4f5a60718"
    assert record.matched_delay_s == (0.2, 0.4)


@pytest.mark.parametrize("family", FAMILIES)
def test_every_declared_scenario_family_is_accepted(family: str) -> None:
    """Dropping a family would silently exclude a quarter of the cohort."""

    results = load_results_module()

    record = results.AEBScenarioResultV1.model_validate(valid_values(family=family))

    assert record.family == family


def test_an_unknown_family_is_refused() -> None:
    """A family outside the frozen mapping is not part of this study."""

    results = load_results_module()

    with pytest.raises(ValidationError):
        results.AEBScenarioResultV1.model_validate(valid_values(family="highway_merge"))


def test_an_extra_field_is_refused() -> None:
    """An unversioned field would change what a published number means."""

    results = load_results_module()

    with pytest.raises(ValidationError):
        results.AEBScenarioResultV1.model_validate(valid_values(notes="looked fine"))


def test_the_model_is_frozen() -> None:
    """A result that can be edited after validation is not evidence."""

    results = load_results_module()
    record = results.AEBScenarioResultV1.model_validate(valid_values())

    with pytest.raises(ValidationError, match="frozen"):
        record.collision_vru = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "field",
    [
        "collision_vru",
        "collision_vehicle",
        "collision_object",
        "missed_interventions",
        "false_interventions",
        "replicate",
    ],
)
def test_a_negative_count_is_refused(field: str) -> None:
    """A negative count cannot happen physically and would corrupt every sum."""

    results = load_results_module()

    with pytest.raises(ValidationError):
        results.AEBScenarioResultV1.model_validate(valid_values(**{field: -1}))


@pytest.mark.parametrize(
    "field",
    ["collision_energy", "min_clearance_m", "max_abs_jerk_mps3", "intervention_duration_s"],
)
def test_a_negative_magnitude_is_refused(field: str) -> None:
    """These are magnitudes; a negative one means the simulation was wrong."""

    results = load_results_module()

    with pytest.raises(ValidationError):
        results.AEBScenarioResultV1.model_validate(valid_values(**{field: -0.1}))


@pytest.mark.parametrize(
    "field",
    [
        "collision_energy",
        "min_ttc_s",
        "min_clearance_m",
        "stop_distance_m",
        "max_deceleration_mps2",
        "max_abs_jerk_mps3",
        "intervention_duration_s",
    ],
)
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_measurement_is_refused(field: str, bad_value: float) -> None:
    """A NaN reaching the aggregate turns every statistic that touches it into NaN."""

    results = load_results_module()

    with pytest.raises(ValidationError):
        results.AEBScenarioResultV1.model_validate(valid_values(**{field: bad_value}))


def test_a_scenario_with_no_threat_may_report_no_time_to_collision() -> None:
    """``None`` means undefined here; zero would mean an imminent collision."""

    results = load_results_module()

    record = results.AEBScenarioResultV1.model_validate(valid_values(min_ttc_s=None))

    assert record.min_ttc_s is None


def test_a_scenario_that_never_stopped_may_report_no_stop_distance() -> None:
    """A scenario that keeps rolling has no stopping distance to report."""

    results = load_results_module()

    record = results.AEBScenarioResultV1.model_validate(valid_values(stop_distance_m=None))

    assert record.stop_distance_m is None


def test_a_valid_result_may_not_carry_an_invalid_reason() -> None:
    """A record that is both valid and explained-away cannot be counted either way."""

    results = load_results_module()

    with pytest.raises(ValidationError, match="invalid_reason"):
        results.AEBScenarioResultV1.model_validate(
            valid_values(valid=True, invalid_reason="ego left the route")
        )


def test_an_invalid_result_must_say_why() -> None:
    """An unexplained exclusion is indistinguishable from a silently dropped run."""

    results = load_results_module()

    with pytest.raises(ValidationError, match="invalid_reason"):
        results.AEBScenarioResultV1.model_validate(valid_values(valid=False, invalid_reason=None))


def test_an_invalid_result_with_a_reason_validates() -> None:
    """Invalidity is a recorded outcome, not a missing one."""

    results = load_results_module()

    record = results.AEBScenarioResultV1.model_validate(
        valid_values(valid=False, invalid_reason="ego left the route")
    )

    assert record.valid is False
    assert record.invalid_reason == "ego left the route"


def test_matched_delays_must_be_finite_and_non_negative() -> None:
    """A negative reaction delay would mean the AEB braked before the threat existed."""

    results = load_results_module()

    with pytest.raises(ValidationError):
        results.AEBScenarioResultV1.model_validate(valid_values(matched_delay_s=[0.2, -0.1]))


def test_matched_delays_may_be_empty() -> None:
    """A scenario where nothing needed matching reports an empty tuple, not zero."""

    results = load_results_module()

    record = results.AEBScenarioResultV1.model_validate(valid_values(matched_delay_s=[]))

    assert record.matched_delay_s == ()


def test_deceleration_is_bounded_by_the_declared_actuator_limit() -> None:
    """The protocol fixes the actuator at -6 m/s2; more than that is not this AEB."""

    results = load_results_module()

    with pytest.raises(ValidationError):
        results.AEBScenarioResultV1.model_validate(valid_values(max_deceleration_mps2=6.1))


def test_an_empty_scenario_token_is_refused() -> None:
    """A result nobody can trace back to a scenario is not evidence of anything."""

    results = load_results_module()

    with pytest.raises(ValidationError):
        results.AEBScenarioResultV1.model_validate(valid_values(scenario_token=""))


def test_an_empty_configuration_id_is_refused() -> None:
    """Every result must say which experiment configuration produced it."""

    results = load_results_module()

    with pytest.raises(ValidationError):
        results.AEBScenarioResultV1.model_validate(valid_values(configuration_id=""))


def test_the_evidence_vocabulary_matches_the_portfolio() -> None:
    """Three repositories labelling evidence differently cannot be compared."""

    results = load_results_module()

    assert results.ALLOWED_EVIDENCE_TYPES == (
        "observed",
        "derived",
        "synthetic",
        "illustrative",
    )
    assert results.ALLOWED_STATUSES == ("draft", "verified", "rejected", "superseded")


def test_the_scenario_families_match_the_frozen_mapping() -> None:
    """The four families are fixed by the protocol; adding one is a new study."""

    results = load_results_module()

    assert results.SCENARIO_FAMILIES == FAMILIES
