"""The published post-hoc addendum to v1.0.0, checked against its own registry.

`docs/posthoc/nuplan_aeb_v2-addendum/` publishes the addendum summary that
`aeb-risk study addendum` wrote from the released records, the evidence
`aeb-risk study evidence --part addendum` derived from it, and the registry
`aeb-risk study claims --part addendum` built from that evidence. The released
records stay outside Git, so the summary is pinned by its SHA-256, and
everything else is derived from it here without the dataset.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aebrisk.analysis.claims import audit_claims, load_registry
from aebrisk.study.claims import build_addendum_claims
from aebrisk.study.evidence import write_addendum_evidence
from aebrisk.study.gates import committed_cohort_tokens, refuse_token_strings

ROOT = Path(__file__).resolve().parents[2]
ADDENDUM = ROOT / "docs" / "posthoc" / "nuplan_aeb_v2-addendum"
EVIDENCE = ADDENDUM / "evidence"
SUMMARY = EVIDENCE / "addendum-summary.json"
DERIVED = EVIDENCE / "attribution-addendum-evidence.json"
REGISTRY = ADDENDUM / "claims.yaml"

#: The SHA-256 of the committed addendum summary, as `study addendum` wrote it.
SUMMARY_SHA256 = "c4d812e2a35ef270fc546f101eada88108c57736519ef1422d3bc1f851f6f8cd"


def test_the_committed_summary_keeps_the_bytes_the_addendum_wrote() -> None:
    """The records it came from are not in Git, so a changed summary could not be rechecked."""

    assert hashlib.sha256(SUMMARY.read_bytes()).hexdigest() == SUMMARY_SHA256


def test_the_committed_evidence_is_derived_from_the_summary_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)

    written = write_addendum_evidence(SUMMARY, tmp_path)

    assert sorted(path.name for path in written) == sorted(path.name for path in EVIDENCE.iterdir())
    for path in written:
        assert path.read_bytes() == (EVIDENCE / path.name).read_bytes(), path.name


def test_the_committed_registry_is_exactly_the_generated_registry() -> None:
    assert load_registry(REGISTRY) == build_addendum_claims(EVIDENCE.relative_to(ROOT), ROOT)


def test_the_committed_registry_audits_clean() -> None:
    assert audit_claims(REGISTRY, ROOT) == ()


def test_the_registry_audit_is_not_vacuous(tmp_path: Path) -> None:
    """One changed digit in one claim of the addendum registry must be reported."""

    text = REGISTRY.read_text(encoding="utf-8")
    original = "text: 'bootstrap: clusters is 198.'"
    assert original in text
    corrupted = tmp_path / "claims.yaml"
    corrupted.write_text(text.replace(original, original.replace("198", "199"), 1), "utf-8")

    violations = audit_claims(corrupted, ROOT)

    assert violations
    assert "p3.posthoc.v1-addendum.bootstrap.clusters" in violations[0]


def test_no_scenario_token_appears_anywhere_in_the_addendum_directory() -> None:
    """Only aggregates are published: no token of the committed cohort, in any file here."""

    tokens = committed_cohort_tokens(ROOT)
    published = sorted(path for path in ADDENDUM.glob("**/*") if path.is_file())

    assert {path.relative_to(ADDENDUM).as_posix() for path in published} >= {
        "claims.yaml",
        "evidence/addendum-summary.json",
        "evidence/attribution-addendum-evidence.json",
    }
    for path in published:
        refuse_token_strings(path.read_text(encoding="utf-8"), tokens)


def test_the_token_check_would_find_a_cohort_token_in_a_published_text() -> None:
    tokens = committed_cohort_tokens(ROOT)
    token = min(tokens)
    text = DERIVED.read_text(encoding="utf-8").replace('"family-log"', f'"{token}"', 1)

    with pytest.raises(ValueError, match="scenario token"):
        refuse_token_strings(text, tokens)
