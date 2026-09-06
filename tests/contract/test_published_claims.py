"""The committed claim registry is generated, auditable, and non-vacuous."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "evidence" / "nuplan_aeb_v2"


def test_the_committed_registry_audits_clean() -> None:
    from aebrisk.analysis.claims import audit_claims

    assert audit_claims(ROOT / "docs" / "claims.yaml", ROOT) == ()


def test_the_audit_is_not_vacuous(tmp_path: Path) -> None:
    """Corrupt one digit of one claim and the audit must say so."""

    from aebrisk.analysis.claims import audit_claims

    registry = yaml.safe_load((ROOT / "docs" / "claims.yaml").read_text(encoding="utf-8"))
    claim = registry["claims"][0]
    digit = next(ch for ch in claim["text"] if ch.isdigit())
    claim["text"] = claim["text"].replace(digit, "9" if digit != "9" else "8", 1)
    corrupted = tmp_path / "claims.yaml"
    corrupted.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    violations = audit_claims(corrupted, ROOT)

    assert violations and claim["claim_id"] in violations[0]


def test_the_committed_registry_is_exactly_the_generated_registry() -> None:
    from aebrisk.analysis.claims import generate_claims, load_registry

    evidence = json.loads((EVIDENCE / "evaluation.json").read_text(encoding="utf-8"))
    generated = generate_claims(
        EVIDENCE,
        evidence["protocol_sha256"],
        evidence["cohort_manifest_sha256"],
    )

    assert load_registry(ROOT / "docs" / "claims.yaml") == generated
