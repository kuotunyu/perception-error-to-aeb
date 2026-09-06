"""Contracts for generated public claims and their evidence audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

PROTOCOL_SHA256 = "a" * 64
COHORT_SHA256 = "b" * 64

VOCABULARY: dict[str, Any] = {
    "allowed_evidence_types": ["observed", "derived", "synthetic", "illustrative"],
    "claim_required_fields": [
        "claim_id",
        "text",
        "evidence_type",
        "protocol_hash",
        "cohort_manifest_hash",
        "artifact_path",
        "metric_path",
        "status",
    ],
    "allowed_statuses": ["draft", "verified", "rejected", "superseded"],
}


def _claim(**overrides: Any) -> dict[str, Any]:
    return {
        "claim_id": "p3.baseline.collisions.no_aeb",
        "text": "no_aeb collisions is 7.",
        "evidence_type": "observed",
        "protocol_hash": PROTOCOL_SHA256,
        "cohort_manifest_hash": COHORT_SHA256,
        "artifact_path": "docs/evidence/study/evaluation.json",
        "metric_path": "/configurations/0/collisions",
        "status": "verified",
    } | overrides


def _write_registry(tmp_path: Path, claims: list[dict[str, Any]], **overrides: Any) -> Path:
    path = tmp_path / "claims.yaml"
    path.write_text(
        yaml.safe_dump(VOCABULARY | {"claims": claims} | overrides, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _write_artifact(root: Path, value: Any = 7, **overrides: Any) -> None:
    path = root / "docs" / "evidence" / "study" / "evaluation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "protocol_sha256": PROTOCOL_SHA256,
        "cohort_manifest_sha256": COHORT_SHA256,
        "configurations": [{"collisions": value}],
    } | overrides
    path.write_text(json.dumps(document), encoding="utf-8")


def _write_evidence(root: Path) -> Path:
    evidence = root / "docs" / "evidence" / "study"
    evidence.mkdir(parents=True)
    common = {
        "protocol_sha256": PROTOCOL_SHA256,
        "cohort_manifest_sha256": COHORT_SHA256,
        "cohort_size": 2,
        "common_valid_tokens": 2,
    }
    documents = {
        "evaluation.json": common
        | {
            "schema_version": "aeb-evaluation/v1",
            "simulated_seconds": 4.50,
            "configurations": [
                {
                    "configuration_id": "coalition-dropout+latency",
                    "group": "coalition",
                    "collisions": 7,
                    "collisions_per_100km": None,
                }
            ],
        },
        "intervals.json": common
        | {
            "schema_version": "aeb-intervals/v1",
            "intervals": {
                "no_aeb": {
                    "collision_indicator": {
                        "confidence": 0.95,
                        "estimate": 0.25,
                        "high": 0.5,
                        "low": 0.0,
                        "resamples": 5000,
                        "seed": 20260831,
                    }
                }
            },
        },
        "shapley.json": common
        | {
            "schema_version": "aeb-shapley/v1",
            "metrics": {
                "collision_indicator": {
                    "efficiency_max_abs_residual": 2.2e-16,
                    "scenarios_attributed": 2,
                    "values": {"dropout": -0.125},
                }
            },
        },
        "exclusions.json": common | {"schema_version": "aeb-exclusions/v1", "excluded": []},
    }
    for name, document in documents.items():
        with (evidence / name).open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return evidence


@pytest.mark.parametrize(
    "override",
    [
        {"allowed_evidence_types": ["observed", "vibes"]},
        {"allowed_statuses": ["draft", "probably-fine"]},
        {"claim_required_fields": ["claim_id", "text"]},
    ],
)
def test_a_registry_cannot_widen_the_shared_vocabulary(
    tmp_path: Path, override: dict[str, Any]
) -> None:
    from aebrisk.analysis.claims import load_registry

    with pytest.raises(ValidationError, match=next(iter(override))):
        load_registry(_write_registry(tmp_path, [], **override))


def test_only_verified_claims_are_public(tmp_path: Path) -> None:
    from aebrisk.analysis.claims import verified_claims

    path = _write_registry(
        tmp_path,
        [
            _claim(claim_id="p3.verified", status="verified"),
            _claim(claim_id="p3.draft", status="draft"),
            _claim(claim_id="p3.rejected", status="rejected"),
            _claim(claim_id="p3.superseded", status="superseded"),
        ],
    )

    assert [entry.claim_id for entry in verified_claims(path)] == ["p3.verified"]


def test_an_exact_number_under_the_pointer_passes_and_a_different_number_fails(
    tmp_path: Path,
) -> None:
    from aebrisk.analysis.claims import audit_claims

    _write_artifact(tmp_path)
    assert audit_claims(_write_registry(tmp_path, [_claim()]), tmp_path) == ()

    violations = audit_claims(
        _write_registry(tmp_path, [_claim(text="no_aeb collisions is 8.")]), tmp_path
    )
    assert violations == ("p3.baseline.collisions.no_aeb: claim number is absent from metric: 8",)


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("missing", "does not exist"),
        ("escape", "escapes repository"),
        ("invalid_json", "not valid UTF-8 JSON"),
        ("array_root", "root must be an object"),
        ("wrong_protocol", "protocol hash mismatch"),
        ("wrong_cohort", "cohort manifest hash mismatch"),
        ("missing_pointer", "JSON pointer does not exist"),
    ],
)
def test_unreproducible_evidence_is_reported(tmp_path: Path, setup: str, expected: str) -> None:
    from aebrisk.analysis.claims import audit_claims

    entry = _claim()
    if setup == "escape":
        entry = _claim(artifact_path="../outside.json")
    elif setup == "invalid_json":
        path = tmp_path / "docs" / "evidence" / "study" / "evaluation.json"
        path.parent.mkdir(parents=True)
        path.write_text("{nope", encoding="utf-8")
    elif setup == "array_root":
        path = tmp_path / "docs" / "evidence" / "study" / "evaluation.json"
        path.parent.mkdir(parents=True)
        path.write_text("[7]", encoding="utf-8")
    elif setup == "wrong_protocol":
        _write_artifact(tmp_path, protocol_sha256="c" * 64)
    elif setup == "wrong_cohort":
        _write_artifact(tmp_path, cohort_manifest_sha256="c" * 64)
    elif setup == "missing_pointer":
        _write_artifact(tmp_path)
        entry = _claim(metric_path="/configurations/0/missed_interventions")
    elif setup != "missing":
        raise AssertionError(setup)

    violations = audit_claims(_write_registry(tmp_path, [entry]), tmp_path)

    assert any(expected in violation for violation in violations), violations


def test_an_invalid_registry_and_non_finite_json_fail_closed(tmp_path: Path) -> None:
    from aebrisk.analysis.claims import audit_claims

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("allowed_evidence_types: [observed]\n", encoding="utf-8")
    assert "registry is invalid" in audit_claims(invalid, tmp_path)[0]

    path = tmp_path / "docs" / "evidence" / "study" / "evaluation.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"protocol_sha256":"'
        + PROTOCOL_SHA256
        + '","cohort_manifest_sha256":"'
        + COHORT_SHA256
        + '","configurations":[{"collisions":NaN}]}',
        encoding="utf-8",
    )
    assert (
        "not valid UTF-8 JSON" in audit_claims(_write_registry(tmp_path, [_claim()]), tmp_path)[0]
    )


def test_pointer_arrays_nested_numbers_booleans_and_qualitative_text(tmp_path: Path) -> None:
    from aebrisk.analysis.claims import audit_claims

    _write_artifact(tmp_path, [{"deep": 7}, {"enabled": True, "note": "last"}])
    nested = _claim(metric_path="/configurations/0/collisions/0")
    assert audit_claims(_write_registry(tmp_path, [nested]), tmp_path) == ()
    containing_list = _claim(metric_path="/configurations/0/collisions")
    assert audit_claims(_write_registry(tmp_path, [containing_list]), tmp_path) == ()

    boolean = _claim(
        text="Exactly 1 result was enabled.", metric_path="/configurations/0/collisions/1"
    )
    assert "absent from metric" in audit_claims(_write_registry(tmp_path, [boolean]), tmp_path)[0]

    qualitative = _claim(
        text="The final result was enabled.", metric_path="/configurations/0/collisions/1/enabled"
    )
    assert audit_claims(_write_registry(tmp_path, [qualitative]), tmp_path) == ()


@pytest.mark.parametrize(
    "pointer", ["/configurations/9", "/configurations/last", "/configurations/01"]
)
def test_invalid_array_indices_are_refused(tmp_path: Path, pointer: str) -> None:
    from aebrisk.analysis.claims import audit_claims

    _write_artifact(tmp_path)
    violations = audit_claims(_write_registry(tmp_path, [_claim(metric_path=pointer)]), tmp_path)
    assert "JSON pointer does not exist" in violations[0]


def test_generation_covers_every_numeric_leaf_verbatim_and_loads_in_the_report(
    tmp_path: Path,
) -> None:
    from aebrisk.analysis.claims import generate_claims
    from aebrisk.report.builder import load_claims

    evidence = _write_evidence(tmp_path)
    registry = generate_claims(evidence, PROTOCOL_SHA256, COHORT_SHA256)
    output = _write_registry(tmp_path, [claim.model_dump(mode="json") for claim in registry.claims])

    assert len(registry.claims) == 19
    assert registry.claims[0].claim_id == "p3.evaluation.cohort_size"
    collision = next(
        claim
        for claim in registry.claims
        if claim.claim_id == "p3.coalition.collisions.coalition-dropout-latency"
    )
    assert collision.text == "coalition-dropout+latency collisions is 7."
    assert collision.metric_path == "/configurations/0/collisions"
    assert collision.artifact_path == "docs/evidence/study/evaluation.json"
    residual = next(
        claim for claim in registry.claims if "efficiency_max_abs_residual" in claim.claim_id
    )
    assert "2.2e-16" in residual.text
    assert all(claim.evidence_type == "observed" for claim in registry.claims)
    assert all(claim.status == "verified" for claim in registry.claims)
    assert len(load_claims(output)) == 19


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("protocol_sha256", "c" * 64, "protocol hash mismatch"),
        ("cohort_manifest_sha256", "c" * 64, "cohort manifest hash mismatch"),
    ],
)
def test_generation_refuses_each_wrong_provenance_key(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    from aebrisk.analysis.claims import generate_claims

    evidence = _write_evidence(tmp_path)
    path = evidence / "shapley.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document[field] = value
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        generate_claims(evidence, PROTOCOL_SHA256, COHORT_SHA256)


def test_generation_refuses_a_missing_or_non_numeric_analysis_document(tmp_path: Path) -> None:
    from aebrisk.analysis.claims import generate_claims

    evidence = _write_evidence(tmp_path)
    (evidence / "intervals.json").unlink()
    with pytest.raises(ValueError, match="missing published evidence"):
        generate_claims(evidence, PROTOCOL_SHA256, COHORT_SHA256)

    evidence = _write_evidence(tmp_path / "second")
    for path in evidence.glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        for key in ("cohort_size", "common_valid_tokens"):
            document.pop(key)
        path.write_text(json.dumps(document), encoding="utf-8")
    evaluation = json.loads((evidence / "evaluation.json").read_text(encoding="utf-8"))
    evaluation.pop("simulated_seconds")
    evaluation["configurations"] = []
    (evidence / "evaluation.json").write_text(json.dumps(evaluation), encoding="utf-8")
    intervals = json.loads((evidence / "intervals.json").read_text(encoding="utf-8"))
    intervals["intervals"] = {}
    (evidence / "intervals.json").write_text(json.dumps(intervals), encoding="utf-8")
    shapley = json.loads((evidence / "shapley.json").read_text(encoding="utf-8"))
    shapley["metrics"] = {}
    (evidence / "shapley.json").write_text(json.dumps(shapley), encoding="utf-8")
    with pytest.raises(ValueError, match="no numeric claims"):
        generate_claims(evidence, PROTOCOL_SHA256, COHORT_SHA256)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("invalid_json", "invalid published evidence"),
        ("array_root", "root must be an object"),
        ("wrong_schema", "does not declare aeb-evaluation/v1"),
        ("duplicate_ids", "duplicate claim ids"),
    ],
)
def test_generation_refuses_malformed_or_ambiguous_documents(
    tmp_path: Path, case: str, message: str
) -> None:
    from aebrisk.analysis.claims import generate_claims

    evidence = _write_evidence(tmp_path)
    evaluation = evidence / "evaluation.json"
    if case == "invalid_json":
        evaluation.write_text("{nope", encoding="utf-8")
    elif case == "array_root":
        evaluation.write_text("[]", encoding="utf-8")
    else:
        document = json.loads(evaluation.read_text(encoding="utf-8"))
        if case == "wrong_schema":
            document["schema_version"] = "aeb-evaluation/v2"
        else:
            document["configurations"].append(document["configurations"][0])
        evaluation.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        generate_claims(evidence, PROTOCOL_SHA256, COHORT_SHA256)


def test_provenance_and_repository_root_are_required(tmp_path: Path) -> None:
    from aebrisk.analysis.claims import evidence_provenance, generate_claims

    evidence = _write_evidence(tmp_path)
    evaluation = evidence / "evaluation.json"
    document = json.loads(evaluation.read_text(encoding="utf-8"))
    document.pop("cohort_manifest_sha256")
    evaluation.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="must name protocol_sha256 and cohort_manifest_sha256"):
        evidence_provenance(evidence)

    outside = tmp_path / "outside" / "evidence"
    outside.mkdir(parents=True)
    for filename in ("evaluation.json", "intervals.json", "shapley.json", "exclusions.json"):
        (outside / filename).write_text("{}", encoding="utf-8")
    (tmp_path / "pyproject.toml").unlink()
    with pytest.raises(ValueError, match="cannot find repository root"):
        generate_claims(outside, PROTOCOL_SHA256, COHORT_SHA256)


def test_claim_commands_generate_a_registry_and_audit_success_or_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aebrisk.cli.app import app

    evidence = _write_evidence(tmp_path)
    output = tmp_path / "claims.yaml"
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    generated = runner.invoke(
        app, ["generate-claims", "--evidence-dir", str(evidence), "--output", str(output)]
    )
    assert generated.exit_code == 0, generated.output
    assert "generated 19 claims" in generated.output

    clean = runner.invoke(app, ["audit-claims", "--claims", str(output)])
    assert clean.exit_code == 0, clean.output
    assert "0 violations" in clean.output

    registry = yaml.safe_load(output.read_text(encoding="utf-8"))
    registry["claims"][0]["text"] = "Evaluation cohort_size is 9."
    output.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    rejected = runner.invoke(app, ["audit-claims", "--claims", str(output)])
    assert rejected.exit_code == 1
    assert registry["claims"][0]["claim_id"] in rejected.output


def test_generate_command_reports_invalid_evidence(tmp_path: Path) -> None:
    from aebrisk.cli.app import app

    result = CliRunner().invoke(
        app,
        [
            "generate-claims",
            "--evidence-dir",
            str(tmp_path / "missing"),
            "--output",
            str(tmp_path / "claims.yaml"),
        ],
    )
    assert result.exit_code == 1
    assert "generate-claims failed" in result.output
