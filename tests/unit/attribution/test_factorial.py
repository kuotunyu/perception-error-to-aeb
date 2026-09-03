"""Contracts for the experiment matrix, which is fixed before any run.

Every cell here is decided in advance and committed, because a matrix chosen
after seeing results is not an experiment. The single-channel sweep answers how
much each error costs on its own; the sixteen coalitions at medium answer
whether the channels interact, which is the only reason the study needs Shapley
rather than a table of main effects.

Two structural properties matter as much as the contents. The IDs must be stable,
because every artifact files its results under them and a renamed cell would
silently look like a new experiment. And there must be no duplicates: the
singleton coalition at medium IS the single-channel medium configuration, so
giving it a second name would run the same simulation twice and let the two
copies disagree.
"""

from __future__ import annotations

import itertools
from types import ModuleType

import pytest


def load_factorial_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.attribution import factorial
    except ImportError:
        pytest.fail("aebrisk.attribution.factorial is missing", pytrace=False)
    return factorial


def configurations() -> tuple:
    return load_factorial_module().formal_configurations()


def by_id() -> dict:
    return {config.configuration_id: config for config in configurations()}


# --------------------------------------------------------------------------
# What is in the matrix
# --------------------------------------------------------------------------


def test_the_channels_are_the_four_the_protocol_freezes() -> None:
    """Adding a fifth is a new study, and the tuple order fixes every enumeration."""

    factorial = load_factorial_module()

    assert factorial.CHANNELS == (
        "dropout",
        "localization_shape",
        "latency",
        "track_instability",
    )


def test_the_matrix_contains_the_no_aeb_baseline() -> None:
    """Every avoided collision is counted against what the road does on its own."""

    config = by_id()["no_aeb"]

    assert config.aeb_enabled is False
    assert config.observation_mode == "oracle"


def test_the_matrix_contains_the_oracle_aeb_reference() -> None:
    """The ceiling: what this AEB achieves with perfect perception."""

    config = by_id()["oracle_aeb"]

    assert config.aeb_enabled is True
    assert config.observation_mode == "oracle"
    assert set(config.severity_by_channel.values()) == {"zero"}


def test_the_matrix_contains_the_all_zero_corrupted_baseline() -> None:
    """The empty coalition, and the pipeline's own identity check.

    Without it the Shapley baseline would have to be the oracle, and any
    difference the pipeline itself introduced at severity zero would be
    attributed to the channels.
    """

    config = by_id()["coalition-none"]

    assert config.observation_mode == "corrupted"
    assert set(config.severity_by_channel.values()) == {"zero"}


@pytest.mark.parametrize(
    "channel",
    ["dropout", "localization_shape", "latency", "track_instability"],
)
@pytest.mark.parametrize("severity", ["low", "medium", "high"])
def test_every_single_channel_cell_is_present(channel: str, severity: str) -> None:
    """Four channels at three severities: the dose-response part of the study."""

    config = by_id()[f"{channel}-{severity}"]

    assert config.severity_by_channel[channel] == severity
    assert all(
        value == "zero" for name, value in config.severity_by_channel.items() if name != channel
    )


def test_every_binary_coalition_at_medium_is_present() -> None:
    """All sixteen, because an exact Shapley value needs every one of them."""

    factorial = load_factorial_module()
    known = by_id()

    for size in range(len(factorial.CHANNELS) + 1):
        for combination in itertools.combinations(factorial.CHANNELS, size):
            identifier = factorial.coalition_configuration_id(frozenset(combination))
            assert identifier in known, identifier
            config = known[identifier]
            for channel in factorial.CHANNELS:
                expected = "medium" if channel in combination else "zero"
                assert config.severity_by_channel[channel] == expected


def test_the_singleton_coalition_is_the_single_channel_medium_cell() -> None:
    """One simulation, one name. Two names would let two copies disagree."""

    factorial = load_factorial_module()

    identifier = factorial.coalition_configuration_id(frozenset({"latency"}))

    assert identifier == "latency-medium"


def test_the_grand_coalition_names_its_channels_in_the_fixed_order() -> None:
    """A set has no order, so the identity has to impose one or it is not stable."""

    factorial = load_factorial_module()

    identifier = factorial.coalition_configuration_id(frozenset(factorial.CHANNELS))

    assert identifier == ("coalition-dropout+localization_shape+latency+track_instability")


def test_the_coalition_identity_does_not_depend_on_insertion_order() -> None:
    """Built from a set, so nothing about how it was built may reach the name."""

    factorial = load_factorial_module()

    first = factorial.coalition_configuration_id(frozenset({"latency", "dropout"}))
    second = factorial.coalition_configuration_id(frozenset({"dropout", "latency"}))

    assert first == second == "coalition-dropout+latency"


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_the_matrix_has_no_duplicate_identifiers() -> None:
    """Two cells with the same name would overwrite each other in every artifact."""

    identifiers = [config.configuration_id for config in configurations()]

    assert len(identifiers) == len(set(identifiers))


def test_the_matrix_has_no_duplicate_contents() -> None:
    """Two names for the same simulation would run it twice and let them disagree."""

    signatures = [
        (
            config.aeb_enabled,
            config.observation_mode,
            tuple(sorted(config.severity_by_channel.items())),
        )
        for config in configurations()
    ]

    assert len(signatures) == len(set(signatures))


def test_the_matrix_is_the_expected_size() -> None:
    """Two baselines, twelve single-channel cells, and twelve more coalitions.

    Twelve rather than sixteen because the four singletons at medium are the
    single-channel medium cells already counted.
    """

    assert len(configurations()) == 26


def test_the_order_is_stable_across_calls() -> None:
    """Results are written and compared in this order, so it is not incidental."""

    first = [config.configuration_id for config in configurations()]
    second = [config.configuration_id for config in configurations()]

    assert first == second


def test_the_baselines_come_first() -> None:
    """A partial run should produce the comparison points before the sweep."""

    identifiers = [config.configuration_id for config in configurations()]

    assert identifiers[:2] == ["no_aeb", "oracle_aeb"]


def test_every_configuration_names_a_severity_for_every_channel() -> None:
    """Enforced by the type, and asserted here so the matrix cannot skip one."""

    factorial = load_factorial_module()

    for config in configurations():
        assert set(config.severity_by_channel) == set(factorial.CHANNELS)


def test_every_configuration_declares_the_same_replicate_count() -> None:
    """Unequal replicates would weight some cells more than others in every mean."""

    counts = {config.replicate_count for config in configurations()}

    assert len(counts) == 1


def test_only_the_two_baselines_observe_the_oracle() -> None:
    """Every measured cell goes through the pipeline, including the zero one."""

    oracle_cells = [
        config.configuration_id
        for config in configurations()
        if config.observation_mode == "oracle"
    ]

    assert oracle_cells == ["no_aeb", "oracle_aeb"]


def test_only_the_no_aeb_baseline_has_the_aeb_off() -> None:
    """Everything else is measuring the AEB, so the AEB has to be running."""

    without = [config.configuration_id for config in configurations() if not config.aeb_enabled]

    assert without == ["no_aeb"]


# --------------------------------------------------------------------------
# The coalition values the Shapley computation reads
# --------------------------------------------------------------------------


def test_the_coalition_configurations_map_back_to_their_coalitions() -> None:
    """The Shapley step reads results by coalition, so the map must round trip."""

    factorial = load_factorial_module()

    for size in range(len(factorial.CHANNELS) + 1):
        for combination in itertools.combinations(factorial.CHANNELS, size):
            coalition = frozenset(combination)
            identifier = factorial.coalition_configuration_id(coalition)
            assert factorial.coalition_configurations()[coalition] == identifier


def test_there_are_sixteen_coalitions() -> None:
    """Two to the fourth, and an exact Shapley value needs all of them."""

    factorial = load_factorial_module()

    assert len(factorial.coalition_configurations()) == 16


def test_an_unknown_channel_in_a_coalition_is_refused() -> None:
    """A typo would produce an identity for a cell that was never run."""

    factorial = load_factorial_module()

    with pytest.raises(ValueError, match="unknown"):
        factorial.coalition_configuration_id(frozenset({"blur"}))


# --------------------------------------------------------------------------
# Imported configurations
# --------------------------------------------------------------------------


def test_the_committed_matrix_matches_the_generated_one_exactly() -> None:
    """The file is the record of what was decided; the code is how it is built.

    Asserted in both directions and in order, so neither a cell added in code
    nor one quietly dropped from the file can pass. This is what makes "the
    matrix was fixed before any run" a checkable statement rather than a claim.
    """

    factorial = load_factorial_module()

    declared = factorial.load_experiment_matrix()

    assert declared["configurations"] == [config.configuration_id for config in configurations()]
    assert declared["channels"] == list(factorial.CHANNELS)
    assert declared["swept_severities"] == list(factorial.SWEPT_SEVERITIES)
    assert declared["coalition_severity"] == factorial.COALITION_SEVERITY
    assert declared["replicate_count"] == factorial.REPLICATE_COUNT


def test_the_committed_matrix_names_its_schema_version() -> None:
    """A result must say which matrix shape produced it."""

    factorial = load_factorial_module()

    assert factorial.load_experiment_matrix()["schema_version"] == "aeb-experiment-matrix/v1"


def test_an_imported_configuration_is_not_part_of_the_factorial() -> None:
    """A cell built from another project's artifact is a separate question.

    Its severities come from measured calibration error rather than from this
    study's fixed grid, so including it in the sixteen coalitions would mix two
    different definitions of severity inside one attribution.
    """

    load_factorial_module()

    identifiers = {config.configuration_id for config in configurations()}
    imported = [name for name in identifiers if name.startswith("calibration_imported_")]

    assert imported == []
