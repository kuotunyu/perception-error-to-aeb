"""The cohort every configuration is compared over, and the cells of the matrix.

The study's central comparison is between configurations, so the set of
scenarios they are averaged over has to be the same set. If a token were valid
in the oracle configuration and dropped in a corrupted one, the two averages
would be taken over different roads, and a difference caused by which scenarios
survived would be reported as a difference caused by perception error.

So a token that is not valid EVERYWHERE is used NOWHERE. That is deliberately
wasteful: one broken configuration discards every good run of that token. The
alternative is a comparison that cannot be defended, and a discarded scenario is
visible in the denominator while a silently unbalanced cohort is not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aebrisk.artifacts.results import AEBScenarioResultV1, ScenarioFamily
from aebrisk.errors.pipeline import ERROR_CHANNELS, SEVERITIES
from aebrisk.nuplan_adapter.query_scenario import ScenarioReference

OBSERVATION_MODES: tuple[str, ...] = ("oracle", "corrupted")


@dataclass(frozen=True)
class CohortScenario:
    """One token of a frozen cohort, and where its recording is."""

    reference: ScenarioReference
    family: ScenarioFamily


@dataclass(frozen=True)
class ExperimentConfiguration:
    """One cell of the experiment matrix."""

    configuration_id: str
    aeb_enabled: bool
    observation_mode: str
    severity_by_channel: Mapping[str, str]
    replicate_count: int

    def __post_init__(self) -> None:
        if not self.configuration_id:
            raise ValueError("configuration_id must not be empty")
        if self.observation_mode not in OBSERVATION_MODES:
            raise ValueError(
                f"observation_mode must be one of {OBSERVATION_MODES}, "
                f"got {self.observation_mode!r}"
            )

        unknown = sorted(set(self.severity_by_channel) - set(ERROR_CHANNELS))
        if unknown:
            raise ValueError(f"unknown error channels in configuration: {unknown}")
        missing = sorted(set(ERROR_CHANNELS) - set(self.severity_by_channel))
        if missing:
            raise ValueError(
                f"configuration does not name a severity for every channel: {missing}; "
                "a channel with no severity has no defined behaviour, not a default one"
            )
        for channel, severity in self.severity_by_channel.items():
            if severity not in SEVERITIES:
                raise ValueError(
                    f"unknown severity {severity!r} for channel {channel!r}; "
                    f"the severities are exactly {SEVERITIES}"
                )

        if self.observation_mode == "oracle" and any(
            severity != "zero" for severity in self.severity_by_channel.values()
        ):
            # The oracle is the reference every corrupted configuration is
            # measured against. One non-zero channel here would move every
            # reported effect by an amount no result could expose.
            raise ValueError("an oracle configuration must carry severity 'zero' on every channel")

        if (
            isinstance(self.replicate_count, bool)
            or not isinstance(self.replicate_count, int)
            or self.replicate_count < 1
        ):
            raise ValueError(
                f"replicate_count must be a positive integer, got {self.replicate_count!r}"
            )


def common_valid_scenarios(
    results_by_config: Mapping[str, tuple[AEBScenarioResultV1, ...]],
) -> tuple[str, ...]:
    """The tokens that produced a valid result in every configuration, sorted.

    Sorted because the order reaches every stratified draw downstream, so it is
    not incidental to anything.
    """

    if not results_by_config:
        return ()

    valid_by_config: list[set[str]] = []
    for configuration_id, results in results_by_config.items():
        valid: set[str] = set()
        invalid: set[str] = set()
        for record in results:
            if record.configuration_id != configuration_id:
                raise ValueError(
                    f"result for {record.scenario_token!r} is filed under "
                    f"{configuration_id!r} but names configuration_id "
                    f"{record.configuration_id!r}"
                )
            # Every replicate must be valid: one broken replicate makes that
            # token's mean incomparable, so it is discarded like any other
            # invalid run rather than averaged over what survived.
            (valid if record.valid else invalid).add(record.scenario_token)
        valid_by_config.append(valid - invalid)

    return tuple(sorted(set.intersection(*valid_by_config)))
