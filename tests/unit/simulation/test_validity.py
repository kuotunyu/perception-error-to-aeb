"""Contracts for the difference between a bad outcome and a broken run.

This distinction is the one most likely to be got wrong quietly, and getting it
wrong in either direction ruins the study.

A COLLISION IS A RESULT. It is the thing being measured. A simulator that
recorded a collision as an invalid scenario would drop exactly the scenarios
where perception error mattered most, and every configuration would look safer
than it is — worst in the configurations with the most error, which is the
direction that would confirm the hypothesis by construction.

AN EXCEPTION IS NOT A RESULT. A scenario that crashed in one configuration
produced no comparable outcome there, so counting the configurations that did
finish would compare different cohorts. The token is excluded from all of them.

A failure therefore has to say enough to be diagnosed later: which phase it
happened in, what was raised, and a hash of the stack. The hash rather than the
stack itself because a traceback carries absolute paths from the machine that
ran it, and those do not belong in a published artifact.
"""

from __future__ import annotations

from types import ModuleType

import pytest


def load_validity_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.simulation import validity
    except ImportError:
        pytest.fail("aebrisk.simulation.validity is missing", pytrace=False)
    return validity


def raised(message: str = "the database went away") -> Exception:
    try:
        raise RuntimeError(message)
    except RuntimeError as error:
        return error


def test_a_failure_records_the_phase_it_happened_in() -> None:
    """A crash in metrics and a crash in initialization need different fixes."""

    validity = load_validity_module()

    invalid = validity.invalid_from_exception("s-0001", "observation", raised())

    assert invalid.phase == "observation"
    assert invalid.scenario_token == "s-0001"


def test_a_failure_records_what_was_raised() -> None:
    """The type is what says whether this is a bug or a missing file."""

    validity = load_validity_module()

    invalid = validity.invalid_from_exception("s-0001", "control", raised())

    assert invalid.exception_type == "RuntimeError"


def test_a_failure_records_a_stack_hash_rather_than_the_stack() -> None:
    """A traceback carries absolute paths from the machine that ran it.

    Those do not belong in a published artifact, but two failures being the
    same failure does, so the hash is kept and the text is not.
    """

    validity = load_validity_module()

    invalid = validity.invalid_from_exception("s-0001", "control", raised())

    assert invalid.stack_sha256 is not None
    assert len(invalid.stack_sha256) == 64
    assert set(invalid.stack_sha256) <= set("0123456789abcdef")


def test_two_failures_at_the_same_place_share_a_stack_hash() -> None:
    """Otherwise the hash could not be used to group failures at all."""

    validity = load_validity_module()

    first = validity.invalid_from_exception("s-0001", "control", raised())
    second = validity.invalid_from_exception("s-0002", "control", raised())

    assert first.stack_sha256 == second.stack_sha256


def test_two_failures_at_different_places_do_not() -> None:
    """The pair to the test above; a constant hash would group everything."""

    validity = load_validity_module()

    def other() -> Exception:
        try:
            raise ValueError("a different thing entirely")
        except ValueError as error:
            return error

    first = validity.invalid_from_exception("s-0001", "control", raised())
    second = validity.invalid_from_exception("s-0001", "control", other())

    assert first.stack_sha256 != second.stack_sha256


def test_the_reason_carries_the_message() -> None:
    """A reason a reader cannot act on is barely better than none."""

    validity = load_validity_module()

    invalid = validity.invalid_from_exception("s-0001", "step", raised("map is missing"))

    assert "map is missing" in invalid.reason


def test_an_unknown_phase_is_refused() -> None:
    """The phases are a closed vocabulary, so an exclusion manifest can be grouped."""

    validity = load_validity_module()

    with pytest.raises(ValueError, match=r"^phase\ must\ be\ one\ of\ "):
        validity.invalid_from_exception("s-0001", "whenever", raised())


def test_every_declared_phase_is_accepted() -> None:
    """The pair to the test above, so the vocabulary cannot quietly shrink."""

    validity = load_validity_module()

    for phase in validity.SIMULATION_PHASES:
        assert validity.invalid_from_exception("s-0001", phase, raised()).phase == phase


def test_an_empty_scenario_token_is_refused() -> None:
    """An exclusion nobody can attribute to a scenario excludes nothing."""

    validity = load_validity_module()

    with pytest.raises(ValueError, match="scenario_token"):
        validity.invalid_from_exception("", "control", raised())


def test_a_collision_is_not_an_infrastructure_failure() -> None:
    """The rule this module exists to state, as its own test.

    Recording a collision as invalid would drop exactly the scenarios where
    perception error mattered most, and every configuration would look safer
    than it is.
    """

    validity = load_validity_module()

    assert validity.is_infrastructure_failure(None) is False


def test_an_exception_is_an_infrastructure_failure() -> None:
    """The other side of the same rule."""

    validity = load_validity_module()

    invalid = validity.invalid_from_exception("s-0001", "step", raised())

    assert validity.is_infrastructure_failure(invalid) is True


def test_the_record_is_frozen() -> None:
    """It is the evidence for why a token left the cohort."""

    import dataclasses

    validity = load_validity_module()
    invalid = validity.invalid_from_exception("s-0001", "step", raised())

    with pytest.raises(dataclasses.FrozenInstanceError):
        invalid.reason = "something else"  # type: ignore[misc]
