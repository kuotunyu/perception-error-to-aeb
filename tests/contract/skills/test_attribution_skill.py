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
            "Dropout's Shapley collision_indicator contribution is "
            "-0.0021802325581395357. "
            "<!-- claim: p3.shapley.collision_indicator-values-dropout -->",
            "Oracle AEB recorded 39 collisions and 1095 contacts_not_at_fault. "
            "<!-- claim: p3.baseline.collisions.oracle_aeb --> "
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
                        "text": "Oracle AEB recorded 39 collisions and 1095 contacts_not_at_fault.",
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
        "For the common-valid cohort of 344 scenarios, dropout's Shapley "
        "collision_indicator contribution is -0.0021802325581395357. "
        "<!-- claim: p3.shapley.collision_indicator-values-dropout -->\n"
        "The collisions per 100 km rate is unavailable.\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert violations == ()


def test_an_unknown_claim_id_is_reported(workspace: dict[str, Path]) -> None:
    """A marker cannot create evidence when the registry has no matching claim."""

    document = _write(
        workspace["root"],
        "README.md",
        "Shapley collision_indicator is 0.5. <!-- claim: p3.shapley.unknown -->\n",
    )

    violations, _ = _validate(workspace, documents=(document,))

    assert any("p3.shapley.unknown" in violation for violation in violations)
    assert any("no registry claim" in violation for violation in violations)


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        (
            "rounded.md",
            "Dropout's Shapley collision_indicator contribution is -0.00218. "
            "<!-- claim: p3.shapley.collision_indicator-values-dropout -->\n",
            "holds -0.0021802325581395357",
        ),
        (
            "wrong-source.md",
            "The Shapley collision contribution is 39. "
            "<!-- claim: p3.baseline.collisions.oracle_aeb -->\n",
            "must trace to shapley.json",
        ),
        (
            "mixed.md",
            "Dropout's Shapley contributions are -0.0021802325581395357 for collision "
            "indicator and -0.10376291989664084 seconds of intervention duration. "
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
            "For a common-valid cohort of 343 scenarios, dropout's Shapley "
            "collision_indicator contribution is -0.0021802325581395357. "
            "<!-- claim: p3.shapley.collision_indicator-values-dropout -->\n",
            "common-valid cohort is 344",
        ),
        (
            "oracle.md",
            "Oracle AEB recorded 39 collisions. "
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


def test_exit_one_lists_every_violation_in_one_run(
    workspace: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """A reviewer should repair the whole document after one validator invocation."""

    document = _write(
        workspace["root"],
        "README.md",
        "\n".join(
            [
                "Dropout's Shapley collision_indicator contribution is -0.00218. "
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
