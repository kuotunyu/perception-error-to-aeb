"""The experiment matrix, fixed before any run and committed.

A matrix chosen after seeing results is not an experiment, so every cell here is
decided in advance. The single-channel sweep answers how much each error costs
on its own; the sixteen coalitions at medium answer whether the channels
interact, which is the only reason this study needs Shapley rather than a table
of main effects.

Two structural properties matter as much as the contents. The identifiers must
be STABLE, because every artifact files its results under them and a renamed
cell would silently look like a new experiment. And there must be NO
DUPLICATES: the singleton coalition at medium IS the single-channel medium
configuration, so giving it a second name would run the same simulation twice
and let the two copies disagree.

An imported configuration built from a `bev-calibration-lab` artifact is
deliberately NOT here. Its severities come from measured calibration error
rather than from this study's fixed grid, so including it in the sixteen
coalitions would mix two different definitions of severity inside one
attribution.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, Optional

import yaml

from aebrisk.attribution.shapley import CHANNELS
from aebrisk.errors.pipeline import configuration_id
from aebrisk.simulation.common_cohort import ExperimentConfiguration

MATRIX_PATH = Path(__file__).resolve().parents[3] / "configs" / "experiments" / "formal_v1.yaml"

#: The dose-response part of the study. Severity zero is not swept: it is the
#: baseline, and it appears once as the empty coalition.
SWEPT_SEVERITIES: tuple[str, ...] = ("low", "medium", "high")

#: The severity every coalition cell activates its channels at. Medium rather
#: than high so that an interaction is not hidden by both channels already
#: saturating the outcome on their own.
COALITION_SEVERITY = "medium"

#: Every cell runs the same number of replicates. Unequal counts would weight
#: some cells more than others in every mean taken over the matrix.
#:
#: THREE, decided from measurement rather than from taste, on 2026-09-06. A
#: 150-step run of a corrupted cell takes 5.5 s on this machine and a baseline
#: cell 1.2 s, so one token's twenty-six cells at three replicates is 403 s.
#: Over an evaluation cohort of 400 tokens that is 45 hours on one core and
#: about 6 across eight, which is the budget the plan allows; ten replicates
#: would be 150 hours and 19. The measurement is in the plan document beside the
#: profile that produced it, and the committed matrix file carries the same
#: number, which a contract test holds to this one.
REPLICATE_COUNT = 3

EMPTY_COALITION_ID = "coalition-none"


def load_experiment_matrix(path: Optional[Path] = None) -> dict[str, Any]:
    """Read the committed record of what the matrix was decided to be.

    This is not how the matrix is built; it is what was written down before any
    run, so that "the matrix was fixed in advance" is a statement a reader can
    check rather than one they have to take on trust.
    """

    source = MATRIX_PATH if path is None else path
    document: dict[str, Any] = yaml.safe_load(source.read_text(encoding="utf-8"))
    return document


def _all_zero() -> dict[str, str]:
    return dict.fromkeys(CHANNELS, "zero")


def coalition_configuration_id(coalition: frozenset[str]) -> str:
    """Name the cell that activates exactly this set of channels at medium.

    A set has no order, so the identity imposes the fixed `CHANNELS` order or
    it would not be stable. A singleton returns the single-channel medium
    identifier, because that is the same simulation under a name the project
    already uses.
    """

    unknown = sorted(coalition - set(CHANNELS))
    if unknown:
        raise ValueError(f"unknown channels in coalition: {unknown}")

    if not coalition:
        return EMPTY_COALITION_ID
    if len(coalition) == 1:
        return configuration_id(next(iter(coalition)), COALITION_SEVERITY)
    ordered = [channel for channel in CHANNELS if channel in coalition]
    return "coalition-" + "+".join(ordered)


def coalition_configurations() -> dict[frozenset[str], str]:
    """Every coalition and the cell whose results give its value."""

    return {
        frozenset(combination): coalition_configuration_id(frozenset(combination))
        for size in range(len(CHANNELS) + 1)
        for combination in itertools.combinations(CHANNELS, size)
    }


def formal_configurations() -> tuple[ExperimentConfiguration, ...]:
    """The whole matrix, in the order it is run and written."""

    configurations: list[ExperimentConfiguration] = [
        # The baselines come first so that a partial run still produces the
        # points every other cell is compared against.
        ExperimentConfiguration(
            configuration_id="no_aeb",
            aeb_enabled=False,
            observation_mode="oracle",
            severity_by_channel=_all_zero(),
            replicate_count=REPLICATE_COUNT,
        ),
        ExperimentConfiguration(
            configuration_id="oracle_aeb",
            aeb_enabled=True,
            observation_mode="oracle",
            severity_by_channel=_all_zero(),
            replicate_count=REPLICATE_COUNT,
        ),
    ]

    for channel in CHANNELS:
        for severity in SWEPT_SEVERITIES:
            severities = _all_zero()
            severities[channel] = severity
            configurations.append(
                ExperimentConfiguration(
                    configuration_id=configuration_id(channel, severity),
                    aeb_enabled=True,
                    observation_mode="corrupted",
                    severity_by_channel=severities,
                    replicate_count=REPLICATE_COUNT,
                )
            )

    emitted = {config.configuration_id for config in configurations}
    for coalition, identifier in coalition_configurations().items():
        # The four singletons at medium were emitted above under the same name,
        # which is the point: one simulation, one identity.
        if identifier in emitted:
            continue
        severities = _all_zero()
        for channel in coalition:
            severities[channel] = COALITION_SEVERITY
        configurations.append(
            ExperimentConfiguration(
                configuration_id=identifier,
                aeb_enabled=True,
                observation_mode="corrupted",
                severity_by_channel=severities,
                replicate_count=REPLICATE_COUNT,
            )
        )
        emitted.add(identifier)

    return tuple(configurations)
