"""The claims registries of the policy v2 study and of the post-hoc addendum.

Each part publishes a registry of its own beside its results page, separate
from `docs/claims.yaml`, and builds it from its published evidence as the
released registry is built from the released evidence. These tests write that
evidence by hand, in the shape `study evidence` writes it, into a scratch
repository that holds a copy of the released `evaluation.json`. A registry
built from it must pass the released claims audit, name every number under its
part's prefix and the row the number belongs to, and let a results page bind
every number it states, including a study reported not completed, whose
registry holds only its gate diagnostics.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Callable, Optional

import pytest
import yaml
from typer.testing import CliRunner

from aebrisk.analysis.attribution_audit import validate_attribution
from aebrisk.analysis.claims import ClaimsRegistryV1, ClaimV1, audit_claims, load_registry
from aebrisk.artifacts.study_documents import (
    COLLISION_GAME_SENTENCE,
    AttributionAddendumEvidenceV1,
    PolicyV2EvidenceV1,
    StudyGatesV1,
)
from aebrisk.attribution.shapley import CHANNELS
from aebrisk.cli.app import app
from aebrisk.study.claims import build_addendum_claims, build_study_claims

ROOT = Path(__file__).resolve().parents[3]
RELEASED_EVALUATION = Path("docs/evidence/nuplan_aeb_v2/evaluation.json")
STUDY_EVIDENCE = Path("docs/studies/aeb-policy-v2/evidence")
ADDENDUM_EVIDENCE = Path("docs/posthoc/nuplan_aeb_v2-addendum/evidence")
POLICY_EVIDENCE = (STUDY_EVIDENCE / "policy-v2-evidence.json").as_posix()
GATES = (STUDY_EVIDENCE / "gates.json").as_posix()
ADDENDUM_NUMBERS = (ADDENDUM_EVIDENCE / "attribution-addendum-evidence.json").as_posix()

PROTOCOL_SHA = "bbf0b6d31943a0f99160afe1d4c18f9a181850367494004037bab98d8f2e59f9"
COHORT_SHA = "65e38df24b91786fb773b883e1cad4348c0cdc58ac976c729871c77e9478daf9"
STUDY_SHA = "5" * 64
OTHER_SHA = "7" * 64

STUDY_PREFIX = "p3.study.policy-v2."
ADDENDUM_PREFIX = "p3.posthoc.v1-addendum."

ARM_A = "A-v1-replication"
ARM_B = "B-v2-gated"
ARMS = (ARM_A, ARM_B, "C-v1-kalman", "D-v2-kalman", "E-v2-channel-rng")
ORACLE = "oracle_aeb"
LOCALIZATION = "localization_shape-medium"
FULL_COALITION = "coalition-dropout+localization_shape+latency+track_instability"
FULL_COALITION_ID = "coalition-dropout-localization_shape-latency-track_instability"
STUDY_CELLS = (
    "no_aeb",
    ORACLE,
    "dropout-medium",
    LOCALIZATION,
    "latency-medium",
    "track_instability-medium",
    "coalition-none",
    FULL_COALITION,
)
#: The id of the primary oracle contrast, B minus A in `oracle_aeb`, on the braking share.
H1 = "braking_share.plus-b-v2-gated-oracle_aeb.minus-a-v1-replication-oracle_aeb"
H1_WORDS = "braking_share of B-v2-gated in oracle_aeb minus A-v1-replication in oracle_aeb"


# --------------------------------------------------------------------------
# The evidence, written by hand
# --------------------------------------------------------------------------


def write_json(path: Path, value: Any) -> Path:
    """Write a document in the stable form the evidence writers use."""

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def term(sign: int, arm: str, cell: str = ORACLE) -> dict[str, Any]:
    return {"sign": sign, "arm": arm, "cell": cell}


def interval(confidence: float, low: float, high: float) -> dict[str, float]:
    return {"confidence": confidence, "low": low, "high": high}


def contrast(
    outcome: str = "braking_share", terms: Optional[list[dict[str, Any]]] = None, **updates: Any
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "outcome": outcome,
        "terms": terms or [term(1, ARM_B), term(-1, ARM_A)],
        "estimate": -0.12345678901234566,
        "cluster": "family-log",
        "interval": interval(0.95, -0.2, -0.05),
    }
    value.update(updates)
    return value


def proportion(arm: str, cell: str, family: Optional[str] = None) -> dict[str, Any]:
    return {
        "event": "collision",
        "arm": arm,
        "cell": cell,
        "family": family,
        "successes": 13,
        "trials": 344,
        "confidence": 0.95,
        "low": 0.020269,
        "high": 0.0636,
    }


def distribution() -> dict[str, Any]:
    return {
        "count": 2,
        "median": 1.5,
        "lower_quartile": 1.25,
        "upper_quartile": 1.75,
        "maximum": 2.0,
    }


def level(arm: str, cell: str, family: Optional[str] = None) -> dict[str, Any]:
    return {
        "arm": arm,
        "cell": cell,
        "family": family,
        "tokens": 344,
        "records": 1032,
        "collisions": 39,
        "collision_tokens": 13,
        "collision_indicator": 0.0377906976744186,
        "collision_clopper_pearson": proportion(arm, cell, family),
        "contacts_not_at_fault": 1095,
        "any_contact": 0.5,
        "simulated_seconds": 10000.25,
        "braking_seconds": 2500.5,
        "braking_share": 0.25004999375,
        "braking_seconds_per_run": 2.4229651162790695,
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


def policy_evidence(reference: str = "released", **updates: Any) -> dict[str, Any]:
    """The numbers of a summary, one row of each kind, as `study evidence` publishes them."""

    primary = contrast(
        simultaneous_interval=interval(0.99, -0.25, -0.01),
        holm_step_interval=interval(0.99, -0.25, -0.01),
        test="bootstrap",
        p_value=0.0004,
    )
    payload: dict[str, Any] = {
        "schema_version": "aeb-policy-v2-evidence/v1",
        "study_sha256": STUDY_SHA,
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": COHORT_SHA,
        "common_valid_tokens": 344,
        "reference": reference,
        "exploratory": reference == "arm-a",
        "hypotheses": [
            {
                "id": "H1",
                "family": "primary",
                "predicted_sign": -1,
                "contrast": primary,
                "holm_step": 1,
                "rejected": True,
                "classification": "supported",
            },
            {
                "id": "Q2.1",
                "family": "Q2",
                "predicted_sign": -1,
                "contrast": contrast(
                    terms=[term(1, "C-v1-kalman", LOCALIZATION), term(-1, ARM_A, LOCALIZATION)]
                ),
                "holm_step": 3,
                "rejected": False,
                "classification": "not_established",
            },
        ],
        "sensitivity": [
            {
                "hypothesis": "H1",
                "analysis": "family_drive_clusters",
                "contrast": contrast(cluster="family-drive"),
            }
        ],
        "q3_ratio": {
            "outcome": "braking_share",
            "b_terms": [term(1, ARM_B, FULL_COALITION), term(-1, ARM_B, LOCALIZATION)],
            "e_terms": [
                term(1, "E-v2-channel-rng", FULL_COALITION),
                term(-1, ARM_B, LOCALIZATION),
            ],
            "sd_b": 0.021,
            "sd_e": 0.0105,
            "ratio": 0.5,
        },
        "secondary": [
            {
                "analysis": "difference_in_differences",
                "hypothesis": None,
                "contrast": contrast(
                    "collision_indicator",
                    [
                        term(1, ARM_B, FULL_COALITION),
                        term(-1, ARM_B, "coalition-none"),
                        term(-1, ARM_A, FULL_COALITION),
                        term(1, ARM_A, "coalition-none"),
                    ],
                    test="sign_flip",
                    test_unit="log",
                    p_value=0.25,
                    clopper_pearson=[
                        proportion(ARM_B, FULL_COALITION),
                        proportion(ARM_A, FULL_COALITION),
                    ],
                ),
            }
        ],
        "descriptive_contrasts": [
            {
                "analysis": "primary_by_family",
                "hypothesis": "H1",
                "contrast": contrast(family="lead_or_stopping"),
            }
        ],
        "levels": [
            level(ARM_B, ORACLE),
            level(ARM_A, FULL_COALITION, "cut_in_or_crossing"),
        ],
    }
    payload.update(updates)
    evidence = PolicyV2EvidenceV1.model_validate(payload)
    return evidence.model_dump(mode="json")


def gate(name: str, passed: bool = True, **counts: int) -> dict[str, Any]:
    return {"gate": name, "passed": passed, "counts": counts}


def gates(
    reference: str = "released", g2: Optional[dict[str, int]] = None, **updates: Any
) -> dict[str, Any]:
    """A published gate file of all five arms; G2 fails when it counts differing documents."""

    g2_counts = dict.fromkeys(STUDY_CELLS, 0) if g2 is None else g2
    payload: dict[str, Any] = {
        "schema_version": "aeb-study-gates/v1",
        "study_sha256": STUDY_SHA,
        "arms_checked": list(ARMS),
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": COHORT_SHA,
        "reference": reference,
        "exploratory": reference == "arm-a",
        "gates": [
            gate("G1", documents=8256, documents_missing=0, distinct_commits=1),
            {"gate": "G2", "passed": not any(g2_counts.values()), "counts": g2_counts},
            {
                "gate": "G3",
                "passed": True,
                "counts": {f"no_aeb:{ARM_B}:released": 0, f"oracle_aeb:C-v1-kalman:{ARM_A}": 0},
            },
            gate("G5", python_mismatches=0, numpy_mismatches=0),
        ],
    }
    payload.update(updates)
    document = StudyGatesV1.model_validate(payload)
    return document.model_dump(mode="json", exclude={"artifacts_only_detail"})


#: G2 of a failed attempt: three documents of one cell differ from the released records.
FAILED_G2 = {**dict.fromkeys(STUDY_CELLS, 0), "dropout-medium": 3}


def failed_attempt(**updates: Any) -> dict[str, Any]:
    """An earlier attempt's gate file: G1 found two documents missing and G2 failed."""

    payload = gates(g2=FAILED_G2, **updates)
    payload["gates"][0] = gate("G1", False, documents=8254, documents_missing=2)
    return payload


def write_study(
    root: Path,
    *,
    completed: bool = True,
    reference: str = "released",
    policy: Optional[dict[str, Any]] = None,
    gate_file: Optional[dict[str, Any]] = None,
    attempts: Sequence[tuple[str, dict[str, Any]]] = (),
) -> Path:
    """The study's evidence directory, with a summary unless the study was not completed.

    The registry reads only whether a summary is there, so the summary written
    here is its schema version alone.
    """

    directory = root / STUDY_EVIDENCE
    write_json(directory / "gates.json", gate_file or gates(reference))
    if completed:
        write_json(directory / "summary.json", {"schema_version": "aeb-policy-v2-summary/v1"})
        write_json(directory / "policy-v2-evidence.json", policy or policy_evidence(reference))
    for name, payload in attempts:
        write_json(directory / name, payload)
    return directory


def estimate(value: float, *levels: float) -> dict[str, Any]:
    return {
        "estimate": value,
        "intervals": [
            interval(confidence, value - 0.125, value + 0.125) for confidence in levels or (0.95,)
        ],
        "computed_before_plan": True,
    }


def difference(plus: str, minus: str, value: float, *levels: float) -> dict[str, Any]:
    return {**estimate(value, *levels), "plus": plus, "minus": minus}


def game(name: str) -> dict[str, Any]:
    return {
        "game": name,
        "shapley_values": {
            channel: estimate(0.01 * (index + 1)) for index, channel in enumerate(CHANNELS)
        },
        "localization_shape_differences": [
            difference("localization_shape", channel, 0.03125, 1 - 0.05 / 6, 0.95)
            for channel in CHANNELS
            if channel != "localization_shape"
        ],
        "other_differences": [difference("dropout", "latency", 0.001)],
        "caution": COLLISION_GAME_SENTENCE if name == "collision_indicator" else None,
    }


def addendum_proportion(events: int, tokens: int = 344) -> dict[str, Any]:
    return {"events": events, "tokens": tokens, "confidence": 0.95, "low": 0.0, "high": 0.0107}


def quartiles(count: int) -> dict[str, Any]:
    present = count > 0
    return {
        "count": count,
        "median": 1.0 if present else None,
        "lower_quartile": 0.5 if present else None,
        "upper_quartile": 1.5 if present else None,
    }


def addendum_evidence(**updates: Any) -> dict[str, Any]:
    """The numbers of an addendum summary, one row of each kind, as `study evidence` publishes them."""

    payload: dict[str, Any] = {
        "schema_version": "aeb-attribution-addendum-evidence/v1",
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": COHORT_SHA,
        "cohort_size": 344,
        "common_valid_tokens": 344,
        "bootstrap": {
            "cluster": "family-log",
            "clusters": 198,
            "resamples": 5000,
            "seed": 20260831,
        },
        "games": [game("intervention_duration_s"), game("collision_indicator")],
        "configuration_contrasts": [
            {**difference(ORACLE, "coalition-none", 0.0125), "metric": "collision_indicator"}
        ],
        "oracle_collisions": {
            "avoided": addendum_proportion(20),
            "induced": addendum_proportion(2),
            "oracle_aeb_contacts_not_at_fault": 1095,
            "computed_before_plan": True,
        },
        "brake_activations": [
            {
                "configuration_id": ORACLE,
                "brake_activations": 400,
                "simulated_seconds": 12000.5,
                "per_hour": 119.99500020832465,
                "computed_before_plan": True,
            }
        ],
        "zero_event_configurations": [
            {
                "configuration_id": FULL_COALITION,
                "overall": addendum_proportion(0),
                "by_family": {"lead_or_stopping": addendum_proportion(0, 100)},
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
                "configuration_id": ORACLE,
                "first_stop_distance_m": quartiles(2),
                "ego_speed_at_collision_mps": quartiles(0),
                "computed_before_plan": False,
            }
        ],
    }
    payload.update(updates)
    return AttributionAddendumEvidenceV1.model_validate(payload).model_dump(mode="json")


def write_addendum(root: Path, evidence: Optional[dict[str, Any]] = None) -> Path:
    directory = root / ADDENDUM_EVIDENCE
    write_json(directory / "attribution-addendum-evidence.json", evidence or addendum_evidence())
    return directory


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A scratch repository that holds a copy of the released `evaluation.json`."""

    released = tmp_path / RELEASED_EVALUATION
    released.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / RELEASED_EVALUATION, released)
    return tmp_path


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def numeric_pointers(value: object, pointer: str = "") -> Iterator[str]:
    """The JSON pointer of every number in a parsed document; a boolean is not a number."""

    if isinstance(value, dict):
        for key, child in value.items():
            yield from numeric_pointers(child, f"{pointer}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from numeric_pointers(child, f"{pointer}/{index}")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield pointer


def pointers_of(path: Path) -> set[str]:
    return set(numeric_pointers(json.loads(path.read_text(encoding="utf-8"))))


def by_id(registry: ClaimsRegistryV1) -> dict[str, ClaimV1]:
    claims = {claim.claim_id: claim for claim in registry.claims}
    assert len(claims) == len(registry.claims)
    return claims


def on(registry: ClaimsRegistryV1, artifact: str) -> set[str]:
    """The metric paths of the claims on one artifact."""

    return {claim.metric_path for claim in registry.claims if claim.artifact_path == artifact}


def gate_pointers(document: dict[str, Any]) -> set[str]:
    """Each gate's outcome and each of its counts."""

    return {
        pointer
        for index, row in enumerate(document["gates"])
        for pointer in (
            f"/gates/{index}/passed",
            *(f"/gates/{index}/counts/{key}" for key in row["counts"]),
        )
    }


def write_registry(root: Path, registry: ClaimsRegistryV1, name: str = "claims.yaml") -> Path:
    path = root / name
    path.write_text(
        yaml.safe_dump(registry.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def rewrite(path: Path, **updates: Any) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(updates)
    write_json(path, document)


# --------------------------------------------------------------------------
# The study's registry
# --------------------------------------------------------------------------


def test_a_study_registry_binds_every_number_of_its_evidence_and_gate_files(
    repository: Path,
) -> None:
    """Every number of the policy evidence, and each gate's outcome and counts, has a claim."""

    directory = write_study(repository, attempts=[("gates-attempt-1.json", failed_attempt())])

    registry = build_study_claims(STUDY_EVIDENCE, repository)

    claims = by_id(registry)
    assert all(claim_id.startswith(STUDY_PREFIX) for claim_id in claims)
    assert on(registry, POLICY_EVIDENCE) == pointers_of(directory / "policy-v2-evidence.json")
    assert on(registry, GATES) == gate_pointers(gates())
    attempt = (STUDY_EVIDENCE / "gates-attempt-1.json").as_posix()
    assert on(registry, attempt) == gate_pointers(failed_attempt())
    assert {claim.artifact_path for claim in registry.claims} == {POLICY_EVIDENCE, GATES, attempt}
    assert {
        (claim.evidence_type, claim.status, claim.protocol_hash, claim.cohort_manifest_hash)
        for claim in registry.claims
    } == {("observed", "verified", PROTOCOL_SHA, COHORT_SHA)}


def test_a_study_claim_names_its_row_and_states_its_number(repository: Path) -> None:
    """An id names the section, the row and the field, lower case, with `+` written `-`."""

    write_study(repository, attempts=[("gates-attempt-1.json", failed_attempt())])

    claims = by_id(build_study_claims(STUDY_EVIDENCE, repository))

    attempt = (STUDY_EVIDENCE / "gates-attempt-1.json").as_posix()
    expected = {
        "common_valid_tokens": (
            POLICY_EVIDENCE,
            "/common_valid_tokens",
            "common_valid_tokens is 344.",
        ),
        "levels.b-v2-gated.oracle_aeb.collisions": (
            POLICY_EVIDENCE,
            "/levels/0/collisions",
            "B-v2-gated in oracle_aeb: collisions is 39.",
        ),
        "levels.b-v2-gated.oracle_aeb.contacts_not_at_fault": (
            POLICY_EVIDENCE,
            "/levels/0/contacts_not_at_fault",
            "B-v2-gated in oracle_aeb: contacts_not_at_fault is 1095.",
        ),
        f"levels.a-v1-replication.{FULL_COALITION_ID}.cut_in_or_crossing"
        ".collision_clopper_pearson-successes": (
            POLICY_EVIDENCE,
            "/levels/1/collision_clopper_pearson/successes",
            f"A-v1-replication in {FULL_COALITION} in family cut_in_or_crossing: "
            "collision_clopper_pearson successes is 13.",
        ),
        f"hypotheses.h1.{H1}.contrast-estimate": (
            POLICY_EVIDENCE,
            "/hypotheses/0/contrast/estimate",
            f"H1, {H1_WORDS}: contrast estimate is -0.12345678901234566.",
        ),
        f"hypotheses.h1.{H1}.contrast-terms-1-sign": (
            POLICY_EVIDENCE,
            "/hypotheses/0/contrast/terms/1/sign",
            f"H1, {H1_WORDS}: contrast terms sign is -1.",
        ),
        "hypotheses.q2-1.braking_share.plus-c-v1-kalman-localization_shape-medium"
        ".minus-a-v1-replication-localization_shape-medium.holm_step": (
            POLICY_EVIDENCE,
            "/hypotheses/1/holm_step",
            "Q2.1, braking_share of C-v1-kalman in localization_shape-medium minus "
            "A-v1-replication in localization_shape-medium: holm_step is 3.",
        ),
        f"sensitivity.h1.family_drive_clusters.{H1}.contrast-interval-low": (
            POLICY_EVIDENCE,
            "/sensitivity/0/contrast/interval/low",
            f"H1 family_drive_clusters, {H1_WORDS}: contrast interval low is -0.2.",
        ),
        "secondary.difference_in_differences.collision_indicator"
        f".plus-b-v2-gated-{FULL_COALITION_ID}.minus-b-v2-gated-coalition-none"
        f".minus-a-v1-replication-{FULL_COALITION_ID}.plus-a-v1-replication-coalition-none"
        ".contrast-clopper_pearson-1-successes": (
            POLICY_EVIDENCE,
            "/secondary/0/contrast/clopper_pearson/1/successes",
            f"difference_in_differences, collision_indicator of B-v2-gated in {FULL_COALITION} "
            f"minus B-v2-gated in coalition-none minus A-v1-replication in {FULL_COALITION} "
            "plus A-v1-replication in coalition-none: contrast clopper_pearson successes is 13.",
        ),
        f"descriptive_contrasts.primary_by_family.h1.{H1}.lead_or_stopping.contrast-estimate": (
            POLICY_EVIDENCE,
            "/descriptive_contrasts/0/contrast/estimate",
            f"primary_by_family H1, {H1_WORDS} in family lead_or_stopping: contrast estimate is "
            "-0.12345678901234566.",
        ),
        "q3_ratio.sd_b": (POLICY_EVIDENCE, "/q3_ratio/sd_b", "q3_ratio: sd_b is 0.021."),
        "gates.g2.passed": (GATES, "/gates/1/passed", "G2 passed."),
        f"gates.g2.{FULL_COALITION_ID}": (
            GATES,
            f"/gates/1/counts/{FULL_COALITION}",
            f"G2 counted 0 for {FULL_COALITION}.",
        ),
        "gates.g3.oracle_aeb-c-v1-kalman-a-v1-replication": (
            GATES,
            "/gates/2/counts/oracle_aeb:C-v1-kalman:A-v1-replication",
            "G3 counted 0 for oracle_aeb:C-v1-kalman:A-v1-replication.",
        ),
        "attempt-1.g1.passed": (attempt, "/gates/0/passed", "In an earlier attempt, G1 failed."),
        "attempt-1.g2.dropout-medium": (
            attempt,
            "/gates/1/counts/dropout-medium",
            "In an earlier attempt, G2 counted 3 for dropout-medium.",
        ),
    }
    for suffix, (artifact, metric_path, text) in expected.items():
        claim = claims[STUDY_PREFIX + suffix]
        assert (claim.artifact_path, claim.metric_path, claim.text) == (
            artifact,
            metric_path,
            text,
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [("protocol_sha256", "protocol hash mismatch"), ("cohort_manifest_sha256", "cohort manifest")],
)
@pytest.mark.parametrize("artifact", ["policy-v2-evidence.json", "gates.json"])
def test_audit_claims_passes_on_a_study_registry_and_fails_when_a_hash_differs(
    repository: Path, field: str, message: str, artifact: str
) -> None:
    """The released audit holds every claim to its artifact's protocol and cohort."""

    directory = write_study(repository)
    claims = write_registry(repository, build_study_claims(STUDY_EVIDENCE, repository))
    assert audit_claims(claims, repository) == ()

    rewrite(directory / artifact, **{field: OTHER_SHA})

    violations = audit_claims(claims, repository)
    assert violations
    assert all(message in violation for violation in violations)


def test_a_study_reported_not_completed_gets_a_gate_only_registry(repository: Path) -> None:
    """Without a summary the registry binds the gate files alone and says nothing of the policy."""

    write_study(
        repository,
        completed=False,
        gate_file=gates(g2=FAILED_G2),
        attempts=[("gates-attempt-1.json", failed_attempt())],
    )

    registry = build_study_claims(STUDY_EVIDENCE, repository)

    attempt = (STUDY_EVIDENCE / "gates-attempt-1.json").as_posix()
    assert {claim.artifact_path for claim in registry.claims} == {GATES, attempt}
    assert on(registry, GATES) == gate_pointers(gates(g2=FAILED_G2))
    claims = by_id(registry)
    assert claims[f"{STUDY_PREFIX}gates.g2.passed"].text == "G2 failed."
    assert claims[f"{STUDY_PREFIX}gates.g2.dropout-medium"].text == (
        "G2 counted 3 for dropout-medium."
    )
    assert audit_claims(write_registry(repository, registry), repository) == ()


def test_a_page_bound_to_a_gate_only_registry_passes_and_a_stated_cohort_is_flagged(
    repository: Path,
) -> None:
    """Every gate count binds, a cell's under its key with underscores, and no cohort is known."""

    write_study(
        repository,
        completed=False,
        gate_file=gates(g2=FAILED_G2),
        attempts=[("gates-attempt-1.json", failed_attempt())],
    )
    claims = write_registry(repository, build_study_claims(STUDY_EVIDENCE, repository))
    page = repository / "results.md"
    page.write_text(
        "# Policy v2 study: not completed\n"
        "G2 failed: `dropout_medium` = 3 "
        f"<!-- claim: {STUDY_PREFIX}gates.g2.dropout-medium --> documents differ from the "
        f"released ones, and `oracle_aeb` = 0 <!-- claim: {STUDY_PREFIX}gates.g2.oracle_aeb -->.\n"
        "In an earlier attempt `documents_missing` = 2 "
        f"<!-- claim: {STUDY_PREFIX}attempt-1.g1.documents_missing -->.\n",
        encoding="utf-8",
    )

    violations, status = validate_attribution(claims, repository, None, [page])

    assert violations == ()
    assert status["common_valid_tokens"] is None
    assert len(status["statements"]) == 3

    stated = repository / "stated.md"
    stated.write_text("The cohort of 344 tokens was never analysed.\n", encoding="utf-8")

    violations, _ = validate_attribution(claims, repository, None, [stated])

    assert (
        "stated.md:1: stated cohort 344, but no evidence of this registry records a "
        "common-valid cohort"
    ) in violations


def test_earlier_attempts_are_bound_in_attempt_order(repository: Path) -> None:
    """Each `gates-attempt-<n>.json` is bound under its number; no other file is read."""

    write_study(
        repository,
        attempts=[
            ("gates-attempt-10.json", failed_attempt()),
            ("gates-attempt-2.json", failed_attempt()),
            ("gates-attempt-01.json", failed_attempt()),
            ("reproduction.json", {"schema_version": "aeb-study-reproduction/v1"}),
        ],
    )
    (repository / STUDY_EVIDENCE / "operator-log.txt").write_text("2026-10-01 note\n", "utf-8")

    registry = build_study_claims(STUDY_EVIDENCE, repository)

    order = [
        claim.claim_id.split(".")[3]
        for claim in registry.claims
        if claim.claim_id.startswith(f"{STUDY_PREFIX}attempt-")
    ]
    assert list(dict.fromkeys(order)) == ["attempt-2", "attempt-10"]
    assert {Path(claim.artifact_path).name for claim in registry.claims} == {
        "policy-v2-evidence.json",
        "gates.json",
        "gates-attempt-2.json",
        "gates-attempt-10.json",
    }


@pytest.mark.parametrize(
    ("completed", "reference", "exploratory"),
    [(True, "arm-a", True), (False, "arm-a", True), (True, "released", False)],
)
def test_every_study_claim_is_exploratory_in_arm_a_reference_mode(
    repository: Path, completed: bool, reference: str, exploratory: bool
) -> None:
    """With `exploratory: true` every claim's text begins "Exploratory:", and otherwise none."""

    write_study(
        repository,
        completed=completed,
        reference=reference,
        gate_file=gates(reference, g2=FAILED_G2),
        attempts=[("gates-attempt-1.json", failed_attempt())],
    )

    registry = build_study_claims(STUDY_EVIDENCE, repository)

    assert {claim.text.startswith("Exploratory: ") for claim in registry.claims} == {exploratory}
    if exploratory:
        claims = by_id(registry)
        assert claims[f"{STUDY_PREFIX}gates.g2.passed"].text == "Exploratory: G2 failed."
        assert claims[f"{STUDY_PREFIX}attempt-1.g2.passed"].text == (
            "Exploratory: In an earlier attempt, G2 failed."
        )


def test_duplicate_claim_ids_are_refused(repository: Path) -> None:
    """Two numbers the evidence gives one name are refused, not silently merged."""

    write_study(
        repository, policy=policy_evidence(levels=[level(ARM_B, ORACLE), level(ARM_B, ORACLE)])
    )

    with pytest.raises(ValueError, match="more than one number the claim id"):
        build_study_claims(STUDY_EVIDENCE, repository)


@pytest.mark.parametrize(
    ("policy", "attempt"),
    [
        (policy_evidence(protocol_sha256=OTHER_SHA), gates()),
        (policy_evidence(), gates(cohort_manifest_sha256=OTHER_SHA)),
    ],
    ids=["policy-evidence-protocol", "attempt-cohort"],
)
def test_study_evidence_of_another_protocol_or_cohort_is_refused(
    repository: Path, policy: dict[str, Any], attempt: dict[str, Any]
) -> None:
    """Every bound document names the protocol and the cohort of `gates.json`."""

    write_study(repository, policy=policy, attempts=[("gates-attempt-1.json", attempt)])

    with pytest.raises(ValueError, match="another protocol or cohort"):
        build_study_claims(STUDY_EVIDENCE, repository)


@pytest.mark.parametrize(
    ("write", "build"),
    [(write_study, build_study_claims), (write_addendum, build_addendum_claims)],
    ids=["study", "addendum"],
)
def test_evidence_outside_the_repository_is_refused(
    tmp_path: Path,
    write: Callable[[Path], Path],
    build: Callable[[Path, Path], ClaimsRegistryV1],
) -> None:
    """An artifact path is read from the repository root, so the evidence must lie under it."""

    evidence = write(tmp_path / "elsewhere")
    root = tmp_path / "repository"
    root.mkdir()

    with pytest.raises(ValueError, match="not in the repository"):
        build(evidence, root)


def test_a_relative_evidence_directory_is_read_from_the_repository_root(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The working directory does not matter; an absolute directory gives the same registry."""

    write_study(repository)
    monkeypatch.chdir(tmp_path.parent)

    relative = build_study_claims(STUDY_EVIDENCE, repository)

    assert relative == build_study_claims(repository / STUDY_EVIDENCE, repository)


# --------------------------------------------------------------------------
# The addendum's registry
# --------------------------------------------------------------------------


def test_an_addendum_registry_binds_every_number_and_the_released_oracle_counts(
    repository: Path,
) -> None:
    """Every number of the addendum evidence, and the two released counts an oracle line needs."""

    directory = write_addendum(repository)

    registry = build_addendum_claims(ADDENDUM_EVIDENCE, repository)

    claims = by_id(registry)
    assert all(claim_id.startswith(ADDENDUM_PREFIX) for claim_id in claims)
    assert on(registry, ADDENDUM_NUMBERS) == pointers_of(
        directory / "attribution-addendum-evidence.json"
    )
    released = RELEASED_EVALUATION.as_posix()
    assert {
        claim_id: (claim.metric_path, claim.text)
        for claim_id, claim in claims.items()
        if claim.artifact_path == released
    } == {
        f"{ADDENDUM_PREFIX}baseline.collisions.oracle_aeb": (
            "/configurations/1/collisions",
            "oracle_aeb collisions is 39.",
        ),
        f"{ADDENDUM_PREFIX}baseline.contacts_not_at_fault.oracle_aeb": (
            "/configurations/1/contacts_not_at_fault",
            "oracle_aeb contacts_not_at_fault is 1095.",
        ),
    }
    assert {
        (claim.evidence_type, claim.status, claim.protocol_hash, claim.cohort_manifest_hash)
        for claim in registry.claims
    } == {("observed", "verified", PROTOCOL_SHA, COHORT_SHA)}


def test_an_addendum_claim_names_its_row_and_states_its_number(repository: Path) -> None:
    """A Shapley value names its game and channel, a difference its two channels."""

    write_addendum(repository)

    claims = by_id(build_addendum_claims(ADDENDUM_EVIDENCE, repository))

    expected = {
        "cohort_size": ("/cohort_size", "cohort_size is 344."),
        "bootstrap.clusters": ("/bootstrap/clusters", "bootstrap: clusters is 198."),
        "shapley.collision_indicator.latency.estimate": (
            "/games/1/shapley_values/latency/estimate",
            "Shapley value of latency in the collision_indicator game: estimate is 0.03.",
        ),
        "difference.intervention_duration_s.localization_shape-minus-dropout.intervals-1-high": (
            "/games/0/localization_shape_differences/0/intervals/1/high",
            "Shapley value of localization_shape minus that of dropout in the "
            "intervention_duration_s game: intervals high is 0.15625.",
        ),
        "difference.collision_indicator.dropout-minus-latency.estimate": (
            "/games/1/other_differences/0/estimate",
            "Shapley value of dropout minus that of latency in the collision_indicator game: "
            "estimate is 0.001.",
        ),
        "configuration_contrasts.collision_indicator.oracle_aeb-minus-coalition-none.estimate": (
            "/configuration_contrasts/0/estimate",
            "collision_indicator of oracle_aeb minus coalition-none: estimate is 0.0125.",
        ),
        "oracle_collisions.avoided-events": (
            "/oracle_collisions/avoided/events",
            "oracle_collisions: avoided events is 20.",
        ),
        "oracle_collisions.oracle_aeb_contacts_not_at_fault": (
            "/oracle_collisions/oracle_aeb_contacts_not_at_fault",
            "oracle_collisions: oracle_aeb_contacts_not_at_fault is 1095.",
        ),
        "brake_activations.oracle_aeb.per_hour": (
            "/brake_activations/0/per_hour",
            "brake_activations of oracle_aeb: per_hour is 119.99500020832465.",
        ),
        f"zero_event_configurations.{FULL_COALITION_ID}.by_family-lead_or_stopping-tokens": (
            "/zero_event_configurations/0/by_family/lead_or_stopping/tokens",
            f"zero_event_configurations of {FULL_COALITION}: by_family lead_or_stopping tokens "
            "is 100.",
        ),
        "matched_onset_delays.coalition-none.matched_onset_delays_s-median": (
            "/matched_onset_delays/0/matched_onset_delays_s/median",
            "matched_onset_delays of coalition-none: matched_onset_delays_s median is 1.0.",
        ),
        "stops_and_collision_speeds.oracle_aeb.ego_speed_at_collision_mps-count": (
            "/stops_and_collision_speeds/0/ego_speed_at_collision_mps/count",
            "stops_and_collision_speeds of oracle_aeb: ego_speed_at_collision_mps count is 0.",
        ),
    }
    for suffix, (metric_path, text) in expected.items():
        claim = claims[ADDENDUM_PREFIX + suffix]
        assert (claim.artifact_path, claim.metric_path, claim.text) == (
            ADDENDUM_NUMBERS,
            metric_path,
            text,
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [("protocol_sha256", "protocol hash mismatch"), ("cohort_manifest_sha256", "cohort manifest")],
)
def test_audit_claims_passes_on_an_addendum_registry_and_fails_when_a_hash_differs(
    repository: Path, field: str, message: str
) -> None:
    """The addendum's claims, the released counts among them, pass the released audit."""

    directory = write_addendum(repository)
    claims = write_registry(repository, build_addendum_claims(ADDENDUM_EVIDENCE, repository))
    assert audit_claims(claims, repository) == ()

    rewrite(directory / "attribution-addendum-evidence.json", **{field: OTHER_SHA})

    violations = audit_claims(claims, repository)
    assert violations
    assert all(message in violation for violation in violations)


def test_released_evidence_of_another_cohort_is_refused(repository: Path) -> None:
    """The released counts are bound only when they come from the addendum's own records."""

    write_addendum(repository)
    rewrite(repository / RELEASED_EVALUATION, cohort_manifest_sha256=OTHER_SHA)

    with pytest.raises(ValueError, match="another protocol or cohort"):
        build_addendum_claims(ADDENDUM_EVIDENCE, repository)


@pytest.mark.parametrize(
    ("write", "build", "line"),
    [
        (
            write_study,
            build_study_claims,
            "Arm B brakes for `braking_share` = 0.2500 <!-- claim: "
            f"{STUDY_PREFIX}levels.b-v2-gated.oracle_aeb.braking_share; rounded: 4 --> "
            "of its simulated time.\n",
        ),
        (
            write_addendum,
            build_addendum_claims,
            "Localization's Shapley value exceeds dropout's by `estimate` = 0.031 <!-- claim: "
            f"{ADDENDUM_PREFIX}difference.collision_indicator.localization_shape-minus-dropout"
            ".estimate; rounded: 3 --> in the collision game.\n",
        ),
    ],
    ids=["study", "addendum"],
)
def test_a_rounded_marker_validates_against_a_claim_in_each_new_registry(
    repository: Path,
    write: Callable[[Path], Path],
    build: Callable[[Path, Path], ClaimsRegistryV1],
    line: str,
) -> None:
    """A shorter display is recomputed from the new evidence as from the released one."""

    directory = write(repository)
    claims = write_registry(repository, build(directory.relative_to(repository), repository))
    page = repository / "results.md"
    page.write_text(line, encoding="utf-8")

    violations, status = validate_attribution(claims, repository, None, [page])

    assert violations == ()
    assert status["common_valid_tokens"] == 344
    assert [trace["rounded_decimal_places"] for trace in status["statements"]] == [
        4 if build is build_study_claims else 3
    ]


# --------------------------------------------------------------------------
# `aeb-risk study claims`
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("part", "write", "build"),
    [
        ("study", write_study, build_study_claims),
        ("addendum", write_addendum, build_addendum_claims),
    ],
)
def test_study_claims_writes_the_registry_of_each_part(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    part: str,
    write: Callable[[Path], Path],
    build: Callable[[Path, Path], ClaimsRegistryV1],
) -> None:
    """The command writes the registry its builder returns, and it passes the released audit."""

    evidence = write(repository).relative_to(repository)
    monkeypatch.chdir(repository)
    output = evidence.parent / "claims.yaml"

    result = CliRunner().invoke(
        app,
        [
            "study",
            "claims",
            "--part",
            part,
            "--evidence-dir",
            str(evidence),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    registry = build(evidence, repository)
    assert load_registry(repository / output) == registry
    assert f"{len(registry.claims)} claims" in result.output
    assert audit_claims(repository / output, repository) == ()


def test_study_claims_refuses_an_unknown_part(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repository)

    result = CliRunner().invoke(
        app,
        ["study", "claims", "--part", "released", "--evidence-dir", ".", "--output", "c.yaml"],
    )

    assert result.exit_code == 1
    assert "--part is study or addendum" in result.output
    assert not (repository / "c.yaml").exists()


def test_study_claims_refuses_evidence_it_cannot_bind(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing evidence ends the command with a diagnostic, and nothing is written."""

    monkeypatch.chdir(repository)

    result = CliRunner().invoke(
        app,
        [
            "study",
            "claims",
            "--part",
            "study",
            "--evidence-dir",
            str(STUDY_EVIDENCE),
            "--output",
            "claims.yaml",
        ],
    )

    assert result.exit_code == 1
    assert "the study claims cannot be built" in result.output
    assert not (repository / "claims.yaml").exists()
