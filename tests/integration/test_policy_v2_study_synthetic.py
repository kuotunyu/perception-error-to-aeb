"""The policy v2 study and the post-hoc addendum, end to end, on the synthetic cohort.

The unit tests hold each `study` command to what it hands on and each function
to what it computes. This test runs the commands in the order the operator
procedure (`docs/verification/policy-v2-study.md`) gives them, in a repository
laid out like the real one, and checks that what one command writes is what the
next one reads: the released run and its evidence, the five arms, the gate
files, the analysis, the addendum, the evidence each part publishes and the
claims registry built from that evidence.

THE INPUTS ARE SYNTHETIC AND NOTHING HERE IS A RESULT. The cohort is the one
synthetic token. Its released run is `aeb-risk simulate --config-id all` on that
token, and its released evidence is what `aeb-risk evaluate` writes from that
run. The study file is the committed one, recording the synthetic protocol and
manifest instead of the released ones, because a formal arm runs only on the
cohort its study file records. The released run's hash list is written here in
the shape of the private one, and the SHA-256 the addendum pins is patched to
that list's, because the committed value pins the released list.

Two studies run, each in its own copy of the repository. In the first, every
gate passes, and both parts publish their evidence and claims. In the second, a
change to the v1 path, patched in while the arms run, makes arm A differ from
the released records. G2 fails, and the study goes on in arm-A reference mode,
where every output it writes is labelled exploratory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import Result
from pydantic import BaseModel
from typer.testing import CliRunner

from aebrisk.aeb.state_machine import policy_for
from aebrisk.analysis.claims import audit_claims, load_registry
from aebrisk.artifacts.documents import DOCUMENT_MODELS
from aebrisk.artifacts.study_documents import (
    AttributionAddendumEvidenceV1,
    AttributionAddendumV1,
    PolicyV2EvidenceV1,
    PolicyV2SummaryV1,
    StudyG0V1,
    StudyGatesV1,
    StudyReproductionV1,
)
from aebrisk.cli.app import app
from aebrisk.cohort.manifest import load_manifest, membership_sha256
from aebrisk.dev import verify_schema_contracts
from aebrisk.simulation.synthetic import SYNTHETIC_LOG, TOKEN
from aebrisk.study.gates import load_gates

ROOT = Path(__file__).resolve().parents[2]
COMMITTED_STUDY = ROOT / "configs" / "experiments" / "aeb_policy_v2_study.yaml"

PROTOCOL_TEXT = "protocol: nuplan_aeb_v2\n"
PROTOCOL_SHA = hashlib.sha256(PROTOCOL_TEXT.encode("utf-8")).hexdigest()
COMMIT = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "sha256:" + "d" * 64

#: The synthetic token's log in the eligibility record, named as a nuPlan log is,
#: so that the analysis can read the drive it belongs to.
LOG = "2000.01.01.00.00.00_veh-00_00000_00001.db"

#: The pre-registration pull request, as `study evidence` is given it.
PREREGISTRATION_PR = 5
PREREGISTRATION_COMMIT = "abcdef0123456789abcdef0123456789abcdef01"
PREREGISTRATION_MERGED_AT = "2026-09-26T08:12:34Z"

ARMS = ("A-v1-replication", "B-v2-gated", "C-v1-kalman", "D-v2-kalman", "E-v2-channel-rng")

# Every path is relative to the repository root, as the operator procedure writes it.
STUDY = "configs/experiments/aeb_policy_v2_study.yaml"
PROTOCOL = "configs/protocols/nuplan_aeb_v2.yaml"
COHORT = "docs/evidence/nuplan_aeb_v2/cohort"
MANIFEST = f"{COHORT}/evaluation.json"
ELIGIBILITY = f"{COHORT}/evaluation-eligibility.json"
RELEASED_ROOT = "artifacts/formal/nuplan_aeb_v2"
RELEASED_EVIDENCE = "docs/evidence/nuplan_aeb_v2"
RELEASED_HASHES = "artifacts/d2-operations/output-hashes.json"
STUDY_ARTIFACTS = "artifacts/formal/aeb_policy_v2"
ARMS_ROOT = f"{STUDY_ARTIFACTS}/attempt-1"
GATES_A = f"{STUDY_ARTIFACTS}/attempt-1.gates-A.json"
GATES = f"{STUDY_ARTIFACTS}/attempt-1.gates.json"
G0 = f"{STUDY_ARTIFACTS}/g0.json"
SUMMARY = "artifacts/studies/aeb_policy_v2/summary.json"
G4 = "artifacts/studies/aeb_policy_v2/g4.json"
ADDENDUM_SUMMARY = "artifacts/posthoc/nuplan_aeb_v2-addendum/addendum-summary.json"
STUDY_EVIDENCE = "docs/studies/aeb-policy-v2/evidence"
STUDY_CLAIMS = "docs/studies/aeb-policy-v2/claims.yaml"
ADDENDUM_EVIDENCE = "docs/posthoc/nuplan_aeb_v2-addendum/evidence"
ADDENDUM_CLAIMS = "docs/posthoc/nuplan_aeb_v2-addendum/claims.yaml"

#: What each part publishes in its evidence directory, and the model each file is registered as.
STUDY_FILES: dict[str, type[BaseModel]] = {
    "gates.json": StudyGatesV1,
    "policy-v2-evidence.json": PolicyV2EvidenceV1,
    "reproduction.json": StudyReproductionV1,
    "summary.json": PolicyV2SummaryV1,
}
ADDENDUM_FILES: dict[str, type[BaseModel]] = {
    "addendum-summary.json": AttributionAddendumV1,
    "attribution-addendum-evidence.json": AttributionAddendumEvidenceV1,
}


def invoke(*arguments: str) -> Result:
    return CliRunner().invoke(app, list(arguments))


def succeed(*arguments: str) -> Result:
    result = invoke(*arguments)
    assert result.exit_code == 0, result.output
    return result


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def manifest_document(split: str) -> dict[str, Any]:
    """A frozen cohort of the one synthetic token, which the synthetic source resolves."""

    return {
        "schema_version": "aeb-cohort-manifest/v1",
        "split": split,
        "protocol_sha256": PROTOCOL_SHA,
        "families": {
            "lead_or_stopping": [TOKEN],
            "cut_in_or_crossing": [],
            "pedestrian_or_crosswalk": [],
            "bicycle_or_vru": [],
        },
        "log_names": [SYNTHETIC_LOG],
    }


def eligibility_document(accepted: bool) -> dict[str, Any]:
    """An eligibility record in the released shape; the evaluation split accepts the token."""

    rows = [
        {
            "accepted": True,
            "family": "lead_or_stopping",
            "initial_ego_speed_mps": 10.0,
            "log_name": LOG,
            "official_split": "val",
            "oracle_enters_corridor_within_4s": True,
            "oracle_min_ttc_within_4s": 2.5,
            "reason": "",
            "scenario_token": TOKEN,
            "scenario_type": "stationary_in_traffic",
        }
    ]
    return {
        "schema_version": "aeb-cohort-eligibility/v1",
        "examined": rows if accepted else [],
        "scenarios_in_split_by_family": {"lead_or_stopping": int(accepted)},
    }


def write_hash_list(released_root: Path, path: Path) -> None:
    """The released run's list of every output, in the shape of the private list.

    Two-space indent, CRLF line ends and a final LF, as the released list was
    written.
    """

    listing = {
        "schema_version": "aeb-d2-output-hashes/v2",
        "generated_at_utc": "2026-09-06T20:54:13.4306365Z",
        "formal_files": [
            {
                "path": item.relative_to(released_root).as_posix(),
                "bytes": item.stat().st_size,
                "sha256": file_sha256(item),
            }
            for item in sorted(released_root.rglob("*"))
            if item.is_file()
        ],
        "operation_files": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\r\n".join(json.dumps(listing, indent=2).split("\n")) + "\n").encode())


def write_study(root: Path) -> None:
    """The committed study file, recording the synthetic protocol and manifest instead.

    Its other inputs, the token-to-log source among them, are the committed
    ones, which this repository lays out at the same paths.
    """

    document = yaml.safe_load(COMMITTED_STUDY.read_text(encoding="utf-8"))
    assert (document["cohort_manifest"], document["token_log_source"]) == (MANIFEST, ELIGIBILITY)
    manifest = root / MANIFEST
    document["seed_namespace_protocol_sha256"] = PROTOCOL_SHA
    document["cohort_manifest_file_sha256"] = file_sha256(manifest)
    document["cohort_membership_sha256"] = membership_sha256(load_manifest(manifest))
    path = root / STUDY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


@pytest.fixture(scope="module")
def synthetic_release(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repository with the synthetic cohort, its released run and evidence, and a study file."""

    root = tmp_path_factory.mktemp("synthetic-release")
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(root)
        patch.setenv("AEBRISK_COMMIT", COMMIT)
        patch.setenv("AEBRISK_IMAGE_DIGEST", IMAGE)
        (root / PROTOCOL).parent.mkdir(parents=True)
        (root / PROTOCOL).write_text(PROTOCOL_TEXT, encoding="utf-8")
        for split in ("smoke", "development", "evaluation"):
            write_json(root / COHORT / f"{split}.json", manifest_document(split))
        write_json(root / COHORT / "development-eligibility.json", eligibility_document(False))
        write_json(root / ELIGIBILITY, eligibility_document(True))
        succeed(
            "simulate",
            "--config-id",
            "all",
            "--protocol",
            PROTOCOL,
            "--manifest",
            MANIFEST,
            "--output-dir",
            RELEASED_ROOT,
            "--scenario-source",
            "synthetic",
            "--workers",
            "1",
        )
        succeed(
            "evaluate",
            "--results-dir",
            RELEASED_ROOT,
            "--manifest",
            MANIFEST,
            "--output-dir",
            RELEASED_EVIDENCE,
        )
        write_hash_list(root / RELEASED_ROOT, root / RELEASED_HASHES)
        write_study(root)
    return root


@pytest.fixture()
def repository(synthetic_release: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the synthetic repository, as the working directory of a formal run.

    The SHA-256 the addendum pins is the synthetic hash list's.
    """

    root = tmp_path / "repository"
    shutil.copytree(synthetic_release, root)
    monkeypatch.chdir(root)
    monkeypatch.setenv("AEBRISK_COMMIT", COMMIT)
    monkeypatch.setenv("AEBRISK_IMAGE_DIGEST", IMAGE)
    monkeypatch.setattr(
        "aebrisk.study.addendum.RELEASED_OUTPUT_HASHES_SHA256", file_sha256(root / RELEASED_HASHES)
    )
    return root


# --------------------------------------------------------------------------
# The commands, as the operator procedure gives them
# --------------------------------------------------------------------------


def simulate_arm(arm: str) -> None:
    succeed(
        "study",
        "simulate",
        "--study",
        STUDY,
        "--arm",
        arm,
        "--protocol",
        PROTOCOL,
        "--manifest",
        MANIFEST,
        "--scenario-source",
        "synthetic",
        "--workers",
        "1",
        "--output-dir",
        f"{ARMS_ROOT}/{arm}",
    )


def study_verify(output: str, *extra: str) -> Result:
    return invoke(
        "study",
        "verify",
        "--study",
        STUDY,
        "--arms-root",
        ARMS_ROOT,
        "--released-root",
        RELEASED_ROOT,
        "--manifest",
        MANIFEST,
        *extra,
        "--output",
        output,
    )


def study_analyse(*extra: str) -> Result:
    return invoke(
        "study",
        "analyse",
        "--study",
        STUDY,
        "--arms-root",
        ARMS_ROOT,
        "--released-root",
        RELEASED_ROOT,
        "--evidence-dir",
        RELEASED_EVIDENCE,
        "--manifest",
        MANIFEST,
        "--gates",
        GATES,
        *extra,
        "--output",
        SUMMARY,
    )


def publish_study() -> None:
    """Write the study's evidence and build its claims registry, from the repository root."""

    succeed(
        "study",
        "evidence",
        "--part",
        "study",
        "--summary",
        SUMMARY,
        "--gates",
        GATES,
        "--arms-root",
        ARMS_ROOT,
        "--g0",
        G0,
        "--preregistration-pr",
        str(PREREGISTRATION_PR),
        "--preregistration-commit",
        PREREGISTRATION_COMMIT,
        "--preregistration-merged-at",
        PREREGISTRATION_MERGED_AT,
        "--output-dir",
        STUDY_EVIDENCE,
    )
    succeed(
        "study",
        "claims",
        "--part",
        "study",
        "--evidence-dir",
        STUDY_EVIDENCE,
        "--output",
        STUDY_CLAIMS,
    )


def write_g0(root: Path) -> dict[str, Any]:
    """A fixture of the operator's G0 record: the tooling commit, its CI run, and the pilot.

    No pilot runs here. The pilot's run contexts are the attempt's own, with
    the smoke manifest's membership hash in place of the cohort's, and its gate
    file passed G1 and G5 over the five arms.
    """

    smoke = membership_sha256(load_manifest(root / COHORT / "smoke.json"))
    contexts = {
        arm: {
            **json.loads((root / ARMS_ROOT / arm / "run_context.json").read_text(encoding="utf-8")),
            "cohort_sha256": smoke,
        }
        for arm in ARMS
    }
    record = {
        "schema_version": "aeb-study-g0/v1",
        "tooling_commit": COMMIT,
        "ci_run_id": 17_234_567_890,
        "pilot_run_contexts": contexts,
        "pilot_gates": {
            "schema_version": "aeb-study-gates/v1",
            "study_sha256": file_sha256(root / STUDY),
            "arms_checked": list(ARMS),
            "protocol_sha256": PROTOCOL_SHA,
            "cohort_manifest_sha256": membership_sha256(load_manifest(root / MANIFEST)),
            "reference": "released",
            "exploratory": False,
            "gates": [
                {"gate": "G1", "passed": True, "counts": {"arms": 5}},
                {"gate": "G5", "passed": True, "counts": {"arms": 5}},
            ],
        },
    }
    write_json(root / G0, record)
    return record


def gate_outcomes(path: Path) -> list[tuple[str, bool]]:
    return [(gate.gate, gate.passed) for gate in load_gates(path).gates]


def published(directory: Path, expected: Mapping[str, type[BaseModel]]) -> dict[str, BaseModel]:
    """Each file in `directory`, validated as the model its schema version is registered with."""

    assert sorted(path.name for path in directory.iterdir()) == sorted(expected)
    documents: dict[str, BaseModel] = {}
    for name, model in expected.items():
        payload = json.loads((directory / name).read_text(encoding="utf-8"))
        assert DOCUMENT_MODELS[payload["schema_version"]] is model
        documents[name] = model.model_validate(payload)
    return documents


def policy_for_with_weaker_v1_partial_braking(version: str) -> Mapping[str, Any]:
    """The committed policies, except that v1's partial stage asks for -2.0 m/s^2, not -3.0."""

    policy = policy_for(version)
    if version != "v1":
        return policy
    return {**policy, "partial": {**policy["partial"], "target_accel_mps2": -2.0}}


# --------------------------------------------------------------------------
# A study whose gates pass
# --------------------------------------------------------------------------


def test_a_study_whose_gates_pass_publishes_the_evidence_and_claims_of_both_parts(
    repository: Path,
) -> None:
    simulate_arm(ARMS[0])
    arm_a = study_verify(GATES_A, "--arm", ARMS[0])

    assert arm_a.exit_code == 0, arm_a.output
    assert sorted(path.name for path in (repository / ARMS_ROOT).iterdir()) == [ARMS[0]]
    assert gate_outcomes(repository / GATES_A) == [("G1", True), ("G2", True), ("G5", True)]

    for arm in ARMS[1:]:
        simulate_arm(arm)
    verified = study_verify(GATES)

    assert verified.exit_code == 0, verified.output
    gates = load_gates(repository / GATES)
    assert [(gate.gate, gate.passed) for gate in gates.gates] == [
        ("G1", True),
        ("G2", True),
        ("G3", True),
        ("G5", True),
    ]
    assert (gates.arms_checked, gates.reference, gates.exploratory) == (ARMS, "released", False)

    g0 = write_g0(repository)
    analysed = study_analyse()

    assert analysed.exit_code == 0, analysed.output
    summary = PolicyV2SummaryV1.model_validate_json((repository / SUMMARY).read_bytes())
    assert gate_outcomes(repository / G4) == [("G4", True)]
    assert (summary.g4.passed, summary.common_valid_tokens) == (True, 1)
    assert (summary.reference, summary.exploratory) == ("released", False)

    succeed(
        "study",
        "addendum",
        "--released-root",
        RELEASED_ROOT,
        "--released-hashes",
        RELEASED_HASHES,
        "--evidence-dir",
        RELEASED_EVIDENCE,
        "--manifest",
        MANIFEST,
        "--eligibility",
        ELIGIBILITY,
        "--output",
        ADDENDUM_SUMMARY,
    )
    addendum = AttributionAddendumV1.model_validate_json(
        (repository / ADDENDUM_SUMMARY).read_bytes()
    )
    assert addendum.reproduction_gate.passed
    assert addendum.released_output_hashes_sha256 == file_sha256(repository / RELEASED_HASHES)

    publish_study()
    succeed(
        "study",
        "evidence",
        "--part",
        "addendum",
        "--addendum",
        ADDENDUM_SUMMARY,
        "--output-dir",
        ADDENDUM_EVIDENCE,
    )
    succeed(
        "study",
        "claims",
        "--part",
        "addendum",
        "--evidence-dir",
        ADDENDUM_EVIDENCE,
        "--output",
        ADDENDUM_CLAIMS,
    )

    study = published(repository / STUDY_EVIDENCE, STUDY_FILES)
    published(repository / ADDENDUM_EVIDENCE, ADDENDUM_FILES)
    assert study["summary.json"] == summary
    reproduction = study["reproduction.json"]
    assert isinstance(reproduction, StudyReproductionV1)
    assert (
        reproduction.preregistration_pull_request,
        reproduction.preregistration_commit,
        reproduction.preregistration_merged_at,
    ) == (PREREGISTRATION_PR, PREREGISTRATION_COMMIT, PREREGISTRATION_MERGED_AT)
    assert reproduction.g0 == StudyG0V1.model_validate(g0)
    assert [(record.gate, record.outcome) for record in reproduction.gates] == [
        (gate, "passed") for gate in ("G0", "G1", "G2", "G3", "G4", "G5")
    ]
    assert [run.arm_id for run in reproduction.arms] == list(ARMS)
    for claims in (STUDY_CLAIMS, ADDENDUM_CLAIMS):
        assert load_registry(repository / claims).claims
        assert audit_claims(repository / claims, repository) == ()
    assert verify_schema_contracts(repository) == 0


# --------------------------------------------------------------------------
# A study whose arm A does not replicate the released records
# --------------------------------------------------------------------------


def test_a_change_to_the_v1_path_fails_g2_and_arm_a_reference_mode_labels_every_output(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every arm's step loop reads its policy through this name. Arms A and C
    # run v1, which the change reaches; B, D and E run v2, which it leaves as is.
    monkeypatch.setattr(
        "aebrisk.simulation.step_loop.policy_for", policy_for_with_weaker_v1_partial_braking
    )
    simulate_arm(ARMS[0])
    arm_a = study_verify(GATES_A, "--arm", ARMS[0])

    assert arm_a.exit_code == 1
    assert gate_outcomes(repository / GATES_A) == [("G1", True), ("G2", False), ("G5", True)]
    replication = load_gates(repository / GATES_A).gates[1]
    assert (replication.counts["no_aeb"], replication.counts["oracle_aeb"]) == (0, 1)

    for arm in ARMS[1:]:
        simulate_arm(arm)
    verified = study_verify(GATES, "--reference", "arm-a")

    assert verified.exit_code == 0, verified.output
    assert gate_outcomes(repository / GATES) == [
        ("G1", True),
        ("G2", False),
        ("G3", True),
        ("G5", True),
    ]

    write_g0(repository)
    refused = study_analyse()

    assert refused.exit_code == 1
    assert "the study is not analysed" in refused.output
    assert "reference" in refused.output
    assert not (repository / SUMMARY).exists()
    assert not (repository / G4).exists()

    analysed = study_analyse("--reference", "arm-a")
    assert analysed.exit_code == 0, analysed.output
    publish_study()

    labelled: list[BaseModel] = [
        load_gates(repository / GATES),
        PolicyV2SummaryV1.model_validate_json((repository / SUMMARY).read_bytes()),
        *published(repository / STUDY_EVIDENCE, STUDY_FILES).values(),
    ]
    for document in labelled:
        fields = document.model_dump(mode="json")
        assert (fields["reference"], fields["exploratory"]) == ("arm-a", True)
    claims = load_registry(repository / STUDY_CLAIMS).claims
    assert claims
    assert all(claim.text.startswith("Exploratory:") for claim in claims)
    assert audit_claims(repository / STUDY_CLAIMS, repository) == ()
