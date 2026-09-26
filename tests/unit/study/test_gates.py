"""The gates of the policy v2 study, and the preflight that runs before any arm.

Every gate reads files an arm wrote, so these tests write the files by hand: a
small cohort of four tokens, a released record for each study cell, and the five
arms of the committed study file. Arm A and every arm's `no_aeb` reproduce the
released records byte for byte, and the oracles agree within each policy, which
is what a correct run produces. Each test then damages one file and checks that
the gate that owns it fails, counts the damage where the gate file publishes it,
and leaves the other gates alone.

The released-hash reader and the preflight read two records of the released run
that are kept outside Git. Their fixtures copy those records' exact shape: the
keys, the CRLF line endings and the seven-digit fractional seconds.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import tracemalloc
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml
from pydantic import ValidationError

from aebrisk.artifacts.envelope import canonical_json_bytes
from aebrisk.artifacts.results import AEBScenarioResultV2, ScenarioFamily
from aebrisk.artifacts.study_documents import StudyGatesV1, StudyGateV1
from aebrisk.cohort.manifest import load_manifest, membership_sha256
from aebrisk.simulation.orchestrate import TokenResultsV1, token_results_bytes
from aebrisk.study.gates import (
    GateResult,
    check_environment,
    check_integrity,
    check_invariance,
    check_released_records,
    check_replication,
    committed_cohort_tokens,
    compare_documents,
    gates_document,
    load_gates,
    preflight,
    published_gates,
    refuse_token_strings,
    required_gates_passed,
    verify_study,
    write_gates,
)

ROOT = Path(__file__).resolve().parents[3]
COMMITTED_STUDY = ROOT / "configs" / "experiments" / "aeb_policy_v2_study.yaml"
COHORT_FILES = ROOT / "docs" / "evidence" / "nuplan_aeb_v2" / "cohort"

#: The released protocol's SHA-256, which the committed study file records.
PROTOCOL_SHA = "bbf0b6d31943a0f99160afe1d4c18f9a181850367494004037bab98d8f2e59f9"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "sha256:" + "d" * 64

FULL_COALITION = "coalition-dropout+localization_shape+latency+track_instability"
STUDY_CELLS = (
    "no_aeb",
    "oracle_aeb",
    "dropout-medium",
    "localization_shape-medium",
    "latency-medium",
    "track_instability-medium",
    "coalition-none",
    FULL_COALITION,
)
REPLICATION = "A-v1-replication"
#: Each arm's policy, keying, velocity estimate and cells, as the study file sets them.
ARMS: Mapping[str, tuple[str, str, str, tuple[str, ...]]] = {
    REPLICATION: ("v1", "dropout-keyed", "finite-difference", STUDY_CELLS),
    "B-v2-gated": ("v2", "dropout-keyed", "finite-difference", STUDY_CELLS),
    "C-v1-kalman": ("v1", "dropout-keyed", "cv-kalman", STUDY_CELLS),
    "D-v2-kalman": ("v2", "dropout-keyed", "cv-kalman", STUDY_CELLS),
    "E-v2-channel-rng": (
        "v2",
        "channel-independent",
        "finite-difference",
        ("no_aeb", "oracle_aeb", "dropout-medium", FULL_COALITION),
    ),
}

#: A cohort of four tokens in two families. None has the shape of a nuPlan token.
TOKENS_BY_FAMILY: Mapping[ScenarioFamily, tuple[str, ...]] = {
    "lead_or_stopping": ("lead-a", "lead-b"),
    "cut_in_or_crossing": ("cut-a",),
    "pedestrian_or_crosswalk": ("walk-a",),
    "bicycle_or_vru": (),
}
FAMILY_OF = {token: family for family, tokens in TOKENS_BY_FAMILY.items() for token in tokens}
TOKENS = tuple(sorted(FAMILY_OF))
LOGS = (
    "2021.01.01.00.00.00_veh-01_00000_00100.db",
    "2021.01.02.00.00.00_veh-02_00000_00200.db",
)

#: The G1 counts that fail the gate; the others describe what was found.
G1_FAILURES = (
    "run_context_problems",
    "run_complete_problems",
    "tokens_not_listed",
    "tokens_not_in_cohort",
    "documents_missing",
    "documents_invalid",
    "invalid_tokens",
    "unexpected_files",
)


# --------------------------------------------------------------------------
# Files an arm writes
# --------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")
    return path


def write_crlf_json(path: Path, value: Any) -> Path:
    """JSON in the shape of the private records: two-space indent, CRLF, a final LF."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\r\n".join(json.dumps(value, indent=2).split("\n")) + "\n").encode())
    return path


def write_manifest(
    path: Path,
    split: str = "evaluation",
    log_names: Sequence[str] = LOGS[:1],
    protocol_sha256: str = PROTOCOL_SHA,
) -> Path:
    return write_json(
        path,
        {
            "schema_version": "aeb-cohort-manifest/v1",
            "split": split,
            "protocol_sha256": protocol_sha256,
            "families": {family: list(tokens) for family, tokens in TOKENS_BY_FAMILY.items()},
            "log_names": list(log_names),
        },
    )


def write_study(path: Path, manifest: Path, **updates: Any) -> Path:
    """The committed study file, recording the fixture cohort instead of the released one."""

    document = yaml.safe_load(COMMITTED_STUDY.read_text(encoding="utf-8"))
    document["cohort_manifest_file_sha256"] = file_sha256(manifest)
    document["cohort_membership_sha256"] = membership_sha256(load_manifest(manifest))
    document.update(updates)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def record(token: str, cell: str, replicate: int, variant: int) -> AEBScenarioResultV2:
    return AEBScenarioResultV2(
        schema_version="aeb-scenario-result/v2",
        scenario_token=token,
        family=FAMILY_OF[token],
        configuration_id=cell,
        replicate=replicate,
        valid=True,
        collision_vru=0,
        collision_vehicle=0,
        collision_object=0,
        collision_energy=0.0,
        contacts_not_at_fault=0,
        min_clearance_m=4.0 + variant,
        missed_interventions=0,
        false_interventions=0,
        max_deceleration_mps2=2.0,
        max_abs_jerk_mps3=3.0,
        intervention_duration_s=0.0,
        simulated_duration_s=10.0,
    )


def results_document(
    token: str, cell: str, manifest: Path, variant: int = 0, replicates: Sequence[int] = (0, 1, 2)
) -> TokenResultsV1:
    cohort = load_manifest(manifest)
    return TokenResultsV1(
        schema_version="aeb-token-results/v1",
        scenario_token=token,
        family=FAMILY_OF[token],
        split=cohort.split,
        configuration_id=cell,
        protocol_sha256=PROTOCOL_SHA,
        cohort_manifest_sha256=membership_sha256(cohort),
        valid=True,
        results=tuple(record(token, cell, replicate, variant) for replicate in replicates),
    )


def write_document(path: Path, document: TokenResultsV1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(token_results_bytes(document))


def variant_of(arm_id: str, cell: str) -> int:
    """Arm A and every no_aeb reproduce the released records; oracles agree within a policy."""

    if arm_id == REPLICATION or cell == "no_aeb":
        return 0
    if cell == "oracle_aeb":
        return 0 if ARMS[arm_id][0] == "v1" else 1
    return 2 + list(ARMS).index(arm_id)


def run_context(arm_id: str, study: Path, manifest: Path, /, **updates: str) -> dict[str, str]:
    policy, scheme, estimator, _ = ARMS[arm_id]
    context = {
        "configuration_id": f"study:{arm_id}",
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_sha256": membership_sha256(load_manifest(manifest)),
        "container_digest": IMAGE,
        "commit": COMMIT,
        "study_sha256": file_sha256(study),
        "arm_id": arm_id,
        "aeb_policy": policy,
        "policy_sha256": "a" * 64,
        "rng_scheme": scheme,
        "velocity_estimator": estimator,
        "velocity_parameters": "",
        "error_config_sha256": "b" * 64,
        "python_version": "3.9.19",
        "numpy_version": "1.23.4",
    }
    context.update(updates)
    return context


def run_complete(manifest: Path, tokens: Sequence[str] = TOKENS) -> dict[str, Any]:
    return {
        "schema_version": "aeb-run-complete/v1",
        "cohort_manifest_sha256": membership_sha256(load_manifest(manifest)),
        "tokens": list(tokens),
    }


def write_arm(arms_root: Path, arm_id: str, study: Path, manifest: Path) -> Path:
    arm_root = arms_root / arm_id
    for cell in ARMS[arm_id][3]:
        for token in TOKENS:
            write_document(
                arm_root / cell / f"{token}.json",
                results_document(token, cell, manifest, variant_of(arm_id, cell)),
            )
    write_json(arm_root / "run_context.json", run_context(arm_id, study, manifest))
    write_json(arm_root / "run_complete.json", run_complete(manifest))
    (arm_root / "run.log").write_text(
        f"2026-09-27T00:00:00.000000Z run context for study:{arm_id} is recorded\n",
        encoding="utf-8",
    )
    return arm_root


def write_released(root: Path, manifest: Path) -> Path:
    for cell in STUDY_CELLS:
        for token in TOKENS:
            write_document(root / cell / f"{token}.json", results_document(token, cell, manifest))
    write_json(root / "run_context.json", {"configuration_id": "all"})
    write_json(root / "run_complete.json", run_complete(manifest))
    (root / "run.log").write_text("released\n", encoding="utf-8")
    return root


@dataclass(frozen=True)
class Run:
    """Five arms of one attempt, the released records they are compared with, and the inputs."""

    study: Path
    manifest: Path
    arms_root: Path
    released_root: Path

    def arm(self, arm_id: str) -> Path:
        return self.arms_root / arm_id

    def document(self, arm_id: str, cell: str, token: str = TOKENS[0]) -> Path:
        return self.arm(arm_id) / cell / f"{token}.json"

    def verify(self, **options: Any) -> dict[str, GateResult]:
        results = verify_study(
            self.study, self.arms_root, self.released_root, self.manifest, **options
        )
        return {result.gate: result for result in results}

    def rewrite(self, path: Path, variant: int) -> None:
        """Replace a document with a valid one that differs from it in one measurement."""

        cell, token = path.parent.name, path.stem
        write_document(path, results_document(token, cell, self.manifest, variant))

    def rewrite_context(self, arm_id: str, /, **updates: str) -> None:
        write_json(
            self.arm(arm_id) / "run_context.json",
            run_context(arm_id, self.study, self.manifest, **updates),
        )


@pytest.fixture()
def run(tmp_path: Path) -> Run:
    manifest = write_manifest(tmp_path / "evaluation.json")
    study = write_study(tmp_path / "study.yaml", manifest)
    arms_root = tmp_path / "attempt-1"
    for arm_id in ARMS:
        write_arm(arms_root, arm_id, study, manifest)
    return Run(
        study=study,
        manifest=manifest,
        arms_root=arms_root,
        released_root=write_released(tmp_path / "released", manifest),
    )


def flip_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))


def failing(result: GateResult, keys: Sequence[str]) -> dict[str, int]:
    return {key: result.counts[key] for key in keys if result.counts[key]}


# --------------------------------------------------------------------------
# Byte equality
# --------------------------------------------------------------------------


def test_identical_copies_pass(run: Run) -> None:
    result = compare_documents(run.arm(REPLICATION), run.released_root, STUDY_CELLS, TOKENS)

    assert result.passed
    assert dict(result.counts) == dict.fromkeys(STUDY_CELLS, 0)
    assert result.local_detail == ()


def test_one_flipped_byte_fails_with_count_one_under_its_cell(run: Run) -> None:
    flip_byte(run.document(REPLICATION, "latency-medium", "cut-a"))

    result = compare_documents(run.arm(REPLICATION), run.released_root, STUDY_CELLS, TOKENS)

    assert not result.passed
    assert dict(result.counts) == {**dict.fromkeys(STUDY_CELLS, 0), "latency-medium": 1}
    assert result.local_detail == ("latency-medium/cut-a.json differs",)


@pytest.mark.parametrize("side", ["left", "right"])
def test_a_missing_file_fails(run: Run, side: str) -> None:
    root = run.arm(REPLICATION) if side == "left" else run.released_root
    (root / "no_aeb" / "walk-a.json").unlink()

    result = compare_documents(run.arm(REPLICATION), run.released_root, STUDY_CELLS, TOKENS)

    assert not result.passed
    assert dict(result.counts) == {**dict.fromkeys(STUDY_CELLS, 0), "no_aeb": 1}
    assert result.local_detail == (f"no_aeb/walk-a.json is missing under {root}",)


@pytest.mark.parametrize("side", ["left", "right", "both"])
def test_an_extra_file_fails(run: Run, side: str) -> None:
    roots = {"left": (run.arm(REPLICATION),), "right": (run.released_root,)}
    for root in roots.get(side, (run.arm(REPLICATION), run.released_root)):
        (root / "oracle_aeb" / "lead-a.json.tmp").write_bytes(b"partial")

    result = compare_documents(run.arm(REPLICATION), run.released_root, STUDY_CELLS, TOKENS)

    assert not result.passed
    assert dict(result.counts) == {**dict.fromkeys(STUDY_CELLS, 0), "oracle_aeb": 1}
    assert result.local_detail == ("oracle_aeb/lead-a.json.tmp is not a document of the cohort",)


def test_only_the_named_cells_are_compared(run: Run) -> None:
    flip_byte(run.document(REPLICATION, "latency-medium"))

    result = compare_documents(run.arm(REPLICATION), run.released_root, ("no_aeb",), TOKENS)

    assert result.passed
    assert dict(result.counts) == {"no_aeb": 0}


# --------------------------------------------------------------------------
# A whole attempt
# --------------------------------------------------------------------------


def test_a_complete_attempt_passes_every_gate(run: Run) -> None:
    gates = run.verify()

    assert list(gates) == ["G1", "G2", "G3", "G5"]
    assert all(result.passed for result in gates.values())
    assert required_gates_passed(tuple(gates.values()))
    assert dict(gates["G1"].counts) == {
        **dict.fromkeys(G1_FAILURES, 0),
        "documents": 4 * 8 * 4 + 4 * 4,
        "distinct_commits": 1,
        "distinct_container_digests": 1,
    }
    assert dict(gates["G2"].counts) == dict.fromkeys(STUDY_CELLS, 0)
    assert dict(gates["G3"].counts) == {
        "no_aeb:A-v1-replication:released": 0,
        "no_aeb:B-v2-gated:released": 0,
        "no_aeb:C-v1-kalman:released": 0,
        "no_aeb:D-v2-kalman:released": 0,
        "no_aeb:E-v2-channel-rng:released": 0,
        "oracle_aeb:A-v1-replication:released": 0,
        "oracle_aeb:C-v1-kalman:released": 0,
        "no_aeb:B-v2-gated:A-v1-replication": 0,
        "no_aeb:C-v1-kalman:A-v1-replication": 0,
        "no_aeb:D-v2-kalman:A-v1-replication": 0,
        "no_aeb:E-v2-channel-rng:A-v1-replication": 0,
        "oracle_aeb:C-v1-kalman:A-v1-replication": 0,
        "oracle_aeb:D-v2-kalman:B-v2-gated": 0,
        "oracle_aeb:E-v2-channel-rng:B-v2-gated": 0,
    }
    assert dict(gates["G5"].counts) == {
        "run_contexts_unreadable": 0,
        "python_mismatches": 0,
        "numpy_mismatches": 0,
    }


def test_an_oracle_that_differs_between_b_and_d_fails_g3(run: Run) -> None:
    run.rewrite(run.document("D-v2-kalman", "oracle_aeb", "lead-b"), variant=9)

    gates = run.verify()

    assert not gates["G3"].passed
    assert gates["G3"].counts["oracle_aeb:D-v2-kalman:B-v2-gated"] == 1
    assert sum(gates["G3"].counts.values()) == 1
    assert gates["G1"].passed and gates["G2"].passed and gates["G5"].passed
    assert not required_gates_passed(tuple(gates.values()))


@pytest.mark.parametrize(
    ("arm_id", "cell", "key"),
    [
        ("B-v2-gated", "no_aeb", "no_aeb:B-v2-gated:released"),
        ("C-v1-kalman", "oracle_aeb", "oracle_aeb:C-v1-kalman:released"),
    ],
)
def test_a_cell_that_differs_from_the_released_records_fails_g3(
    run: Run, arm_id: str, cell: str, key: str
) -> None:
    run.rewrite(run.document(arm_id, cell), variant=9)

    gates = run.verify()

    assert not gates["G3"].passed
    assert gates["G3"].counts[key] == 1


def test_g2_counts_each_differing_document_of_arm_a_under_its_cell(run: Run) -> None:
    run.rewrite(run.document(REPLICATION, "dropout-medium", "lead-a"), variant=9)
    run.rewrite(run.document(REPLICATION, "dropout-medium", "walk-a"), variant=9)
    run.rewrite(run.document(REPLICATION, FULL_COALITION), variant=9)

    result = check_replication(run.study, run.arms_root, run.released_root, TOKENS)

    assert result.gate == "G2"
    assert not result.passed
    assert dict(result.counts) == {
        **dict.fromkeys(STUDY_CELLS, 0),
        "dropout-medium": 2,
        FULL_COALITION: 1,
    }


def test_a_per_arm_run_of_a_passes_with_b_to_e_absent(run: Run) -> None:
    for arm_id in list(ARMS)[1:]:
        shutil.rmtree(run.arm(arm_id))

    gates = run.verify(arm=REPLICATION)

    assert list(gates) == ["G1", "G2", "G5"]
    assert all(result.passed for result in gates.values())
    assert gates["G1"].counts["documents"] == 8 * 4


def test_a_per_arm_run_of_another_arm_runs_g1_and_g5_only(run: Run) -> None:
    gates = run.verify(arm="D-v2-kalman")

    assert list(gates) == ["G1", "G5"]
    assert all(result.passed for result in gates.values())


def test_a_full_run_with_an_arm_missing_fails(run: Run) -> None:
    arm = run.arm("E-v2-channel-rng")
    arm.rename(arm.with_name("E-v2-channel-rng-elsewhere"))

    gates = run.verify()

    assert not gates["G1"].passed
    assert failing(gates["G1"], G1_FAILURES) == {
        "run_context_problems": 1,
        "run_complete_problems": 1,
        "documents_missing": 4 * 4,
    }
    assert not gates["G3"].passed
    assert not gates["G5"].passed
    assert gates["G5"].counts["run_contexts_unreadable"] == 1
    assert gates["G2"].passed
    assert not required_gates_passed(tuple(gates.values()))


# --------------------------------------------------------------------------
# Arm-A reference mode
# --------------------------------------------------------------------------


def rebuilt_environment(run: Run) -> None:
    """Released records that the rebuilt image does not reproduce, in three cells."""

    for cell, token in (
        ("no_aeb", "lead-a"),
        ("oracle_aeb", "cut-a"),
        ("dropout-medium", "lead-a"),
        ("dropout-medium", "walk-a"),
    ):
        run.rewrite(run.released_root / cell / f"{token}.json", variant=7)


def test_in_arm_a_reference_mode_a_g2_difference_is_reported_per_cell_and_does_not_stop_g3(
    run: Run,
) -> None:
    rebuilt_environment(run)

    gates = run.verify(reference="arm-a")

    assert not gates["G2"].passed
    assert dict(gates["G2"].counts) == {
        **dict.fromkeys(STUDY_CELLS, 0),
        "no_aeb": 1,
        "oracle_aeb": 1,
        "dropout-medium": 2,
    }
    assert gates["G3"].passed
    assert gates["G1"].passed and gates["G5"].passed
    assert required_gates_passed(tuple(gates.values()), reference="arm-a")
    assert not required_gates_passed(tuple(gates.values()))


def test_in_arm_a_reference_mode_g3_passes_on_internal_invariance_when_released_comparisons_differ(
    run: Run,
) -> None:
    rebuilt_environment(run)

    released = run.verify()
    arm_a = run.verify(reference="arm-a")

    assert not released["G3"].passed
    assert arm_a["G3"].passed
    assert dict(arm_a["G3"].counts) == dict(released["G3"].counts)
    assert {key: count for key, count in arm_a["G3"].counts.items() if count} == {
        "no_aeb:A-v1-replication:released": 1,
        "no_aeb:B-v2-gated:released": 1,
        "no_aeb:C-v1-kalman:released": 1,
        "no_aeb:D-v2-kalman:released": 1,
        "no_aeb:E-v2-channel-rng:released": 1,
        "oracle_aeb:A-v1-replication:released": 1,
        "oracle_aeb:C-v1-kalman:released": 1,
    }


@pytest.mark.parametrize(
    ("arm_id", "cell", "key"),
    [
        ("C-v1-kalman", "oracle_aeb", "oracle_aeb:C-v1-kalman:A-v1-replication"),
        ("E-v2-channel-rng", "oracle_aeb", "oracle_aeb:E-v2-channel-rng:B-v2-gated"),
        ("D-v2-kalman", "no_aeb", "no_aeb:D-v2-kalman:A-v1-replication"),
    ],
)
def test_in_arm_a_reference_mode_g3_fails_on_an_internal_difference(
    run: Run, arm_id: str, cell: str, key: str
) -> None:
    rebuilt_environment(run)
    run.rewrite(run.document(arm_id, cell, "walk-a"), variant=9)

    gates = run.verify(reference="arm-a")

    assert not gates["G3"].passed
    assert gates["G3"].counts[key] == 1
    assert not required_gates_passed(tuple(gates.values()), reference="arm-a")


def test_check_invariance_defaults_to_the_released_reference(run: Run) -> None:
    rebuilt_environment(run)

    released = check_invariance(run.study, run.arms_root, run.released_root, TOKENS)
    arm_a = check_invariance(run.study, run.arms_root, run.released_root, TOKENS, "arm-a")

    assert released.gate == arm_a.gate == "G3"
    assert not released.passed
    assert arm_a.passed


def test_the_gate_file_of_arm_a_reference_mode_carries_reference_arm_a_and_exploratory(
    run: Run,
) -> None:
    rebuilt_environment(run)
    results = tuple(run.verify(reference="arm-a").values())

    document = gates_document(run.study, tuple(ARMS), results, reference="arm-a")
    released = gates_document(run.study, tuple(ARMS), results)

    assert (document.reference, document.exploratory) == ("arm-a", True)
    assert (released.reference, released.exploratory) == ("released", False)
    assert [(gate.gate, gate.passed) for gate in document.gates] == [
        ("G1", True),
        ("G2", False),
        ("G3", True),
        ("G5", True),
    ]
    assert document.gates[1].counts["dropout-medium"] == 2


# --------------------------------------------------------------------------
# G5
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("updates", "key"),
    [
        ({"numpy_version": "1.24.0"}, "numpy_mismatches"),
        ({"python_version": "3.9.20"}, "python_mismatches"),
    ],
)
def test_an_arm_context_with_another_version_fails_g5(
    run: Run, updates: dict[str, str], key: str
) -> None:
    run.rewrite_context("C-v1-kalman", **updates)

    gates = run.verify()

    assert not gates["G5"].passed
    assert {name: count for name, count in gates["G5"].counts.items() if count} == {key: 1}
    assert gates["G1"].passed
    assert "C-v1-kalman: Python" in "\n".join(gates["G5"].local_detail)


def test_g5_checks_one_arm_when_one_is_named(run: Run) -> None:
    run.rewrite_context("C-v1-kalman", numpy_version="1.24.0")

    assert check_environment(run.study, run.arms_root, "B-v2-gated").passed
    assert not check_environment(run.study, run.arms_root, "C-v1-kalman").passed
    assert not check_environment(run.study, run.arms_root).passed


# --------------------------------------------------------------------------
# G1
# --------------------------------------------------------------------------

ARM = "B-v2-gated"


def set_context(**updates: str) -> Callable[[Run], None]:
    return lambda run: run.rewrite_context(ARM, **updates)


def remove(relative: str) -> Callable[[Run], None]:
    return lambda run: (run.arm(ARM) / relative).unlink()


def write_bytes(relative: str, data: bytes) -> Callable[[Run], None]:
    def damage(run: Run) -> None:
        path = run.arm(ARM) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    return damage


def set_run_complete(**updates: Any) -> Callable[[Run], None]:
    def damage(run: Run) -> None:
        write_json(run.arm(ARM) / "run_complete.json", {**run_complete(run.manifest), **updates})

    return damage


def set_document(cell: str, token: str, **updates: Any) -> Callable[[Run], None]:
    def damage(run: Run) -> None:
        document = results_document(token, cell, run.manifest)
        write_document(run.document(ARM, cell, token), document.model_copy(update=updates))

    return damage


def invalid_token(run: Run) -> None:
    for cell in ARMS[ARM][3]:
        document = results_document("walk-a", cell, run.manifest).model_copy(
            update={
                "valid": False,
                "invalid_reason": "the log ends before the scenario does",
                "invalid_phase": "simulate",
                "results": (),
            }
        )
        write_document(run.document(ARM, cell, "walk-a"), document)


def other_replicates(run: Run) -> None:
    document = results_document("cut-a", "no_aeb", run.manifest, replicates=(0, 1))
    write_document(run.document(ARM, "no_aeb", "cut-a"), document)


def foreign_record(run: Run) -> None:
    document = results_document("cut-a", "coalition-none", run.manifest)
    records = (*document.results[:2], record("lead-a", "coalition-none", 2, 0))
    write_document(
        run.document(ARM, "coalition-none", "cut-a"),
        document.model_copy(update={"results": records}),
    )


def run_context_names() -> dict[str, str]:
    return dict.fromkeys(
        (
            "configuration_id",
            "protocol_sha256",
            "cohort_sha256",
            "container_digest",
            "commit",
            "study_sha256",
            "arm_id",
            "aeb_policy",
            "policy_sha256",
            "rng_scheme",
            "velocity_estimator",
            "velocity_parameters",
            "error_config_sha256",
            "python_version",
            "numpy_version",
        ),
        "x",
    )


INTEGRITY_DAMAGE: Mapping[str, tuple[Callable[[Run], None], str]] = {
    "no run context": (remove("run_context.json"), "run_context_problems"),
    "a run context that is not one": (
        write_bytes("run_context.json", b'{"arm_id": "B-v2-gated"}\n'),
        "run_context_problems",
    ),
    "a run context that is not JSON": (
        write_bytes("run_context.json", b"{"),
        "run_context_problems",
    ),
    "a run context holding a number": (
        write_bytes(
            "run_context.json",
            canonical_json_bytes({**run_context_names(), "commit": 1}),
        ),
        "run_context_problems",
    ),
    "another study file": (set_context(study_sha256="f" * 64), "run_context_problems"),
    "another protocol": (set_context(protocol_sha256="e" * 64), "run_context_problems"),
    "another cohort": (set_context(cohort_sha256="e" * 64), "run_context_problems"),
    "another arm": (set_context(arm_id="D-v2-kalman"), "run_context_problems"),
    "another configuration id": (set_context(configuration_id="all"), "run_context_problems"),
    "another policy": (set_context(aeb_policy="v1"), "run_context_problems"),
    "another keying": (set_context(rng_scheme="channel-independent"), "run_context_problems"),
    "another estimator": (set_context(velocity_estimator="cv-kalman"), "run_context_problems"),
    "no commit": (set_context(commit="0" * 40), "run_context_problems"),
    "a commit that is not a SHA": (set_context(commit="main"), "run_context_problems"),
    "no image": (set_context(container_digest="unknown"), "run_context_problems"),
    "an empty image": (set_context(container_digest=""), "run_context_problems"),
    "no completion marker": (remove("run_complete.json"), "run_complete_problems"),
    "a completion marker that is not one": (
        write_bytes("run_complete.json", b"[]\n"),
        "run_complete_problems",
    ),
    "a completion marker of another cohort": (
        set_run_complete(cohort_manifest_sha256="e" * 64),
        "run_complete_problems",
    ),
    "a token not listed": (set_run_complete(tokens=list(TOKENS[1:])), "tokens_not_listed"),
    "a token outside the cohort": (
        set_run_complete(tokens=[*TOKENS, "lead-z"]),
        "tokens_not_in_cohort",
    ),
    "a missing document": (remove("latency-medium/lead-b.json"), "documents_missing"),
    "a stray file": (write_bytes("notes.txt", b"note"), "unexpected_files"),
    "a cell the arm does not run": (
        write_bytes("dropout-high/lead-a.json", b"{}"),
        "unexpected_files",
    ),
    "a partial write": (write_bytes("no_aeb/lead-a.json.tmp", b"{"), "unexpected_files"),
    "a document outside the cohort": (
        write_bytes("no_aeb/lead-z.json", b"{}"),
        "unexpected_files",
    ),
    "a document that is not JSON": (
        write_bytes("oracle_aeb/lead-a.json", b"{"),
        "documents_invalid",
    ),
    "a document of another cell": (
        set_document("oracle_aeb", "lead-a", configuration_id="no_aeb"),
        "documents_invalid",
    ),
    "a document of another split": (
        set_document("oracle_aeb", "lead-a", split="development"),
        "documents_invalid",
    ),
    "a document of another protocol": (
        set_document("oracle_aeb", "lead-a", protocol_sha256="e" * 64),
        "documents_invalid",
    ),
    "a document with two replicates": (other_replicates, "documents_invalid"),
    "a document with a record of another token": (foreign_record, "documents_invalid"),
    "an invalid token": (invalid_token, "invalid_tokens"),
}


@pytest.mark.parametrize("damage", sorted(INTEGRITY_DAMAGE))
def test_g1_fails_and_counts_each_integrity_problem(run: Run, damage: str) -> None:
    apply, key = INTEGRITY_DAMAGE[damage]
    apply(run)

    result = check_integrity(run.study, ARM, run.arm(ARM), run.manifest)
    gates = run.verify()

    assert result.gate == "G1"
    assert not result.passed
    assert failing(result, G1_FAILURES) == {key: 1}
    assert all(line.startswith(f"{ARM}: ") for line in result.local_detail)
    assert len(result.local_detail) == 1
    assert not gates["G1"].passed
    assert failing(gates["G1"], G1_FAILURES) == {key: 1}


def test_g1_passes_on_an_arm_that_is_whole(run: Run) -> None:
    result = check_integrity(
        run.study, "E-v2-channel-rng", run.arm("E-v2-channel-rng"), run.manifest
    )

    assert result.passed
    assert dict(result.counts) == {**dict.fromkeys(G1_FAILURES, 0), "documents": 4 * 4}
    assert result.local_detail == ()


@pytest.mark.parametrize(
    ("updates", "key"),
    [
        ({"container_digest": "sha256:" + "e" * 64}, "distinct_container_digests"),
        ({"commit": "1" * 40}, "distinct_commits"),
    ],
)
def test_g1_requires_one_image_and_one_commit_across_the_arms(
    run: Run, updates: dict[str, str], key: str
) -> None:
    run.rewrite_context("D-v2-kalman", **updates)

    gates = run.verify()

    assert not gates["G1"].passed
    assert gates["G1"].counts[key] == 2
    assert failing(gates["G1"], G1_FAILURES) == {}
    assert run.verify(arm="D-v2-kalman")["G1"].passed


# --------------------------------------------------------------------------
# A pilot
# --------------------------------------------------------------------------


@pytest.fixture()
def pilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Run:
    """Five pilot arms on the smoke manifest, under artifacts/pilot/ of the working directory."""

    monkeypatch.chdir(tmp_path)
    evaluation = write_manifest(tmp_path / "evaluation.json")
    smoke = write_manifest(tmp_path / "smoke.json", split="smoke")
    study = write_study(tmp_path / "study.yaml", evaluation)
    arms_root = Path("artifacts") / "pilot" / "pilot-1"
    for arm_id in ARMS:
        write_arm(tmp_path / arms_root, arm_id, study, smoke)
    return Run(study=study, manifest=smoke, arms_root=arms_root, released_root=tmp_path / "none")


def test_a_pilot_runs_g1_on_the_smoke_manifest_and_g5_and_skips_g2_and_g3(pilot: Run) -> None:
    results = verify_study(pilot.study, pilot.arms_root, None, pilot.manifest, pilot=True)

    assert [result.gate for result in results] == ["G1", "G5"]
    assert all(result.passed for result in results)
    assert results[0].counts["documents"] == 4 * 8 * 4 + 4 * 4


def test_a_pilot_of_one_arm_checks_that_arm(pilot: Run) -> None:
    results = verify_study(
        pilot.study, pilot.arms_root, None, pilot.manifest, arm="B-v2-gated", pilot=True
    )

    assert [result.gate for result in results] == ["G1", "G5"]
    assert results[0].counts["documents"] == 8 * 4


@pytest.mark.parametrize(
    "arms_root",
    ["artifacts/pilot-2/pilot-1", "artifacts/formal/aeb_policy_v2/attempt-1", "pilot"],
)
def test_a_pilot_arms_root_outside_artifacts_pilot_is_refused(pilot: Run, arms_root: str) -> None:
    with pytest.raises(ValueError, match="artifacts/pilot/"):
        verify_study(pilot.study, Path(arms_root), None, pilot.manifest, pilot=True)


def test_a_pilot_arms_root_may_be_artifacts_pilot_itself(pilot: Run, tmp_path: Path) -> None:
    (tmp_path / pilot.arms_root).rename(tmp_path / "moved")
    (tmp_path / "artifacts" / "pilot").rmdir()
    (tmp_path / "moved").rename(tmp_path / "artifacts" / "pilot")

    results = verify_study(pilot.study, Path("artifacts/pilot"), None, pilot.manifest, pilot=True)

    assert all(result.passed for result in results)


def test_a_pilot_on_a_manifest_other_than_smoke_is_refused(pilot: Run, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="smoke"):
        verify_study(pilot.study, pilot.arms_root, None, tmp_path / "evaluation.json", pilot=True)


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_formal_verify_without_the_released_records_is_refused(run: Run) -> None:
    with pytest.raises(ValueError, match="released records"):
        verify_study(run.study, run.arms_root, None, run.manifest)


def test_a_formal_verify_on_another_cohort_is_refused(run: Run, tmp_path: Path) -> None:
    other = write_manifest(tmp_path / "other.json", split="development")

    with pytest.raises(ValueError, match="membership"):
        verify_study(run.study, run.arms_root, run.released_root, other)


def test_an_unknown_arm_is_refused(run: Run) -> None:
    with pytest.raises(ValueError, match="F-unknown"):
        run.verify(arm="F-unknown")


def test_an_unknown_reference_is_refused(run: Run) -> None:
    with pytest.raises(ValueError, match="reference"):
        run.verify(reference="arm-b")


# --------------------------------------------------------------------------
# The released-hash reader
# --------------------------------------------------------------------------


def hash_list(root: Path, **updates: Any) -> dict[str, Any]:
    """A list in the shape of the released run's `output-hashes.json`."""

    document: dict[str, Any] = {
        "schema_version": "aeb-d2-output-hashes/v2",
        "generated_at_utc": "2026-09-06T20:54:13.4306365Z",
        "formal_files": [
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ],
        "operation_files": [
            {"path": "write_output_hashes.ps1", "bytes": 1773, "sha256": "c" * 64},
        ],
    }
    document.update(updates)
    return document


@pytest.fixture()
def released(tmp_path: Path) -> tuple[Path, Path]:
    root = write_released(tmp_path / "released", write_manifest(tmp_path / "evaluation.json"))
    return root, write_crlf_json(tmp_path / "output-hashes.json", hash_list(root))


def test_the_released_hash_reader_accepts_records_that_match_the_list(
    released: tuple[Path, Path],
) -> None:
    root, hashes = released
    assert b"\r\n" in hashes.read_bytes()

    result = check_released_records(root, hashes, file_sha256(hashes))

    assert result.passed
    assert dict(result.counts) == {
        "listed_files": 8 * 4 + 3,
        "missing_files": 0,
        "extra_files": 0,
        "size_mismatches": 0,
        "hash_mismatches": 0,
    }


def test_the_released_hash_reader_refuses_a_list_whose_sha256_differs(
    released: tuple[Path, Path],
) -> None:
    root, hashes = released

    result = check_released_records(root, hashes, "0" * 64)

    assert not result.passed
    assert dict(result.counts) == {"hash_list_refused": 1}
    assert file_sha256(hashes) in result.local_detail[0]


@pytest.mark.parametrize(
    "updates",
    [
        {"schema_version": "aeb-d2-output-hashes/v1"},
        {"formal_files": "none"},
        {"operation_files": None},
        {"reviewed_by": "nobody"},
    ],
)
def test_the_released_hash_reader_refuses_another_schema_version_or_shape(
    released: tuple[Path, Path], updates: dict[str, Any]
) -> None:
    root, hashes = released
    write_crlf_json(hashes, hash_list(root, **updates))

    result = check_released_records(root, hashes, file_sha256(hashes))

    assert not result.passed
    assert dict(result.counts) == {"hash_list_refused": 1}


def test_the_released_hash_reader_refuses_a_list_naming_a_file_twice(
    released: tuple[Path, Path],
) -> None:
    root, hashes = released
    document = hash_list(root)
    document["formal_files"].append(document["formal_files"][0])
    write_crlf_json(hashes, document)

    result = check_released_records(root, hashes, file_sha256(hashes))

    assert not result.passed
    assert dict(result.counts) == {"hash_list_refused": 1}


@pytest.mark.parametrize(
    ("change", "key"),
    [("remove", "missing_files"), ("add", "extra_files"), ("add-nested", "extra_files")],
)
def test_the_released_hash_reader_refuses_a_missing_or_an_extra_file(
    released: tuple[Path, Path], change: str, key: str
) -> None:
    root, hashes = released
    if change == "remove":
        (root / "coalition-none" / "cut-a.json").unlink()
    elif change == "add":
        (root / "run_complete.json.tmp").write_bytes(b"{}")
    else:
        (root / "dropout-high").mkdir()
        (root / "dropout-high" / "lead-a.json").write_bytes(b"{}")

    result = check_released_records(root, hashes, file_sha256(hashes))

    assert not result.passed
    assert {name: count for name, count in result.counts.items() if count} == {
        "listed_files": 8 * 4 + 3,
        key: 1,
    }


def test_the_released_hash_reader_refuses_a_size_mismatch(released: tuple[Path, Path]) -> None:
    root, hashes = released
    document = hash_list(root)
    document["formal_files"][5]["bytes"] += 1
    write_crlf_json(hashes, document)

    result = check_released_records(root, hashes, file_sha256(hashes))

    assert not result.passed
    assert result.counts["size_mismatches"] == 1
    assert result.counts["hash_mismatches"] == 0


def test_the_released_hash_reader_refuses_a_hash_mismatch(released: tuple[Path, Path]) -> None:
    root, hashes = released
    flip_byte(root / "run.log")

    result = check_released_records(root, hashes, file_sha256(hashes))

    assert not result.passed
    assert result.counts["size_mismatches"] == 0
    assert result.counts["hash_mismatches"] == 1
    assert result.local_detail == ("run.log does not have the listed SHA-256",)


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------

#: The record's own time format: seven fractional digits and a trailing Z.
RECORDED_TIME = "2022-09-23T05:47:01.0000000Z"


def input_databases(data_root: Path, split: str = "val") -> dict[str, Any]:
    """A record in the shape of the released run's `d2-input-databases.json`."""

    files = []
    for log in LOGS:
        relative = f"nuplan-v1.1/splits/{split}/{log}"
        path = data_root / relative
        files.append(
            {
                "relative_path": relative,
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
                "last_write_time_utc": RECORDED_TIME,
            }
        )
    return {
        "kind": "private-d2-dataset-input-fingerprint",
        "dataset": "nuplan-v1.1",
        "split": split,
        "started_at_utc": "2026-09-06T16:43:44.8540585Z",
        "finished_at_utc": "2026-09-06T16:51:21.6058631Z",
        "files": files,
    }


@dataclass(frozen=True)
class Preflight:
    study: Path
    protocol: Path
    released_root: Path
    released_hashes: Path
    input_databases: Path
    manifest: Path
    data_root: Path
    split: str = "val"

    def run(self) -> GateResult:
        return preflight(
            self.study,
            self.protocol,
            self.released_root,
            self.released_hashes,
            self.input_databases,
            self.manifest,
            self.data_root,
            self.split,
        )

    def log(self, name: str) -> Path:
        return self.data_root / "nuplan-v1.1" / "splits" / self.split / name


def build_preflight(
    root: Path, edit_record: Optional[Callable[[dict[str, Any]], None]] = None
) -> Preflight:
    """Every input of the preflight, with the study file pinning the two private records."""

    protocol = root / "protocol.yaml"
    protocol.write_bytes(b"protocol: nuplan_aeb_v2\r\n")
    manifest = write_manifest(root / "evaluation.json", log_names=LOGS)
    released_root = write_released(root / "released", manifest)
    hashes = write_crlf_json(root / "output-hashes.json", hash_list(released_root))
    data_root = root / "data"
    for index, log in enumerate(LOGS):
        path = data_root / "nuplan-v1.1" / "splits" / "val" / log
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"log {index}\n".encode() * (1000 + index))
    record_document = input_databases(data_root)
    if edit_record is not None:
        edit_record(record_document)
    record_path = write_crlf_json(root / "d2-input-databases.json", record_document)
    study = write_study(
        root / "study.yaml",
        manifest,
        seed_namespace_protocol_sha256=file_sha256(protocol),
        released_output_hashes_sha256=file_sha256(hashes),
        input_databases_sha256=file_sha256(record_path),
    )
    return Preflight(
        study=study,
        protocol=protocol,
        released_root=released_root,
        released_hashes=hashes,
        input_databases=record_path,
        manifest=manifest,
        data_root=data_root,
    )


PREFLIGHT_FAILURES = (
    "protocol_mismatches",
    "cohort_manifest_mismatches",
    "released_missing_files",
    "released_extra_files",
    "released_size_mismatches",
    "released_hash_mismatches",
    "logs_missing",
    "logs_missing_from_record",
    "logs_not_referenced",
    "log_size_mismatches",
    "log_hash_mismatches",
)


def test_preflight_passes_on_the_recorded_inputs(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    sizes = [inputs.log(log).stat().st_size for log in LOGS]

    result = inputs.run()

    assert result.gate == "preflight"
    assert result.passed
    assert dict(result.counts) == {
        **dict.fromkeys(PREFLIGHT_FAILURES, 0),
        "released_listed_files": 8 * 4 + 3,
        "referenced_logs": 2,
        "logs_present": 2,
        "log_bytes": sum(sizes),
    }
    split_directory = inputs.data_root / "nuplan-v1.1" / "splits" / "val"
    assert f"split directory {split_directory}" in result.local_detail
    assert (
        f"released output hash list SHA-256 {file_sha256(inputs.released_hashes)}"
        in result.local_detail
    )
    assert (
        f"input databases record SHA-256 {file_sha256(inputs.input_databases)}"
        in result.local_detail
    )
    assert f"study file SHA-256 {file_sha256(inputs.study)}" in result.local_detail


@pytest.mark.parametrize("change", ["size", "sha256"])
def test_preflight_fails_on_a_log_whose_size_or_sha256_differs_from_the_record(
    tmp_path: Path, change: str
) -> None:
    inputs = build_preflight(tmp_path)
    log = inputs.log(LOGS[1])
    if change == "size":
        log.write_bytes(log.read_bytes() + b"x")
    else:
        flip_byte(log)

    result = inputs.run()

    assert not result.passed
    expected = {"log_hash_mismatches": 1}
    if change == "size":
        expected["log_size_mismatches"] = 1
    assert failing(result, PREFLIGHT_FAILURES) == expected
    assert any(LOGS[1] in line and "record" in line for line in result.local_detail)


def test_preflight_fails_on_a_referenced_log_missing_from_the_record(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path, edit_record=lambda record: record["files"].pop(0))

    result = inputs.run()

    assert not result.passed
    assert failing(result, PREFLIGHT_FAILURES) == {"logs_missing_from_record": 1}


def test_preflight_fails_on_a_record_that_lists_a_log_the_manifest_does_not_reference(
    tmp_path: Path,
) -> None:
    def add(record: dict[str, Any]) -> None:
        record["files"].append(
            {**record["files"][0], "relative_path": "nuplan-v1.1/splits/val/other.db"}
        )

    result = build_preflight(tmp_path, edit_record=add).run()

    assert not result.passed
    assert failing(result, PREFLIGHT_FAILURES) == {"logs_not_referenced": 1}


def test_preflight_fails_on_a_referenced_log_missing_from_the_split(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    inputs.log(LOGS[0]).unlink()

    result = inputs.run()

    assert not result.passed
    assert failing(result, PREFLIGHT_FAILURES) == {"logs_missing": 1}
    assert result.counts["logs_present"] == 1


def test_preflight_passes_when_only_a_log_modification_time_differs(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    moment = datetime(2001, 2, 3, 4, 5, 6, 789000, tzinfo=timezone.utc).timestamp()
    for log in LOGS:
        os.utime(inputs.log(log), (moment, moment))

    result = inputs.run()

    assert result.passed
    for log in LOGS:
        assert f"{log} modified 2001-02-03T04:05:06.789000Z" in result.local_detail


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "private-d2-dataset-output-fingerprint"),
        ("dataset", "nuplan-v1.0"),
        ("split", "mini"),
    ],
)
def test_preflight_refuses_another_kind_dataset_or_split(
    tmp_path: Path, field: str, value: str
) -> None:
    inputs = build_preflight(tmp_path, edit_record=lambda record: record.update({field: value}))

    with pytest.raises(ValueError, match=field):
        inputs.run()


def test_preflight_refuses_a_record_of_another_shape(tmp_path: Path) -> None:
    inputs = build_preflight(
        tmp_path, edit_record=lambda record: record["files"][0].update(bytes="large")
    )

    with pytest.raises(ValidationError):
        inputs.run()


def test_preflight_refuses_a_record_that_lists_a_log_twice(tmp_path: Path) -> None:
    inputs = build_preflight(
        tmp_path, edit_record=lambda record: record["files"].append(record["files"][0])
    )

    with pytest.raises(ValueError, match="twice"):
        inputs.run()


@pytest.mark.parametrize("record", ["released_hashes", "input_databases"])
def test_preflight_refuses_a_record_other_than_the_one_the_study_file_pins(
    tmp_path: Path, record: str
) -> None:
    inputs = build_preflight(tmp_path)
    path: Path = getattr(inputs, record)
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))

    with pytest.raises(ValueError, match="pins"):
        inputs.run()


def test_preflight_fails_on_another_protocol(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    inputs.protocol.write_bytes(b"protocol: nuplan_aeb_v2\n")

    result = inputs.run()

    assert not result.passed
    assert failing(result, PREFLIGHT_FAILURES) == {"protocol_mismatches": 1}


@pytest.mark.parametrize(("change", "expected"), [("bytes", 1), ("membership", 2)])
def test_preflight_fails_on_another_cohort_manifest(
    tmp_path: Path, change: str, expected: int
) -> None:
    inputs = build_preflight(tmp_path)
    document = json.loads(inputs.manifest.read_bytes())
    if change == "membership":
        document["families"]["bicycle_or_vru"] = ["bike-a"]
    inputs.manifest.write_text(json.dumps(document, indent=4), encoding="utf-8")

    result = inputs.run()

    assert not result.passed
    assert failing(result, PREFLIGHT_FAILURES) == {"cohort_manifest_mismatches": expected}


def test_preflight_fails_when_the_released_records_differ(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    flip_byte(inputs.released_root / "oracle_aeb" / "lead-b.json")

    result = inputs.run()

    assert not result.passed
    assert failing(result, PREFLIGHT_FAILURES) == {"released_hash_mismatches": 1}
    assert "oracle_aeb/lead-b.json does not have the listed SHA-256" in result.local_detail


def test_preflight_refuses_a_study_whose_inputs_changed(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    document = yaml.safe_load(inputs.study.read_text(encoding="utf-8"))
    document["input_sha256"]["aeb/policy_v2.yaml"] = "0" * 64
    inputs.study.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="policy_v2"):
        inputs.run()


# --------------------------------------------------------------------------
# The gate file
# --------------------------------------------------------------------------


def a_gate_file(**updates: Any) -> StudyGatesV1:
    values: dict[str, Any] = {
        "schema_version": "aeb-study-gates/v1",
        "study_sha256": "1" * 64,
        "arms_checked": list(ARMS),
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": "2" * 64,
        "reference": "released",
        "exploratory": False,
        "gates": [
            {"gate": "G1", "passed": True, "counts": {"documents": 1392}},
            {"gate": "G2", "passed": False, "counts": {"oracle_aeb": 3}},
        ],
    }
    values.update(updates)
    return StudyGatesV1.model_validate(values)


@pytest.mark.parametrize(
    "detail", [None, {"G2": ["oracle_aeb/lead-a.json differs", "oracle_aeb/lead-b.json differs"]}]
)
def test_the_gate_file_round_trips_with_and_without_artifacts_only_detail(
    tmp_path: Path, detail: Optional[dict[str, list[str]]]
) -> None:
    gates = a_gate_file() if detail is None else a_gate_file(artifacts_only_detail=detail)
    path = tmp_path / "attempt-1.gates.json"

    write_gates(path, gates)

    assert load_gates(path) == gates
    raw = path.read_bytes()
    assert raw.endswith(b"}\n")
    assert b"\r" not in raw
    assert json.loads(raw)["artifacts_only_detail"] == ({} if detail is None else detail)


def test_the_published_gate_file_leaves_out_the_local_detail_and_still_loads(
    tmp_path: Path,
) -> None:
    gates = a_gate_file(artifacts_only_detail={"G2": ["oracle_aeb/lead-a.json differs"]})
    published = published_gates(gates)
    path = tmp_path / "gates.json"
    path.write_text(json.dumps(published), encoding="utf-8")

    loaded = load_gates(path)

    assert "artifacts_only_detail" not in published
    assert loaded == gates.model_copy(update={"artifacts_only_detail": {}})


def test_the_gate_file_carries_the_study_files_protocol_and_cohort_hashes(run: Run) -> None:
    study = yaml.safe_load(run.study.read_text(encoding="utf-8"))

    document = gates_document(run.study, tuple(ARMS), tuple(run.verify().values()))

    assert document.study_sha256 == file_sha256(run.study)
    assert document.protocol_sha256 == study["seed_namespace_protocol_sha256"]
    assert document.cohort_manifest_sha256 == study["cohort_membership_sha256"]
    assert document.arms_checked == tuple(ARMS)
    assert document.artifacts_only_detail == {}


def test_the_gate_file_keeps_each_gates_local_detail_apart(run: Run) -> None:
    run.rewrite(run.document(REPLICATION, "coalition-none", "walk-a"), variant=9)

    document = gates_document(run.study, tuple(ARMS), tuple(run.verify().values()))

    assert document.artifacts_only_detail == {"G2": ("coalition-none/walk-a.json differs",)}


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"exploratory": True}, "exploratory"),
        ({"reference": "arm-a"}, "exploratory"),
        (
            {"gates": [{"gate": "G1", "passed": True, "counts": {}}] * 2},
            "more than once",
        ),
        ({"artifacts_only_detail": {"G4": ["no such gate"]}}, "G4"),
        ({"gates": [{"gate": "G1", "passed": True, "counts": {"documents": -1}}]}, "0"),
    ],
)
def test_the_gate_file_refuses_an_inconsistent_document(
    updates: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        a_gate_file(**updates)


def test_a_gate_is_published_as_its_name_outcome_and_counts() -> None:
    gate = StudyGateV1(gate="G5", passed=True, counts={"numpy_mismatches": 0})

    assert gate.model_dump(mode="json") == {
        "gate": "G5",
        "passed": True,
        "counts": {"numpy_mismatches": 0},
    }


# --------------------------------------------------------------------------
# Token refusal
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cohort_tokens() -> frozenset[str]:
    return committed_cohort_tokens(ROOT)


def test_the_committed_cohort_tokens_come_from_every_cohort_file(
    cohort_tokens: frozenset[str],
) -> None:
    expected: set[str] = set()
    for path in COHORT_FILES.glob("*.json"):
        document = json.loads(path.read_bytes())
        if "families" in document:
            expected.update(token for tokens in document["families"].values() for token in tokens)
        else:
            expected.update(row["scenario_token"] for row in document["examined"])

    assert cohort_tokens == expected
    assert "15c3255839035bee" in cohort_tokens


def test_the_committed_cohort_tokens_refuse_an_unknown_cohort_file(tmp_path: Path) -> None:
    directory = tmp_path / "docs" / "evidence" / "nuplan_aeb_v2" / "cohort"
    directory.mkdir(parents=True)
    write_json(directory / "evaluation.json", {"schema_version": "aeb-token-results/v1"})

    with pytest.raises(ValueError, match=r"evaluation\.json"):
        committed_cohort_tokens(tmp_path)


def test_the_committed_cohort_tokens_refuse_a_repository_without_them(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cohort"):
        committed_cohort_tokens(tmp_path)


def test_numbers_hashes_and_a_commit_pass_the_token_refusal(
    cohort_tokens: frozenset[str],
) -> None:
    refuse_token_strings(
        {
            "estimate": 4.0670219638242875,
            "epsilon": 2.220446049250313e-16,
            "protocol_sha256": PROTOCOL_SHA,
            "commit": "e6e1f39c95b2a8a7c1f7d0b1e7f2d3c4a5b6c7d8",
            "counts": {"oracle_aeb": 3, "passed": True, "none": None},
            "rows": [1, 2.5, "coalition-none"],
        },
        cohort_tokens,
    )


@pytest.mark.parametrize(
    "document",
    [
        {"token": "15c3255839035bee"},
        {"detail": ["oracle_aeb/15c3255839035bee.json differs"]},
        {"counts": {"no_aeb 15c3255839035bee": 1}},
        [{"nested": ("the token 15c3255839035bee",)}],
    ],
)
def test_a_string_holding_a_cohort_token_is_refused(
    cohort_tokens: frozenset[str], document: Any
) -> None:
    with pytest.raises(ValueError, match="15c3255839035bee"):
        refuse_token_strings(document, cohort_tokens)


@pytest.mark.parametrize(
    "document",
    [{"token": "0123456789abcdef"}, {"0123456789abcdef": 1}, ["0123456789abcdef"]],
)
def test_a_token_shaped_string_is_refused(cohort_tokens: frozenset[str], document: Any) -> None:
    with pytest.raises(ValueError, match="0123456789abcdef"):
        refuse_token_strings(document, cohort_tokens)


def test_a_string_that_only_contains_a_token_shape_passes(cohort_tokens: frozenset[str]) -> None:
    refuse_token_strings({"note": "run 0123456789abcdef0 of 3"}, cohort_tokens)


@pytest.mark.parametrize("token", ["lead-a", "15C3255839035BEE", "15c3255839035bee0"])
def test_a_cohort_token_of_another_shape_is_refused(token: str) -> None:
    """Strings are compared sixteen characters at a time, which finds no other shape."""

    with pytest.raises(ValueError, match="is not sixteen lower-case hexadecimal digits"):
        refuse_token_strings({"note": f"x {token}"}, frozenset({token}))


def test_the_published_part_of_a_gate_result_passes(
    run: Run, cohort_tokens: frozenset[str]
) -> None:
    """The local detail may name a token; the published fields never do."""

    run.rewrite(run.document(REPLICATION, "no_aeb", "lead-a"), variant=9)
    document = gates_document(run.study, tuple(ARMS), tuple(run.verify().values()))
    named = document.model_copy(
        update={"artifacts_only_detail": {"G2": ("no_aeb/15c3255839035bee.json differs",)}}
    )

    refuse_token_strings(published_gates(named), cohort_tokens)

    with pytest.raises(ValueError, match="15c3255839035bee"):
        refuse_token_strings(named.model_dump(mode="json"), cohort_tokens)


# --------------------------------------------------------------------------
# What each gate names and counts
# --------------------------------------------------------------------------


def membership(manifest: Path) -> str:
    return membership_sha256(load_manifest(manifest))


def identity(run: Run, token: str, cell: str) -> dict[str, str]:
    """The fields that tie a document to its token, split, cell, protocol and cohort."""

    return {
        "scenario_token": token,
        "family": FAMILY_OF[token],
        "split": "evaluation",
        "configuration_id": cell,
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": membership(run.manifest),
    }


def not_found(path: Path) -> str:
    return f"[Errno 2] No such file or directory: {str(path)!r}"


#: The line G1 writes for each damage, after the arm's name.
INTEGRITY_DETAIL: Mapping[str, Callable[[Run], str]] = {
    "no run context": lambda run: (
        f"run_context.json cannot be read: {not_found(run.arm(ARM) / 'run_context.json')}"
    ),
    "a run context that is not one": lambda run: "run_context.json is not a study run context",
    "a commit that is not a SHA": lambda run: "run_context.json records no tooling commit: 'main'",
    "an empty image": lambda run: "run_context.json records no image identifier: ''",
    "no completion marker": lambda run: (
        f"run_complete.json cannot be read: {not_found(run.arm(ARM) / 'run_complete.json')}"
    ),
    "a completion marker of another cohort": lambda run: (
        f"run_complete.json names the cohort {'e' * 64}, not {membership(run.manifest)}"
    ),
    "a missing document": lambda run: "latency-medium/lead-b.json is missing",
    "a document of another cell": lambda run: (
        f"oracle_aeb/lead-a.json records {identity(run, 'lead-a', 'no_aeb')}, "
        f"not {identity(run, 'lead-a', 'oracle_aeb')}"
    ),
    "a document with two replicates": lambda run: (
        "no_aeb/cut-a.json does not hold replicates (0, 1, 2) of its own token and cell"
    ),
    "an invalid token": lambda run: "walk-a is invalid: the log ends before the scenario does",
}


@pytest.mark.parametrize(
    "damage",
    [pytest.param(name, id=name.replace(" ", "-")) for name in sorted(INTEGRITY_DETAIL)],
)
def test_g1_names_each_integrity_problem_in_its_detail(run: Run, damage: str) -> None:
    apply, _ = INTEGRITY_DAMAGE[damage]
    apply(run)

    result = check_integrity(run.study, ARM, run.arm(ARM), run.manifest)

    assert result.local_detail == (f"{ARM}: {INTEGRITY_DETAIL[damage](run)}",)


def test_g1_counts_every_document_that_is_not_its_token_and_cells(run: Run) -> None:
    for token in ("cut-a", "lead-b"):
        document = results_document(token, "no_aeb", run.manifest, replicates=(0, 1))
        write_document(run.document(ARM, "no_aeb", token), document)
    for token in ("lead-a", "walk-a"):
        set_document("oracle_aeb", token, configuration_id="no_aeb")(run)

    result = check_integrity(run.study, ARM, run.arm(ARM), run.manifest)

    assert failing(result, G1_FAILURES) == {"documents_invalid": 4}


def test_g1_counts_each_invalid_token_once_and_names_it(run: Run) -> None:
    reason = "the log ends before the scenario does"
    for token in ("cut-a", "walk-a"):
        for cell in ARMS[ARM][3]:
            document = results_document(token, cell, run.manifest).model_copy(
                update={
                    "valid": False,
                    "invalid_reason": reason,
                    "invalid_phase": "simulate",
                    "results": (),
                }
            )
            write_document(run.document(ARM, cell, token), document)

    result = check_integrity(run.study, ARM, run.arm(ARM), run.manifest)

    assert failing(result, G1_FAILURES) == {"invalid_tokens": 2}
    assert result.local_detail == (
        f"{ARM}: cut-a is invalid: {reason}",
        f"{ARM}: walk-a is invalid: {reason}",
    )


def test_g1_refuses_a_document_whose_valid_flag_is_a_number(run: Run) -> None:
    """A document is read strictly: `1` is not a boolean."""

    path = run.document(ARM, "no_aeb", "lead-a")
    document = json.loads(path.read_bytes())
    document["valid"] = 1
    path.write_text(json.dumps(document), encoding="utf-8")

    result = check_integrity(run.study, ARM, run.arm(ARM), run.manifest)

    assert failing(result, G1_FAILURES) == {"documents_invalid": 1}


def test_g1_names_the_commits_the_arms_disagree_on(run: Run) -> None:
    run.rewrite_context("D-v2-kalman", commit="1" * 40)

    gates = run.verify()

    assert gates["G1"].local_detail == (
        f"the arms record 2 values where one is required: {sorted([COMMIT, '1' * 40])}",
    )


def test_g5_counts_and_names_every_arm_it_cannot_read_and_checks_the_rest(run: Run) -> None:
    for arm_id in (REPLICATION, "B-v2-gated"):
        write_json(run.arm(arm_id) / "run_context.json", {"arm_id": arm_id})
    run.rewrite_context("C-v1-kalman", numpy_version="1.24.0")

    result = check_environment(run.study, run.arms_root)

    assert dict(result.counts) == {
        "run_contexts_unreadable": 2,
        "python_mismatches": 0,
        "numpy_mismatches": 1,
    }
    assert result.local_detail == (
        f"{REPLICATION}: run_context.json is not a study run context",
        "B-v2-gated: run_context.json is not a study run context",
        "C-v1-kalman: Python 3.9.19, numpy 1.24.0; "
        "the study file records Python 3.9.19, numpy 1.23.4",
    )


def test_g3_names_each_differing_document_under_its_comparison(run: Run) -> None:
    run.rewrite(run.document("D-v2-kalman", "oracle_aeb", "lead-b"), variant=9)

    result = check_invariance(run.study, run.arms_root, run.released_root, TOKENS)

    assert result.local_detail == (
        "oracle_aeb:D-v2-kalman:B-v2-gated: oracle_aeb/lead-b.json differs",
    )


def test_a_comparison_of_two_runs_is_named_documents(run: Run) -> None:
    result = compare_documents(run.arm(REPLICATION), run.released_root, STUDY_CELLS, TOKENS)

    assert result.gate == "documents"


def test_a_formal_verify_without_the_released_records_says_to_name_them(run: Run) -> None:
    message = "a formal check compares the arms with the released records; name them"

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        verify_study(run.study, run.arms_root, None, run.manifest)


def test_a_repository_without_cohort_files_is_refused_with_the_directory_searched(
    tmp_path: Path,
) -> None:
    message = "no committed cohort file under docs/evidence/nuplan_aeb_v2/cohort"

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        committed_cohort_tokens(tmp_path)


def test_the_released_hash_reader_names_its_result_released_records(
    released: tuple[Path, Path],
) -> None:
    root, hashes = released

    accepted = check_released_records(root, hashes, file_sha256(hashes))
    refused = check_released_records(root, hashes, "0" * 64)

    assert (accepted.gate, accepted.passed) == ("released_records", True)
    assert refused.gate == "released_records"
    assert refused.passed is False


def test_the_released_hash_reader_says_why_it_refuses_a_list_of_another_shape(
    released: tuple[Path, Path],
) -> None:
    root, hashes = released
    write_crlf_json(hashes, hash_list(root, schema_version="aeb-d2-output-hashes/v1"))

    result = check_released_records(root, hashes, file_sha256(hashes))

    assert len(result.local_detail) == 1
    assert result.local_detail[0].startswith(
        f"the hash list {hashes} is not an aeb-d2-output-hashes/v2 list: "
    )


def test_the_released_hash_reader_counts_and_names_every_size_mismatch(
    released: tuple[Path, Path],
) -> None:
    root, hashes = released
    document = hash_list(root)
    for entry in document["formal_files"][5:7]:
        entry["bytes"] += 1
    write_crlf_json(hashes, document)
    names = sorted(entry["path"] for entry in document["formal_files"][5:7])

    result = check_released_records(root, hashes, file_sha256(hashes))

    assert result.counts["size_mismatches"] == 2
    assert result.counts["hash_mismatches"] == 0
    assert result.local_detail == tuple(f"{name} does not have the listed size" for name in names)


def test_the_released_hash_reader_counts_every_hash_mismatch(
    released: tuple[Path, Path],
) -> None:
    root, hashes = released
    flip_byte(root / "run.log")
    flip_byte(root / "no_aeb" / "cut-a.json")

    result = check_released_records(root, hashes, file_sha256(hashes))

    assert result.counts["size_mismatches"] == 0
    assert result.counts["hash_mismatches"] == 2


def test_a_file_is_hashed_a_chunk_at_a_time(tmp_path: Path) -> None:
    """Hashing a file of 32 MiB never holds more than a few MiB, so a log of gigabytes fits."""

    root = tmp_path / "released"
    root.mkdir()
    (root / "large.bin").write_bytes(bytes(32 * 2**20))
    hashes = write_crlf_json(tmp_path / "output-hashes.json", hash_list(root))
    expected = file_sha256(hashes)

    tracemalloc.start()
    try:
        result = check_released_records(root, hashes, expected)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.passed
    assert peak < 8 * 2**20


@pytest.mark.parametrize(
    ("record", "name"),
    [
        pytest.param("released_hashes", "released output hash list", id="hash-list"),
        pytest.param("input_databases", "input databases record", id="input-databases"),
    ],
)
def test_preflight_names_the_record_whose_sha256_is_not_the_pinned_one(
    tmp_path: Path, record: str, name: str
) -> None:
    inputs = build_preflight(tmp_path)
    path: Path = getattr(inputs, record)
    pinned = file_sha256(path)
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    message = f"the {name} {path} has SHA-256 {file_sha256(path)}, but the study file pins {pinned}"

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        inputs.run()


def test_preflight_names_each_hash_that_differs_from_the_study_file(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    study = yaml.safe_load(inputs.study.read_text(encoding="utf-8"))
    inputs.protocol.write_bytes(b"protocol: nuplan_aeb_v2\n")
    document = json.loads(inputs.manifest.read_bytes())
    document["families"]["bicycle_or_vru"] = ["bike-a"]
    inputs.manifest.write_text(json.dumps(document, indent=4), encoding="utf-8")

    result = inputs.run()

    assert result.local_detail[4:7] == (
        f"the protocol hash is {file_sha256(inputs.protocol)}, "
        f"and the study file records {study['seed_namespace_protocol_sha256']}",
        f"the cohort manifest file hash is {file_sha256(inputs.manifest)}, "
        f"and the study file records {study['cohort_manifest_file_sha256']}",
        f"the cohort membership hash is {membership(inputs.manifest)}, "
        f"and the study file records {study['cohort_membership_sha256']}",
    )


def test_preflight_counts_and_names_every_missing_log(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    for log in LOGS:
        inputs.log(log).unlink()

    result = inputs.run()

    assert failing(result, PREFLIGHT_FAILURES) == {"logs_missing": 2}
    split_directory = inputs.data_root / "nuplan-v1.1" / "splits" / "val"
    assert result.local_detail[4:] == tuple(
        f"{log} is missing from {split_directory}" for log in LOGS
    )


def test_preflight_checks_every_log_after_a_missing_one(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    inputs.log(LOGS[0]).unlink()
    log = inputs.log(LOGS[1])
    log.write_bytes(log.read_bytes() + b"x")

    result = inputs.run()

    assert failing(result, PREFLIGHT_FAILURES) == {
        "logs_missing": 1,
        "log_size_mismatches": 1,
        "log_hash_mismatches": 1,
    }
    assert result.counts["logs_present"] == 1


def test_preflight_counts_every_log_whose_size_and_sha256_differ(tmp_path: Path) -> None:
    inputs = build_preflight(tmp_path)
    for log in LOGS:
        path = inputs.log(log)
        path.write_bytes(path.read_bytes() + b"x")

    result = inputs.run()

    assert failing(result, PREFLIGHT_FAILURES) == {
        "log_size_mismatches": 2,
        "log_hash_mismatches": 2,
    }


@pytest.fixture()
def clock_eight_hours_east_of_utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A machine whose local time is eight hours ahead of UTC, and UTC again afterwards."""

    monkeypatch.setenv("TZ", "TST-08")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.usefixtures("clock_eight_hours_east_of_utc")
def test_preflight_writes_each_log_modification_time_in_utc_to_the_microsecond(
    tmp_path: Path,
) -> None:
    inputs = build_preflight(tmp_path)
    seconds = int(datetime(2001, 2, 3, 4, 5, 6, tzinfo=timezone.utc).timestamp())
    nanoseconds = seconds * 1_000_000_000 + 123_456_999
    for log in LOGS:
        os.utime(inputs.log(log), ns=(nanoseconds, nanoseconds))

    result = inputs.run()

    for log in LOGS:
        assert f"{log} modified 2001-02-03T04:05:06.123456Z" in result.local_detail


STUDY_HASH = "1" * 64
COHORT_HASH = "2" * 64


def test_the_gate_file_is_sorted_two_space_indented_utf8_json(tmp_path: Path) -> None:
    gates = a_gate_file(
        arms_checked=["B-v2-gated"],
        gates=[{"gate": "G2", "passed": False, "counts": {"oracle_aeb": 3, "no_aeb": 0}}],
        artifacts_only_detail={"G2": ["no_aeb/lead-a.json is missing under released/références"]},
    )
    path = tmp_path / "gates.json"

    write_gates(path, gates)

    lines = [
        "{",
        '  "arms_checked": [',
        '    "B-v2-gated"',
        "  ],",
        '  "artifacts_only_detail": {',
        '    "G2": [',
        '      "no_aeb/lead-a.json is missing under released/références"',
        "    ]",
        "  },",
        f'  "cohort_manifest_sha256": "{COHORT_HASH}",',
        '  "exploratory": false,',
        '  "gates": [',
        "    {",
        '      "counts": {',
        '        "no_aeb": 0,',
        '        "oracle_aeb": 3',
        "      },",
        '      "gate": "G2",',
        '      "passed": false',
        "    }",
        "  ],",
        f'  "protocol_sha256": "{PROTOCOL_SHA}",',
        '  "reference": "released",',
        '  "schema_version": "aeb-study-gates/v1",',
        f'  "study_sha256": "{STUDY_HASH}"',
        "}",
    ]
    assert path.read_bytes() == ("\n".join(lines) + "\n").encode("utf-8")


def test_the_published_gate_file_is_the_written_one_without_its_local_detail(
    tmp_path: Path,
) -> None:
    gates = a_gate_file(artifacts_only_detail={"G2": ["oracle_aeb/lead-a.json differs"]})
    path = tmp_path / "gates.json"
    write_gates(path, gates)
    written = json.loads(path.read_bytes())
    del written["artifacts_only_detail"]

    assert published_gates(gates) == written
