"""Contracts for the ``auditing-aeb-error-attribution`` skill.

The generated claims registry proves its own evidence, but a README sentence
can still round a value, mix two Shapley estimands, change the cohort, or omit
the rear-end contacts that explain the oracle collision count.  These tests
exercise the publication boundary over controlled evidence and require every
failure to remain visible in one validator run.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from aebrisk.analysis.claims import (
    ALLOWED_EVIDENCE_TYPES,
    ALLOWED_STATUSES,
    CLAIM_REQUIRED_FIELDS,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / ".agents" / "skills" / "auditing-aeb-error-attribution"
SKILL_DOC = SKILL_DIR / "SKILL.md"
VALIDATOR = SKILL_DIR / "scripts" / "validate_attribution.py"

PROTOCOL_HASH = "1" * 64
COHORT_HASH = "2" * 64
SHAPLEY_PATH = "docs/evidence/nuplan_aeb_v2/shapley.json"
EVALUATION_PATH = "docs/evidence/nuplan_aeb_v2/evaluation.json"


def _claim(claim_id: str, artifact_path: str, metric_path: str, text: str) -> dict[str, str]:
    return {
        "claim_id": claim_id,
        "text": text,
        "evidence_type": "observed",
        "protocol_hash": PROTOCOL_HASH,
        "cohort_manifest_hash": COHORT_HASH,
        "artifact_path": artifact_path,
        "metric_path": metric_path,
        "status": "verified",
    }


CLAIMS = [
    _claim(
        "p3.shapley.common_valid_tokens",
        SHAPLEY_PATH,
        "/common_valid_tokens",
        "Shapley common_valid_tokens is 344.",
    ),
    _claim(
        "p3.shapley.collision_indicator-values-dropout",
        SHAPLEY_PATH,
        "/metrics/collision_indicator/values/dropout",
        "Shapley collision_indicator values-dropout is -0.0021802325581395357.",
    ),
    _claim(
        "p3.shapley.intervention_duration_s-values-dropout",
        SHAPLEY_PATH,
        "/metrics/intervention_duration_s/values/dropout",
        "Shapley intervention_duration_s values-dropout is -0.10376291989664084.",
    ),
    _claim(
        "p3.baseline.collisions.oracle_aeb",
        EVALUATION_PATH,
        "/configurations/0/collisions",
        "oracle_aeb collisions is 39.",
    ),
    _claim(
        "p3.baseline.contacts_not_at_fault.oracle_aeb",
        EVALUATION_PATH,
        "/configurations/0/contacts_not_at_fault",
        "oracle_aeb contacts_not_at_fault is 1095.",
    ),
]


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path]:
    """Build the smallest repository-shaped evidence set that audits cleanly."""

    shapley = tmp_path / SHAPLEY_PATH
    shapley.parent.mkdir(parents=True)
    shapley.write_text(
        json.dumps(
            {
                "schema_version": "aeb-shapley/v1",
                "protocol_sha256": PROTOCOL_HASH,
                "cohort_manifest_sha256": COHORT_HASH,
                "common_valid_tokens": 344,
                "metrics": {
                    "collision_indicator": {"values": {"dropout": -0.0021802325581395357}},
                    "intervention_duration_s": {"values": {"dropout": -0.10376291989664084}},
                },
            }
        ),
        encoding="utf-8",
    )
    evaluation = tmp_path / EVALUATION_PATH
    evaluation.write_text(
        json.dumps(
            {
                "schema_version": "aeb-evaluation/v1",
                "protocol_sha256": PROTOCOL_HASH,
                "cohort_manifest_sha256": COHORT_HASH,
                "common_valid_tokens": 344,
                "configurations": [
                    {
                        "configuration_id": "oracle_aeb",
                        "collisions": 39,
                        "contacts_not_at_fault": 1095,
                        "collisions_per_100km": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    claims = tmp_path / "docs" / "claims.yaml"
    claims.write_text(
        yaml.safe_dump(
            {
                "allowed_evidence_types": list(ALLOWED_EVIDENCE_TYPES),
                "claim_required_fields": list(CLAIM_REQUIRED_FIELDS),
                "allowed_statuses": list(ALLOWED_STATUSES),
                "claims": CLAIMS,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return {"root": tmp_path, "claims": claims}


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def _module() -> Any:
    return importlib.import_module("aebrisk.analysis.attribution_audit")


def _validate(
    workspace: dict[str, Path],
    *,
    proposal: Path | None = None,
    documents: tuple[Path, ...] = (),
) -> tuple[tuple[str, ...], dict[str, Any]]:
    return _module().validate_attribution(
        workspace["claims"], workspace["root"], proposal, list(documents)
    )


def _clean_markdown() -> str:
    return "\n".join(
        [
            "# Results",
            "Dropout's Shapley `collision_indicator` = -0.0021802325581395357. "
            "<!-- claim: p3.shapley.collision_indicator-values-dropout -->",
            "Oracle AEB recorded `collisions` = 39 "
            "<!-- claim: p3.baseline.collisions.oracle_aeb --> and "
            "`contacts_not_at_fault` = 1095 "
            "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->",
            "",
        ]
    )


def test_the_skill_declares_its_name_and_claim_publication_trigger() -> None:
    """A skill without precise front matter will not activate when results are written."""

    front_matter = yaml.safe_load(SKILL_DOC.read_text(encoding="utf-8").split("---")[1])

    assert front_matter["name"] == "auditing-aeb-error-attribution"
    description = front_matter["description"].lower()
    assert description.startswith("use when")
    assert "shapley" in description
    assert "readme" in description


def test_the_skill_excludes_generic_aeb_explanations() -> None:
    """A conceptual AEB question must not trigger a repository publication audit."""

    text = SKILL_DOC.read_text(encoding="utf-8").lower()

    assert "not for" in text
    assert "how aeb works" in text or "generic" in text


def test_the_skill_routes_results_through_its_validator() -> None:
    """The mechanical gate and marker must be discoverable from the skill."""

    text = SKILL_DOC.read_text(encoding="utf-8")

    assert "validate_attribution.py" in text
    assert "<!-- claim:" in text
    assert VALIDATOR.is_file()


def test_a_clean_markdown_document_is_accepted_with_exact_traces(
    workspace: dict[str, Path],
) -> None:
    """Separate exact estimands and the oracle context form a publishable document."""

    document = _write(workspace["root"], "README.md", _clean_markdown())

    violations, status = _validate(workspace, documents=(document,))

    assert violations == ()
    assert status["validator"] == "validate_attribution"
    assert {trace["claim_id"] for trace in status["statements"]} == {
        "p3.shapley.collision_indicator-values-dropout",
        "p3.baseline.collisions.oracle_aeb",
        "p3.baseline.contacts_not_at_fault.oracle_aeb",
    }
    assert all(trace["verdict"] == "pass" for trace in status["statements"])


def test_a_clean_yaml_proposal_is_accepted(workspace: dict[str, Path]) -> None:
    """Pre-publication prose gets the same audit before it reaches Markdown."""

    proposal = _write(
        workspace["root"],
        "proposal.yaml",
        yaml.safe_dump(
            {
                "proposals": [
                    {
                        "claim_ids": [
                            "p3.baseline.collisions.oracle_aeb",
                            "p3.baseline.contacts_not_at_fault.oracle_aeb",
                        ],
                        "text": "Oracle AEB recorded `collisions` = 39 and "
                        "`contacts_not_at_fault` = 1095.",
                    }
                ]
            }
        ),
    )

    violations, status = _validate(workspace, proposal=proposal)

    assert violations == ()
    assert len(status["statements"]) == 2


def test_the_exact_common_cohort_and_an_unavailable_distance_rate_are_accepted(
    workspace: dict[str, Path],
) -> None:
    """Provenance may state the true cohort and explain that an absent rate is absent."""

    document = _write(
        workspace["root"],
        "README.md",
        "The common-valid cohort has `common_valid_tokens` = 344. "
        "<!-- claim: p3.shapley.common_valid_tokens -->\n"
        "Dropout's Shapley `collision_indicator` = -0.0021802325581395357. "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout -->\n"
        "The collisions per 100 km rate is unavailable.\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "Dropout Shapley `collision_indicator` = -0.10376291989664084. "
            "<!-- claim: p3.shapley.intervention_duration_s-values-dropout -->",
            "binds collision_indicator to intervention_duration_s",
        ),
        (
            "Dropout Shapley `collision_indicator` = -0.0021802325581395357%. "
            "<!-- claim: p3.shapley.collision_indicator-values-dropout -->",
            "percent conversion is not registered",
        ),
        (
            "Dropout Shapley `collision_indicator` = 344. "
            "<!-- claim: p3.shapley.collision_indicator-values-dropout -->",
            "holds -0.0021802325581395357",
        ),
        (
            "Oracle AEB recorded `collisions` = 1095 "
            "<!-- claim: p3.baseline.collisions.oracle_aeb --> and "
            "`contacts_not_at_fault` = 39 "
            "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->.",
            "collisions claim holds 39",
        ),
    ],
)
def test_a_value_is_bound_to_the_metric_and_unit_of_its_own_claim(
    workspace: dict[str, Path], text: str, message: str
) -> None:
    """A union of cited numbers cannot authorize a swap, cohort value, or conversion."""

    document = _write(workspace["root"], "README.md", text)

    violations, _ = _validate(workspace, documents=(document,))

    assert any(message in violation for violation in violations), violations


def test_a_spaced_percent_suffix_is_still_a_unit_conversion(
    workspace: dict[str, Path],
) -> None:
    """Whitespace cannot detach an incompatible percent unit from its binding."""

    document = _write(
        workspace["root"],
        "README.md",
        "Dropout Shapley `collision_indicator` = -0.0021802325581395357 %. "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("percent conversion is not registered" in item for item in violations)


def test_clean_metric_value_bindings_are_accepted(workspace: dict[str, Path]) -> None:
    """Each exact value remains publishable when paired with its own metric and claim."""

    document = _write(
        workspace["root"],
        "README.md",
        "Dropout Shapley `collision_indicator` = -0.0021802325581395357. "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout -->\n"
        "Dropout Shapley `intervention_duration_s` = -0.10376291989664084. "
        "<!-- claim: p3.shapley.intervention_duration_s-values-dropout -->\n"
        "The common-valid cohort has `common_valid_tokens` = 344. "
        "<!-- claim: p3.shapley.common_valid_tokens -->\n"
        "Oracle AEB recorded `collisions` = 39 "
        "<!-- claim: p3.baseline.collisions.oracle_aeb --> and "
        "`contacts_not_at_fault` = 1095 "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->.\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ()


def test_an_unbound_extra_number_fails_closed(workspace: dict[str, Path]) -> None:
    """A valid binding cannot authorize another number elsewhere in the result."""

    document = _write(
        workspace["root"],
        "README.md",
        "Dropout Shapley `collision_indicator` = -0.0021802325581395357 over 1032 runs. "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("every numeric result needs a metric binding" in item for item in violations)


def test_a_cohort_only_result_line_is_discovered(workspace: dict[str, Path]) -> None:
    """Cohort prose is a numeric result even when it names no safety metric."""

    document = _write(
        workspace["root"], "README.md", "The common-valid cohort contains 1032 scenarios.\n"
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("no <!-- claim: ... --> marker" in violation for violation in violations)
    assert any("common-valid cohort is 344" in violation for violation in violations)


@pytest.mark.parametrize(
    "text",
    [
        "`common_valid_tokens` = 1032.\n",
        "\u5171\u540c\u6709\u6548\u6a23\u672c\u70ba 1032 \u500b\u60c5\u5883\u3002\n",
    ],
)
def test_supported_cohort_contexts_do_not_silently_disappear(
    workspace: dict[str, Path], text: str
) -> None:
    """Stable keys and the supported Chinese cohort phrase activate the audit."""

    document = _write(workspace["root"], "README.md", text)

    violations, _ = _validate(workspace, documents=(document,))

    assert any("no <!-- claim: ... --> marker" in item for item in violations)
    assert any("common-valid cohort is 344" in item for item in violations)


def test_a_numeric_row_under_a_result_table_header_is_discovered(
    workspace: dict[str, Path],
) -> None:
    """A Markdown table does not hide a numeric result from marker enforcement."""

    document = _write(
        workspace["root"],
        "README.md",
        "| Channel | Shapley collision_indicator |\n| --- | ---: |\n| Dropout | 999 |\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("README.md:3" in violation for violation in violations)
    assert any("no <!-- claim: ... --> marker" in violation for violation in violations)


def test_a_table_row_with_constrained_binding_is_accepted(workspace: dict[str, Path]) -> None:
    """Tables remain available when the result cell carries the same auditable shape."""

    document = _write(
        workspace["root"],
        "README.md",
        "| Channel | Shapley result |\n"
        "| --- | --- |\n"
        "| Dropout | `collision_indicator` = -0.0021802325581395357 "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout --> |\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ()


def test_a_pipe_optional_result_table_is_discovered(workspace: dict[str, Path]) -> None:
    """Ordinary Markdown tables receive the same fail-closed row discovery."""

    document = _write(
        workspace["root"],
        "README.md",
        "Channel | Shapley collision_indicator\n--- | ---\nDropout | 999\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("README.md:3" in item for item in violations)
    assert any("no <!-- claim: ... --> marker" in item for item in violations)


def test_a_pipe_optional_table_with_a_constrained_binding_is_accepted(
    workspace: dict[str, Path],
) -> None:
    """The supported table form stays usable with an exact binding and marker."""

    document = _write(
        workspace["root"],
        "README.md",
        "Channel | Shapley result\n"
        "--- | ---\n"
        "Dropout | `collision_indicator` = -0.0021802325581395357 "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ()


def test_marker_identity_detects_an_unlike_estimand_comparison(
    workspace: dict[str, Path],
) -> None:
    """Removing the word Shapley cannot make seconds comparable with a proportion."""

    document = _write(
        workspace["root"],
        "README.md",
        "Dropout contributed `collision_indicator` = -0.0021802325581395357 "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout --> and "
        "`intervention_duration_s` = -0.10376291989664084 "
        "<!-- claim: p3.shapley.intervention_duration_s-values-dropout -->; "
        "the latter is larger.\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("separate estimands" in violation for violation in violations)


def test_oracle_contact_marker_without_the_contact_value_is_rejected(
    workspace: dict[str, Path],
) -> None:
    """Citing the contact claim cannot replace stating its exact associated count."""

    document = _write(
        workspace["root"],
        "README.md",
        "Oracle AEB recorded `collisions` = 39 "
        "<!-- claim: p3.baseline.collisions.oracle_aeb -->; "
        "contacts_not_at_fault were excluded "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->.\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("must state contacts_not_at_fault = 1095" in violation for violation in violations)


def test_oracle_claim_identity_requires_contact_context_without_oracle_prose(
    workspace: dict[str, Path],
) -> None:
    """The registry identity activates the obligation even when prose says baseline."""

    document = _write(
        workspace["root"],
        "README.md",
        "The baseline recorded `collisions` = 39. "
        "<!-- claim: p3.baseline.collisions.oracle_aeb -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("must also state contacts_not_at_fault" in item for item in violations)


def test_oracle_claim_identity_accepts_both_exact_bound_counts(
    workspace: dict[str, Path],
) -> None:
    """Claim-derived obligations do not require a literal Oracle phrase."""

    document = _write(
        workspace["root"],
        "README.md",
        "The baseline recorded `collisions` = 39 "
        "<!-- claim: p3.baseline.collisions.oracle_aeb --> and "
        "`contacts_not_at_fault` = 1095 "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->.\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ()


def test_oracle_contact_expectation_comes_from_its_claim(
    workspace: dict[str, Path],
) -> None:
    """The validator resolves contact evidence instead of embedding the release value."""

    evaluation_path = workspace["root"] / EVALUATION_PATH
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["configurations"][0]["contacts_not_at_fault"] = 42
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    registry = yaml.safe_load(workspace["claims"].read_text(encoding="utf-8"))
    contact_claim = next(
        claim
        for claim in registry["claims"]
        if claim["claim_id"] == "p3.baseline.contacts_not_at_fault.oracle_aeb"
    )
    contact_claim["text"] = "oracle_aeb contacts_not_at_fault is 42."
    workspace["claims"].write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    document = _write(
        workspace["root"],
        "README.md",
        "Oracle AEB recorded `collisions` = 39 "
        "<!-- claim: p3.baseline.collisions.oracle_aeb --> and "
        "`contacts_not_at_fault` = 42 "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->.\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ()


def test_unavailable_distance_rate_is_allowed_beside_valid_counts(
    workspace: dict[str, Path],
) -> None:
    """The literal denominator in an unavailable-rate clause is not a claimed rate."""

    document = _write(
        workspace["root"],
        "README.md",
        "Oracle AEB recorded `collisions` = 39 "
        "<!-- claim: p3.baseline.collisions.oracle_aeb --> and "
        "`contacts_not_at_fault` = 1095 "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->; "
        "collisions per 100 km were unavailable.\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ()


def test_a_chinese_numeric_per_distance_unit_is_rejected(
    workspace: dict[str, Path],
) -> None:
    """The Chinese denominator form cannot turn a raw collision count into a rate."""

    document = _write(
        workspace["root"],
        "README.md",
        "Oracle AEB: `collisions` = 39 \u6bcf 100 km. "
        "<!-- claim: p3.baseline.collisions.oracle_aeb --> "
        "`contacts_not_at_fault` = 1095. "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("per-100 km rate is unpublished" in item for item in violations)


def test_traditional_chinese_prose_uses_the_same_metric_value_contract(
    workspace: dict[str, Path],
) -> None:
    """The contract depends on stable metric keys, not the surrounding language."""

    document = _write(
        workspace["root"],
        "README.md",
        "共同有效樣本為 `common_valid_tokens` = 344。"
        "<!-- claim: p3.shapley.common_valid_tokens -->\n"
        "Oracle AEB 的 `collisions` = 39 "
        "<!-- claim: p3.baseline.collisions.oracle_aeb -->, "
        "`contacts_not_at_fault` = 1095 "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->; "
        "每 100 km 碰撞率未提供。\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ()


def test_an_unknown_claim_id_is_reported(workspace: dict[str, Path]) -> None:
    """A marker cannot create evidence when the registry has no matching claim."""

    document = _write(
        workspace["root"],
        "README.md",
        "Shapley `collision_indicator` = 0.5. <!-- claim: p3.shapley.unknown -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("p3.shapley.unknown" in violation for violation in violations)
    assert any("no registry claim" in violation for violation in violations)


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        (
            "rounded.md",
            "Dropout's Shapley `collision_indicator` = -0.00218. "
            "<!-- claim: p3.shapley.collision_indicator-values-dropout -->\n",
            "holds -0.0021802325581395357",
        ),
        (
            "wrong-source.md",
            "The Shapley `collisions` = 39. <!-- claim: p3.baseline.collisions.oracle_aeb -->\n",
            "must trace to shapley.json",
        ),
        (
            "mixed.md",
            "Dropout's Shapley `collision_indicator` = -0.0021802325581395357 and "
            "`intervention_duration_s` = -0.10376291989664084. "
            "<!-- claim: p3.shapley.collision_indicator-values-dropout --> "
            "<!-- claim: p3.shapley.intervention_duration_s-values-dropout -->\n",
            "separate estimands",
        ),
        (
            "distance.md",
            "Oracle AEB had 12.3 collisions per 100 km. "
            "<!-- claim: p3.baseline.collisions.oracle_aeb -->\n",
            "per-100 km rate is unpublished",
        ),
        (
            "cohort.md",
            "For the common-valid cohort, `common_valid_tokens` = 343. "
            "<!-- claim: p3.shapley.common_valid_tokens -->\n",
            "common-valid cohort is 344",
        ),
        (
            "oracle.md",
            "Oracle AEB recorded `collisions` = 39. "
            "<!-- claim: p3.baseline.collisions.oracle_aeb -->\n",
            "must also state contacts_not_at_fault",
        ),
        (
            "unmarked.md",
            "Dropout's Shapley collision_indicator contribution is -0.0021802325581395357.\n",
            "no <!-- claim: ... --> marker",
        ),
    ],
)
def test_each_publication_violation_is_rejected(
    workspace: dict[str, Path], name: str, text: str, message: str
) -> None:
    """Each named contract failure is independently observable, not a blanket refusal."""

    document = _write(workspace["root"], name, text)

    violations, _ = _validate(workspace, documents=(document,))

    assert any(message in violation for violation in violations), violations


def test_a_declared_rounded_binding_is_recomputed_from_its_claim(
    workspace: dict[str, Path],
) -> None:
    """A shorter display stays traceable when the marker declares its precision."""

    document = _write(
        workspace["root"],
        "README.md",
        "Dropout's Shapley `collision_indicator` = -0.00218. "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout; rounded: 5 -->\n"
        "Oracle AEB recorded `collisions` = 39 "
        "<!-- claim: p3.baseline.collisions.oracle_aeb; rounded: 0 --> and "
        "`contacts_not_at_fault` = 1095 "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->\n",
    )

    violations, status = _validate(workspace, documents=(document,))

    assert violations == ()
    rounding = {
        trace["claim_id"]: trace.get("rounded_decimal_places") for trace in status["statements"]
    }
    assert rounding == {
        "p3.shapley.collision_indicator-values-dropout": 5,
        "p3.baseline.collisions.oracle_aeb": 0,
        "p3.baseline.contacts_not_at_fault.oracle_aeb": None,
    }


@pytest.mark.parametrize(
    ("display", "places"),
    [
        ("-0.00219", "5"),
        ("-0.002180", "5"),
        ("-2.18e-3", "5"),
        ("-0.0022", "5"),
        ("-0.00218", "4"),
    ],
)
def test_a_declared_rounded_binding_must_be_the_canonical_rounding(
    workspace: dict[str, Path], display: str, places: str
) -> None:
    """A wrong digit, a padded or E-notation spelling, or a mismatched precision fails."""

    document = _write(
        workspace["root"],
        "README.md",
        f"Dropout's Shapley `collision_indicator` = {display}. "
        f"<!-- claim: p3.shapley.collision_indicator-values-dropout; rounded: {places} -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("rounds half to even to" in violation for violation in violations), violations


@pytest.mark.parametrize(
    "marker",
    [
        "<!-- claim: p3.shapley.collision_indicator-values-dropout; rounded: 10 -->",
        "<!-- claim: p3.shapley.collision_indicator-values-dropout; rounded: x -->",
        "<!-- claim: p3.shapley.collision_indicator-values-dropout rounded 5 -->",
    ],
)
def test_a_malformed_claim_marker_is_rejected(workspace: dict[str, Path], marker: str) -> None:
    """A marker that does not parse cannot silently authorize or hide a value."""

    document = _write(
        workspace["root"],
        "README.md",
        f"Dropout's Shapley `collision_indicator` = -0.00218. {marker}\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("invalid claim marker" in violation for violation in violations), violations


def test_a_malformed_marker_is_found_on_a_line_with_no_result(
    workspace: dict[str, Path],
) -> None:
    """Prose without a metric word or number still cannot carry a broken marker."""

    document = _write(
        workspace["root"],
        "README.md",
        "See the figure below. <!-- claim: p3.shapley.common_valid_tokens; rounded: -1 -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("README.md:1: invalid claim marker" in item for item in violations), violations


def test_a_malformed_marker_cannot_hide_beside_a_valid_marker(
    workspace: dict[str, Path],
) -> None:
    """Every claim-like comment on a marked line must parse, including a later one."""

    document = _write(
        workspace["root"],
        "README.md",
        "Dropout's Shapley `collision_indicator` = -0.0021802325581395357. "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout --> "
        "<!-- claim: p3.shapley.intervention_duration_s-values-dropout; rounded: 99 -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("invalid claim marker" in violation for violation in violations), violations


def test_a_proposal_may_carry_its_well_formed_markers(workspace: dict[str, Path]) -> None:
    """A drafted README line pasted with its markers is audited, not refused as malformed."""

    proposal = _write(
        workspace["root"],
        "proposal.yaml",
        yaml.safe_dump(
            {
                "proposals": [
                    {
                        "claim_ids": [
                            "p3.baseline.collisions.oracle_aeb",
                            "p3.baseline.contacts_not_at_fault.oracle_aeb",
                        ],
                        "text": "Oracle AEB recorded `collisions` = 39 "
                        "<!-- claim: p3.baseline.collisions.oracle_aeb --> and "
                        "`contacts_not_at_fault` = 1095. "
                        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->",
                    }
                ]
            }
        ),
    )

    violations, status = _validate(workspace, proposal=proposal)

    assert violations == ()
    assert len(status["statements"]) == 2


def test_a_malformed_marker_in_a_proposal_is_still_rejected(workspace: dict[str, Path]) -> None:
    """Only the well-formed markers are exempt; a broken one in a proposal still fails."""

    proposal = _write(
        workspace["root"],
        "proposal.yaml",
        yaml.safe_dump(
            {
                "proposals": [
                    {
                        "claim_ids": ["p3.shapley.collision_indicator-values-dropout"],
                        "text": "Dropout's Shapley `collision_indicator` = -0.00218. "
                        "<!-- claim: p3.shapley.collision_indicator-values-dropout; "
                        "rounded: 10 -->",
                    }
                ]
            }
        ),
    )

    violations, _ = _validate(workspace, proposal=proposal)

    assert any("proposal[0]: invalid claim marker" in item for item in violations), violations


def test_exit_one_lists_every_violation_in_one_run(
    workspace: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """A reviewer should repair the whole document after one validator invocation."""

    document = _write(
        workspace["root"],
        "README.md",
        "\n".join(
            [
                "Dropout's Shapley `collision_indicator` = -0.00218. "
                "<!-- claim: p3.shapley.collision_indicator-values-dropout -->",
                "Oracle AEB had 12.3 collisions per 100 km. "
                "<!-- claim: p3.baseline.collisions.oracle_aeb -->",
                "For a common-valid cohort of 343 scenarios, collision_indicator is 0.1.",
                "",
            ]
        ),
    )

    exit_code = _module().main(
        [
            "--claims",
            str(workspace["claims"]),
            "--repo-root",
            str(workspace["root"]),
            "--document",
            str(document),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    for message in (
        "holds -0.0021802325581395357",
        "per-100 km rate is unpublished",
        "must also state contacts_not_at_fault",
        "common-valid cohort is 344",
        "no <!-- claim: ... --> marker",
    ):
        assert message in captured.err


def test_fenced_examples_are_not_result_lines(workspace: dict[str, Path]) -> None:
    """A JSON or command example must not be mistaken for a publication claim."""

    document = _write(
        workspace["root"],
        "example.md",
        '```json\n{"collision_indicator": 0.5}\n```\n',
    )

    violations, status = _validate(workspace, documents=(document,))

    assert violations == ()
    assert status["statements"] == []


def test_malformed_proposals_fail_as_unchecked_input(workspace: dict[str, Path]) -> None:
    """An entry without text and claim IDs cannot disappear from the audit."""

    proposal = _write(
        workspace["root"], "proposal.yaml", yaml.safe_dump({"proposals": [{"text": 39}]})
    )

    violations, _ = _validate(workspace, proposal=proposal)

    assert any("needs text and claim_ids" in violation for violation in violations)


def test_a_proposal_with_text_but_no_claim_ids_is_rejected(workspace: dict[str, Path]) -> None:
    """Visible prose does not replace the structured evidence link."""

    proposal = _write(
        workspace["root"],
        "proposal.yaml",
        yaml.safe_dump({"proposals": [{"text": "Oracle AEB recorded 39 collisions."}]}),
    )

    violations, _ = _validate(workspace, proposal=proposal)

    assert any("needs text and claim_ids" in violation for violation in violations)


def test_a_proposal_without_a_proposals_list_is_exit_two(
    workspace: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed YAML root means no proposal was checked."""

    proposal = _write(workspace["root"], "proposal.yaml", "proposals: not-a-list\n")

    exit_code = _module().main(
        [
            "--claims",
            str(workspace["claims"]),
            "--repo-root",
            str(workspace["root"]),
            "--proposal",
            str(proposal),
        ]
    )

    assert exit_code == 2
    assert "expected a list" in capsys.readouterr().err


def test_no_input_is_exit_two(
    workspace: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty invocation checked nothing and therefore cannot be called a pass."""

    exit_code = _module().main(
        ["--claims", str(workspace["claims"]), "--repo-root", str(workspace["root"])]
    )

    assert exit_code == 2
    assert "nothing to audit" in capsys.readouterr().err


def test_an_unreadable_registry_is_exit_two(
    workspace: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Failure to load evidence cannot be mistaken for publication approval."""

    document = _write(workspace["root"], "README.md", _clean_markdown())

    exit_code = _module().main(
        [
            "--claims",
            str(workspace["root"] / "missing.yaml"),
            "--repo-root",
            str(workspace["root"]),
            "--document",
            str(document),
        ]
    )

    assert exit_code == 2
    assert "validation could not run" in capsys.readouterr().err


def test_a_clean_main_run_prints_json_trace(
    workspace: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The command's successful output is durable machine-readable evidence."""

    document = _write(workspace["root"], "README.md", _clean_markdown())

    exit_code = _module().main(
        [
            "--claims",
            str(workspace["claims"]),
            "--repo-root",
            str(workspace["root"]),
            "--document",
            str(document),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["validator"] == "validate_attribution"


def test_the_thin_entry_point_exposes_every_input_option() -> None:
    """The checked command must be reproducible without importing package internals."""

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for option in ("--claims", "--repo-root", "--proposal", "--document"):
        assert option in result.stdout


# --------------------------------------------------------------------------
# The registries of the policy v2 study and of the post-hoc addendum
# --------------------------------------------------------------------------

STUDY_PREFIX = "p3.study.policy-v2."
ADDENDUM_PREFIX = "p3.posthoc.v1-addendum."
ADDENDUM_PATH = "docs/posthoc/nuplan_aeb_v2-addendum/evidence/attribution-addendum-evidence.json"
POLICY_PATH = "docs/studies/aeb-policy-v2/evidence/policy-v2-evidence.json"
GATES_PATH = "docs/studies/aeb-policy-v2/evidence/gates.json"
ATTEMPT_PATH = "docs/studies/aeb-policy-v2/evidence/gates-attempt-2.json"
ADDENDUM_SHAPLEY = _claim(
    f"{ADDENDUM_PREFIX}shapley.collision_indicator.dropout.estimate",
    ADDENDUM_PATH,
    "/games/1/shapley_values/dropout/estimate",
    "Shapley value of dropout in the collision_indicator game: estimate is -0.0022.",
)
POLICY_ESTIMATE = _claim(
    f"{STUDY_PREFIX}hypotheses.h1.contrast-estimate",
    POLICY_PATH,
    "/hypotheses/0/contrast/estimate",
    "H1: contrast estimate is -0.0022.",
)
GATE_COUNT = _claim(
    f"{STUDY_PREFIX}gates.g2.dropout-medium",
    GATES_PATH,
    "/gates/0/counts/dropout-medium",
    "G2 counted 3 for dropout-medium.",
)


def _evidence(root: Path, relative: str, **fields: Any) -> None:
    """Write one evidence document under the workspace's protocol and cohort."""

    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"protocol_sha256": PROTOCOL_HASH, "cohort_manifest_sha256": COHORT_HASH, **fields}
        ),
        encoding="utf-8",
    )


def _registry(workspace: dict[str, Path], claims: list[dict[str, str]]) -> None:
    """Replace the workspace's registry with `claims`, and write the evidence they cite."""

    root = workspace["root"]
    _evidence(
        root,
        ADDENDUM_PATH,
        common_valid_tokens=344,
        games=[{}, {"shapley_values": {"dropout": {"estimate": -0.0022}}}],
    )
    _evidence(
        root,
        POLICY_PATH,
        common_valid_tokens=344,
        hypotheses=[{"contrast": {"estimate": -0.0022}}],
    )
    gate_file = {"gates": [{"gate": "G2", "passed": False, "counts": {"dropout-medium": 3}}]}
    _evidence(root, GATES_PATH, **gate_file)
    _evidence(root, ATTEMPT_PATH, **gate_file)
    workspace["claims"].write_text(
        yaml.safe_dump(
            {
                "allowed_evidence_types": list(ALLOWED_EVIDENCE_TYPES),
                "claim_required_fields": list(CLAIM_REQUIRED_FIELDS),
                "allowed_statuses": list(ALLOWED_STATUSES),
                "claims": claims,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _renamed(claim: dict[str, str], claim_id: str, **fields: str) -> dict[str, str]:
    return {**claim, "claim_id": claim_id, **fields}


def test_a_registry_without_shapley_json_reads_its_cohort_from_its_other_evidence(
    workspace: dict[str, Path],
) -> None:
    """A registry that cites no Shapley value still resolves its common-valid cohort."""

    _registry(
        workspace,
        [
            _claim(
                "p3.evaluation.common_valid_tokens",
                EVALUATION_PATH,
                "/common_valid_tokens",
                "Evaluation common_valid_tokens is 344.",
            ),
            *CLAIMS[3:],
        ],
    )
    document = _write(
        workspace["root"],
        "README.md",
        "The common-valid cohort has `common_valid_tokens` = 344. "
        "<!-- claim: p3.evaluation.common_valid_tokens -->\n"
        "Oracle AEB recorded `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> "
        "and `contacts_not_at_fault` = 1095 "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->\n",
    )

    violations, status = _validate(workspace, documents=(document,))

    assert violations == ()
    assert status["common_valid_tokens"] == 344


def test_a_registry_whose_evidence_states_no_cohort_flags_any_stated_cohort(
    workspace: dict[str, Path],
) -> None:
    """A gate-only registry has no cohort, so a page bound to it may state none."""

    _registry(workspace, [GATE_COUNT])
    bound = _write(
        workspace["root"],
        "bound.md",
        f"G2 found `dropout_medium` = 3 <!-- claim: {GATE_COUNT['claim_id']} --> differing.\n",
    )
    stated = _write(workspace["root"], "stated.md", "The cohort of 344 tokens was not analysed.\n")

    violations, status = _validate(workspace, documents=(bound,))

    assert violations == ()
    assert status["common_valid_tokens"] is None

    violations, _ = _validate(workspace, documents=(stated,))

    assert (
        "stated.md:1: stated cohort 344, but no evidence of this registry records a "
        "common-valid cohort"
    ) in violations


def test_registry_evidence_that_states_two_cohorts_stops_the_audit(
    workspace: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Two artifacts that disagree on the cohort leave no denominator to check against."""

    evaluation_path = workspace["root"] / EVALUATION_PATH
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["common_valid_tokens"] = 343
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    document = _write(workspace["root"], "README.md", _clean_markdown())

    with pytest.raises(ValueError, match="different common-valid cohorts"):
        _validate(workspace, documents=(document,))

    exit_code = _module().main(
        [
            "--claims",
            str(workspace["claims"]),
            "--repo-root",
            str(workspace["root"]),
            "--document",
            str(document),
        ]
    )

    assert exit_code == 2
    assert "validation could not run" in capsys.readouterr().err


def test_a_shapley_number_may_trace_to_the_addendum_evidence_in_the_addendum_registry_only(
    workspace: dict[str, Path],
) -> None:
    """The addendum's own Shapley values are a source in its registry, and in no other."""

    line = "Dropout's Shapley `estimate` = -0.0022 <!-- claim: {} -->\n"
    _registry(workspace, [ADDENDUM_SHAPLEY])
    document = _write(workspace["root"], "results.md", line.format(ADDENDUM_SHAPLEY["claim_id"]))

    violations, status = _validate(workspace, documents=(document,))

    assert violations == ()
    assert status["common_valid_tokens"] == 344

    released_id = "p3.addendum-copy.shapley.collision_indicator.dropout"
    _registry(workspace, [*CLAIMS, _renamed(ADDENDUM_SHAPLEY, released_id)])
    document = _write(workspace["root"], "results.md", line.format(released_id))

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ("results.md:1: every Shapley number must trace to shapley.json",)


def test_a_shapley_number_traced_to_the_policy_v2_evidence_fails(
    workspace: dict[str, Path],
) -> None:
    """The study's evidence holds no Shapley value, so the released rule applies unchanged."""

    _registry(workspace, [POLICY_ESTIMATE])
    document = _write(
        workspace["root"],
        "results.md",
        f"The Shapley `estimate` = -0.0022 <!-- claim: {POLICY_ESTIMATE['claim_id']} -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ("results.md:1: every Shapley number must trace to shapley.json",)


def test_the_addendum_registry_names_both_shapley_sources(workspace: dict[str, Path]) -> None:
    """In the addendum's registry a Shapley line may cite either source, and the rule names both."""

    oracle = _renamed(CLAIMS[3], f"{ADDENDUM_PREFIX}baseline.collisions.oracle_aeb")
    contact = _renamed(CLAIMS[4], f"{ADDENDUM_PREFIX}baseline.contacts_not_at_fault.oracle_aeb")
    _registry(workspace, [ADDENDUM_SHAPLEY, oracle, contact])
    document = _write(
        workspace["root"],
        "results.md",
        f"Unlike its Shapley values, oracle AEB recorded `collisions` = 39 "
        f"<!-- claim: {oracle['claim_id']} --> and `contacts_not_at_fault` = 1095 "
        f"<!-- claim: {contact['claim_id']} -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == (
        "results.md:1: every Shapley number must trace to shapley.json or "
        "attribution-addendum-evidence.json",
    )


def test_duplicate_claim_ids_are_refused(workspace: dict[str, Path]) -> None:
    """One id for two claims would let a marker cite whichever the registry lists last."""

    _registry(workspace, [*CLAIMS, CLAIMS[0]])
    document = _write(workspace["root"], "README.md", _clean_markdown())

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ("registry: claim id p3.shapley.common_valid_tokens appears 2 times",)


@pytest.mark.parametrize(
    ("claims", "message"),
    [
        (
            [POLICY_ESTIMATE, ADDENDUM_SHAPLEY],
            "registry: claim ids begin with both p3.study.policy-v2. and "
            "p3.posthoc.v1-addendum.; the study and the addendum each keep their own registry",
        ),
        (
            [ADDENDUM_SHAPLEY, CLAIMS[0]],
            "registry: every claim id of a registry that uses p3.posthoc.v1-addendum. must "
            "begin with it; ids that do not: 1, such as p3.shapley.common_valid_tokens",
        ),
    ],
    ids=["the-other-part", "the-released-registry"],
)
def test_ids_with_another_prefix_than_the_registrys_part_are_refused(
    workspace: dict[str, Path], claims: list[dict[str, str]], message: str
) -> None:
    """A part's registry holds only its own ids; a mixed one keeps the released Shapley rule."""

    _registry(workspace, claims)
    document = _write(
        workspace["root"],
        "results.md",
        f"Dropout's Shapley `estimate` = -0.0022 <!-- claim: {ADDENDUM_SHAPLEY['claim_id']} -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == (
        message,
        "results.md:1: every Shapley number must trace to shapley.json",
    )


def test_a_gate_count_binds_under_its_key_spelled_with_underscores(
    workspace: dict[str, Path],
) -> None:
    """A cell such as `dropout-medium` is no metric key, so its count binds as `dropout_medium`."""

    earlier = _renamed(
        GATE_COUNT, f"{STUDY_PREFIX}attempt-2.g2.dropout-medium", artifact_path=ATTEMPT_PATH
    )
    _registry(workspace, [GATE_COUNT, earlier])
    bound = _write(
        workspace["root"],
        "bound.md",
        f"G2 found `dropout_medium` = 3 <!-- claim: {GATE_COUNT['claim_id']} --> and before "
        f"`dropout_medium` = 3 <!-- claim: {earlier['claim_id']} -->\n",
    )
    raw = _write(
        workspace["root"],
        "raw.md",
        f"G2 found `dropout-medium` = 3 <!-- claim: {GATE_COUNT['claim_id']} -->\n",
    )

    violations, _ = _validate(workspace, documents=(bound,))

    assert violations == ()

    violations, _ = _validate(workspace, documents=(raw,))

    assert any("unsupported result syntax" in violation for violation in violations)


def test_the_skill_reads_the_cohort_from_the_registrys_evidence() -> None:
    """Step 5 names every registry's evidence, not only the released Shapley values."""

    text = " ".join(SKILL_DOC.read_text(encoding="utf-8").split())

    assert "Read it from the registry's evidence that carries `common_valid_tokens`" in text
    assert "Read it from `shapley.json`" not in text
