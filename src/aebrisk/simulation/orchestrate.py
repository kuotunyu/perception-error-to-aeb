"""Running a frozen cohort through the matrix, and writing what came back.

The pieces below this one each do one thing: the manifest says which scenarios,
the adapter reads one of them, the runner drives a token through every
configuration. This module is the join, and it exists to make two failures
impossible rather than unlikely.

A TOKEN THE MANIFEST NAMES AND THE DATA DOES NOT HAVE IS A REFUSAL. The cohort
is resolved against the logs the manifest itself names, before anything is
simulated, and a token that cannot be found there stops the run with its name in
the message. Silently running the tokens that resolved would publish a number
measured over a cohort nobody chose, and the manifest hash would still match.

RESULTS ARE WRITTEN ONLY FOR THE COHORT THAT WAS ASKED FOR. The token set of
what is about to be written is compared with the manifest's before a byte is
written, so a run that drifted — a filter changed, a log re-read, a resume that
lost half the list — cannot leave an artifact that looks complete.

The oracle configuration is always part of a run even when one cell is under
test, because missed and false interventions are defined by comparison with it
and by nothing else. Its records are written only by its own run, so no cell's
directory is written twice.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field

from aebrisk.artifacts.results import AEBScenarioResultV1, ScenarioFamily
from aebrisk.cohort.filters import FAMILY_TYPES, family_of
from aebrisk.cohort.manifest import CohortManifestV1
from aebrisk.nuplan_adapter.database import NuPlanInstallation
from aebrisk.nuplan_adapter.nuplan_scenario import NuPlanScenario
from aebrisk.nuplan_adapter.query_scenario import ScenarioReference, scenarios_of_type
from aebrisk.simulation.common_cohort import ExperimentConfiguration
from aebrisk.simulation.runner import REFERENCE_OBSERVATION_MODE, run_common_scenario
from aebrisk.simulation.validity import InvalidScenario

#: Named so a test can resolve a cohort without a log database.
QUERY_SCENARIOS = scenarios_of_type
BUILD_TOKEN = NuPlanScenario

#: Every scenario type any family claims, which is what a log is searched for.
PINNED_TYPES: tuple[str, ...] = tuple(
    sorted({name for names in FAMILY_TYPES.values() for name in names})
)

TOKEN_RESULTS_SCHEMA_VERSION = "aeb-token-results/v1"

#: The four strata, as the `ScenarioFamily` literals the records are typed with.
FAMILIES: tuple[ScenarioFamily, ...] = get_args(ScenarioFamily)


@dataclass(frozen=True)
class CohortScenario:
    """One token of a frozen cohort, and where its recording is."""

    reference: ScenarioReference
    family: ScenarioFamily


@dataclass(frozen=True)
class TokenRun:
    """What one token produced across the configurations it was driven through."""

    token: str
    family: ScenarioFamily
    results: tuple[AEBScenarioResultV1, ...]
    invalid: Optional[InvalidScenario]


class TokenResultsV1(BaseModel):
    """One token's records for one configuration, as they are written to disk."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aeb-token-results/v1"]
    scenario_token: str = Field(min_length=1)
    family: ScenarioFamily
    split: Literal["development", "evaluation"]
    configuration_id: str = Field(min_length=1)
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cohort_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    valid: bool
    invalid_reason: Optional[str] = None
    invalid_phase: Optional[str] = None
    results: tuple[AEBScenarioResultV1, ...]


def resolve_cohort(
    manifest: CohortManifestV1, installation: NuPlanInstallation
) -> tuple[CohortScenario, ...]:
    """Find every token the manifest names, in the logs the manifest names.

    Searching only the manifest's own logs is both far cheaper than sweeping a
    split of 1,381 databases and a check on the manifest: a token that is not in
    the logs the freeze recorded means the two halves of that document disagree.
    """

    by_name = {path.name: path for path in installation.log_databases}
    absent = [name for name in manifest.log_names if name not in by_name]
    if absent:
        raise ValueError(
            f"the {manifest.split} manifest names {len(absent)} log(s) the {installation.split!r} "
            f"split does not have, beginning with {absent[0]!r}; the cohort was frozen "
            "against a different installation"
        )

    found: dict[str, ScenarioReference] = {}
    for name in manifest.log_names:
        for reference in QUERY_SCENARIOS(str(by_name[name]), PINNED_TYPES):
            found[reference.token] = reference

    cohort: list[CohortScenario] = []
    for family in FAMILIES:
        for token in manifest.families[family]:
            resolved = found.get(token)
            if resolved is None:
                raise ValueError(
                    f"token {token!r} of family {family!r} is in the manifest but in none of "
                    f"the {len(manifest.log_names)} logs it names; running the rest would "
                    "measure a cohort nobody chose"
                )
            actual = family_of(resolved.scenario_type)
            if actual != family:
                raise ValueError(
                    f"token {token!r} is filed under {family!r} but its recorded type "
                    f"{resolved.scenario_type!r} belongs to {actual!r}; a stratum that does "
                    "not hold what it says holds makes every per-family rate wrong"
                )
            cohort.append(CohortScenario(reference=resolved, family=family))
    return tuple(cohort)


def configurations_for(
    configurations: Sequence[ExperimentConfiguration], configuration_id: Optional[str]
) -> tuple[ExperimentConfiguration, ...]:
    """The cells one invocation runs: the one under test, and always the reference.

    A cell is measured against the oracle's braking, so a run of one cell alone
    could not produce the two outcomes the study is about. The oracle is
    therefore added rather than requested, and running the whole matrix at once
    is the cheaper path because it computes the reference once instead of once
    per cell.
    """

    if configuration_id is None:
        return tuple(configurations)
    chosen = [
        configuration
        for configuration in configurations
        if configuration.configuration_id == configuration_id
    ]
    if not chosen:
        names = [configuration.configuration_id for configuration in configurations]
        raise ValueError(f"{configuration_id!r} is not one of the matrix's cells: {names}")
    reference = [
        configuration
        for configuration in configurations
        if configuration.aeb_enabled
        and configuration.observation_mode == REFERENCE_OBSERVATION_MODE
    ]
    # Deduplicated by name rather than by identity, because asking for the
    # oracle itself must not run it twice, and a configuration carries a mapping
    # and so cannot be put in a set.
    picked: list[ExperimentConfiguration] = []
    seen: set[str] = set()
    for configuration in (*reference, *chosen):
        if configuration.configuration_id not in seen:
            seen.add(configuration.configuration_id)
            picked.append(configuration)
    return tuple(picked)


def run_cohort(
    cohort: Sequence[CohortScenario],
    configurations: Sequence[ExperimentConfiguration],
    *,
    protocol_hash: str,
    protocol: Any,
    on_token: Optional[Callable[[TokenRun], None]] = None,
) -> tuple[TokenRun, ...]:
    """Drive every token of a cohort through every configuration of a run."""

    if not cohort:
        raise ValueError("the cohort is empty; a run over nothing produces nothing to compare")

    runs: list[TokenRun] = []
    for scenario in cohort:
        results, invalid = run_common_scenario(
            BUILD_TOKEN(scenario.reference, scenario.family, protocol_hash),
            tuple(configurations),
            protocol,
        )
        run = TokenRun(
            token=scenario.reference.token,
            family=scenario.family,
            results=results,
            invalid=invalid,
        )
        runs.append(run)
        if on_token is not None:
            on_token(run)
    return tuple(runs)


def token_results_bytes(document: TokenResultsV1) -> bytes:
    """Serialise one token's records the single way every rerun must reproduce."""

    payload = json.dumps(
        document.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (payload + "\n").encode("utf-8")


def documents_for(
    run: TokenRun,
    *,
    split: Literal["development", "evaluation"],
    written_configurations: Sequence[str],
    protocol_sha256: str,
    cohort_manifest_sha256: str,
) -> tuple[TokenResultsV1, ...]:
    """One document per configuration this invocation is responsible for writing."""

    by_configuration: dict[str, list[AEBScenarioResultV1]] = {
        name: [] for name in written_configurations
    }
    for record in run.results:
        if record.configuration_id in by_configuration:
            by_configuration[record.configuration_id].append(record)

    return tuple(
        TokenResultsV1(
            schema_version="aeb-token-results/v1",
            scenario_token=run.token,
            family=run.family,
            split=split,
            configuration_id=name,
            protocol_sha256=protocol_sha256,
            cohort_manifest_sha256=cohort_manifest_sha256,
            valid=run.invalid is None,
            invalid_reason=None if run.invalid is None else run.invalid.reason,
            invalid_phase=None if run.invalid is None else run.invalid.phase,
            results=tuple(by_configuration[name]),
        )
        for name in written_configurations
    )


def write_cohort_results(
    runs: Sequence[TokenRun],
    manifest: CohortManifestV1,
    output_dir: Path,
    *,
    written_configurations: Sequence[str],
    protocol_sha256: str,
    cohort_manifest_sha256: str,
) -> tuple[Path, ...]:
    """Write every token's records, or refuse if the run is not the cohort.

    The comparison is made before the first byte is written. A partial run whose
    files were already on disk would be indistinguishable from a complete one to
    anything downstream, and the manifest hash beside it would still match.
    """

    expected = {token for family in FAMILIES for token in manifest.families[family]}
    produced = {run.token for run in runs}
    if produced != expected:
        missing = sorted(expected - produced)
        extra = sorted(produced - expected)
        raise ValueError(
            f"the run covered {len(produced)} tokens and the {manifest.split} manifest names "
            f"{len(expected)}; missing {missing[:3]}, unexpected {extra[:3]}. Nothing is "
            "written, because a partial cohort on disk is indistinguishable from a whole one"
        )

    written: list[Path] = []
    for run in runs:
        documents = documents_for(
            run,
            split=manifest.split,
            written_configurations=written_configurations,
            protocol_sha256=protocol_sha256,
            cohort_manifest_sha256=cohort_manifest_sha256,
        )
        for document in documents:
            directory = output_dir / document.configuration_id
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{run.token}.json"
            path.write_bytes(token_results_bytes(document))
            written.append(path)
    return tuple(written)


def summarize(runs: Sequence[TokenRun]) -> Mapping[str, int]:
    """The counts an operator reads off the end of a long run."""

    return {
        "tokens": len(runs),
        "valid": sum(1 for run in runs if run.invalid is None),
        "invalid": sum(1 for run in runs if run.invalid is not None),
        "records": sum(len(run.results) for run in runs),
    }
