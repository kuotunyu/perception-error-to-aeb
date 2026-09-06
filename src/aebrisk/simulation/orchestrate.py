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

FINISHED TOKENS ARE SAVED IMMEDIATELY. A completion marker is written only when
the run covers the manifest's token set. The whole-cohort convenience writer
also compares membership before writing any token, preserving its fail-closed
contract for callers that already hold every result.

The oracle configuration is always part of a run even when one cell is under
test, because missed and false interventions are defined by comparison with it
and by nothing else. Its records are written only by its own run, so no cell's
directory is written twice.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
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
from aebrisk.simulation.common_cohort import CohortScenario, ExperimentConfiguration
from aebrisk.simulation.runner import REFERENCE_OBSERVATION_MODE, run_common_scenario
from aebrisk.simulation.synthetic import SYNTHETIC_LOG, SyntheticLeadScenario, synthetic_cohort
from aebrisk.simulation.validity import InvalidScenario

#: Named so a test can resolve a cohort without a log database.
QUERY_SCENARIOS = scenarios_of_type


def build_token(reference: ScenarioReference, family: ScenarioFamily, protocol_hash: str) -> Any:
    """The scenario object for one cohort entry: recording or synthetic source."""

    if reference.log_file == SYNTHETIC_LOG:
        return SyntheticLeadScenario()
    return NuPlanScenario(reference, family, protocol_hash)


BUILD_TOKEN = build_token

#: Every scenario type any family claims, which is what a log is searched for.
PINNED_TYPES: tuple[str, ...] = tuple(
    sorted({name for names in FAMILY_TYPES.values() for name in names})
)

TOKEN_RESULTS_SCHEMA_VERSION = "aeb-token-results/v1"

#: The four strata, as the `ScenarioFamily` literals the records are typed with.
FAMILIES: tuple[ScenarioFamily, ...] = get_args(ScenarioFamily)


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
    split: Literal["development", "evaluation", "smoke"]
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


def _run_one(
    scenario: CohortScenario,
    configurations: tuple[ExperimentConfiguration, ...],
    protocol_hash: str,
    protocol: Any,
) -> TokenRun:
    results, invalid = run_common_scenario(
        BUILD_TOKEN(scenario.reference, scenario.family, protocol_hash), configurations, protocol
    )
    return TokenRun(scenario.reference.token, scenario.family, results, invalid)


def run_cohort(
    cohort: Sequence[CohortScenario],
    configurations: Sequence[ExperimentConfiguration],
    *,
    protocol_hash: str,
    protocol: Any,
    on_token: Optional[Callable[[TokenRun], None]] = None,
    workers: int = 1,
) -> tuple[TokenRun, ...]:
    """Save via callbacks in completion order and return results in cohort order."""

    if not cohort:
        raise ValueError("the cohort is empty; a run over nothing produces nothing to compare")

    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError(f"workers must be a positive integer, got {workers!r}")
    cells = tuple(configurations)
    runs: list[TokenRun] = []
    if workers == 1:
        for scenario in cohort:
            run = _run_one(scenario, cells, protocol_hash, protocol)
            runs.append(run)
            if on_token is not None:
                on_token(run)
        return tuple(runs)

    indexed: dict[int, TokenRun] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, scenario, cells, protocol_hash, protocol): index
            for index, scenario in enumerate(cohort)
        }
        for future in as_completed(futures):
            run = future.result()
            indexed[futures[future]] = run
            if on_token is not None:
                on_token(run)
    return tuple(indexed[index] for index in range(len(cohort)))


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
    split: Literal["development", "evaluation", "smoke"],
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


def _check_run_membership(runs: Sequence[TokenRun], manifest: CohortManifestV1) -> None:
    expected_families = {
        token: family for family in FAMILIES for token in manifest.families[family]
    }
    expected = set(expected_families)
    produced = {run.token for run in runs}
    if produced != expected:
        missing = sorted(expected - produced)
        extra = sorted(produced - expected)
        raise ValueError(
            f"the run covered {len(produced)} tokens and the {manifest.split} manifest names "
            f"{len(expected)}; missing {missing[:3]}, unexpected {extra[:3]}. "
            "The requested write is refused because the token sets differ"
        )
    for run in runs:
        if run.family != expected_families[run.token]:
            raise ValueError(
                f"token {run.token!r} has family {run.family!r}, but the manifest names "
                f"{expected_families[run.token]!r}"
            )


def resolve_synthetic_cohort(manifest: CohortManifestV1) -> tuple[CohortScenario, ...]:
    """Resolve the fixed synthetic source only against its actual frozen membership."""

    cohort = synthetic_cohort()
    _check_run_membership(
        tuple(TokenRun(s.reference.token, s.family, (), None) for s in cohort), manifest
    )
    if manifest.log_names != (SYNTHETIC_LOG,):
        raise ValueError("the synthetic scenario source requires exactly the synthetic log")
    return cohort


def _publish_bytes(path: Path, payload: bytes) -> None:
    """Expose a final filename only after its complete bytes have been written."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def write_token_run(
    run: TokenRun,
    manifest: CohortManifestV1,
    output_dir: Path,
    *,
    written_configurations: Sequence[str],
    protocol_sha256: str,
    cohort_manifest_sha256: str,
) -> tuple[Path, ...]:
    """Persist one finished token without waiting for the rest of its cohort."""

    written: list[Path] = []
    for document in documents_for(
        run,
        split=manifest.split,
        written_configurations=written_configurations,
        protocol_sha256=protocol_sha256,
        cohort_manifest_sha256=cohort_manifest_sha256,
    ):
        directory = output_dir / document.configuration_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{run.token}.json"
        _publish_bytes(path, token_results_bytes(document))
        written.append(path)
    return tuple(written)


def write_run_complete(
    runs: Sequence[TokenRun],
    manifest: CohortManifestV1,
    output_dir: Path,
    *,
    cohort_manifest_sha256: str,
) -> Path:
    """Mark a cohort complete only when every manifest token is accounted for."""

    _check_run_membership(runs, manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "run_complete.json"
    payload = {
        "schema_version": "aeb-run-complete/v1",
        "cohort_manifest_sha256": cohort_manifest_sha256,
        "tokens": sorted({run.token for run in runs}),
    }
    _publish_bytes(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return path


def finished_tokens(
    output_dir: Path, written_configurations: Sequence[str], tokens: Iterable[str]
) -> frozenset[str]:
    """Resume only tokens whose every requested configuration file exists."""

    return frozenset(
        token
        for token in tokens
        if all((output_dir / name / f"{token}.json").is_file() for name in written_configurations)
    )


def validate_resume_results(
    manifest: CohortManifestV1,
    output_dir: Path,
    *,
    written_configurations: Sequence[str],
    protocol_sha256: str,
    cohort_manifest_sha256: str,
) -> None:
    """Validate every existing requested final document before any evidence is reused."""

    for family in FAMILIES:
        for token in manifest.families[family]:
            for name in written_configurations:
                path = output_dir / name / f"{token}.json"
                if path.exists():
                    try:
                        document = TokenResultsV1.model_validate_json(
                            path.read_bytes(), strict=True
                        )
                    except (OSError, ValueError) as error:
                        raise ValueError(
                            f"the resume token document cannot be read: {path}: {error}"
                        ) from error
                    expected = {
                        "scenario_token": token,
                        "family": family,
                        "split": manifest.split,
                        "configuration_id": name,
                        "protocol_sha256": protocol_sha256,
                        "cohort_manifest_sha256": cohort_manifest_sha256,
                    }
                    if document.model_dump(include=set(expected)) != expected:
                        raise ValueError(
                            f"the resume token document does not match the current run inputs: {path}"
                        )
                    identities = {
                        (record.scenario_token, record.family, record.configuration_id)
                        for record in document.results
                    }
                    if identities - {(token, family, name)}:
                        raise ValueError(
                            f"the resume token document contains records for another token, family or configuration: {path}"
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

    Membership is checked before the first byte; the completion marker follows
    all token writes. Call write_token_run for incremental persistence instead.
    """

    _check_run_membership(runs, manifest)

    written: list[Path] = []
    for run in runs:
        written.extend(
            write_token_run(
                run,
                manifest,
                output_dir,
                written_configurations=written_configurations,
                protocol_sha256=protocol_sha256,
                cohort_manifest_sha256=cohort_manifest_sha256,
            )
        )
    write_run_complete(runs, manifest, output_dir, cohort_manifest_sha256=cohort_manifest_sha256)
    return tuple(written)


def summarize(runs: Sequence[TokenRun]) -> Mapping[str, int]:
    """The counts an operator reads off the end of a long run."""

    return {
        "tokens": len(runs),
        "valid": sum(1 for run in runs if run.invalid is None),
        "invalid": sum(1 for run in runs if run.invalid is not None),
        "records": sum(len(run.results) for run in runs),
    }
