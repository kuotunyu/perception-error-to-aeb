"""The gates of the policy v2 study, and the preflight that runs before any arm.

`docs/studies/aeb-policy-v2/analysis-plan.md` (sections 8 and 9) defines the
gates and what a failure means. Each gate reads only what the arms and the
released run wrote, and returns a `GateResult`: whether it passed, what it
counted, and, file by file, what it found behind each count. `study verify` and
`study preflight` write the results into a `StudyGatesV1` gate file.

- THE PREFLIGHT runs before any arm. It holds the study's inputs, the protocol
  and the cohort manifest to the hashes the study file records; the released
  records to the list of their SHA-256 written after the released run
  (`check_released_records`); and each log the cohort references to its size
  and SHA-256 in the released run's record of each log. Those two records are
  kept outside Git, and each is held to the SHA-256 the study file pins: another
  file is refused, not checked.
- G1, INTEGRITY (`check_integrity`): an arm lists exactly the cohort's tokens as
  complete, holds one valid document per token and cell with the three
  replicates and no other file, and names in its run context the protocol, the
  cohort, the study file, its three factors, the tooling commit and the image.
  When several arms are checked, they name one commit and one image.
- G2, REPLICATION (`check_replication`): every document of arm A is
  byte-identical to the released one.
- G3, INVARIANCE (`check_invariance`): `no_aeb` is byte-identical in every arm,
  and `oracle_aeb` among the arms of one policy, because neither reads the
  factors that differ. Every arm's `no_aeb` and each v1 arm's oracle must also
  be byte-identical to the released records, except in arm-A reference mode,
  where those comparisons are counted and not required.
- G5, ENVIRONMENT (`check_environment`): every arm's run context names the
  Python and numpy versions the study file records.

A gate file publishes each gate's name, outcome and counts. The detail may name
files and tokens, so it stays under `artifacts/`, and `refuse_token_strings`
guards what is published: no string with the shape of a scenario token, and none
that holds a token of the committed cohort files.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aebrisk.artifacts.study_documents import (
    STUDY_GATES_SCHEMA_VERSION,
    Reference,
    StudyGatesV1,
    StudyGateV1,
)
from aebrisk.cli.simulate import UNKNOWN_COMMIT, UNKNOWN_DIGEST
from aebrisk.cohort.filters import SCENARIO_FAMILIES
from aebrisk.cohort.manifest import (
    CohortEligibilityV1,
    CohortManifestV1,
    load_manifest,
    membership_sha256,
)
from aebrisk.simulation.orchestrate import TokenResultsV1
from aebrisk.study.definition import (
    ORACLE_CELL,
    STUDY_REPLICATES,
    StudyArmV1,
    StudyDefinitionV1,
    StudyRunContext,
    load_study,
    study_sha256,
)

#: The arm that runs the released controller; G2 compares it with the released records.
REPLICATION_ARM = "A-v1-replication"

#: The cell without an AEB, which no factor of the study can change.
NO_AEB_CELL = "no_aeb"

#: The policy of the released run. The oracle of an arm under it is the released oracle.
RELEASED_POLICY = "v1"

RELEASED: Reference = "released"
ARM_A: Reference = "arm-a"
REFERENCES: tuple[Reference, ...] = (RELEASED, ARM_A)

#: Where a pilot writes, one directory per arm. Nothing under it is analysed.
PILOT_ROOT = "artifacts/pilot"

#: The split a pilot's manifest declares.
PILOT_SPLIT = "smoke"

#: The files an arm writes beside its documents.
ARM_FILES: tuple[str, ...] = ("run_context.json", "run_complete.json", "run.log")

#: The released run's list of the SHA-256 of every output, and its record of each log.
RELEASED_HASHES_SCHEMA_VERSION = "aeb-d2-output-hashes/v2"
INPUT_DATABASES_KIND = "private-d2-dataset-input-fingerprint"
INPUT_DATABASES_DATASET = "nuplan-v1.1"

#: The committed cohort files, whose tokens no published string may hold.
COHORT_DIRECTORY: tuple[str, ...] = ("docs", "evidence", "nuplan_aeb_v2", "cohort")

#: A nuPlan scenario token: sixteen lower-case hexadecimal digits.
TOKEN_SHAPE = re.compile(r"[0-9a-f]{16}")
_TOKEN_LENGTH = 16

_COMMIT = re.compile(r"[0-9a-f]{40}")
_CHUNK_BYTES = 1 << 20
_RUN_CONTEXT_FIELDS = tuple(sorted(field.name for field in dataclasses.fields(StudyRunContext)))


@dataclasses.dataclass(frozen=True)
class GateResult:
    """One gate's outcome: whether it passed, what it counted, and what it found.

    `counts` is what a gate file publishes; for G2 it is the number of
    differing documents per cell. `local_detail` names the files and tokens
    behind the counts, and stays under `artifacts/`.
    """

    gate: str
    passed: bool
    counts: Mapping[str, int]
    local_detail: tuple[str, ...]


class _Private(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ListedFile(_Private):
    path: str = Field(min_length=1)
    bytes: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _ReleasedOutputHashes(_Private):
    """The list of every released output, written after the released run finished."""

    schema_version: Literal["aeb-d2-output-hashes/v2"]
    generated_at_utc: str
    formal_files: tuple[_ListedFile, ...]
    operation_files: tuple[_ListedFile, ...]

    @model_validator(mode="after")
    def refuse_a_file_listed_twice(self) -> _ReleasedOutputHashes:
        paths = [entry.path for entry in self.formal_files]
        if len(set(paths)) != len(paths):
            raise ValueError("the list names a released file twice")
        return self


class _RecordedLog(_Private):
    relative_path: str = Field(min_length=1)
    bytes: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: Kept and never compared or parsed: the Docker Desktop mount may report
    #: other times, and the record writes seven fractional digits.
    last_write_time_utc: str


class _InputDatabases(_Private):
    """The released run's record of each log it read: its size and SHA-256."""

    kind: str
    dataset: str
    split: str
    started_at_utc: str
    finished_at_utc: str
    files: tuple[_RecordedLog, ...]


class _RunComplete(_Private):
    schema_version: Literal["aeb-run-complete/v1"]
    cohort_manifest_sha256: str
    tokens: tuple[str, ...]


# --------------------------------------------------------------------------
# Reading files
# --------------------------------------------------------------------------


def _file_sha256(path: Path) -> str:
    """The SHA-256 of a file's bytes, read in chunks so a log of gigabytes fits in memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_under(root: Path) -> dict[str, Path]:
    """Every file under `root`, by its path relative to it; nothing when it does not exist."""

    return {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _utc(nanoseconds: int) -> str:
    seconds, remainder = divmod(nanoseconds, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, timezone.utc) + timedelta(
        microseconds=remainder // 1000
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _read_run_context(arm_root: Path) -> Union[StudyRunContext, str]:
    """An arm's run context, or why it cannot be read as one."""

    try:
        value = json.loads((arm_root / "run_context.json").read_bytes())
    except (OSError, ValueError) as error:
        return f"run_context.json cannot be read: {error}"
    if (
        not isinstance(value, dict)
        or tuple(sorted(value)) != _RUN_CONTEXT_FIELDS
        or not all(isinstance(item, str) for item in value.values())
    ):
        return "run_context.json is not a study run context"
    return StudyRunContext(**value)


def _tokens(cohort: CohortManifestV1) -> dict[str, str]:
    """Each token of the cohort and its family, in token order."""

    families = {token: family for family in SCENARIO_FAMILIES for token in cohort.families[family]}
    return {token: families[token] for token in sorted(families)}


def _arm(definition: StudyDefinitionV1, arm_id: str) -> StudyArmV1:
    arms = {arm.id: arm for arm in definition.arms}
    if arm_id not in arms:
        raise ValueError(f"unknown arm {arm_id!r}; the study's arms are {list(arms)}")
    return arms[arm_id]


def checked_arms(definition: StudyDefinitionV1, arm: Optional[str] = None) -> tuple[str, ...]:
    """The arm named, or every arm of the study in the study file's order."""

    if arm is None:
        return tuple(candidate.id for candidate in definition.arms)
    return (_arm(definition, arm).id,)


def _refuse_an_unknown_reference(reference: str) -> None:
    if reference not in REFERENCES:
        raise ValueError(f"unknown reference {reference!r}; expected one of {list(REFERENCES)}")


# --------------------------------------------------------------------------
# The released records and the preflight
# --------------------------------------------------------------------------


def _refused_list(reason: str) -> GateResult:
    return GateResult("released_records", False, {"hash_list_refused": 1}, (reason,))


def check_released_records(
    released_root: Path, released_hashes: Path, expected_sha256: str
) -> GateResult:
    """Hold the released records to the list of their SHA-256 written after the run.

    A list whose own SHA-256 is not `expected_sha256`, or that is not an
    `aeb-d2-output-hashes/v2` list, is refused and nothing is compared. Then
    every file under `released_root` must be listed, every listed file present,
    and each one's size and SHA-256 the listed ones.
    """

    actual = _file_sha256(released_hashes)
    if actual != expected_sha256:
        return _refused_list(
            f"the hash list {released_hashes} has SHA-256 {actual}, not the expected "
            f"{expected_sha256}"
        )
    try:
        listing = _ReleasedOutputHashes.model_validate_json(released_hashes.read_bytes())
    except ValidationError as error:
        return _refused_list(
            f"the hash list {released_hashes} is not an {RELEASED_HASHES_SCHEMA_VERSION} list: "
            f"{error}"
        )

    listed = {entry.path: entry for entry in listing.formal_files}
    present = _files_under(released_root)
    missing = sorted(set(listed) - set(present))
    extra = sorted(set(present) - set(listed))
    detail = [f"{name} is listed and missing" for name in missing]
    detail.extend(f"{name} is present and not listed" for name in extra)
    size_mismatches = hash_mismatches = 0
    for name in sorted(set(listed) & set(present)):
        entry, path = listed[name], present[name]
        if path.stat().st_size != entry.bytes:
            size_mismatches += 1
            detail.append(f"{name} does not have the listed size")
        if _file_sha256(path) != entry.sha256:
            hash_mismatches += 1
            detail.append(f"{name} does not have the listed SHA-256")
    counts = {
        "listed_files": len(listed),
        "missing_files": len(missing),
        "extra_files": len(extra),
        "size_mismatches": size_mismatches,
        "hash_mismatches": hash_mismatches,
    }
    passed = not (missing or extra or size_mismatches or hash_mismatches)
    return GateResult("released_records", passed, counts, tuple(detail))


def _read_input_databases(path: Path, split: str) -> _InputDatabases:
    """The released run's record of each log, refused unless it records `split` of nuPlan v1.1."""

    record = _InputDatabases.model_validate_json(path.read_bytes())
    for name, expected in (
        ("kind", INPUT_DATABASES_KIND),
        ("dataset", INPUT_DATABASES_DATASET),
        ("split", split),
    ):
        value = getattr(record, name)
        if value != expected:
            raise ValueError(
                f"the input databases record {path} has {name} {value!r}, not {expected!r}"
            )
    paths = [entry.relative_path for entry in record.files]
    if len(set(paths)) != len(paths):
        raise ValueError(f"the input databases record {path} lists a log twice")
    return record


def _mismatches(checks: Sequence[tuple[str, str, str]], detail: list[str]) -> int:
    count = 0
    for name, actual, recorded in checks:
        if actual != recorded:
            count += 1
            detail.append(f"the {name} hash is {actual}, and the study file records {recorded}")
    return count


def _check_logs(
    log_names: Sequence[str],
    record: _InputDatabases,
    split_directory: Path,
    split: str,
    detail: list[str],
) -> dict[str, int]:
    """Each referenced log's presence, size and SHA-256 against the record of the released run.

    The record must list exactly the referenced logs. A log's modification time
    is written into the detail and never compared.
    """

    recorded = {entry.relative_path: entry for entry in record.files}
    referenced = {f"nuplan-v1.1/splits/{split}/{log}": log for log in log_names}
    not_recorded = sorted(set(referenced) - set(recorded))
    not_referenced = sorted(set(recorded) - set(referenced))
    detail.extend(
        f"{referenced[path]} is referenced and not in the record" for path in not_recorded
    )
    detail.extend(f"{path} is in the record and not referenced" for path in not_referenced)
    present = total_bytes = missing = size_mismatches = hash_mismatches = 0
    for relative, log in sorted(referenced.items()):
        path = split_directory / log
        if not path.is_file():
            missing += 1
            detail.append(f"{log} is missing from {split_directory}")
            continue
        status = path.stat()
        present += 1
        total_bytes += status.st_size
        detail.append(f"{log} modified {_utc(status.st_mtime_ns)}")
        entry = recorded.get(relative)
        if entry is None:
            continue
        if status.st_size != entry.bytes:
            size_mismatches += 1
            detail.append(f"{log} has {status.st_size} bytes, and the record lists {entry.bytes}")
        if _file_sha256(path) != entry.sha256:
            hash_mismatches += 1
            detail.append(f"{log} does not have the SHA-256 the record lists")
    return {
        "referenced_logs": len(referenced),
        "logs_present": present,
        "log_bytes": total_bytes,
        "logs_missing": missing,
        "logs_missing_from_record": len(not_recorded),
        "logs_not_referenced": len(not_referenced),
        "log_size_mismatches": size_mismatches,
        "log_hash_mismatches": hash_mismatches,
    }


def preflight(
    study: Path,
    protocol: Path,
    released_root: Path,
    released_hashes: Path,
    input_databases: Path,
    manifest_path: Path,
    data_root: Path,
    split: str,
) -> GateResult:
    """Check everything the arms read, and the released records, before any arm runs.

    Refused, with a `ValueError`: a study file whose inputs have other bytes
    than it records, a hash list or a record of the logs whose SHA-256 is not
    the one the study file pins, and a record of another kind, dataset or split.
    Counted, and failing the preflight: another protocol or cohort manifest,
    released records that differ from their list, and a referenced log that is
    missing from `nuplan-v1.1/splits/<split>` under `data_root` or whose size or
    SHA-256 is not the recorded one. Every referenced log is re-hashed in full.

    The detail records the study file's SHA-256, the SHA-256 of the two private
    records, the split directory, and each log's modification time.
    """

    definition = load_study(study)
    pinned = (
        ("released output hash list", released_hashes, definition.released_output_hashes_sha256),
        ("input databases record", input_databases, definition.input_databases_sha256),
    )
    for name, path, expected in pinned:
        actual = _file_sha256(path)
        if actual != expected:
            raise ValueError(
                f"the {name} {path} has SHA-256 {actual}, but the study file pins {expected}"
            )
    record = _read_input_databases(input_databases, split)
    cohort = load_manifest(manifest_path)
    split_directory = data_root / "nuplan-v1.1" / "splits" / split
    detail = [
        f"study file SHA-256 {study_sha256(study)}",
        f"released output hash list SHA-256 {definition.released_output_hashes_sha256}",
        f"input databases record SHA-256 {definition.input_databases_sha256}",
        f"split directory {split_directory}",
    ]
    counts = {
        "protocol_mismatches": _mismatches(
            (("protocol", _file_sha256(protocol), definition.seed_namespace_protocol_sha256),),
            detail,
        ),
        "cohort_manifest_mismatches": _mismatches(
            (
                (
                    "cohort manifest file",
                    _file_sha256(manifest_path),
                    definition.cohort_manifest_file_sha256,
                ),
                (
                    "cohort membership",
                    membership_sha256(cohort),
                    definition.cohort_membership_sha256,
                ),
            ),
            detail,
        ),
    }
    released = check_released_records(
        released_root, released_hashes, definition.released_output_hashes_sha256
    )
    counts.update({f"released_{name}": count for name, count in released.counts.items()})
    detail.extend(released.local_detail)
    logs = _check_logs(cohort.log_names, record, split_directory, split, detail)
    counts.update(logs)
    failures = (
        counts["protocol_mismatches"],
        counts["cohort_manifest_mismatches"],
        logs["logs_missing"],
        logs["logs_missing_from_record"],
        logs["logs_not_referenced"],
        logs["log_size_mismatches"],
        logs["log_hash_mismatches"],
    )
    return GateResult("preflight", released.passed and not any(failures), counts, tuple(detail))


# --------------------------------------------------------------------------
# G1: integrity
# --------------------------------------------------------------------------


def _check_run_context(
    definition: StudyDefinitionV1,
    arm: StudyArmV1,
    study_hash: str,
    cohort_sha256: str,
    arm_root: Path,
    detail: list[str],
) -> int:
    context = _read_run_context(arm_root)
    if isinstance(context, str):
        detail.append(context)
        return 1
    expected = {
        "configuration_id": f"study:{arm.id}",
        "protocol_sha256": definition.seed_namespace_protocol_sha256,
        "cohort_sha256": cohort_sha256,
        "study_sha256": study_hash,
        "arm_id": arm.id,
        "aeb_policy": arm.aeb_policy,
        "rng_scheme": arm.rng_scheme,
        "velocity_estimator": arm.velocity_estimator,
    }
    problems = [
        f"run_context.json records {name} {getattr(context, name)!r}, not {value!r}"
        for name, value in expected.items()
        if getattr(context, name) != value
    ]
    if context.commit == UNKNOWN_COMMIT or not _COMMIT.fullmatch(context.commit):
        problems.append(f"run_context.json records no tooling commit: {context.commit!r}")
    if context.container_digest in ("", UNKNOWN_DIGEST):
        problems.append(
            f"run_context.json records no image identifier: {context.container_digest!r}"
        )
    detail.extend(problems)
    return len(problems)


def _check_run_complete(
    arm_root: Path, tokens: Sequence[str], cohort_sha256: str, detail: list[str]
) -> dict[str, int]:
    try:
        marker = _RunComplete.model_validate_json((arm_root / "run_complete.json").read_bytes())
    except (OSError, ValueError) as error:
        detail.append(f"run_complete.json cannot be read: {error}")
        return {"run_complete_problems": 1, "tokens_not_listed": 0, "tokens_not_in_cohort": 0}
    if marker.cohort_manifest_sha256 != cohort_sha256:
        detail.append(
            f"run_complete.json names the cohort {marker.cohort_manifest_sha256}, not "
            f"{cohort_sha256}"
        )
        return {"run_complete_problems": 1, "tokens_not_listed": 0, "tokens_not_in_cohort": 0}
    listed = set(marker.tokens)
    not_listed = sorted(set(tokens) - listed)
    not_in_cohort = sorted(listed - set(tokens))
    detail.extend(f"run_complete.json does not list {token}" for token in not_listed)
    detail.extend(
        f"run_complete.json lists {token}, which is not in the cohort" for token in not_in_cohort
    )
    return {
        "run_complete_problems": 0,
        "tokens_not_listed": len(not_listed),
        "tokens_not_in_cohort": len(not_in_cohort),
    }


def _read_document(path: Path, identity: Mapping[str, str]) -> Union[TokenResultsV1, str]:
    """One token's document for one cell, or why it is not that token's document."""

    try:
        document = TokenResultsV1.model_validate_json(path.read_bytes(), strict=True)
    except (OSError, ValueError) as error:
        return f"is not a token results document: {error}"
    recorded = document.model_dump(include=set(identity))
    if recorded != dict(identity):
        return f"records {recorded}, not {dict(identity)}"
    return document


def _holds_the_replicates(document: TokenResultsV1, identity: tuple[str, str, str]) -> bool:
    records = {
        (result.scenario_token, result.family, result.configuration_id)
        for result in document.results
    }
    replicates = tuple(sorted(result.replicate for result in document.results))
    return records <= {identity} and replicates == STUDY_REPLICATES


def _check_documents(
    arm: StudyArmV1,
    arm_root: Path,
    cohort: CohortManifestV1,
    protocol_sha256: str,
    cohort_sha256: str,
    detail: list[str],
) -> dict[str, int]:
    tokens = _tokens(cohort)
    expected_files = set(ARM_FILES)
    found = missing = invalid = 0
    invalid_tokens: dict[str, str] = {}
    for cell in arm.cells:
        for token, family in tokens.items():
            name = f"{cell}/{token}.json"
            expected_files.add(name)
            path = arm_root / cell / f"{token}.json"
            if not path.is_file():
                missing += 1
                detail.append(f"{name} is missing")
                continue
            found += 1
            identity = {
                "scenario_token": token,
                "family": family,
                "split": cohort.split,
                "configuration_id": cell,
                "protocol_sha256": protocol_sha256,
                "cohort_manifest_sha256": cohort_sha256,
            }
            document = _read_document(path, identity)
            if isinstance(document, str):
                invalid += 1
                detail.append(f"{name} {document}")
            elif not document.valid:
                invalid_tokens.setdefault(token, f"{token} is invalid: {document.invalid_reason}")
            elif not _holds_the_replicates(document, (token, family, cell)):
                invalid += 1
                detail.append(
                    f"{name} does not hold replicates {STUDY_REPLICATES} of its own token and cell"
                )
    detail.extend(invalid_tokens.values())
    unexpected = sorted(set(_files_under(arm_root)) - expected_files)
    detail.extend(f"{name} is not a file an arm writes" for name in unexpected)
    return {
        "documents": found,
        "documents_missing": missing,
        "documents_invalid": invalid,
        "invalid_tokens": len(invalid_tokens),
        "unexpected_files": len(unexpected),
    }


def check_integrity(study: Path, arm_id: str, arm_root: Path, manifest: Path) -> GateResult:
    """G1 for one arm, against the cohort of `manifest`.

    `run_complete.json` lists exactly the cohort's tokens; there is one document
    per token per cell of the arm and no file other than `run_context.json`,
    `run_complete.json` and `run.log`; every document is its token's and cell's,
    valid, with replicates 0, 1 and 2; and the run context names the protocol and
    the cohort, the study file, the arm and its three factors, a tooling commit
    and an image identifier.
    """

    definition = load_study(study)
    arm = _arm(definition, arm_id)
    cohort = load_manifest(manifest)
    cohort_sha256 = membership_sha256(cohort)
    detail: list[str] = []
    counts = {
        "run_context_problems": _check_run_context(
            definition, arm, study_sha256(study), cohort_sha256, arm_root, detail
        ),
        **_check_run_complete(arm_root, tuple(_tokens(cohort)), cohort_sha256, detail),
        **_check_documents(
            arm, arm_root, cohort, definition.seed_namespace_protocol_sha256, cohort_sha256, detail
        ),
    }
    passed = not any(count for name, count in counts.items() if name != "documents")
    return GateResult("G1", passed, counts, tuple(f"{arm.id}: {line}" for line in detail))


def _check_integrity_of_arms(
    study: Path, arm_ids: Sequence[str], arms_root: Path, manifest: Path
) -> GateResult:
    """G1 over several arms: each arm's counts summed, and one commit and one image across them."""

    results = [check_integrity(study, arm_id, arms_root / arm_id, manifest) for arm_id in arm_ids]
    counts: dict[str, int] = {}
    for result in results:
        for name, count in result.counts.items():
            counts[name] = counts.get(name, 0) + count
    contexts = [
        context
        for context in (_read_run_context(arms_root / arm_id) for arm_id in arm_ids)
        if isinstance(context, StudyRunContext)
    ]
    detail = [line for result in results for line in result.local_detail]
    identities = {
        "distinct_commits": sorted({context.commit for context in contexts}),
        "distinct_container_digests": sorted({context.container_digest for context in contexts}),
    }
    for name, values in identities.items():
        counts[name] = len(values)
        if len(values) > 1:
            detail.append(f"the arms record {len(values)} values where one is required: {values}")
    passed = all(result.passed for result in results) and all(
        len(values) <= 1 for values in identities.values()
    )
    return GateResult("G1", passed, counts, tuple(detail))


# --------------------------------------------------------------------------
# G5: environment
# --------------------------------------------------------------------------


def check_environment(study: Path, arms_root: Path, arm: Optional[str] = None) -> GateResult:
    """G5: every checked arm's run context names the Python and numpy versions the study records."""

    definition = load_study(study)
    python_version = definition.environment.python_version
    numpy_version = definition.environment.numpy_version
    unreadable = python_mismatches = numpy_mismatches = 0
    detail: list[str] = []
    for arm_id in checked_arms(definition, arm):
        context = _read_run_context(arms_root / arm_id)
        if isinstance(context, str):
            unreadable += 1
            detail.append(f"{arm_id}: {context}")
            continue
        python_mismatches += context.python_version != python_version
        numpy_mismatches += context.numpy_version != numpy_version
        if (context.python_version, context.numpy_version) != (python_version, numpy_version):
            detail.append(
                f"{arm_id}: Python {context.python_version}, numpy {context.numpy_version}; "
                f"the study file records Python {python_version}, numpy {numpy_version}"
            )
    counts = {
        "run_contexts_unreadable": unreadable,
        "python_mismatches": python_mismatches,
        "numpy_mismatches": numpy_mismatches,
    }
    return GateResult("G5", not any(counts.values()), counts, tuple(detail))


# --------------------------------------------------------------------------
# G2 and G3: byte equality
# --------------------------------------------------------------------------


def compare_documents(
    left_root: Path, right_root: Path, cells: Sequence[str], tokens: Sequence[str]
) -> GateResult:
    """Compare two runs' documents byte for byte, cell by cell.

    A document differs when its bytes differ or when one side lacks it; a file
    in a cell's directory that is not a document of `tokens` is counted too.
    `counts` gives the number of differing documents per cell.
    """

    expected = {f"{token}.json" for token in tokens}
    counts: dict[str, int] = {}
    detail: list[str] = []
    for cell in cells:
        left = _files_under(left_root / cell)
        right = _files_under(right_root / cell)
        differing = 0
        for name in sorted(expected | set(left) | set(right)):
            reason = _difference(name, expected, (left_root, left), (right_root, right))
            if reason is not None:
                differing += 1
                detail.append(f"{cell}/{name} {reason}")
        counts[cell] = differing
    return GateResult("documents", not any(counts.values()), counts, tuple(detail))


def _difference(
    name: str,
    expected: AbstractSet[str],
    left: tuple[Path, Mapping[str, Path]],
    right: tuple[Path, Mapping[str, Path]],
) -> Optional[str]:
    """Why one file of a cell's directory differs between two runs, or `None` when it does not."""

    (left_root, left_files), (right_root, right_files) = left, right
    if name not in expected:
        return "is not a document of the cohort"
    if name not in left_files:
        return f"is missing under {left_root}"
    if name not in right_files:
        return f"is missing under {right_root}"
    if left_files[name].read_bytes() != right_files[name].read_bytes():
        return "differs"
    return None


def check_replication(
    study: Path, arms_root: Path, released_root: Path, tokens: Sequence[str]
) -> GateResult:
    """G2: every document of arm A is byte-identical to the released one, cell by cell."""

    arm = _arm(load_study(study), REPLICATION_ARM)
    compared = compare_documents(arms_root / arm.id, released_root, arm.cells, tokens)
    return dataclasses.replace(compared, gate="G2")


def _invariance_comparisons(definition: StudyDefinitionV1) -> tuple[tuple[str, str, str], ...]:
    """(cell, arm, what it is compared with): the released records, arm A, or its policy's first arm."""

    comparisons: list[tuple[str, str, str]] = [
        (NO_AEB_CELL, arm.id, RELEASED) for arm in definition.arms
    ]
    comparisons.extend(
        (ORACLE_CELL, arm.id, RELEASED)
        for arm in definition.arms
        if arm.aeb_policy == RELEASED_POLICY
    )
    comparisons.extend(
        (NO_AEB_CELL, arm.id, REPLICATION_ARM)
        for arm in definition.arms
        if arm.id != REPLICATION_ARM
    )
    leaders: dict[str, str] = {}
    for arm in definition.arms:
        leader = leaders.setdefault(arm.aeb_policy, arm.id)
        if leader != arm.id:
            comparisons.append((ORACLE_CELL, arm.id, leader))
    return tuple(comparisons)


def check_invariance(
    study: Path,
    arms_root: Path,
    released_root: Path,
    tokens: Sequence[str],
    reference: Reference = RELEASED,
) -> GateResult:
    """G3: the cells no factor changes are byte-identical where the plan requires it.

    `no_aeb` is compared across the arms and `oracle_aeb` within each policy,
    and both are required. Every arm's `no_aeb` and each v1 arm's oracle are
    also compared with the released records; with `reference="arm-a"` those
    comparisons are counted and not required. Each count is keyed
    `<cell>:<arm>:<released or the arm compared with>`.
    """

    _refuse_an_unknown_reference(reference)
    counts: dict[str, int] = {}
    detail: list[str] = []
    passed = True
    for cell, arm_id, other in _invariance_comparisons(load_study(study)):
        against_released = other == RELEASED
        right = released_root if against_released else arms_root / other
        compared = compare_documents(arms_root / arm_id, right, (cell,), tokens)
        key = f"{cell}:{arm_id}:{other}"
        counts[key] = compared.counts[cell]
        detail.extend(f"{key}: {line}" for line in compared.local_detail)
        required = reference == RELEASED or not against_released
        passed = passed and (compared.passed or not required)
    return GateResult("G3", passed, counts, tuple(detail))


# --------------------------------------------------------------------------
# One verification
# --------------------------------------------------------------------------


def verify_study(
    study: Path,
    arms_root: Path,
    released_root: Optional[Path],
    manifest: Path,
    arm: Optional[str] = None,
    reference: Reference = RELEASED,
    pilot: bool = False,
) -> tuple[GateResult, ...]:
    """The gates over the arms under `arms_root`, one directory per arm, in gate order.

    Without `arm`, G1 and G5 on every arm of the study, then G2 and G3; an arm
    that is missing fails them. With `arm`, G1 and G5 on that arm, and G2 when it
    is arm A. With `pilot`, G1 against the smoke manifest and G5 on the arms
    under `artifacts/pilot/`, and no comparison with the released records.
    Refused: an unknown arm or reference, a pilot outside `artifacts/pilot/` or
    on another manifest than the smoke one, and a formal check without the
    released records or on another cohort than the study's.
    """

    definition = load_study(study)
    _refuse_an_unknown_reference(reference)
    arm_ids = checked_arms(definition, arm)
    cohort = load_manifest(manifest)
    compared_with: Optional[Path] = None
    if pilot:
        pilot_root = (Path.cwd() / PILOT_ROOT).resolve()
        if not arms_root.resolve().is_relative_to(pilot_root):
            raise ValueError(f"a pilot is verified under {PILOT_ROOT}/, and {arms_root} is not")
        if cohort.split != PILOT_SPLIT:
            raise ValueError(
                f"a pilot runs on the {PILOT_SPLIT} manifest, and {manifest} declares the split "
                f"{cohort.split!r}"
            )
    elif released_root is None:
        raise ValueError("a formal check compares the arms with the released records; name them")
    elif membership_sha256(cohort) != definition.cohort_membership_sha256:
        raise ValueError(
            f"the manifest's membership hash {membership_sha256(cohort)} is not the cohort "
            f"membership hash {definition.cohort_membership_sha256} the study file records"
        )
    else:
        compared_with = released_root

    results = [_check_integrity_of_arms(study, arm_ids, arms_root, manifest)]
    if compared_with is not None:
        tokens = tuple(_tokens(cohort))
        if REPLICATION_ARM in arm_ids:
            results.append(check_replication(study, arms_root, compared_with, tokens))
        if arm is None:
            results.append(check_invariance(study, arms_root, compared_with, tokens, reference))
    results.append(check_environment(study, arms_root, arm))
    return tuple(results)


def required_gates_passed(results: Sequence[GateResult], reference: Reference = RELEASED) -> bool:
    """Whether every gate the mode requires passed; arm-A reference mode does not require G2."""

    return all(result.passed or (reference == ARM_A and result.gate == "G2") for result in results)


# --------------------------------------------------------------------------
# The gate file
# --------------------------------------------------------------------------


def gates_document(
    study: Path,
    arms_checked: Sequence[str],
    results: Sequence[GateResult],
    reference: Reference = RELEASED,
) -> StudyGatesV1:
    """The gate file for `results`, naming the study file and the protocol and cohort it records."""

    definition = load_study(study)
    return StudyGatesV1(
        schema_version=STUDY_GATES_SCHEMA_VERSION,
        study_sha256=study_sha256(study),
        arms_checked=tuple(arms_checked),
        protocol_sha256=definition.seed_namespace_protocol_sha256,
        cohort_manifest_sha256=definition.cohort_membership_sha256,
        reference=reference,
        exploratory=reference == ARM_A,
        gates=tuple(
            StudyGateV1(gate=result.gate, passed=result.passed, counts=dict(result.counts))
            for result in results
        ),
        artifacts_only_detail={
            result.gate: result.local_detail for result in results if result.local_detail
        },
    )


def write_gates(path: Path, gates: StudyGatesV1) -> None:
    """Write a gate file as stable UTF-8/LF JSON, the form of the published documents."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            gates.model_dump(mode="json"),
            handle,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")


def load_gates(path: Path) -> StudyGatesV1:
    """Read a gate file, with or without its local detail."""

    return StudyGatesV1.model_validate_json(path.read_bytes())


def published_gates(gates: StudyGatesV1) -> dict[str, Any]:
    """What a gate file publishes: everything but `artifacts_only_detail`."""

    return gates.model_dump(mode="json", exclude={"artifacts_only_detail"})


# --------------------------------------------------------------------------
# Token refusal
# --------------------------------------------------------------------------


def committed_cohort_tokens(repository_root: Path) -> frozenset[str]:
    """Every scenario token of the committed cohort files, manifests and eligibility records alike."""

    directory = repository_root.joinpath(*COHORT_DIRECTORY)
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"no committed cohort file under {'/'.join(COHORT_DIRECTORY)}")
    tokens: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        version = json.loads(text).get("schema_version")
        if version == "aeb-cohort-manifest/v1":
            manifest = CohortManifestV1.model_validate_json(text)
            tokens.update(token for family in manifest.families.values() for token in family)
        elif version == "aeb-cohort-eligibility/v1":
            eligibility = CohortEligibilityV1.model_validate_json(text)
            tokens.update(row.scenario_token for row in eligibility.examined)
        else:
            raise ValueError(f"{path.name} is neither a cohort manifest nor an eligibility record")
    return frozenset(tokens)


def _strings(value: object) -> Iterator[str]:
    """Every string in parsed JSON, keys included; numbers are never read as text."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def refuse_token_strings(value: object, cohort_tokens: AbstractSet[str]) -> None:
    """Refuse a document that could identify a scenario.

    Refused: any string, value or key, that is sixteen lower-case hexadecimal
    digits, and any string that contains a token of `cohort_tokens`. Numbers
    are not scanned, so a float whose digits run sixteen long passes. A token
    of the scenario shape is found by comparing sixteen-character windows; a
    member of any other shape is searched as a substring, and the one that
    sorts first is named.
    """

    if "" in cohort_tokens:
        raise ValueError("a cohort token must not be empty")
    other_shapes = sorted(token for token in cohort_tokens if not TOKEN_SHAPE.fullmatch(token))
    for text in _strings(value):
        if TOKEN_SHAPE.fullmatch(text):
            raise ValueError(f"{text!r} has the shape of a scenario token")
        for start in range(len(text) - _TOKEN_LENGTH + 1):
            window = text[start : start + _TOKEN_LENGTH]
            if window in cohort_tokens:
                raise ValueError(f"a string holds the scenario token {window!r}")
        for token in other_shapes:
            if token in text:
                raise ValueError(f"a string holds the scenario token {token!r}")
