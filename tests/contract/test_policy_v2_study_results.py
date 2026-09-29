"""The published results of the policy v2 study, held to the evidence they come from.

`docs/studies/aeb-policy-v2/` publishes what `study analyse`, `study evidence`
and `study claims` wrote for the attempt whose gates passed: the summary, the
evidence derived from it, the gate report, the reproduction record and the
claims registry. It also publishes the operator log, `results.md` and a
licence notice. These tests pin the summary, derive the evidence from it again,
rebuild the registry, audit `results.md` against that registry, keep scenario
tokens and local paths out of every published file, and check that the plan was
frozen before any arm ran.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from aebrisk.analysis import attribution_audit
from aebrisk.analysis.attribution_audit import validate_attribution
from aebrisk.analysis.claims import load_registry
from aebrisk.artifacts.study_documents import (
    PolicyV2EvidenceV1,
    PolicyV2SummaryV1,
    StudyGatesV1,
    StudyReproductionV1,
)
from aebrisk.study.claims import build_study_claims
from aebrisk.study.evidence import is_ancestor
from aebrisk.study.gates import committed_cohort_tokens, refuse_token_strings

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "docs" / "studies" / "aeb-policy-v2"
EVIDENCE = STUDY / "evidence"
CLAIMS = STUDY / "claims.yaml"
RESULTS = STUDY / "results.md"
OPERATOR_LOG = EVIDENCE / "operator-log.txt"

#: The SHA-256 of the summary `study analyse` wrote for attempt 1, in the
#: LF form Git stores.
SUMMARY_SHA256 = "e291baa569a5f853f8ca1f3b7ed2eb50c935617a0413f2e95e11fb3bc03aa54d"

#: The files the results pull request publishes in the study's directory,
#: beside the analysis plan that was frozen before them.
PUBLISHED = (
    "NOTICE.md",
    "claims.yaml",
    "evidence/gates.json",
    "evidence/operator-log.txt",
    "evidence/policy-v2-evidence.json",
    "evidence/reproduction.json",
    "evidence/summary.json",
    "results.md",
)

#: How `results.md` prints the Q1 label the summary records.
LABELS = {"support": "Support", "partial_support": "Partial support", "no_support": "No support"}
H5_QUALIFIER = " (with more oracle collisions)"

#: A path on a local machine: a drive letter, or a home, mount or container root.
LOCAL_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|(?<![\w.$])/(?:home|Users|mnt|work|tmp)/")
#: Sixteen lower-case hexadecimal digits standing alone, the shape of a scenario token.
TOKEN_SHAPED = re.compile(r"(?<![0-9a-f.])[0-9a-f]{16}(?![0-9a-f])")


def stable_bytes(payload: Any) -> bytes:
    """The stable UTF-8/LF JSON form every published document is written in."""

    return (
        json.dumps(payload, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def lf_normalised_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def summary() -> PolicyV2SummaryV1:
    return PolicyV2SummaryV1.model_validate_json((EVIDENCE / "summary.json").read_bytes())


def reproduction() -> StudyReproductionV1:
    return StudyReproductionV1.model_validate_json((EVIDENCE / "reproduction.json").read_bytes())


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


# --------------------------------------------------------------------------
# The evidence
# --------------------------------------------------------------------------


def test_the_committed_summary_is_the_one_the_analysis_wrote() -> None:
    assert lf_normalised_sha256(EVIDENCE / "summary.json") == SUMMARY_SHA256


def test_the_summary_is_stable_and_the_evidence_is_derived_from_it_byte_for_byte() -> None:
    """The derived evidence is every evidence field of the summary, copied, and nothing else."""

    committed = summary()
    copied = committed.model_dump(mode="json")
    fields = set(PolicyV2EvidenceV1.model_fields) - {"schema_version"}
    derived = PolicyV2EvidenceV1.model_validate(
        {field: copied[field] for field in fields} | {"schema_version": "aeb-policy-v2-evidence/v1"}
    )

    assert (EVIDENCE / "summary.json").read_bytes() == stable_bytes(copied)
    assert (EVIDENCE / "policy-v2-evidence.json").read_bytes() == stable_bytes(
        derived.model_dump(mode="json")
    )


def test_the_gate_report_and_the_reproduction_record_agree_with_the_summary() -> None:
    committed = summary()
    gates_payload = json.loads((EVIDENCE / "gates.json").read_text(encoding="utf-8"))
    gates = StudyGatesV1.model_validate(gates_payload)
    record = reproduction()
    identity = ("study_sha256", "protocol_sha256", "cohort_manifest_sha256", "reference")
    outcomes = {str(gate.gate): gate for gate in record.gates}

    assert "artifacts_only_detail" not in gates_payload
    assert (EVIDENCE / "gates.json").read_bytes() == stable_bytes(gates_payload)
    assert all(getattr(gates, field) == getattr(committed, field) for field in identity)
    assert all(getattr(record, field) == getattr(committed, field) for field in identity)
    assert (committed.reference, committed.exploratory) == ("released", False)
    assert [gate.gate for gate in record.gates] == ["G0", "G1", "G2", "G3", "G4", "G5"]
    assert all(gate.outcome == "passed" for gate in record.gates)
    for gate in gates.gates:
        assert (outcomes[gate.gate].outcome, dict(outcomes[gate.gate].counts)) == (
            "passed" if gate.passed else "failed",
            dict(gate.counts),
        )
    assert dict(outcomes["G4"].counts) == dict(committed.g4.counts)
    assert [(item.file, item.attempt, item.passed) for item in record.gate_files] == [
        ("gates.json", 1, True)
    ]


def test_no_published_file_holds_a_scenario_token() -> None:
    """Only aggregates are published: no string may hold a token of the committed cohorts."""

    tokens = committed_cohort_tokens(ROOT)
    published = sorted(
        path.relative_to(STUDY).as_posix()
        for path in STUDY.glob("**/*")
        if path.is_file() and path.name != "analysis-plan.md"
    )

    assert published == sorted(PUBLISHED)
    for relative in PUBLISHED:
        text = (STUDY / relative).read_text(encoding="utf-8")
        payload = json.loads(text) if relative.endswith(".json") else text
        refuse_token_strings(payload, tokens)
        assert TOKEN_SHAPED.findall(text) == [], relative


def test_the_operator_log_names_no_local_path_and_writes_the_dataset_root_as_a_variable() -> None:
    text = OPERATOR_LOG.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert "\r" not in text
    assert all(re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ", line) for line in lines)
    assert [line.split(" ", 1)[1] for line in lines if "NUPLAN_DATA_ROOT=" in line] == [
        "NUPLAN_DATA_ROOT=$NUPLAN_DATA_ROOT"
    ]
    for relative in PUBLISHED:
        assert LOCAL_PATH.findall((STUDY / relative).read_text(encoding="utf-8")) == [], relative


# --------------------------------------------------------------------------
# The claims registry and the results page
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def page_audit() -> tuple[str, ...]:
    """One full attribution audit of `results.md`, which also runs the registry's claims audit.

    Auditing the registry reads the evidence once per claim, which takes minutes,
    so the tests below share one run.
    """

    violations, _ = validate_attribution(CLAIMS, ROOT, None, [RESULTS])
    return violations


def test_the_study_registry_audits_clean(page_audit: tuple[str, ...]) -> None:
    assert [violation for violation in page_audit if violation.startswith("registry:")] == []


def test_the_study_registry_is_exactly_the_registry_built_from_the_evidence() -> None:
    assert load_registry(CLAIMS) == build_study_claims(EVIDENCE, ROOT)


def test_every_number_on_the_results_page_is_bound_to_the_study_registry(
    page_audit: tuple[str, ...],
) -> None:
    assert page_audit == ()


def test_the_page_audit_refuses_a_changed_primary_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not vacuous: one digit of H1's estimate changed is refused.

    The registry's own audit is replaced here; the tests above run it.
    """

    monkeypatch.setattr(attribution_audit, "audit_claims", lambda *_: ())
    text = RESULTS.read_text(encoding="utf-8")
    binding = re.search(
        r"`estimate` = (-0\.476) <!-- claim: p3\.study\.policy-v2\.hypotheses\.h1\.[^>]*-->", text
    )
    assert binding is not None
    changed = tmp_path / "results.md"
    changed.write_text(
        text[: binding.start(1)] + "-0.477" + text[binding.end(1) :], encoding="utf-8"
    )

    violations, _ = validate_attribution(CLAIMS, ROOT, None, [changed])

    assert len(violations) == 1
    assert "`estimate` says -0.477" in violations[0]


def paragraph_after(text: str, heading: str) -> str:
    """The first paragraph after a heading, its source lines joined by spaces."""

    body = text.split(f"\n{heading}\n", 1)[1].lstrip("\n")
    return " ".join(body.split("\n\n", 1)[0].splitlines())


def test_the_answer_states_the_label_and_the_fixed_sentences_the_summary_chose() -> None:
    """Section 7.2 of the analysis plan sets what the verdict paragraph must say."""

    committed = summary()
    text = RESULTS.read_text(encoding="utf-8")
    verdict = paragraph_after(text, "## Answer")
    label = LABELS[committed.q1_label] + (H5_QUALIFIER if committed.h5_qualifier else "")
    statements = committed.statements

    assert verdict.startswith(f"**Q1: {label}.**")
    assert statements.h5 in verdict
    assert statements.h4 in verdict
    assert verdict.index(statements.h5) < verdict.index(statements.h4)
    for hypothesis in ("H4", "H5"):
        assert f"For {hypothesis} (" in verdict
    assert "induced-collision indicator" in verdict
    assert "avoided-collision indicator" in verdict
    assert statements.h3 in text
    assert re.search(r"\bsafer\b|\bfixes\b", text, flags=re.IGNORECASE) is None


def slug(name: str) -> str:
    """An arm or cell name as the claim identifiers spell it."""

    return re.sub(r"[^a-z0-9_]+", "-", name.lower())


def test_every_zero_event_cell_prints_its_clopper_pearson_bounds_overall_and_per_family() -> None:
    """Section 6 of the analysis plan gives a zero-event cell's bound overall and per family."""

    text = RESULTS.read_text(encoding="utf-8")
    levels = summary().levels
    zero_event = [
        (level.arm, level.cell)
        for level in levels
        if level.family is None and level.collision_tokens == 0
    ]

    assert zero_event
    for arm, cell in zero_event:
        rows = [level for level in levels if (level.arm, level.cell) == (arm, cell)]
        assert len(rows) == 5
        for row in rows:
            prefix = ".".join(
                part
                for part in ("p3.study.policy-v2.levels", slug(arm), slug(cell), row.family)
                if part
            )
            for field in ("low", "high"):
                marker = f"<!-- claim: {prefix}.collision_clopper_pearson-{field};"
                assert marker in text, (prefix, field)


def test_the_answer_gives_the_h5_qualifier_its_own_rule_not_holms() -> None:
    """The qualifier follows H5's unadjusted p and the sign of its estimate, whatever Holm says."""

    committed = summary()
    verdict = paragraph_after(RESULTS.read_text(encoding="utf-8"), "## Answer")

    assert not committed.h5_qualifier
    assert (
        "The label carries no qualifier: the qualifier needs H5's unadjusted sign-flip p "
        "below the five-percent level and an estimate above zero" in verdict
    )
    assert re.search(r"Holm[^.]*so the label carries no qualifier", verdict) is None


def test_the_answer_places_both_splits_and_follows_h4_with_its_intervals_and_exposure() -> None:
    """Section 7.2: the oracle and full-coalition splits, then H4's intervals and exposure."""

    verdict = paragraph_after(RESULTS.read_text(encoding="utf-8"), "## Answer")
    h4 = "p3.study.policy-v2.hypotheses.h4.collision_indicator."
    exposure = "p3.study.policy-v2.secondary.exposure."
    after_h4 = verdict[verdict.index(summary().statements.h4) :]
    h4_part = after_h4[: after_h4.index("Exposure moved with the policy")]

    assert "benefit_and_harm.induced.plus-b-v2-gated-oracle_aeb." in verdict
    assert "benefit_and_harm.avoided.plus-b-v2-gated-oracle_aeb." in verdict
    assert "In the full coalition, the induced-collision indicator" in after_h4
    for arm in ("0", "1"):
        for bound in ("low", "high"):
            assert re.search(rf"{re.escape(h4)}[^ ]*clopper_pearson-{arm}-{bound}", h4_part)
    for outcome in ("simulated_seconds", "early_ends"):
        for cell in (
            "oracle_aeb",
            "coalition-dropout-localization_shape-latency-track_instability",
        ):
            assert f"{exposure}{outcome}.plus-b-v2-gated-{cell}." in after_h4


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_preregistration_precedes_every_arm() -> None:
    """The plan was frozen at a merge that every arm's tooling commit descends from."""

    record = reproduction()
    merged_at = utc(record.preregistration_merged_at)

    assert record.arms
    for arm in record.arms:
        assert is_ancestor(ROOT, record.preregistration_commit, arm.tooling_commit), arm.arm_id
        assert merged_at < utc(arm.started_at_utc) < utc(arm.finished_at_utc), arm.arm_id
