"""The evidence a results pull request commits, written from what the study left under `artifacts/`.

Section 11 of `docs/studies/aeb-policy-v2/analysis-plan.md` and section 8 of
`docs/posthoc/nuplan_aeb_v2-addendum/addendum-plan.md` name what each part
publishes in its `evidence/` directory. The writers here copy numbers and
recompute none.

- THE STUDY (`write_study_evidence`) writes `summary.json`, a copy of the
  summary; `policy-v2-evidence.json`, the summary's numbers under the hashes
  the claims audit reads; `gates.json`, the gate file of the attempt the
  results come from, without its local detail; `gates-attempt-<n>.json` for
  each earlier failed attempt; and `reproduction.json`, which records the
  pre-registration pull request, each arm's run and the outcome of every gate.
  A study reported not completed (analysis plan section 9) has no summary, so
  it publishes only the gate files and `reproduction.json`.
- THE ADDENDUM (`write_addendum_evidence`) writes `addendum-summary.json`, a
  copy of the addendum summary, which carries its reproduction gate's outcome,
  and `attribution-addendum-evidence.json`, the summary's numbers.

Nothing is written unless every file can be: an output under the released
evidence or under the other part's directory is refused, as is a file that
would replace a different existing one, and any string with the shape of a
scenario token or holding a token of the committed cohort files. An arm's run
log names tokens, so only the times at the start of its first and last lines
are read from it.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional, TypeVar

from pydantic import BaseModel

from aebrisk.artifacts.study_documents import (
    ATTRIBUTION_ADDENDUM_EVIDENCE_SCHEMA_VERSION,
    POLICY_V2_EVIDENCE_SCHEMA_VERSION,
    STUDY_GATES,
    STUDY_REPRODUCTION_SCHEMA_VERSION,
    AttributionAddendumEvidenceV1,
    AttributionAddendumV1,
    PolicyV2EvidenceV1,
    PolicyV2SummaryV1,
    StudyArmRunV1,
    StudyG0V1,
    StudyGateFileV1,
    StudyGateName,
    StudyGateRecordV1,
    StudyGatesV1,
    StudyGateV1,
    StudyReproductionV1,
    StudyRunContextV1,
)
from aebrisk.study.gates import (
    ARM_A,
    committed_cohort_tokens,
    load_gates,
    published_gates,
    refuse_token_strings,
)

SUMMARY_FILE = "summary.json"
POLICY_V2_EVIDENCE_FILE = "policy-v2-evidence.json"
GATES_FILE = "gates.json"
REPRODUCTION_FILE = "reproduction.json"
ADDENDUM_SUMMARY_FILE = "addendum-summary.json"
ADDENDUM_EVIDENCE_FILE = "attribution-addendum-evidence.json"

#: The released evidence, which no writer adds to, and each part's own directory.
RELEASED_EVIDENCE_DIRECTORY: tuple[str, ...] = ("docs", "evidence", "nuplan_aeb_v2")
STUDY_DIRECTORY: tuple[str, ...] = ("docs", "studies", "aeb-policy-v2")
ADDENDUM_DIRECTORY: tuple[str, ...] = ("docs", "posthoc", "nuplan_aeb_v2-addendum")

#: The gate file of one attempt, as `study verify` writes it for all arms or for arm A alone.
ATTEMPT_GATES = re.compile(r"attempt-([1-9][0-9]*)\.gates(?:-A)?\.json")

#: The time each line of an arm's run log begins with, then a space.
_RUN_LOG_TIME = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z) ")

_Evidence = TypeVar("_Evidence", bound=BaseModel)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _stable_bytes(payload: Mapping[str, Any]) -> bytes:
    """The stable UTF-8/LF JSON form of every published document."""

    return (
        json.dumps(payload, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _refuse_output_directory(
    output_dir: Path, repository_root: Path, other_part: tuple[str, ...]
) -> None:
    target = (repository_root / output_dir).resolve()
    for parts in (RELEASED_EVIDENCE_DIRECTORY, other_part):
        if target.is_relative_to(repository_root.joinpath(*parts).resolve()):
            raise ValueError(
                f"this evidence is never written under {'/'.join(parts)}/, and {output_dir} is"
            )


def _publish(
    output_dir: Path, documents: Sequence[tuple[str, Mapping[str, Any]]], repository_root: Path
) -> tuple[Path, ...]:
    """Write every document, or none: each is checked for tokens and against what is there."""

    cohort_tokens = committed_cohort_tokens(repository_root)
    rendered: list[tuple[Path, bytes]] = []
    for name, payload in documents:
        refuse_token_strings(payload, cohort_tokens)
        rendered.append((output_dir / name, _stable_bytes(payload)))
    different = [
        path.name for path, data in rendered if path.exists() and path.read_bytes() != data
    ]
    if different:
        raise ValueError(
            f"refusing to replace different existing files in {output_dir}: {different}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, data in rendered:
        path.write_bytes(data)
    return tuple(path for path, _ in rendered)


def _copied(source: BaseModel, model: type[_Evidence], schema_version: str) -> _Evidence:
    """Every field of `model` but its version, copied from the field of the same name in `source`."""

    fields = set(model.model_fields) - {"schema_version"}
    return model.model_validate(
        {**source.model_dump(mode="json", include=fields), "schema_version": schema_version}
    )


# --------------------------------------------------------------------------
# The inputs of the study's evidence
# --------------------------------------------------------------------------


def _read_g0(path: Path) -> StudyG0V1:
    """The operator's G0 record, with the pilot's local detail left out."""

    if not path.is_file():
        raise ValueError(f"G0 is recorded from g0.json only, and {path} does not exist")
    g0 = StudyG0V1.model_validate_json(path.read_bytes())
    return g0.model_copy(
        update={"pilot_gates": g0.pilot_gates.model_copy(update={"artifacts_only_detail": {}})}
    )


def _gate(gates: StudyGatesV1, name: str) -> Optional[StudyGateV1]:
    return next((gate for gate in gates.gates if gate.gate == name), None)


def _refuse_another_study(
    label: str, document: BaseModel, gates: StudyGatesV1, fields: Sequence[str]
) -> None:
    """Refuse a document that names another study, protocol, cohort or reference than the gate file."""

    differing = [field for field in fields if getattr(document, field) != getattr(gates, field)]
    if differing:
        raise ValueError(f"the {label} and the gate file differ in {differing}")


def _gate_file_passed(gates: StudyGatesV1) -> bool:
    """Whether every gate the file's mode requires passed; arm-A reference mode does not require G2."""

    return all(
        gate.passed or (gates.reference == ARM_A and gate.gate == "G2") for gate in gates.gates
    )


def _earlier_attempts(
    earlier_gates: Sequence[Path], gates: StudyGatesV1, attempt: Optional[int]
) -> list[tuple[int, StudyGatesV1]]:
    """The earlier attempts' gate files by attempt number, each of the same protocol and cohort."""

    earlier: list[tuple[int, StudyGatesV1]] = []
    for path in earlier_gates:
        match = ATTEMPT_GATES.fullmatch(path.name)
        if match is None:
            raise ValueError(
                "an earlier attempt's gate file is named attempt-<n>.gates.json or "
                f"attempt-<n>.gates-A.json, got {path.name}"
            )
        document = load_gates(path)
        _refuse_another_study(
            f"gate file {path.name}", document, gates, ("protocol_sha256", "cohort_manifest_sha256")
        )
        earlier.append((int(match.group(1)), document))
    numbers = [number for number, _ in earlier] + ([] if attempt is None else [attempt])
    repeated = sorted({number for number in numbers if numbers.count(number) > 1})
    if repeated:
        raise ValueError(
            f"one gate file per attempt is published, got two for attempt {repeated[0]}"
        )
    return sorted(earlier, key=lambda item: item[0])


def _run_times(path: Path) -> tuple[str, str]:
    """The times at the start of the first and last lines of a run log, and nothing else of it."""

    if not path.is_file():
        raise ValueError(f"an arm's start and end are read from its run.log, and {path} is missing")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{path} is empty, so the arm has no start or end")
    times = [_RUN_LOG_TIME.match(line) for line in (lines[0], lines[-1])]
    if times[0] is None or times[-1] is None:
        raise ValueError(f"the first and last lines of {path} must begin with a UTC time")
    return times[0].group(1), times[-1].group(1)


def _arm_run(arm_root: Path) -> StudyArmRunV1:
    path = arm_root / "run_context.json"
    if not path.is_file():
        raise ValueError(f"an arm's commit and image are read from {path}, which is missing")
    context = StudyRunContextV1.model_validate_json(path.read_bytes())
    if context.arm_id != arm_root.name:
        raise ValueError(f"{path} is the run context of arm {context.arm_id}, not {arm_root.name}")
    started, finished = _run_times(arm_root / "run.log")
    return StudyArmRunV1(
        arm_id=context.arm_id,
        tooling_commit=context.commit,
        image_id=context.container_digest,
        started_at_utc=started,
        finished_at_utc=finished,
        python_version=context.python_version,
        numpy_version=context.numpy_version,
    )


def _arm_runs(
    arms_root: Path, arm_ids: Sequence[str], every_arm: bool
) -> tuple[StudyArmRunV1, ...]:
    """The runs of the checked arms present under `arms_root`; a completed study needs every one."""

    present = [arm for arm in arm_ids if (arms_root / arm).is_dir()]
    missing = [arm for arm in arm_ids if arm not in present]
    if every_arm and missing:
        raise ValueError(
            f"a completed study records every arm, and {missing} are not under {arms_root}"
        )
    return tuple(_arm_run(arms_root / arm) for arm in present)


def _record(name: StudyGateName, gate: Optional[StudyGateV1]) -> StudyGateRecordV1:
    if gate is None:
        return StudyGateRecordV1(gate=name, outcome="not run")
    return StudyGateRecordV1(
        gate=name, outcome="passed" if gate.passed else "failed", counts=dict(gate.counts)
    )


# --------------------------------------------------------------------------
# The writers
# --------------------------------------------------------------------------


def write_study_evidence(
    summary_path: Optional[Path],
    gates_path: Path,
    g0_path: Path,
    arms_root: Path,
    preregistration_pr: int,
    preregistration_commit: str,
    preregistration_merged_at: str,
    output_dir: Path,
    not_completed: bool = False,
    earlier_gates: Sequence[Path] = (),
    g4_path: Optional[Path] = None,
) -> tuple[Path, ...]:
    """Write the policy v2 study's evidence into `output_dir` and return the files written.

    With a summary: `summary.json`, `policy-v2-evidence.json`, `gates.json`,
    `gates-attempt-<n>.json` for each of `earlier_gates`, and
    `reproduction.json`. With `not_completed` there is no summary, and only the
    gate files and `reproduction.json` are written; G4 is then recorded from
    `g4_path` when it is given, and as not run otherwise, and the arms are
    those present under `arms_root`. The repository is the working directory.
    """

    repository_root = Path.cwd()
    _refuse_output_directory(output_dir, repository_root, ADDENDUM_DIRECTORY)
    if not_completed and summary_path is not None:
        raise ValueError("a study reported not completed has no summary; name none")
    if not not_completed and summary_path is None:
        raise ValueError("the evidence of a completed study copies its summary; name it")
    gates = load_gates(gates_path)
    g5 = _gate(gates, "G5")
    if g5 is None:
        raise ValueError(f"G5 is recorded from the gate file, and {gates_path} holds no G5 result")
    g0 = _read_g0(g0_path)
    identity = ("study_sha256", "protocol_sha256", "cohort_manifest_sha256", "reference")

    summary: Optional[PolicyV2SummaryV1] = None
    g4: Optional[StudyGateV1] = None
    if summary_path is None:
        if g4_path is not None:
            g4_file = load_gates(g4_path)
            _refuse_another_study("G4 file", g4_file, gates, identity)
            g4 = _gate(g4_file, "G4")
            if g4 is None:
                raise ValueError(f"{g4_path} holds no G4 result")
    else:
        summary = PolicyV2SummaryV1.model_validate_json(summary_path.read_bytes())
        _refuse_another_study("summary", summary, gates, identity)
        g4 = summary.g4

    attempt_match = ATTEMPT_GATES.fullmatch(gates_path.name)
    attempt = None if attempt_match is None else int(attempt_match.group(1))
    earlier = _earlier_attempts(earlier_gates, gates, attempt)

    runs = _arm_runs(arms_root, gates.arms_checked, every_arm=summary is not None)
    for run in runs:
        if run.tooling_commit != g0.tooling_commit:
            raise ValueError(
                f"g0.json names the tooling commit {g0.tooling_commit}, and arm {run.arm_id} "
                f"ran {run.tooling_commit}"
            )

    pilot_passed = bool(g0.pilot_gates.gates) and all(gate.passed for gate in g0.pilot_gates.gates)
    outcomes: dict[StudyGateName, Optional[StudyGateV1]] = {
        "G1": _gate(gates, "G1"),
        "G2": _gate(gates, "G2"),
        "G3": _gate(gates, "G3"),
        "G4": g4,
        "G5": g5,
    }
    reproduction = StudyReproductionV1(
        schema_version=STUDY_REPRODUCTION_SCHEMA_VERSION,
        study_sha256=gates.study_sha256,
        protocol_sha256=gates.protocol_sha256,
        cohort_manifest_sha256=gates.cohort_manifest_sha256,
        reference=gates.reference,
        exploratory=gates.exploratory,
        preregistration_pull_request=preregistration_pr,
        preregistration_commit=preregistration_commit,
        preregistration_merged_at=preregistration_merged_at,
        arms=runs,
        gates=(
            StudyGateRecordV1(gate="G0", outcome="passed" if pilot_passed else "failed"),
            *(_record(name, outcomes[name]) for name in STUDY_GATES[1:]),
        ),
        gate_files=(
            StudyGateFileV1(file=GATES_FILE, attempt=attempt, passed=_gate_file_passed(gates)),
            *(
                StudyGateFileV1(
                    file=f"gates-attempt-{number}.json",
                    attempt=number,
                    passed=_gate_file_passed(document),
                )
                for number, document in earlier
            ),
        ),
        g0=g0,
    )

    documents: list[tuple[str, Mapping[str, Any]]] = []
    if summary is not None:
        evidence = _copied(summary, PolicyV2EvidenceV1, POLICY_V2_EVIDENCE_SCHEMA_VERSION)
        documents.append((SUMMARY_FILE, summary.model_dump(mode="json")))
        documents.append((POLICY_V2_EVIDENCE_FILE, evidence.model_dump(mode="json")))
    documents.append((GATES_FILE, published_gates(gates)))
    documents.extend(
        (f"gates-attempt-{number}.json", published_gates(document)) for number, document in earlier
    )
    documents.append((REPRODUCTION_FILE, reproduction.model_dump(mode="json")))
    return _publish(output_dir, documents, repository_root)


def write_addendum_evidence(addendum_path: Path, output_dir: Path) -> tuple[Path, ...]:
    """Write `addendum-summary.json` and `attribution-addendum-evidence.json` into `output_dir`.

    The repository is the working directory.
    """

    repository_root = Path.cwd()
    _refuse_output_directory(output_dir, repository_root, STUDY_DIRECTORY)
    addendum = AttributionAddendumV1.model_validate_json(addendum_path.read_bytes())
    evidence = _copied(
        addendum, AttributionAddendumEvidenceV1, ATTRIBUTION_ADDENDUM_EVIDENCE_SCHEMA_VERSION
    )
    return _publish(
        output_dir,
        (
            (ADDENDUM_SUMMARY_FILE, addendum.model_dump(mode="json")),
            (ADDENDUM_EVIDENCE_FILE, evidence.model_dump(mode="json")),
        ),
        repository_root,
    )


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    """Whether `ancestor` is `descendant` or one of its ancestors in the Git history at `repo`.

    It wraps `git merge-base --is-ancestor`. A commit Git cannot resolve is
    refused rather than reported as no ancestor.
    """

    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git could not compare {ancestor} with {descendant}: {detail}")
    return result.returncode == 0
