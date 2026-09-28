"""The policy v2 study, as its committed study file declares it.

`configs/experiments/aeb_policy_v2_study.yaml` writes down what
`docs/studies/aeb-policy-v2/analysis-plan.md` fixed before any arm ran: the
eight cells, the five arms and the factors each one sets, the released cohort
and inputs the arms hold fixed, the resampling settings and the hypothesis
families. The study is read from that file rather than from code, so the file a
reader checks is the study that ran, and its SHA-256 identifies it.

The loader refuses a file that describes a study the plan does not: a cell the
released matrix does not have, an arm without its own oracle, two arms under
one name, replicates other than the released three, and an input whose bytes
are not the ones recorded.

`StudyRunContext` is what each arm's run records about itself, and what the
gates read back.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.filters import SCENARIO_FAMILIES
from aebrisk.cohort.manifest import CohortEligibilityV1, load_manifest
from aebrisk.committed_config import read_committed_config
from aebrisk.simulation.common_cohort import (
    AEB_POLICIES,
    RNG_SCHEMES,
    VELOCITY_ESTIMATORS,
    ExperimentConfiguration,
)

#: Every cell of every arm runs the released three replicates, so each
#: simulated document pairs with its released record replicate by replicate.
STUDY_REPLICATES: tuple[int, ...] = (0, 1, 2)

#: Missed and false interventions are measured against the oracle of the same
#: run, so every arm runs its own.
ORACLE_CELL = "oracle_aeb"

#: A nuPlan log is named by the drive's start time and vehicle, then the
#: segment's range; the drive is everything up to and including the vehicle.
_LOG_NAME = re.compile(r"(\d{4}(?:\.\d{2}){5}_veh-\d+)_\d+_\d+(?:\.db)?")

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Version = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
Seed = Annotated[int, Field(ge=0, strict=True)]
Count = Annotated[int, Field(ge=1, strict=True)]
Confidence = Annotated[float, Field(gt=0.0, lt=1.0)]
StandardDeviation = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
Name = Annotated[str, Field(min_length=1)]


def _refuse_cells_outside_the_formal_matrix(cells: Iterable[str], owner: str) -> None:
    formal = {configuration.configuration_id for configuration in formal_configurations()}
    unknown = [cell for cell in cells if cell not in formal]
    if unknown:
        raise ValueError(f"{owner} names cells that are not cells of the formal matrix: {unknown}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StudyArmV1(_Strict):
    """One arm: one simulation run of some of the study's cells under three factors.

    The factor names are the fields of `ExperimentConfiguration` they set.
    """

    id: Name
    aeb_policy: str
    rng_scheme: str
    velocity_estimator: str
    cells: tuple[str, ...]

    @model_validator(mode="after")
    def validate_arm(self) -> StudyArmV1:
        """Refuse a factor the simulation does not run, an unknown cell, or no oracle."""

        factors = (
            ("aeb_policy", AEB_POLICIES),
            ("rng_scheme", RNG_SCHEMES),
            ("velocity_estimator", VELOCITY_ESTIMATORS),
        )
        for name, allowed in factors:
            value = getattr(self, name)
            if value not in allowed:
                raise ValueError(f"arm {self.id!r}: {name} must be one of {allowed}, got {value!r}")
        _refuse_cells_outside_the_formal_matrix(self.cells, f"arm {self.id!r}")
        if ORACLE_CELL not in self.cells:
            raise ValueError(
                f"arm {self.id!r} does not run {ORACLE_CELL!r}; every arm is measured "
                "against the oracle of its own run"
            )
        return self


class StudyCVKalmanV1(_Strict):
    """The one parameter set of the cv-kalman velocity estimate."""

    process_accel_std_mps2: StandardDeviation
    measurement_std_m: StandardDeviation
    initial_velocity_std_mps: StandardDeviation


class StudyEnvironmentV1(_Strict):
    """The versions every arm's container must report."""

    python_version: Version
    numpy_version: Version


class StudyBootstrapV1(_Strict):
    """The family-stratified log-cluster bootstrap every interval is drawn from."""

    seed: Seed
    resamples: Count
    cluster: Literal["family-log"]
    primary_confidence: Confidence
    secondary_confidence: Confidence


class StudySignFlipV1(_Strict):
    """The paired sign-flip test over logs that decides collision-indicator contrasts."""

    unit: Literal["log"]
    enumerate_up_to: Count
    random_flips: Count
    seed: Seed


class StudyHypothesisV1(_Strict):
    """One contrast: the plus arm minus the minus arm, in one cell, on one outcome.

    A predicted sign of 0 is a two-sided hypothesis.
    """

    id: Name
    plus: Name
    minus: Name
    cell: str
    outcome: Literal[
        "collision_indicator",
        "braking_share",
        "not_at_fault_contact_rate",
        "missed_interventions",
        "false_interventions",
    ]
    test: Literal["bootstrap", "sign_flip"]
    predicted_sign: Literal[-1, 0, 1]

    @field_validator("cell")
    @classmethod
    def validate_cell(cls, value: str) -> str:
        _refuse_cells_outside_the_formal_matrix((value,), "a hypothesis")
        return value


class StudyHypothesisFamilyV1(_Strict):
    """A family of hypotheses that Holm's procedure decides together."""

    id: Name
    hypotheses: tuple[StudyHypothesisV1, ...] = Field(min_length=1)


class StudyDefinitionV1(_Strict):
    """The whole study file."""

    schema_version: Literal["aeb-study/v1"]
    study_id: Name
    analysis_plan: Name
    seed_namespace_protocol_sha256: Sha256
    cohort_manifest: Name
    cohort_manifest_file_sha256: Sha256
    cohort_membership_sha256: Sha256
    token_log_source: Name
    replicates: tuple[Annotated[int, Field(strict=True)], ...]
    input_sha256: dict[str, Sha256]
    cv_kalman: StudyCVKalmanV1
    environment: StudyEnvironmentV1
    released_output_hashes_sha256: Sha256
    input_databases_sha256: Sha256
    cells: tuple[str, ...]
    arms: tuple[StudyArmV1, ...]
    bootstrap: StudyBootstrapV1
    sign_flip: StudySignFlipV1
    hypothesis_families: tuple[StudyHypothesisFamilyV1, ...]

    @model_validator(mode="after")
    def validate_study(self) -> StudyDefinitionV1:
        """Refuse other replicates, an unknown cell, or two arms under one name."""

        if self.replicates != STUDY_REPLICATES:
            raise ValueError(
                f"replicates must be exactly {STUDY_REPLICATES}, the released three, "
                f"got {self.replicates}"
            )
        _refuse_cells_outside_the_formal_matrix(self.cells, "the study")
        ids = [arm.id for arm in self.arms]
        duplicated = sorted({arm_id for arm_id in ids if ids.count(arm_id) > 1})
        if duplicated:
            raise ValueError(f"duplicate arm ids: {duplicated}")
        return self


def load_study(path: Path) -> StudyDefinitionV1:
    """Read and validate a study file, and hold its inputs to the recorded hashes.

    Each `input_sha256` entry names a committed config by its path under
    `configs/`. It is hashed as the run reads it, through the package's copy,
    which `tests/contract/test_package_data.py` holds byte-identical to the
    repository's.
    """

    study = StudyDefinitionV1.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    for name, recorded in study.input_sha256.items():
        text = read_committed_config(*name.split("/"))
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual != recorded:
            raise ValueError(
                f"{name} has SHA-256 {actual}, but the study file records {recorded}; "
                "the arms would not read the inputs the study names"
            )
    return study


def study_sha256(path: Path) -> str:
    """The SHA-256 of the study file's bytes, which every arm's run context records."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def arm_configurations(
    study: StudyDefinitionV1, arm_id: str
) -> tuple[ExperimentConfiguration, ...]:
    """The formal cells an arm runs, in formal order, with the arm's three factors.

    Every other field is the released cell's, so an arm with the default factors
    is the released matrix restricted to its cells.
    """

    arms = {arm.id: arm for arm in study.arms}
    if arm_id not in arms:
        raise ValueError(f"unknown arm {arm_id!r}; the study's arms are {sorted(arms)}")
    arm = arms[arm_id]
    return tuple(
        dataclasses.replace(
            configuration,
            aeb_policy=arm.aeb_policy,
            rng_scheme=arm.rng_scheme,
            velocity_estimator=arm.velocity_estimator,
        )
        for configuration in formal_configurations()
        if configuration.configuration_id in arm.cells
    )


def token_log_map(eligibility: Path, manifest: Path) -> Mapping[str, str]:
    """Each cohort token's nuPlan log, read from its accepted eligibility row.

    A token with no accepted row, or with more than one, has no single log to
    be clustered under, so it is refused rather than guessed.
    """

    cohort = load_manifest(manifest)
    examined = CohortEligibilityV1.model_validate_json(
        eligibility.read_text(encoding="utf-8")
    ).examined
    accepted: dict[str, list[str]] = {}
    for row in examined:
        if row.accepted:
            accepted.setdefault(row.scenario_token, []).append(row.log_name)

    log_by_token: dict[str, str] = {}
    for token in sorted(token for family in SCENARIO_FAMILIES for token in cohort.families[family]):
        logs = accepted.get(token, [])
        if len(logs) != 1:
            raise ValueError(
                f"token {token!r} has {len(logs)} accepted rows in {eligibility.name}; "
                "exactly one is required"
            )
        log_by_token[token] = logs[0]
    return MappingProxyType(log_by_token)


def drive_of(log_name: str) -> str:
    """The drive a log is a segment of: its name up to and including the vehicle id."""

    match = _LOG_NAME.fullmatch(log_name)
    if match is None:
        raise ValueError(f"{log_name!r} is not a nuPlan log name")
    return match.group(1)


@dataclasses.dataclass(frozen=True)
class StudyRunContext:
    """Everything that identifies one arm's run, written beside its documents.

    The first five fields are those of the released run context, with the arm
    named in `configuration_id` as ``study:<arm id>``. The others name the study
    file, the arm and its three factors, the committed policy and error
    configuration the arm read, and the Python and numpy versions inside the
    container, so that each gate can check an arm from its own directory. Like
    the released run context, it carries no schema version.
    """

    configuration_id: str
    protocol_sha256: str
    cohort_sha256: str
    container_digest: str
    commit: str
    study_sha256: str
    arm_id: str
    aeb_policy: str
    policy_sha256: str
    rng_scheme: str
    velocity_estimator: str
    velocity_parameters: str
    error_config_sha256: str
    python_version: str
    numpy_version: str
