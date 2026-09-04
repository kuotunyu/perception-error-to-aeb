"""Contracts for the cohort every configuration is compared over.

The study's central comparison is between configurations, so the set of
scenarios they are averaged over has to be the same set. If a token were valid
in the oracle configuration and dropped in a corrupted one, the two averages
would be taken over different roads, and a difference caused by which scenarios
survived would be reported as a difference caused by perception error.

So a token that is not valid EVERYWHERE is used NOWHERE. That is deliberately
wasteful: one broken configuration discards fifty good runs. The alternative is
a comparison that cannot be defended, and a discarded scenario is visible in the
denominator while a silently unbalanced cohort is not.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest


def load_common_cohort_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.simulation import common_cohort
    except ImportError:
        pytest.fail("aebrisk.simulation.common_cohort is missing", pytrace=False)
    return common_cohort


def result(token: str, configuration: str = "oracle_aeb", *, valid: bool = True) -> Any:
    from aebrisk.artifacts.results import AEBScenarioResultV1

    return AEBScenarioResultV1(
        schema_version="aeb-scenario-result/v1",
        scenario_token=token,
        family="lead_or_stopping",
        configuration_id=configuration,
        replicate=0,
        valid=valid,
        invalid_reason=None if valid else "the simulator raised during the step phase",
        collision_vru=0,
        collision_vehicle=0,
        collision_object=0,
        collision_energy=0.0,
        min_ttc_s=2.0,
        min_clearance_m=1.0,
        missed_interventions=0,
        false_interventions=0,
        matched_delay_s=(),
        stop_distance_m=None,
        max_deceleration_mps2=3.0,
        max_abs_jerk_mps3=5.0,
        intervention_duration_s=1.0,
    )


# --------------------------------------------------------------------------
# The common cohort
# --------------------------------------------------------------------------


def test_a_token_valid_everywhere_is_in_the_cohort() -> None:
    """The base case the whole comparison rests on."""

    common_cohort = load_common_cohort_module()

    cohort = common_cohort.common_valid_scenarios(
        {
            "no_aeb": (result("s-0001", "no_aeb"),),
            "oracle_aeb": (result("s-0001", "oracle_aeb"),),
        }
    )

    assert cohort == ("s-0001",)


def test_a_token_invalid_in_one_configuration_is_used_in_none() -> None:
    """Deliberately wasteful, and the reason is the comparison itself.

    Keeping the configurations that finished would average them over a
    different set of roads than the one that failed, and the difference would
    be reported as an effect of perception error.
    """

    common_cohort = load_common_cohort_module()

    cohort = common_cohort.common_valid_scenarios(
        {
            "no_aeb": (result("s-0001", "no_aeb"),),
            "oracle_aeb": (result("s-0001", "oracle_aeb", valid=False),),
        }
    )

    assert cohort == ()


def test_a_token_missing_from_one_configuration_is_excluded() -> None:
    """Absent is not the same as valid; it was never run there."""

    common_cohort = load_common_cohort_module()

    cohort = common_cohort.common_valid_scenarios(
        {
            "no_aeb": (result("s-0001", "no_aeb"), result("s-0002", "no_aeb")),
            "oracle_aeb": (result("s-0001", "oracle_aeb"),),
        }
    )

    assert cohort == ("s-0001",)


def test_every_replicate_of_a_token_must_be_valid() -> None:
    """One broken replicate makes that token's mean incomparable."""

    common_cohort = load_common_cohort_module()
    good = result("s-0001", "corrupted")
    broken = result("s-0001", "corrupted", valid=False)

    cohort = common_cohort.common_valid_scenarios(
        {"no_aeb": (result("s-0001", "no_aeb"),), "corrupted": (good, broken)}
    )

    assert cohort == ()


def test_the_cohort_is_sorted() -> None:
    """The order reaches every stratified draw downstream, so it is not incidental."""

    common_cohort = load_common_cohort_module()
    tokens = ("s-0003", "s-0001", "s-0002")

    cohort = common_cohort.common_valid_scenarios(
        {
            "no_aeb": tuple(result(token, "no_aeb") for token in tokens),
            "oracle_aeb": tuple(result(token, "oracle_aeb") for token in reversed(tokens)),
        }
    )

    assert cohort == ("s-0001", "s-0002", "s-0003")


def test_no_configurations_produce_no_cohort() -> None:
    """An empty comparison is empty, not universal."""

    common_cohort = load_common_cohort_module()

    assert common_cohort.common_valid_scenarios({}) == ()


def test_a_single_configuration_is_still_a_cohort() -> None:
    """The degenerate case, which a set intersection gets wrong if seeded badly."""

    common_cohort = load_common_cohort_module()

    cohort = common_cohort.common_valid_scenarios(
        {"no_aeb": (result("s-0002", "no_aeb"), result("s-0001", "no_aeb"))}
    )

    assert cohort == ("s-0001", "s-0002")


def test_a_result_filed_under_the_wrong_configuration_is_refused() -> None:
    """A misfiled result would make one configuration's cohort silently another's."""

    common_cohort = load_common_cohort_module()

    with pytest.raises(ValueError, match="configuration_id"):
        common_cohort.common_valid_scenarios({"no_aeb": (result("s-0001", "oracle_aeb"),)})


# --------------------------------------------------------------------------
# The configurations themselves
# --------------------------------------------------------------------------


def configuration(**overrides: Any) -> Any:
    from aebrisk.simulation.common_cohort import ExperimentConfiguration

    fields: dict[str, Any] = {
        "configuration_id": "dropout-medium",
        "aeb_enabled": True,
        "observation_mode": "corrupted",
        "severity_by_channel": {
            "dropout": "medium",
            "localization_shape": "zero",
            "latency": "zero",
            "track_instability": "zero",
        },
        "replicate_count": 3,
    }
    fields.update(overrides)
    return ExperimentConfiguration(**fields)


def test_a_configuration_names_a_severity_for_every_channel() -> None:
    """A channel with no severity has no defined behaviour, not a default one."""

    load_common_cohort_module()

    with pytest.raises(ValueError, match="latency"):
        configuration(
            severity_by_channel={
                "dropout": "medium",
                "localization_shape": "zero",
                "track_instability": "zero",
            }
        )


def test_a_configuration_may_not_name_an_unknown_channel() -> None:
    """A typo would silently become a channel that is never applied."""

    load_common_cohort_module()

    with pytest.raises(ValueError, match="blur"):
        configuration(
            severity_by_channel={
                "dropout": "medium",
                "localization_shape": "zero",
                "latency": "zero",
                "track_instability": "zero",
                "blur": "high",
            }
        )


def test_an_unknown_severity_is_refused() -> None:
    """The four severities are the experiment; a fifth is a different study."""

    load_common_cohort_module()

    with pytest.raises(ValueError, match="severity"):
        configuration(
            severity_by_channel={
                "dropout": "extreme",
                "localization_shape": "zero",
                "latency": "zero",
                "track_instability": "zero",
            }
        )


def test_the_oracle_observation_mode_must_be_error_free() -> None:
    """An oracle carrying a non-zero severity is not an oracle.

    It is the reference every corrupted configuration is measured against, so a
    single non-zero channel there would move every reported effect by an amount
    no result could expose.
    """

    load_common_cohort_module()

    with pytest.raises(ValueError, match="oracle"):
        configuration(
            observation_mode="oracle",
            severity_by_channel={
                "dropout": "low",
                "localization_shape": "zero",
                "latency": "zero",
                "track_instability": "zero",
            },
        )


def test_an_all_zero_corrupted_configuration_is_allowed() -> None:
    """It is the pipeline's own identity check, and the matrix needs it."""

    load_common_cohort_module()

    built = configuration(
        severity_by_channel={
            "dropout": "zero",
            "localization_shape": "zero",
            "latency": "zero",
            "track_instability": "zero",
        }
    )

    assert built.observation_mode == "corrupted"


def test_an_unknown_observation_mode_is_refused() -> None:
    """There are two ways to observe this study's world, and no third."""

    load_common_cohort_module()

    with pytest.raises(ValueError, match=r"^observation_mode\ must\ be\ one\ of\ "):
        configuration(observation_mode="approximate")


@pytest.mark.parametrize("bad_value", [0, -1, True, 2.5])
def test_an_impossible_replicate_count_is_refused(bad_value: object) -> None:
    """Zero replicates would make a configuration silently contribute nothing."""

    load_common_cohort_module()

    with pytest.raises(
        ValueError, match=r"^replicate_count\ must\ be\ a\ positive\ integer,\ got\ "
    ):
        configuration(replicate_count=bad_value)


def test_an_empty_configuration_id_is_refused() -> None:
    """Every result is filed under it, so it has to name something."""

    load_common_cohort_module()

    with pytest.raises(ValueError, match="configuration_id"):
        configuration(configuration_id="")


def test_the_configuration_is_frozen() -> None:
    """It is the definition of one cell of the experiment matrix."""

    import dataclasses

    load_common_cohort_module()
    built = configuration()

    with pytest.raises(dataclasses.FrozenInstanceError):
        built.aeb_enabled = False  # type: ignore[misc]


def test_a_configuration_with_the_aeb_off_is_allowed_to_be_corrupted() -> None:
    """The no-AEB baseline still observes; it just never brakes.

    Refusing this would remove the only configuration that says what the road
    does on its own, which is what every avoided collision is counted against.
    """

    load_common_cohort_module()

    built = configuration(aeb_enabled=False)

    assert built.aeb_enabled is False
