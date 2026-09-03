"""Contracts for cutting the two cohorts out of the candidates that passed.

Two properties carry the whole design. The selection must not depend on the
order candidates arrive in, or the cohort would change when the filesystem did.
And development and evaluation must never share a log: scenarios cut from one
log are the same road, the same traffic and often the same agents a minute
apart, so a shared log would let a choice made on development leak into the
result reported as held out.
"""

from __future__ import annotations

import random
from types import ModuleType
from typing import Any

import pytest

PROTOCOL_HASH = "a" * 64


def load_splits_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.cohort import splits
    except ImportError:
        pytest.fail("aebrisk.cohort.splits is missing", pytrace=False)
    return splits


def make_candidates(
    count: int,
    *,
    family: str = "lead_or_stopping",
    scenario_type: str = "following_lane_with_lead",
    logs: int = 8,
) -> tuple[Any, ...]:
    from aebrisk.cohort.filters import CorridorCandidate

    return tuple(
        CorridorCandidate(
            scenario_token=f"{family}-{index:05d}",
            log_name=f"log-{index % logs:03d}",
            scenario_type=scenario_type,
            family=family,
            official_split="train",
            initial_ego_speed_mps=8.0,
            oracle_enters_corridor_within_4s=True,
            oracle_min_ttc_within_4s=3.5,
        )
        for index in range(count)
    )


def test_the_development_cohort_is_capped_per_family() -> None:
    """Fifty per family is the plan's budget; more would cost simulation time."""

    splits = load_splits_module()
    candidates = make_candidates(400)

    frozen = splits.freeze_family_cohort(candidates, "development", protocol_hash=PROTOCOL_HASH)

    assert len(frozen) == 50


def test_the_evaluation_cohort_is_capped_per_family() -> None:
    """A hundred per family is the plan's budget for the held-out split."""

    splits = load_splits_module()
    candidates = make_candidates(400)

    frozen = splits.freeze_family_cohort(candidates, "evaluation", protocol_hash=PROTOCOL_HASH)

    assert len(frozen) == 100


def test_a_family_with_fewer_candidates_than_the_cap_uses_all_of_them() -> None:
    """Padding a thin family with anything would fabricate a stratum."""

    splits = load_splits_module()
    candidates = make_candidates(9, logs=1)

    frozen = splits.freeze_family_cohort(candidates, "development", protocol_hash=PROTOCOL_HASH)
    evaluation = splits.freeze_family_cohort(candidates, "evaluation", protocol_hash=PROTOCOL_HASH)

    assert len(frozen) + len(evaluation) == 9


def test_the_selection_does_not_depend_on_input_order() -> None:
    """The filesystem's order is not a scientific choice, so it must not survive."""

    splits = load_splits_module()
    candidates = list(make_candidates(300))
    shuffled = candidates[:]
    random.Random(20260903).shuffle(shuffled)

    first = splits.freeze_family_cohort(
        tuple(candidates), "development", protocol_hash=PROTOCOL_HASH
    )
    second = splits.freeze_family_cohort(
        tuple(shuffled), "development", protocol_hash=PROTOCOL_HASH
    )

    assert first == second


def test_the_selection_is_the_same_every_time() -> None:
    """A cohort that is not reproducible cannot defend a published number."""

    splits = load_splits_module()
    candidates = make_candidates(300)

    first = splits.freeze_family_cohort(candidates, "development", protocol_hash=PROTOCOL_HASH)
    second = splits.freeze_family_cohort(candidates, "development", protocol_hash=PROTOCOL_HASH)

    assert first == second


def test_a_different_protocol_selects_a_different_cohort() -> None:
    """The protocol hash is in the key so a new protocol cannot inherit a tuned split."""

    splits = load_splits_module()
    candidates = make_candidates(300)

    first = splits.freeze_family_cohort(candidates, "development", protocol_hash=PROTOCOL_HASH)
    second = splits.freeze_family_cohort(candidates, "development", protocol_hash="b" * 64)

    assert first != second


def test_the_two_cohorts_never_share_a_scenario() -> None:
    """One scenario in both splits would be tuned on and then reported as held out."""

    splits = load_splits_module()
    candidates = make_candidates(400)

    development = splits.freeze_family_cohort(
        candidates, "development", protocol_hash=PROTOCOL_HASH
    )
    evaluation = splits.freeze_family_cohort(candidates, "evaluation", protocol_hash=PROTOCOL_HASH)

    assert set(development).isdisjoint(evaluation)


def test_the_two_cohorts_never_share_a_log() -> None:
    """This is the property that makes the evaluation split actually held out.

    Scenarios from one log are minutes apart on the same road with the same
    traffic. Sharing a log would leak the development split into the evaluation
    one far more thoroughly than sharing a scenario would.
    """

    splits = load_splits_module()
    candidates = make_candidates(400, logs=12)
    by_token = {candidate.scenario_token: candidate for candidate in candidates}

    development = splits.freeze_family_cohort(
        candidates, "development", protocol_hash=PROTOCOL_HASH
    )
    evaluation = splits.freeze_family_cohort(candidates, "evaluation", protocol_hash=PROTOCOL_HASH)

    development_logs = {by_token[token].log_name for token in development}
    evaluation_logs = {by_token[token].log_name for token in evaluation}

    assert development_logs.isdisjoint(evaluation_logs)


def test_a_log_keeps_its_split_when_other_logs_are_added() -> None:
    """Freezing a larger cohort later must not reshuffle what was already frozen.

    The assignment is a property of the log and the protocol alone, so adding
    logs extends the cohort rather than redefining it.
    """

    splits = load_splits_module()
    small = make_candidates(60, logs=6)
    large = make_candidates(200, logs=20)

    def logs_for(candidates: tuple[Any, ...]) -> set[str]:
        by_token = {candidate.scenario_token: candidate for candidate in candidates}
        frozen = splits.freeze_family_cohort(candidates, "development", protocol_hash=PROTOCOL_HASH)
        return {by_token[token].log_name for token in frozen}

    small_logs = logs_for(small)
    large_logs = logs_for(large)

    assert small_logs <= large_logs


def test_each_family_is_capped_on_its_own() -> None:
    """The strata cannot borrow from each other, so each is frozen by its own call."""

    splits = load_splits_module()
    lead = make_candidates(200)
    bikes = make_candidates(200, family="bicycle_or_vru", scenario_type="behind_bike")

    frozen_lead = splits.freeze_family_cohort(lead, "development", protocol_hash=PROTOCOL_HASH)
    frozen_bikes = splits.freeze_family_cohort(bikes, "development", protocol_hash=PROTOCOL_HASH)

    assert len(frozen_lead) == 50
    assert len(frozen_bikes) == 50
    assert all(token.startswith("lead_or_stopping") for token in frozen_lead)
    assert all(token.startswith("bicycle_or_vru") for token in frozen_bikes)


def test_candidates_from_several_families_are_refused() -> None:
    """Capping a mixed input would silently apply one family's budget to several."""

    splits = load_splits_module()
    candidates = make_candidates(10) + make_candidates(
        10, family="bicycle_or_vru", scenario_type="behind_bike"
    )

    with pytest.raises(ValueError, match="family"):
        splits.freeze_family_cohort(candidates, "development", protocol_hash=PROTOCOL_HASH)


def test_an_unknown_split_name_is_refused() -> None:
    """Only two cohorts exist; a third name would silently return the wrong one."""

    splits = load_splits_module()

    with pytest.raises(ValueError, match="split"):
        splits.freeze_family_cohort(make_candidates(10), "test", protocol_hash=PROTOCOL_HASH)


def test_candidates_that_fail_the_prefilter_are_refused() -> None:
    """Freezing an unfiltered candidate would put an unmeasurable scenario in the cohort."""

    from aebrisk.cohort.filters import CorridorCandidate

    splits = load_splits_module()
    candidates = (
        *make_candidates(10),
        CorridorCandidate(
            scenario_token="too-slow",
            log_name="log-000",
            scenario_type="following_lane_with_lead",
            family="lead_or_stopping",
            official_split="train",
            initial_ego_speed_mps=0.5,
            oracle_enters_corridor_within_4s=True,
            oracle_min_ttc_within_4s=3.5,
        ),
    )

    with pytest.raises(ValueError, match="prefilter"):
        splits.freeze_family_cohort(candidates, "development", protocol_hash=PROTOCOL_HASH)


def test_an_empty_candidate_set_freezes_an_empty_cohort() -> None:
    """A family with no candidates is a fact about the data, not an error."""

    splits = load_splits_module()

    assert splits.freeze_family_cohort((), "development", protocol_hash=PROTOCOL_HASH) == ()


def test_the_frozen_cohort_is_sorted_by_the_selection_key() -> None:
    """A stable order makes two freezes byte-comparable, not merely set-equal."""

    splits = load_splits_module()
    candidates = make_candidates(300)

    frozen = splits.freeze_family_cohort(candidates, "development", protocol_hash=PROTOCOL_HASH)

    assert list(frozen) == sorted(frozen, key=lambda t: splits.selection_key(PROTOCOL_HASH, t))


def test_duplicate_scenario_tokens_are_refused() -> None:
    """One scenario listed twice would be simulated twice and counted twice."""

    splits = load_splits_module()
    candidates = make_candidates(4)

    with pytest.raises(ValueError, match="duplicate"):
        splits.freeze_family_cohort(
            candidates + candidates[:1], "development", protocol_hash=PROTOCOL_HASH
        )
