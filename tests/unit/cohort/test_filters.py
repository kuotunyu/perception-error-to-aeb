"""Contracts for deciding which scenarios are worth simulating at all.

The prefilter runs before any AEB exists, and everything it looks at is a
property of the recording: how fast the ego was going, whether anything entered
its path, and how close that came. It never looks at what an AEB did, because a
cohort selected on the outcome would answer a question nobody asked.

A candidate therefore carries evidence, not verdicts, and the model refuses any
field that looks like a simulated result.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest


def load_filters_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.cohort import filters
    except ImportError:
        pytest.fail("aebrisk.cohort.filters is missing", pytrace=False)
    return filters


def candidate_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "scenario_token": "c" * 16,
        "log_name": "2021.05.12.22.00.38_veh-35_01008_01518",
        "scenario_type": "following_lane_with_lead",
        "family": "lead_or_stopping",
        "official_split": "train",
        "initial_ego_speed_mps": 8.0,
        "oracle_enters_corridor_within_4s": True,
        "oracle_min_ttc_within_4s": 3.5,
    }
    values.update(overrides)
    return values


def make_candidate(filters: ModuleType, **overrides: Any) -> Any:
    return filters.CorridorCandidate(**candidate_values(**overrides))


def test_a_candidate_carries_the_evidence_the_prefilter_needs() -> None:
    """Each field is a property of the recording, not of any simulation."""

    filters = load_filters_module()

    candidate = make_candidate(filters)

    assert candidate.scenario_token == "c" * 16
    assert candidate.family == "lead_or_stopping"
    assert candidate.oracle_enters_corridor_within_4s is True
    assert candidate.oracle_min_ttc_within_4s == 3.5


def test_a_candidate_is_frozen() -> None:
    """A cohort whose members can be edited after freezing is not frozen."""

    import dataclasses

    filters = load_filters_module()
    candidate = make_candidate(filters)

    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.family = "bicycle_or_vru"  # type: ignore[misc]


def test_a_candidate_refuses_a_simulated_outcome_field() -> None:
    """Selecting on an outcome is how a cohort answers a question nobody asked.

    The constructor is the only place this can be stopped, because by the time a
    field like ``collided`` exists on a candidate somebody has already written
    the code that fills it in.
    """

    filters = load_filters_module()

    with pytest.raises(TypeError):
        filters.CorridorCandidate(**candidate_values(), collided=True)


def test_a_typical_scenario_passes() -> None:
    """The success path must pass or the cohort would be empty."""

    filters = load_filters_module()

    assert filters.passes_prefilter(make_candidate(filters)) is True


def test_a_scenario_whose_type_is_not_in_the_frozen_mapping_is_refused() -> None:
    """An unmapped type has no family, so it cannot be stratified or reported."""

    filters = load_filters_module()

    assert filters.passes_prefilter(make_candidate(filters, scenario_type="stationary")) is False


def test_a_type_that_disagrees_with_its_declared_family_is_refused() -> None:
    """A mislabelled family would move a scenario between strata silently."""

    filters = load_filters_module()

    assert (
        filters.passes_prefilter(
            make_candidate(filters, scenario_type="behind_bike", family="lead_or_stopping")
        )
        is False
    )


def test_the_minimum_initial_speed_passes_exactly_at_the_threshold() -> None:
    """Two metres per second is in the cohort; the boundary is inclusive."""

    filters = load_filters_module()

    assert filters.passes_prefilter(make_candidate(filters, initial_ego_speed_mps=2.0)) is True


def test_a_scenario_slower_than_the_threshold_is_refused() -> None:
    """A stationary ego cannot brake to avoid anything, so it measures nothing."""

    filters = load_filters_module()

    assert filters.passes_prefilter(make_candidate(filters, initial_ego_speed_mps=1.999)) is False


def test_a_scenario_where_nothing_enters_the_corridor_is_refused() -> None:
    """If nothing ever crosses the ego's path, no AEB decision is being tested."""

    filters = load_filters_module()

    assert (
        filters.passes_prefilter(make_candidate(filters, oracle_enters_corridor_within_4s=False))
        is False
    )


def test_a_scenario_with_no_measured_time_to_collision_is_refused() -> None:
    """``None`` means nothing was ever on a collision course within the horizon."""

    filters = load_filters_module()

    assert filters.passes_prefilter(make_candidate(filters, oracle_min_ttc_within_4s=None)) is False


def test_a_time_to_collision_exactly_at_the_limit_is_refused() -> None:
    """The bound is strict: six seconds is not close, and the plan fixes this."""

    filters = load_filters_module()

    assert filters.passes_prefilter(make_candidate(filters, oracle_min_ttc_within_4s=6.0)) is False


def test_a_time_to_collision_just_inside_the_limit_passes() -> None:
    """The pair with the test above is what pins the comparison as strict."""

    filters = load_filters_module()

    assert filters.passes_prefilter(make_candidate(filters, oracle_min_ttc_within_4s=5.999)) is True


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -1.0])
def test_an_impossible_speed_is_refused(bad_value: float) -> None:
    """A NaN or negative speed is a broken reading, not a slow scenario."""

    filters = load_filters_module()

    with pytest.raises(
        ValueError, match=r"^initial_ego_speed_mps\ must\ be\ a\ finite,\ non\-negative\ speed"
    ):
        make_candidate(filters, initial_ego_speed_mps=bad_value)


@pytest.mark.parametrize("bad_value", [float("nan"), -0.5])
def test_an_impossible_time_to_collision_is_refused(bad_value: float) -> None:
    """A negative time to collision would mean the collision already happened."""

    filters = load_filters_module()

    with pytest.raises(
        ValueError,
        match=r"^oracle_min_ttc_within_4s\ must\ be\ a\ finite,\ non\-negative\ time\ or\ None",
    ):
        make_candidate(filters, oracle_min_ttc_within_4s=bad_value)


@pytest.mark.parametrize("field", ["scenario_token", "log_name"])
def test_an_empty_identifier_is_refused(field: str) -> None:
    """A scenario nobody can name cannot be frozen into a manifest."""

    filters = load_filters_module()

    with pytest.raises(ValueError, match=rf"^{field} must not be empty$"):
        make_candidate(filters, **{field: ""})


@pytest.mark.parametrize("official_split", ["train", "val"])
def test_both_official_splits_are_accepted(official_split: str) -> None:
    """The study's own splits are cut across both; the label is recorded, not used."""

    filters = load_filters_module()

    assert make_candidate(filters, official_split=official_split).official_split == official_split


def test_an_unknown_official_split_is_refused() -> None:
    """`test` has no labels, so a candidate claiming it is a mistake worth catching."""

    filters = load_filters_module()

    with pytest.raises(ValueError, match=r"^official_split\ must\ be\ one\ of\ "):
        make_candidate(filters, official_split="test")


def test_the_family_of_every_mapped_type_can_be_looked_up() -> None:
    """The mapping is the study's stratification; every type must resolve."""

    filters = load_filters_module()

    assert filters.family_of("crossed_by_bike") == "bicycle_or_vru"
    assert filters.family_of("stopping_at_stop_sign_with_lead") == "lead_or_stopping"


def test_an_unknown_type_fails_closed_rather_than_defaulting() -> None:
    """A new devkit type silently grouped somewhere would corrupt the strata."""

    filters = load_filters_module()

    with pytest.raises(KeyError):
        filters.family_of("merging_onto_highway")


def test_every_declared_family_has_at_least_one_type() -> None:
    """An empty family would report a stratum the cohort can never fill."""

    filters = load_filters_module()

    for family in filters.SCENARIO_FAMILIES:
        assert filters.types_in_family(family), family


def test_no_type_belongs_to_two_families() -> None:
    """A type in two strata would be counted twice and weighted twice."""

    filters = load_filters_module()

    seen = [t for family in filters.SCENARIO_FAMILIES for t in filters.types_in_family(family)]

    assert len(seen) == len(set(seen))


def test_the_four_families_match_the_result_schema() -> None:
    """The cohort and the results must stratify identically or they cannot be joined."""

    from aebrisk.artifacts.results import SCENARIO_FAMILIES as RESULT_FAMILIES

    filters = load_filters_module()

    assert filters.SCENARIO_FAMILIES == RESULT_FAMILIES
