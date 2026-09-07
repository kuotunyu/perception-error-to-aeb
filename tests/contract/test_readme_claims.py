"""The actual release prose must remain bound to the frozen evidence registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from aebrisk.analysis.attribution_audit import validate_attribution

ROOT = Path(__file__).parents[2]
DOCUMENTS = (ROOT / "README.md", ROOT / "README.en.md", ROOT / "docs/release-notes/v1.0.0.md")


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_actual_release_document_passes_the_attribution_audit(document: Path) -> None:
    violations, _ = validate_attribution(ROOT / "docs/claims.yaml", ROOT, None, [document])

    assert violations == ()


def test_actual_readme_with_one_changed_result_is_refused(tmp_path: Path) -> None:
    changed = tmp_path / "changed.md"
    changed.write_text(
        (ROOT / "README.en.md")
        .read_text(encoding="utf-8")
        .replace("`common_valid_tokens` = 344", "`common_valid_tokens` = 343", 1),
        encoding="utf-8",
    )

    violations, _ = validate_attribution(ROOT / "docs/claims.yaml", ROOT, None, [changed])

    assert any("common-valid cohort is 344" in violation for violation in violations)


def test_actual_readme_without_the_oracle_contact_claim_is_refused(tmp_path: Path) -> None:
    changed = tmp_path / "missing-contact.md"
    text = (ROOT / "README.en.md").read_text(encoding="utf-8")
    contact = (
        " | `contacts_not_at_fault` = 1095 "
        "<!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->"
    )
    assert contact in text
    changed.write_text(text.replace(contact, "", 1), encoding="utf-8")

    violations, _ = validate_attribution(ROOT / "docs/claims.yaml", ROOT, None, [changed])

    assert any("must also state contacts_not_at_fault" in violation for violation in violations)
