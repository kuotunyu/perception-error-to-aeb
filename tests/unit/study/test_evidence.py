"""The published evidence of the policy v2 study and of the post-hoc addendum.

`study evidence` turns what `study analyse`, `study verify` and `study addendum`
wrote under `artifacts/` into the files a results pull request commits. These
tests write those inputs by hand: a summary and an addendum summary with a few
rows each, a gate file whose local detail names a path and a cohort token, the
operator's `g0.json`, and five arm directories whose run logs name a cohort
token between their first and last lines. The writers must copy numbers and
recompute none, leave out everything that may name a path or a token, and
refuse anything a reader must not see or could not trust.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import pytest
from pydantic import BaseModel, ValidationError

from aebrisk import dev
from aebrisk.artifacts.documents import DOCUMENT_MODELS, write_document
from aebrisk.artifacts.study_documents import (
    COLLISION_GAME_SENTENCE,
    AttributionAddendumEvidenceV1,
    AttributionAddendumV1,
    PolicyV2EvidenceV1,
    PolicyV2SummaryV1,
    StudyG0V1,
    StudyGatesV1,
    StudyReproductionV1,
    StudyRunContextV1,
)
from aebrisk.attribution.shapley import CHANNELS
from aebrisk.study.definition import StudyRunContext
from aebrisk.study.evidence import (
    ADDENDUM_EVIDENCE_FILE,
    ADDENDUM_SUMMARY_FILE,
    GATES_FILE,
    POLICY_V2_EVIDENCE_FILE,
    REPRODUCTION_FILE,
    SUMMARY_FILE,
    is_ancestor,
    write_addendum_evidence,
    write_study_evidence,
)

ROOT = Path(__file__).resolve().parents[3]

PROTOCOL_SHA = "bbf0b6d31943a0f99160afe1d4c18f9a181850367494004037bab98d8f2e59f9"
COHORT_SHA = "65e38df24b91786fb773b883e1cad4348c0cdc58ac976c729871c77e9478daf9"
STUDY_SHA = "5" * 64
RELEASED_HASHES_SHA = "47439aad52f112ff2d3e1142cc8530ddd678c5ae5e58a77416beccfe8c730db0"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "89abcdef0123456789abcdef0123456789abcdef"
IMAGE = "sha256:" + "d" * 64
PREREGISTRATION_PR = 5
PREREGISTRATION_COMMIT = "abcdef0123456789abcdef0123456789abcdef01"
MERGED_AT = "2026-09-26T08:12:34Z"

#: A scenario token of the committed cohort files (an examined row of the eligibility record).
COHORT_TOKEN = "15c3255839035bee"
#: A string with the shape of a scenario token that no committed cohort file holds.
TOKEN_SHAPE = "0123456789abcdef"

ARMS = ("A-v1-replication", "B-v2-gated", "C-v1-kalman", "D-v2-kalman", "E-v2-channel-rng")
LOCALIZATION = "localization_shape-medium"
FULL_COALITION = "coalition-dropout+localization_shape+latency+track_instability"
STUDY_CELLS = (
    "no_aeb",
    "oracle_aeb",
    "dropout-medium",
    LOCALIZATION,
    "latency-medium",
    "track_instability-medium",
    "coalition-none",
    FULL_COALITION,
)

#: The fields a summary carries beside its numbers, which its evidence leaves out.
SUMMARY_ONLY = {"schema_version", "g4", "q1_label", "h5_qualifier", "statements"}
#: The fields an addendum summary carries beside its numbers, which its evidence leaves out.
ADDENDUM_SUMMARY_ONLY = {"schema_version", "released_output_hashes_sha256", "reproduction_gate"}


# --------------------------------------------------------------------------
# The inputs, written by hand
# --------------------------------------------------------------------------


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    return path


def run_context(arm: str, **updates: str) -> dict[str, str]:
    """An arm's run context, with every field of `StudyRunContext`."""

    context = {
        "configuration_id": f"study:{arm}",
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_sha256": COHORT_SHA,
        "container_digest": IMAGE,
        "commit": COMMIT,
        "study_sha256": STUDY_SHA,
        "arm_id": arm,
        "aeb_policy": "v1" if arm[0] in "AC" else "v2",
        "policy_sha256": "1" * 64,
        "rng_scheme": "channel-independent" if arm[0] == "E" else "dropout-keyed",
        "velocity_estimator": "cv-kalman" if arm[0] in "CD" else "finite-difference",
        "velocity_parameters": "",
        "error_config_sha256": "2" * 64,
        "python_version": "3.9.19",
        "numpy_version": "1.23.4",
    }
    context.update(updates)
    return context


def started(index: int) -> str:
    return f"2026-10-0{index + 1}T00:00:00.000001Z"


def finished(index: int) -> str:
    return f"2026-10-0{index + 1}T06:30:00.654321Z"


def write_arm(arms_root: Path, arm: str, **context: str) -> Path:
    """An arm directory with its run context and a run log that names a cohort token."""

    index = ARMS.index(arm)
    root = arms_root / arm
    write_json(root / "run_context.json", run_context(arm, **context))
    (root / "run.log").write_text(
        f"{started(index)} protocol {PROTOCOL_SHA} and 3 cells\n"
        f"2026-10-0{index + 1}T01:00:00.000000Z {COHORT_TOKEN} complete\n"
        f"{finished(index)} run complete\n",
        encoding="utf-8",
    )
    return root


def gate(name: str, passed: bool = True, **counts: int) -> dict[str, Any]:
    return {"gate": name, "passed": passed, "counts": counts}


def gates_payload(**updates: Any) -> dict[str, Any]:
    """A gate file of all five arms, whose local detail names a path and a cohort token.

    The detail is kept only for the gates the file holds, as the model requires.
    """

    payload: dict[str, Any] = {
        "schema_version": "aeb-study-gates/v1",
        "study_sha256": STUDY_SHA,
        "arms_checked": list(ARMS),
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": COHORT_SHA,
        "reference": "released",
        "exploratory": False,
        "gates": [
            gate("G1", arms=5, documents_missing=0, distinct_commits=1),
            {"gate": "G2", "passed": True, "counts": dict.fromkeys(STUDY_CELLS, 0)},
            {"gate": "G3", "passed": True, "counts": {"no_aeb:B-v2-gated:released": 0}},
            gate("G5", arms=5, version_mismatches=0),
        ],
        "artifacts_only_detail": {
            "G1": [f"A-v1-replication: artifacts/formal/aeb_policy_v2/no_aeb/{COHORT_TOKEN}.json"]
        },
    }
    payload.update(updates)
    held = {row["gate"] for row in payload["gates"]}
    payload["artifacts_only_detail"] = {
        name: detail for name, detail in payload["artifacts_only_detail"].items() if name in held
    }
    return payload


def pilot_gates_payload(**updates: Any) -> dict[str, Any]:
    pilot: dict[str, Any] = {
        "gates": [gate("G1", arms=5), gate("G5", arms=5)],
        "artifacts_only_detail": {"G1": ["artifacts/pilot/A-v1-replication checked"]},
    }
    pilot.update(updates)
    return gates_payload(**pilot)


def g0_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "aeb-study-g0/v1",
        "tooling_commit": COMMIT,
        "ci_run_id": 17_234_567_890,
        "pilot_run_contexts": {arm: run_context(arm) for arm in ARMS},
        "pilot_gates": pilot_gates_payload(),
    }
    payload.update(updates)
    return payload


def term(sign: int, arm: str, cell: str = LOCALIZATION) -> dict[str, Any]:
    return {"sign": sign, "arm": arm, "cell": cell}


def contrast(outcome: str = "braking_share", **updates: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "outcome": outcome,
        "terms": [term(1, "B-v2-gated"), term(-1, "A-v1-replication")],
        "estimate": -0.12345678901234567,
        "cluster": "family-log",
        "interval": {"confidence": 0.95, "low": -0.2, "high": -0.05},
    }
    value.update(updates)
    return value


def distribution(count: int = 2) -> dict[str, Any]:
    return {
        "count": count,
        "median": 1.5,
        "lower_quartile": 1.25,
        "upper_quartile": 1.75,
        "maximum": 2.0,
    }


def level(arm: str = "B-v2-gated", cell: str = "oracle_aeb") -> dict[str, Any]:
    return {
        "arm": arm,
        "cell": cell,
        "tokens": 344,
        "records": 1032,
        "collisions": 39,
        "collision_tokens": 13,
        "collision_indicator": 0.0377906976744186,
        "collision_clopper_pearson": {
            "event": "collision",
            "arm": arm,
            "cell": cell,
            "successes": 13,
            "trials": 344,
            "confidence": 0.95,
            "low": 0.020269,
            "high": 0.0636,
        },
        "contacts_not_at_fault": 1095,
        "any_contact": 0.5,
        "simulated_seconds": 10000.25,
        "braking_seconds": 2500.5,
        "braking_share": 0.25004999375,
        "braking_seconds_per_run": 2.4229651162790697,
        "not_at_fault_contact_rate": 0.1,
        "brake_activations": 400,
        "activation_rate": 143.99640009,
        "early_ends": 3,
        "max_deceleration_mps2": 6.0,
        "missed_interventions": 0,
        "false_interventions": 2,
        "matched_delays_s": distribution(),
        "stop_distance_m": distribution(),
        "runs_never_stopped": 1,
        "collision_speed_mps": distribution(),
    }


def summary_payload(**updates: Any) -> dict[str, Any]:
    """A summary with one row of each kind, in the shape `study analyse` writes."""

    primary = contrast(
        simultaneous_interval={"confidence": 0.99, "low": -0.25, "high": -0.01},
        holm_step_interval={"confidence": 0.99, "low": -0.25, "high": -0.01},
        test="bootstrap",
        p_value=0.0004,
    )
    payload: dict[str, Any] = {
        "schema_version": "aeb-policy-v2-summary/v1",
        "study_sha256": STUDY_SHA,
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": COHORT_SHA,
        "common_valid_tokens": 344,
        "reference": "released",
        "exploratory": False,
        "g4": gate("G4", intervals_compared=32, differing_values=0),
        "hypotheses": [
            {
                "id": "H1",
                "family": "primary",
                "predicted_sign": -1,
                "contrast": primary,
                "holm_step": 1,
                "rejected": True,
                "classification": "supported",
            }
        ],
        "q1_label": "partial_support",
        "h5_qualifier": False,
        "statements": {
            # A character outside ASCII, which every published file keeps as UTF-8.
            "h3": "Excluded contacts and counted collisions are coupled \u2014 by the stopped-ego rule.",
            "h4": "This study does not show that the zero depends on the target selection.",
            "h5": "This study does not detect a change in counted collisions.",
        },
        "sensitivity": [
            {
                "hypothesis": "H1",
                "analysis": "family_drive_clusters",
                "contrast": contrast(cluster="family-drive"),
            }
        ],
        "q3_ratio": {
            "outcome": "braking_share",
            "b_terms": [term(1, "B-v2-gated", FULL_COALITION), term(-1, "B-v2-gated")],
            "e_terms": [term(1, "E-v2-channel-rng", FULL_COALITION), term(-1, "B-v2-gated")],
            "sd_b": 0.021,
            "sd_e": 0.0105,
            "ratio": 0.5,
        },
        "secondary": [
            {"analysis": "other_cells", "hypothesis": None, "contrast": contrast("any_contact")}
        ],
        "descriptive_contrasts": [
            {"analysis": "velocity_by_cell", "contrast": contrast("collision_indicator")}
        ],
        "levels": [level(), level("A-v1-replication")],
    }
    payload.update(updates)
    return payload


def estimate(value: float, *levels: float, before: bool = False) -> dict[str, Any]:
    return {
        "estimate": value,
        "intervals": [
            {"confidence": confidence, "low": value - 0.125, "high": value + 0.125}
            for confidence in levels or (0.95,)
        ],
        "computed_before_plan": before,
    }


def difference(plus: str, minus: str, value: float, *levels: float) -> dict[str, Any]:
    return {**estimate(value, *levels), "plus": plus, "minus": minus}


def game(name: str) -> dict[str, Any]:
    return {
        "game": name,
        "shapley_values": {
            channel: estimate(0.01 * (index + 1), before=True)
            for index, channel in enumerate(CHANNELS)
        },
        "localization_shape_differences": [
            difference("localization_shape", channel, 0.03125, 1 - 0.05 / 6, 0.95)
            for channel in CHANNELS
            if channel != "localization_shape"
        ],
        "other_differences": [difference("dropout", "latency", 0.001)],
        "caution": COLLISION_GAME_SENTENCE if name == "collision_indicator" else None,
    }


def proportion(events: int, tokens: int = 344) -> dict[str, Any]:
    return {"events": events, "tokens": tokens, "confidence": 0.95, "low": 0.0, "high": 0.0107}


def quartiles(count: int) -> dict[str, Any]:
    present = count > 0
    return {
        "count": count,
        "median": 1.0 if present else None,
        "lower_quartile": 0.5 if present else None,
        "upper_quartile": 1.5 if present else None,
    }


def addendum_payload(**updates: Any) -> dict[str, Any]:
    """An addendum summary with one row of each kind, in the shape `study addendum` writes."""

    payload: dict[str, Any] = {
        "schema_version": "aeb-attribution-addendum/v1",
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": COHORT_SHA,
        "cohort_size": 344,
        "common_valid_tokens": 344,
        "released_output_hashes_sha256": RELEASED_HASHES_SHA,
        "reproduction_gate": gate("reproduction", listed_files=8947, differing_values=0),
        "bootstrap": {
            "cluster": "family-log",
            "clusters": 198,
            "resamples": 5000,
            "seed": 20260831,
        },
        "games": [game("intervention_duration_s"), game("collision_indicator")],
        "configuration_contrasts": [
            {**difference("oracle_aeb", "coalition-none", 0.0125), "metric": "collision_indicator"}
        ],
        "oracle_collisions": {
            "avoided": proportion(20),
            "induced": proportion(2),
            "oracle_aeb_contacts_not_at_fault": 1095,
            "computed_before_plan": True,
        },
        "brake_activations": [
            {
                "configuration_id": "oracle_aeb",
                "brake_activations": 400,
                "simulated_seconds": 12000.5,
                "per_hour": 119.99500020832466,
                "computed_before_plan": True,
            }
        ],
        "zero_event_configurations": [
            {
                "configuration_id": FULL_COALITION,
                "overall": proportion(0),
                "by_family": {"lead_or_stopping": proportion(0, 100)},
                "computed_before_plan": True,
            }
        ],
        "matched_onset_delays": [
            {
                "configuration_id": "coalition-none",
                "matched_onset_delays_s": quartiles(3),
                "computed_before_plan": False,
            }
        ],
        "stops_and_collision_speeds": [
            {
                "configuration_id": "oracle_aeb",
                "first_stop_distance_m": quartiles(2),
                "ego_speed_at_collision_mps": quartiles(0),
                "computed_before_plan": False,
            }
        ],
    }
    payload.update(updates)
    return payload


def document_bytes(document: BaseModel, directory: Path) -> bytes:
    """The bytes the released `write_document` writes for a registered document."""

    path = directory / "document.json"
    write_document(document, path)
    return path.read_bytes()


@dataclass
class Study:
    """The inputs of `study evidence --part study`, and where it writes."""

    root: Path
    summary: Path
    gates: Path
    g0: Path
    arms_root: Path
    output_dir: Path

    def write(
        self,
        summary: Optional[Path] = None,
        gates: Optional[Path] = None,
        not_completed: bool = False,
        earlier_gates: Sequence[Path] = (),
        g4: Optional[Path] = None,
        **overrides: Any,
    ) -> tuple[Path, ...]:
        arguments: dict[str, Any] = {
            "summary_path": None if not_completed else (summary or self.summary),
            "gates_path": gates or self.gates,
            "g0_path": self.g0,
            "arms_root": self.arms_root,
            "preregistration_pr": PREREGISTRATION_PR,
            "preregistration_commit": PREREGISTRATION_COMMIT,
            "preregistration_merged_at": MERGED_AT,
            "output_dir": self.output_dir,
            "not_completed": not_completed,
            "earlier_gates": tuple(earlier_gates),
            "g4_path": g4,
        }
        arguments.update(overrides)
        return write_study_evidence(**arguments)

    def published(self, name: str) -> dict[str, Any]:
        value: dict[str, Any] = json.loads((self.output_dir / name).read_text(encoding="utf-8"))
        return value

    def gate_file(self, name: str, **updates: Any) -> Path:
        return write_json(self.root / "artifacts" / name, gates_payload(**updates))


@pytest.fixture
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Study:
    """Every input of a completed study, with the repository as the working directory."""

    monkeypatch.chdir(ROOT)
    artifacts = tmp_path / "artifacts"
    arms_root = artifacts / "attempt-1"
    for arm in ARMS:
        write_arm(arms_root, arm)
    summary = artifacts / "summary.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    write_document(PolicyV2SummaryV1.model_validate(summary_payload()), summary)
    return Study(
        root=tmp_path,
        summary=summary,
        gates=write_json(artifacts / "attempt-1.gates.json", gates_payload()),
        g0=write_json(artifacts / "g0.json", g0_payload()),
        arms_root=arms_root,
        output_dir=tmp_path / "docs" / "studies" / "aeb-policy-v2" / "evidence",
    )


def rewrite_summary(study: Study, **updates: Any) -> None:
    """Replace the summary with one whose fields are updated, still written as `study analyse` does."""

    write_document(PolicyV2SummaryV1.model_validate(summary_payload(**updates)), study.summary)


def nothing_written(directory: Path) -> bool:
    return not directory.exists() or not any(directory.iterdir())


# --------------------------------------------------------------------------
# The models
# --------------------------------------------------------------------------


def test_the_run_context_model_has_exactly_the_fields_of_the_run_context() -> None:
    """`g0.json` copies each pilot arm's run context, field for field."""

    assert tuple(StudyRunContextV1.model_fields) == tuple(
        field.name for field in dataclasses.fields(StudyRunContext)
    )


def test_the_evidence_models_copy_only_fields_their_summaries_hold() -> None:
    """The evidence is derived from its summary alone, so each of its fields must be there."""

    for evidence, summary in (
        (PolicyV2EvidenceV1, PolicyV2SummaryV1),
        (AttributionAddendumEvidenceV1, AttributionAddendumV1),
    ):
        assert set(evidence.model_fields) <= set(summary.model_fields)


def test_every_evidence_model_carries_both_hashes_under_the_names_the_claims_audit_reads() -> None:
    for model in (PolicyV2EvidenceV1, StudyReproductionV1, AttributionAddendumEvidenceV1):
        assert {"protocol_sha256", "cohort_manifest_sha256"} <= set(model.model_fields)


def test_the_g0_record_is_not_a_published_document() -> None:
    """`g0.json` stays under `artifacts/`; it reaches the evidence only inside `reproduction.json`."""

    assert "aeb-study-g0/v1" not in DOCUMENT_MODELS
    assert StudyG0V1.model_validate(g0_payload()).tooling_commit == COMMIT


@pytest.mark.parametrize(
    "updates",
    [
        {"pilot_run_contexts": {arm: run_context(arm) for arm in ARMS[:4]}},
        {"pilot_run_contexts": {**{arm: run_context(arm) for arm in ARMS}, "F": run_context("F")}},
        {
            "pilot_run_contexts": {
                **{arm: run_context(arm) for arm in ARMS},
                "B-v2-gated": run_context("A-v1-replication"),
            }
        },
        {"ci_run_id": 0},
        {"ci_run_id": "17234567890"},
        {"tooling_commit": COMMIT[:12]},
        {"unexpected": True},
    ],
    ids=[
        "four-pilot-arms",
        "six-pilot-arms",
        "context-filed-under-another-arm",
        "zero-run-id",
        "run-id-as-text",
        "short-commit",
        "unknown-field",
    ],
)
def test_a_g0_record_that_fails_its_model_is_refused(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        StudyG0V1.model_validate(g0_payload(**updates))


def reproduction_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "aeb-study-reproduction/v1",
        "study_sha256": STUDY_SHA,
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": COHORT_SHA,
        "reference": "released",
        "exploratory": False,
        "preregistration_pull_request": PREREGISTRATION_PR,
        "preregistration_commit": PREREGISTRATION_COMMIT,
        "preregistration_merged_at": MERGED_AT,
        "arms": [
            {
                "arm_id": arm,
                "tooling_commit": COMMIT,
                "image_id": IMAGE,
                "started_at_utc": started(index),
                "finished_at_utc": finished(index),
                "python_version": "3.9.19",
                "numpy_version": "1.23.4",
            }
            for index, arm in enumerate(ARMS)
        ],
        "gates": [
            {"gate": name, "outcome": "passed", "counts": {}}
            for name in ("G0", "G1", "G2", "G3", "G4", "G5")
        ],
        "gate_files": [{"file": "gates.json", "attempt": 1, "passed": True}],
        "g0": g0_payload(pilot_gates=pilot_gates_payload(artifacts_only_detail={})),
    }
    payload.update(updates)
    return payload


def test_a_complete_reproduction_record_validates() -> None:
    StudyReproductionV1.model_validate(reproduction_payload())


@pytest.mark.parametrize(
    "updates",
    [
        {"exploratory": True},
        {"reference": "arm-a"},
        {"gates": reproduction_payload()["gates"][1:]},
        {"gates": list(reversed(reproduction_payload()["gates"]))},
        {
            "gates": [
                {"gate": name, "outcome": "not run" if name == "G4" else "passed", "counts": {}}
                | ({"counts": {"differing_values": 1}} if name == "G4" else {})
                for name in ("G0", "G1", "G2", "G3", "G4", "G5")
            ]
        },
        {"g0": g0_payload()},
        {"arms": reproduction_payload()["arms"] * 2},
        {"gate_files": [{"file": "gates.json", "attempt": 1, "passed": True}] * 2},
        {"gate_files": [{"file": "attempt-1.gates.json", "attempt": 1, "passed": True}]},
        {"preregistration_merged_at": "2026-09-26 08:12:34"},
        {"preregistration_commit": PREREGISTRATION_COMMIT.upper()},
        {"preregistration_pull_request": 0},
    ],
    ids=[
        "exploratory-without-arm-a",
        "arm-a-without-exploratory",
        "a-gate-missing",
        "gates-out-of-order",
        "a-gate-not-run-with-counts",
        "g0-with-local-detail",
        "an-arm-twice",
        "a-gate-file-twice",
        "a-gate-file-under-its-artifacts-name",
        "merged-at-not-in-utc-form",
        "commit-in-upper-case",
        "no-pull-request",
    ],
)
def test_a_reproduction_record_that_contradicts_itself_is_refused(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        StudyReproductionV1.model_validate(reproduction_payload(**updates))


@pytest.mark.parametrize("model", [PolicyV2EvidenceV1, StudyReproductionV1])
def test_the_study_evidence_ties_the_exploratory_label_to_the_reference(
    model: type[BaseModel],
) -> None:
    payload: dict[str, Any] = (
        reproduction_payload()
        if model is StudyReproductionV1
        else {
            key: value
            for key, value in summary_payload().items()
            if key not in SUMMARY_ONLY | {"schema_version"}
        }
        | {"schema_version": "aeb-policy-v2-evidence/v1"}
    )

    model.model_validate(payload)
    with pytest.raises(ValidationError, match="exploratory"):
        model.model_validate(payload | {"exploratory": True})


def test_the_addendum_evidence_keeps_the_duration_game_before_the_collision_game() -> None:
    payload = {
        key: value for key, value in addendum_payload().items() if key not in ADDENDUM_SUMMARY_ONLY
    } | {"schema_version": "aeb-attribution-addendum-evidence/v1"}

    AttributionAddendumEvidenceV1.model_validate(payload)
    with pytest.raises(ValidationError, match="duration game"):
        AttributionAddendumEvidenceV1.model_validate(
            payload | {"games": list(reversed(payload["games"]))}
        )


# --------------------------------------------------------------------------
# The study writer: a completed study
# --------------------------------------------------------------------------


def test_the_study_writer_writes_exactly_its_four_files(study: Study) -> None:
    written = study.write()

    assert [path.name for path in written] == [
        SUMMARY_FILE,
        POLICY_V2_EVIDENCE_FILE,
        GATES_FILE,
        REPRODUCTION_FILE,
    ]
    assert [SUMMARY_FILE, POLICY_V2_EVIDENCE_FILE, GATES_FILE, REPRODUCTION_FILE] == [
        "summary.json",
        "policy-v2-evidence.json",
        "gates.json",
        "reproduction.json",
    ]
    assert sorted(study.output_dir.iterdir()) == sorted(written)


def test_the_summary_is_copied_and_the_evidence_derived_from_it_byte_for_byte(
    study: Study, tmp_path: Path
) -> None:
    study.write()

    summary = PolicyV2SummaryV1.model_validate_json(study.summary.read_bytes())
    assert "\u2014".encode() in study.summary.read_bytes()
    assert (study.output_dir / SUMMARY_FILE).read_bytes() == study.summary.read_bytes()
    assert (study.output_dir / SUMMARY_FILE).read_bytes() == document_bytes(summary, tmp_path)

    copied = summary.model_dump(mode="json")
    evidence = study.published(POLICY_V2_EVIDENCE_FILE)
    assert evidence["schema_version"] == "aeb-policy-v2-evidence/v1"
    assert set(evidence) == set(copied) - SUMMARY_ONLY | {"schema_version"}
    for field in set(evidence) - {"schema_version"}:
        assert json.dumps(evidence[field], sort_keys=True) == json.dumps(
            copied[field], sort_keys=True
        )
    assert (study.output_dir / POLICY_V2_EVIDENCE_FILE).read_bytes() == document_bytes(
        PolicyV2EvidenceV1.model_validate(evidence), tmp_path
    )


def test_gates_json_is_the_gate_file_without_its_local_detail(study: Study) -> None:
    study.write()

    published = study.published(GATES_FILE)
    expected = gates_payload()
    del expected["artifacts_only_detail"]
    assert "artifacts_only_detail" not in published
    assert published == expected
    assert StudyGatesV1.model_validate(published).artifacts_only_detail == {}
    assert (study.output_dir / GATES_FILE).read_bytes() == (
        json.dumps(published, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def test_reproduction_records_the_preregistration_the_arms_and_every_gate(study: Study) -> None:
    study.write()

    reproduction = study.published(REPRODUCTION_FILE)
    assert reproduction["schema_version"] == "aeb-study-reproduction/v1"
    assert reproduction["preregistration_pull_request"] == PREREGISTRATION_PR
    assert reproduction["preregistration_commit"] == PREREGISTRATION_COMMIT
    assert reproduction["preregistration_merged_at"] == MERGED_AT
    assert (reproduction["study_sha256"], reproduction["protocol_sha256"]) == (
        STUDY_SHA,
        PROTOCOL_SHA,
    )
    assert reproduction["cohort_manifest_sha256"] == COHORT_SHA
    assert (reproduction["reference"], reproduction["exploratory"]) == ("released", False)
    assert reproduction["arms"] == [
        {
            "arm_id": arm,
            "tooling_commit": COMMIT,
            "image_id": IMAGE,
            "started_at_utc": started(index),
            "finished_at_utc": finished(index),
            "python_version": "3.9.19",
            "numpy_version": "1.23.4",
        }
        for index, arm in enumerate(ARMS)
    ]
    gates = {row["gate"]: row for row in gates_payload()["gates"]}
    assert reproduction["gates"] == [
        {"gate": "G0", "outcome": "passed", "counts": {}},
        *(
            {"gate": name, "outcome": "passed", "counts": gates[name]["counts"]}
            for name in ("G1", "G2", "G3")
        ),
        {"gate": "G4", "outcome": "passed", "counts": summary_payload()["g4"]["counts"]},
        {"gate": "G5", "outcome": "passed", "counts": gates["G5"]["counts"]},
    ]
    assert reproduction["gate_files"] == [{"file": "gates.json", "attempt": 1, "passed": True}]
    StudyReproductionV1.model_validate(reproduction)


def test_reproduction_nests_g0_without_the_pilots_local_detail(study: Study) -> None:
    study.write()

    expected = g0_payload()
    expected["pilot_gates"]["artifacts_only_detail"] = {}
    assert study.published(REPRODUCTION_FILE)["g0"] == expected


def test_a_pilot_gate_that_failed_records_g0_as_failed(study: Study) -> None:
    write_json(
        study.g0,
        g0_payload(pilot_gates=pilot_gates_payload(gates=[gate("G1", False), gate("G5")])),
    )
    study.write()

    assert study.published(REPRODUCTION_FILE)["gates"][0] == {
        "gate": "G0",
        "outcome": "failed",
        "counts": {},
    }


def test_a_pilot_without_a_gate_does_not_pass_g0(study: Study) -> None:
    write_json(study.g0, g0_payload(pilot_gates=pilot_gates_payload(gates=[])))
    study.write()

    assert study.published(REPRODUCTION_FILE)["gates"][0]["outcome"] == "failed"


def test_nothing_of_a_run_log_but_its_first_and_last_time_is_published(study: Study) -> None:
    study.write()

    for path in study.output_dir.iterdir():
        text = path.read_text(encoding="utf-8")
        assert COHORT_TOKEN not in text
        assert "run complete" not in text
        assert "artifacts/" not in text


def test_the_published_files_pass_the_schema_contracts(study: Study, tmp_path: Path) -> None:
    study.write()

    assert dev.verify_schema_contracts(tmp_path) == 0


def test_arm_a_reference_mode_reaches_the_evidence_and_reproduction(study: Study) -> None:
    g2 = {"gate": "G2", "passed": False, "counts": {"no_aeb": 3, "oracle_aeb": 0}}
    gates = study.gate_file(
        "attempt-1.gates.json",
        reference="arm-a",
        exploratory=True,
        gates=[gate("G1"), g2, gate("G3"), gate("G5")],
    )
    rewrite_summary(study, reference="arm-a", exploratory=True)

    study.write(gates=gates)

    reproduction = study.published(REPRODUCTION_FILE)
    assert (reproduction["reference"], reproduction["exploratory"]) == ("arm-a", True)
    assert reproduction["gates"][2] == {"gate": "G2", "outcome": "failed", "counts": g2["counts"]}
    assert reproduction["gate_files"] == [{"file": "gates.json", "attempt": 1, "passed": True}]
    evidence = study.published(POLICY_V2_EVIDENCE_FILE)
    assert (evidence["reference"], evidence["exploratory"]) == ("arm-a", True)
    assert study.published(GATES_FILE)["exploratory"] is True


def test_a_failed_g2_is_a_failed_gate_file_outside_arm_a_reference_mode(study: Study) -> None:
    gates = study.gate_file(
        "attempt-2.gates.json", gates=[gate("G1"), gate("G2", False), gate("G3"), gate("G5")]
    )

    study.write(gates=gates)

    assert study.published(REPRODUCTION_FILE)["gate_files"] == [
        {"file": "gates.json", "attempt": 2, "passed": False}
    ]


def test_a_gate_file_named_without_an_attempt_is_listed_without_one(study: Study) -> None:
    study.write(gates=study.gate_file("gates.json"))

    assert study.published(REPRODUCTION_FILE)["gate_files"] == [
        {"file": "gates.json", "attempt": None, "passed": True}
    ]


def test_with_a_summary_g4_comes_from_the_summary(study: Study) -> None:
    g4 = write_json(
        study.root / "g4.json",
        gates_payload(arms_checked=[], gates=[gate("G4", False, differing_values=4)]),
    )
    study.write(g4=g4)

    assert study.published(REPRODUCTION_FILE)["gates"][4] == {
        "gate": "G4",
        "outcome": "passed",
        "counts": summary_payload()["g4"]["counts"],
    }


def test_writing_the_same_evidence_again_changes_nothing(study: Study) -> None:
    first = {path.name: path.read_bytes() for path in study.write()}
    second = {path.name: path.read_bytes() for path in study.write()}

    assert second == first


# --------------------------------------------------------------------------
# The study writer: earlier attempts and a study reported not completed
# --------------------------------------------------------------------------


def test_an_earlier_attempts_gate_file_is_published_and_listed(study: Study) -> None:
    gates = study.gate_file("attempt-2.gates.json")
    earlier = study.gate_file(
        "attempt-1.gates-A.json",
        arms_checked=["A-v1-replication"],
        gates=[gate("G1"), {"gate": "G2", "passed": False, "counts": {"no_aeb": 1}}, gate("G5")],
    )

    written = study.write(gates=gates, earlier_gates=(earlier,))

    assert [path.name for path in written] == [
        SUMMARY_FILE,
        POLICY_V2_EVIDENCE_FILE,
        GATES_FILE,
        "gates-attempt-1.json",
        REPRODUCTION_FILE,
    ]
    published = study.published("gates-attempt-1.json")
    assert "artifacts_only_detail" not in published
    assert StudyGatesV1.model_validate(published).gates[1].counts == {"no_aeb": 1}
    assert study.published(REPRODUCTION_FILE)["gate_files"] == [
        {"file": "gates.json", "attempt": 2, "passed": True},
        {"file": "gates-attempt-1.json", "attempt": 1, "passed": False},
    ]


def test_earlier_attempts_are_listed_in_attempt_order(study: Study) -> None:
    gates = study.gate_file("attempt-3.gates.json")
    second = study.gate_file("attempt-2.gates.json", gates=[gate("G1", False), gate("G5")])
    first = study.gate_file("attempt-1.gates.json", gates=[gate("G1"), gate("G5", False)])

    study.write(gates=gates, earlier_gates=(second, first))

    assert [row["file"] for row in study.published(REPRODUCTION_FILE)["gate_files"]] == [
        "gates.json",
        "gates-attempt-1.json",
        "gates-attempt-2.json",
    ]


@pytest.mark.parametrize(
    "name",
    ["gates.json", "attempt-0.gates.json", "attempt-1.gates.json.bak", "attempt-01.gates.json"],
)
def test_an_earlier_gate_file_named_otherwise_is_refused(study: Study, name: str) -> None:
    earlier = study.gate_file(name)

    with pytest.raises(ValueError, match="attempt-<n>"):
        study.write(gates=study.gate_file("attempt-2.gates.json"), earlier_gates=(earlier,))
    assert nothing_written(study.output_dir)


@pytest.mark.parametrize(
    ("main", "earlier"),
    [
        ("attempt-2.gates.json", ("attempt-1.gates.json", "attempt-1.gates-A.json")),
        ("attempt-1.gates.json", ("attempt-1.gates-A.json",)),
    ],
    ids=["two-earlier-files-of-one-attempt", "an-earlier-file-of-the-same-attempt"],
)
def test_two_gate_files_of_one_attempt_are_refused(
    study: Study, main: str, earlier: tuple[str, ...]
) -> None:
    gates = study.gate_file(main)
    paths = tuple(study.gate_file(name) for name in earlier)

    with pytest.raises(ValueError, match="attempt 1"):
        study.write(gates=gates, earlier_gates=paths)
    assert nothing_written(study.output_dir)


def test_a_study_reported_not_completed_publishes_the_gate_report_and_reproduction_only(
    study: Study,
) -> None:
    for arm in ARMS[1:]:
        remove_arm(study.arms_root / arm)
    other_protocol, other_cohort = "a" * 64, "b" * 64
    gates = study.gate_file(
        "attempt-2.gates-A.json",
        protocol_sha256=other_protocol,
        cohort_manifest_sha256=other_cohort,
        arms_checked=["A-v1-replication"],
        gates=[gate("G1"), {"gate": "G2", "passed": False, "counts": {"no_aeb": 7}}, gate("G5")],
    )
    earlier = study.gate_file(
        "attempt-1.gates.json",
        protocol_sha256=other_protocol,
        cohort_manifest_sha256=other_cohort,
        gates=[gate("G1", False, documents_missing=2), gate("G5")],
    )

    written = study.write(gates=gates, not_completed=True, earlier_gates=(earlier,))

    assert [path.name for path in written] == [
        GATES_FILE,
        "gates-attempt-1.json",
        REPRODUCTION_FILE,
    ]
    assert sorted(study.output_dir.iterdir()) == sorted(written)
    reproduction = study.published(REPRODUCTION_FILE)
    assert (reproduction["protocol_sha256"], reproduction["cohort_manifest_sha256"]) == (
        other_protocol,
        other_cohort,
    )
    assert [row["outcome"] for row in reproduction["gates"]] == [
        "passed",
        "passed",
        "failed",
        "not run",
        "not run",
        "passed",
    ]
    assert [arm["arm_id"] for arm in reproduction["arms"]] == ["A-v1-replication"]
    assert reproduction["gate_files"] == [
        {"file": "gates.json", "attempt": 2, "passed": False},
        {"file": "gates-attempt-1.json", "attempt": 1, "passed": False},
    ]
    StudyReproductionV1.model_validate(reproduction)


def test_a_study_not_completed_covers_only_the_arms_present(study: Study) -> None:
    for arm in ("C-v1-kalman", "D-v2-kalman", "E-v2-channel-rng"):
        for path in sorted((study.arms_root / arm).iterdir()):
            path.unlink()
        (study.arms_root / arm).rmdir()

    study.write(not_completed=True)

    assert [arm["arm_id"] for arm in study.published(REPRODUCTION_FILE)["arms"]] == [
        "A-v1-replication",
        "B-v2-gated",
    ]


def arm_a_gate_file(study: Study) -> Path:
    """The gate file of an attempt whose only verified arm is arm A."""

    return study.gate_file(
        "attempt-1.gates-A.json",
        arms_checked=["A-v1-replication"],
        gates=[gate("G1"), gate("G2"), gate("G5")],
    )


def test_a_study_not_completed_records_every_arm_under_its_arms_root(study: Study) -> None:
    """An attempt that ended in arm C, with only arm A verified, still records when B and C ran."""

    for arm in ("D-v2-kalman", "E-v2-channel-rng"):
        remove_arm(study.arms_root / arm)

    study.write(gates=arm_a_gate_file(study), not_completed=True)

    assert [arm["arm_id"] for arm in study.published(REPRODUCTION_FILE)["arms"]] == [
        "A-v1-replication",
        "B-v2-gated",
        "C-v1-kalman",
    ]


def test_a_g0_whose_tooling_commit_differs_from_an_unverified_arms_commit_is_refused(
    study: Study,
) -> None:
    write_arm(study.arms_root, "C-v1-kalman", commit=OTHER_COMMIT)

    with pytest.raises(ValueError, match="C-v1-kalman"):
        study.write(gates=arm_a_gate_file(study), not_completed=True)
    assert nothing_written(study.output_dir)


def test_without_a_summary_a_failed_g4_is_recorded_as_failed(study: Study) -> None:
    g4 = write_json(
        study.root / "g4.json",
        gates_payload(
            arms_checked=[],
            gates=[gate("G4", False, differing_values=2)],
            artifacts_only_detail={"G4": ["intervals.json /intervals/oracle_aeb differs"]},
        ),
    )

    study.write(not_completed=True, g4=g4)

    assert study.published(REPRODUCTION_FILE)["gates"][4] == {
        "gate": "G4",
        "outcome": "failed",
        "counts": {"differing_values": 2},
    }


def test_without_a_summary_or_a_g4_file_g4_is_recorded_as_not_run(study: Study) -> None:
    study.write(not_completed=True)

    assert study.published(REPRODUCTION_FILE)["gates"][4] == {
        "gate": "G4",
        "outcome": "not run",
        "counts": {},
    }


def test_a_g4_file_without_g4_is_refused(study: Study) -> None:
    g4 = write_json(study.root / "g4.json", gates_payload(arms_checked=[], gates=[gate("G5")]))

    with pytest.raises(ValueError, match="G4"):
        study.write(not_completed=True, g4=g4)
    assert nothing_written(study.output_dir)


def test_a_summary_with_not_completed_is_refused(study: Study) -> None:
    with pytest.raises(ValueError, match="not completed"):
        study.write(not_completed=True, summary_path=study.summary)
    assert nothing_written(study.output_dir)


def test_no_summary_without_not_completed_is_refused(study: Study) -> None:
    with pytest.raises(ValueError, match="summary"):
        study.write(summary_path=None)
    assert nothing_written(study.output_dir)


# --------------------------------------------------------------------------
# The study writer: refusals
# --------------------------------------------------------------------------


def test_g0_without_g0_json_is_refused(study: Study) -> None:
    study.g0.unlink()

    with pytest.raises(ValueError, match=r"g0\.json"):
        study.write()
    assert nothing_written(study.output_dir)


def test_a_g0_json_that_fails_its_model_is_refused(study: Study) -> None:
    write_json(study.g0, g0_payload(pilot_run_contexts={arm: run_context(arm) for arm in ARMS[:4]}))

    with pytest.raises(ValidationError):
        study.write()
    assert nothing_written(study.output_dir)


def test_g5_without_a_g5_result_in_the_gate_file_is_refused(study: Study) -> None:
    gates = study.gate_file("attempt-1.gates.json", gates=[gate("G1"), gate("G2"), gate("G3")])

    with pytest.raises(ValueError, match="G5"):
        study.write(gates=gates)
    assert nothing_written(study.output_dir)


def test_a_g0_whose_tooling_commit_differs_from_an_arms_commit_is_refused(study: Study) -> None:
    write_arm(study.arms_root, "D-v2-kalman", commit=OTHER_COMMIT)

    with pytest.raises(ValueError, match="D-v2-kalman"):
        study.write()
    assert nothing_written(study.output_dir)


def remove_arm_file(name: str) -> Callable[[Path], None]:
    def damage(arm_root: Path) -> None:
        (arm_root / name).unlink()

    return damage


def write_arm_file(name: str, text: str) -> Callable[[Path], None]:
    def damage(arm_root: Path) -> None:
        (arm_root / name).write_text(text, encoding="utf-8")

    return damage


def remove_arm(arm_root: Path) -> None:
    for path in sorted(arm_root.iterdir()):
        path.unlink()
    arm_root.rmdir()


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (remove_arm, "every arm"),
        (remove_arm_file("run_context.json"), "run_context.json"),
        (remove_arm_file("run.log"), "run.log"),
        (write_arm_file("run.log", ""), "empty"),
        (write_arm_file("run.log", "started without a time\n"), "UTC time"),
        (
            write_arm_file("run.log", f"{started(2)} started\n2026-10-03 06:30:00 done\n"),
            "UTC time",
        ),
        (
            write_arm_file("run_context.json", json.dumps(run_context("B-v2-gated"))),
            "B-v2-gated",
        ),
    ],
    ids=[
        "arm-missing",
        "no-run-context",
        "no-run-log",
        "empty-run-log",
        "first-line-without-a-time",
        "last-line-without-a-time",
        "context-of-another-arm",
    ],
)
def test_an_arm_that_cannot_be_recorded_is_refused(
    study: Study, damage: Callable[[Path], None], message: str
) -> None:
    damage(study.arms_root / "C-v1-kalman")

    with pytest.raises(ValueError, match=message):
        study.write()
    assert nothing_written(study.output_dir)


@pytest.mark.parametrize(
    "updates",
    [
        {"study_sha256": "6" * 64},
        {"protocol_sha256": "a" * 64},
        {"cohort_manifest_sha256": "b" * 64},
        {"reference": "arm-a", "exploratory": True},
    ],
    ids=["study", "protocol", "cohort", "reference"],
)
def test_a_summary_of_another_study_than_the_gate_file_is_refused(
    study: Study, updates: dict[str, Any]
) -> None:
    rewrite_summary(study, **updates)

    with pytest.raises(ValueError, match="gate file"):
        study.write()
    assert nothing_written(study.output_dir)


@pytest.mark.parametrize(
    "updates",
    [
        {"study_sha256": "6" * 64},
        {"protocol_sha256": "a" * 64},
        {"cohort_manifest_sha256": "b" * 64},
        {"reference": "arm-a", "exploratory": True},
    ],
    ids=["study", "protocol", "cohort", "reference"],
)
def test_a_g4_file_of_another_study_than_the_gate_file_is_refused(
    study: Study, updates: dict[str, Any]
) -> None:
    g4 = write_json(
        study.root / "g4.json", gates_payload(arms_checked=[], gates=[gate("G4")], **updates)
    )

    with pytest.raises(ValueError, match="gate file"):
        study.write(not_completed=True, g4=g4)
    assert nothing_written(study.output_dir)


@pytest.mark.parametrize(
    "updates",
    [{"protocol_sha256": "a" * 64}, {"cohort_manifest_sha256": "b" * 64}],
    ids=["protocol", "cohort"],
)
def test_an_earlier_gate_file_of_another_protocol_or_cohort_is_refused(
    study: Study, updates: dict[str, Any]
) -> None:
    earlier = study.gate_file("attempt-1.gates.json", **updates)

    with pytest.raises(ValueError, match="gate file"):
        study.write(gates=study.gate_file("attempt-2.gates.json"), earlier_gates=(earlier,))
    assert nothing_written(study.output_dir)


def test_an_earlier_gate_file_of_another_study_file_or_reference_is_published(
    study: Study,
) -> None:
    """A tooling fix between attempts may change nothing the evidence compares but the files."""

    earlier = study.gate_file(
        "attempt-1.gates.json", study_sha256="6" * 64, reference="arm-a", exploratory=True
    )

    study.write(gates=study.gate_file("attempt-2.gates.json"), earlier_gates=(earlier,))

    assert study.published("gates-attempt-1.json")["study_sha256"] == "6" * 64


@pytest.mark.parametrize(
    ("where", "change"),
    [
        ("summary", {"statements": {"h3": f"token {COHORT_TOKEN}", "h4": "a", "h5": "b"}}),
        ("summary", {"levels": [level("B-v2-gated", TOKEN_SHAPE)]}),
        (
            "summary",
            {"g4": {"gate": "G4", "passed": True, "counts": {f"no_aeb:{COHORT_TOKEN}": 0}}},
        ),
        (
            "gates",
            {"gates": [{"gate": "G1", "passed": True, "counts": {TOKEN_SHAPE: 1}}, gate("G5")]},
        ),
        ("g0", {"velocity_parameters": COHORT_TOKEN}),
        (
            "earlier",
            {
                "gates": [
                    {"gate": "G1", "passed": True, "counts": {f"{COHORT_TOKEN} missing": 1}},
                    gate("G5"),
                ]
            },
        ),
    ],
    ids=[
        "a-summary-sentence-with-a-cohort-token",
        "a-summary-cell-shaped-like-a-token",
        "a-summary-count-naming-a-cohort-token",
        "a-gate-count-shaped-like-a-token",
        "a-pilot-run-context-with-a-cohort-token",
        "an-earlier-gate-count-naming-a-cohort-token",
    ],
)
def test_a_token_string_in_any_published_study_document_is_refused(
    study: Study, where: str, change: dict[str, Any]
) -> None:
    earlier: tuple[Path, ...] = ()
    gates = study.gates
    if where == "summary":
        rewrite_summary(study, **change)
    elif where == "gates":
        gates = study.gate_file("attempt-1.gates.json", **change)
    elif where == "g0":
        contexts = {arm: run_context(arm, **change) for arm in ARMS}
        write_json(study.g0, g0_payload(pilot_run_contexts=contexts))
    else:
        gates = study.gate_file("attempt-2.gates.json")
        earlier = (study.gate_file("attempt-1.gates.json", **change),)

    with pytest.raises(ValueError, match=f"{COHORT_TOKEN}|{TOKEN_SHAPE}"):
        study.write(gates=gates, earlier_gates=earlier)
    assert nothing_written(study.output_dir)


def test_overwriting_a_different_file_is_refused_and_nothing_is_written(study: Study) -> None:
    study.output_dir.mkdir(parents=True)
    existing = study.output_dir / REPRODUCTION_FILE
    existing.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"reproduction\.json"):
        study.write()
    assert sorted(path.name for path in study.output_dir.iterdir()) == [REPRODUCTION_FILE]
    assert existing.read_text(encoding="utf-8") == "{}\n"


def scratch_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory holding the three evidence directories and nothing else.

    A writer that failed to refuse one of them would write here, and not into
    the repository's released evidence; with no committed cohort file here, it
    would then fail before writing anything.
    """

    root = tmp_path / "repository"
    for directory in (
        "docs/evidence/nuplan_aeb_v2",
        "docs/studies/aeb-policy-v2",
        "docs/posthoc/nuplan_aeb_v2-addendum",
    ):
        (root / directory).mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


STUDY_REFUSED_DIRECTORIES = (
    "docs/evidence/nuplan_aeb_v2",
    "docs/evidence/nuplan_aeb_v2/policy-v2",
    "docs/posthoc/nuplan_aeb_v2-addendum/evidence",
    "docs/posthoc/nuplan_aeb_v2-addendum",
)


@pytest.mark.parametrize("relative", STUDY_REFUSED_DIRECTORIES)
@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
def test_the_study_writer_refuses_the_released_evidence_and_the_addendums_directory(
    study: Study, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, absolute: bool
) -> None:
    root = scratch_repository(tmp_path, monkeypatch)
    output_dir = root / relative if absolute else Path(relative)
    before = sorted(path.name for path in (root / relative).parent.iterdir())

    with pytest.raises(ValueError, match="never written under"):
        study.write(output_dir=output_dir)
    assert sorted(path.name for path in (root / relative).parent.iterdir()) == before


# --------------------------------------------------------------------------
# The addendum writer
# --------------------------------------------------------------------------


@pytest.fixture
def addendum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(ROOT)
    path = tmp_path / "artifacts" / "addendum.json"
    path.parent.mkdir(parents=True)
    write_document(AttributionAddendumV1.model_validate(addendum_payload()), path)
    return path


def addendum_output(tmp_path: Path) -> Path:
    return tmp_path / "docs" / "posthoc" / "nuplan_aeb_v2-addendum" / "evidence"


def test_the_addendum_writer_writes_exactly_its_two_files(addendum: Path, tmp_path: Path) -> None:
    written = write_addendum_evidence(addendum, addendum_output(tmp_path))

    assert [path.name for path in written] == [ADDENDUM_SUMMARY_FILE, ADDENDUM_EVIDENCE_FILE]
    assert [ADDENDUM_SUMMARY_FILE, ADDENDUM_EVIDENCE_FILE] == [
        "addendum-summary.json",
        "attribution-addendum-evidence.json",
    ]
    assert sorted(addendum_output(tmp_path).iterdir()) == sorted(written)
    assert dev.verify_schema_contracts(tmp_path) == 0


def test_the_addendum_summary_is_copied_and_its_evidence_derived_byte_for_byte(
    addendum: Path, tmp_path: Path
) -> None:
    output_dir = addendum_output(tmp_path)
    write_addendum_evidence(addendum, output_dir)

    assert (output_dir / ADDENDUM_SUMMARY_FILE).read_bytes() == addendum.read_bytes()
    copied = json.loads(addendum.read_text(encoding="utf-8"))
    evidence = json.loads((output_dir / ADDENDUM_EVIDENCE_FILE).read_text(encoding="utf-8"))
    assert evidence["schema_version"] == "aeb-attribution-addendum-evidence/v1"
    assert set(evidence) == set(copied) - ADDENDUM_SUMMARY_ONLY | {"schema_version"}
    for field in set(evidence) - {"schema_version"}:
        assert json.dumps(evidence[field], sort_keys=True) == json.dumps(
            copied[field], sort_keys=True
        )
    assert (output_dir / ADDENDUM_EVIDENCE_FILE).read_bytes() == document_bytes(
        AttributionAddendumEvidenceV1.model_validate(evidence), tmp_path
    )


def test_the_addendum_evidence_holds_every_shapley_value_and_the_summary_the_gate(
    addendum: Path, tmp_path: Path
) -> None:
    output_dir = addendum_output(tmp_path)
    write_addendum_evidence(addendum, output_dir)

    evidence = json.loads((output_dir / ADDENDUM_EVIDENCE_FILE).read_text(encoding="utf-8"))
    summary = json.loads((output_dir / ADDENDUM_SUMMARY_FILE).read_text(encoding="utf-8"))
    for index, name in enumerate(("intervention_duration_s", "collision_indicator")):
        assert evidence["games"][index]["game"] == name
        assert set(evidence["games"][index]["shapley_values"]) == set(CHANNELS)
    assert summary["reproduction_gate"]["passed"] is True
    assert summary["released_output_hashes_sha256"] == RELEASED_HASHES_SHA


@pytest.mark.parametrize(
    "change",
    [
        {"shapley_values": {TOKEN_SHAPE: estimate(0.5)}},
        {"other_differences": [difference("dropout", f"near {COHORT_TOKEN}", 0.5)]},
    ],
    ids=["a-key-shaped-like-a-token", "a-name-holding-a-cohort-token"],
)
def test_a_token_string_in_the_addendum_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    monkeypatch.chdir(ROOT)
    games = [game("intervention_duration_s") | change, game("collision_indicator")]
    path = tmp_path / "addendum.json"
    write_document(AttributionAddendumV1.model_validate(addendum_payload(games=games)), path)

    with pytest.raises(ValueError, match=f"{COHORT_TOKEN}|{TOKEN_SHAPE}"):
        write_addendum_evidence(path, addendum_output(tmp_path))
    assert nothing_written(addendum_output(tmp_path))


def test_the_addendum_writer_refuses_to_overwrite_a_different_file(
    addendum: Path, tmp_path: Path
) -> None:
    output_dir = addendum_output(tmp_path)
    output_dir.mkdir(parents=True)
    (output_dir / ADDENDUM_EVIDENCE_FILE).write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=ADDENDUM_EVIDENCE_FILE):
        write_addendum_evidence(addendum, output_dir)
    assert sorted(path.name for path in output_dir.iterdir()) == [ADDENDUM_EVIDENCE_FILE]

    (output_dir / ADDENDUM_EVIDENCE_FILE).unlink()
    first = {path.name: path.read_bytes() for path in write_addendum_evidence(addendum, output_dir)}
    again = {path.name: path.read_bytes() for path in write_addendum_evidence(addendum, output_dir)}
    assert again == first


ADDENDUM_REFUSED_DIRECTORIES = (
    "docs/evidence/nuplan_aeb_v2",
    "docs/evidence/nuplan_aeb_v2/addendum",
    "docs/studies/aeb-policy-v2/evidence",
    "docs/studies/aeb-policy-v2",
)


@pytest.mark.parametrize("relative", ADDENDUM_REFUSED_DIRECTORIES)
@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
def test_the_addendum_writer_refuses_the_released_evidence_and_the_studys_directory(
    addendum: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, absolute: bool
) -> None:
    root = scratch_repository(tmp_path, monkeypatch)
    output_dir = root / relative if absolute else Path(relative)
    before = sorted(path.name for path in (root / relative).parent.iterdir())

    with pytest.raises(ValueError, match="never written under"):
        write_addendum_evidence(addendum, output_dir)
    assert sorted(path.name for path in (root / relative).parent.iterdir()) == before


# --------------------------------------------------------------------------
# is_ancestor
# --------------------------------------------------------------------------


class Repository:
    """A throwaway repository driven by the system git with no inherited git settings."""

    def __init__(self, root: Path) -> None:
        self.path = root / "repo"
        self.path.mkdir()
        config = root / "gitconfig"
        config.write_text("", encoding="utf-8")
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update({"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"})
        self.git("init", "--quiet")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return result.stdout.strip()

    def commit(self, message: str) -> str:
        self.git(
            "-c",
            "user.name=kuotunyu",
            "-c",
            "user.email=61350295+kuotunyu@users.noreply.github.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            message,
        )
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path: Path) -> Mapping[str, Any]:
    """Two commits on the main line and one on a branch from the first."""

    repository = Repository(tmp_path)
    first = repository.commit("First")
    second = repository.commit("Second")
    repository.git("checkout", "--quiet", "-b", "side", first)
    side = repository.commit("Side")
    return {"path": repository.path, "first": first, "second": second, "side": side}


def test_a_commit_is_an_ancestor_of_its_descendants_and_of_itself(
    history: Mapping[str, Any],
) -> None:
    path = history["path"]

    assert is_ancestor(path, history["first"], history["second"]) is True
    assert is_ancestor(path, history["first"], history["side"]) is True
    assert is_ancestor(path, history["second"], history["second"]) is True


def test_a_later_or_parallel_commit_is_not_an_ancestor(history: Mapping[str, Any]) -> None:
    path = history["path"]

    assert is_ancestor(path, history["second"], history["first"]) is False
    assert is_ancestor(path, history["side"], history["second"]) is False
    assert is_ancestor(path, history["second"], history["side"]) is False


def test_an_unknown_commit_is_refused_rather_than_called_no_ancestor(
    history: Mapping[str, Any],
) -> None:
    with pytest.raises(ValueError, match="could not compare"):
        is_ancestor(history["path"], "f" * 40, history["second"])
