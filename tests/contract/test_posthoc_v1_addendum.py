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
import json
import re
from pathlib import Path

import pytest
import yaml

from aebrisk.analysis.attribution_audit import NUMBER, validate_attribution
from aebrisk.analysis.claims import audit_claims, load_registry
from aebrisk.artifacts.study_documents import COLLISION_GAME_SENTENCE
from aebrisk.study.claims import build_addendum_claims
from aebrisk.study.evidence import write_addendum_evidence
from aebrisk.study.gates import committed_cohort_tokens, refuse_token_strings

ROOT = Path(__file__).resolve().parents[2]
ADDENDUM = ROOT / "docs" / "posthoc" / "nuplan_aeb_v2-addendum"
EVIDENCE = ADDENDUM / "evidence"
SUMMARY = EVIDENCE / "addendum-summary.json"
DERIVED = EVIDENCE / "attribution-addendum-evidence.json"
REGISTRY = ADDENDUM / "claims.yaml"
RESULTS = ADDENDUM / "results.md"
TITLE = "# Post-hoc addendum to v1.0.0 (not pre-registered; partly computed before writing)"

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


#: Sixteen lower-case hexadecimal digits standing alone in running text, other
#: than the digits after a decimal point.
TOKEN_IN_TEXT = re.compile(r"(?<![0-9A-Za-z.])[0-9a-f]{16}(?![0-9A-Za-z])")


def refuse_published_tokens(name: str, text: str, tokens: frozenset[str]) -> None:
    """Refuse a published file that holds a cohort token or a token-shaped string.

    JSON and YAML are parsed, so that every string, value or key, is checked
    against the token's shape on its own; Markdown is searched for the shape.
    Every file is also searched for the committed cohort's tokens.
    """

    if name.endswith(".json"):
        refuse_token_strings(json.loads(text), tokens)
    elif name.endswith(".yaml"):
        refuse_token_strings(yaml.safe_load(text), tokens)
    else:
        found = TOKEN_IN_TEXT.search(text)
        if found:
            raise ValueError(f"{found.group()!r} has the shape of a scenario token")
    refuse_token_strings(text, tokens)


def test_no_scenario_token_appears_anywhere_in_the_addendum_directory() -> None:
    """Only aggregates are published: no token of the committed cohort, in any file here."""

    tokens = committed_cohort_tokens(ROOT)
    published = sorted(path for path in ADDENDUM.glob("**/*") if path.is_file())

    assert {path.relative_to(ADDENDUM).as_posix() for path in published} >= {
        "NOTICE.md",
        "claims.yaml",
        "results.md",
        "evidence/addendum-summary.json",
        "evidence/attribution-addendum-evidence.json",
    }
    for path in published:
        refuse_published_tokens(path.name, path.read_text(encoding="utf-8"), tokens)


def test_the_token_check_would_find_a_cohort_token_in_a_published_text() -> None:
    tokens = committed_cohort_tokens(ROOT)
    token = min(tokens)
    text = DERIVED.read_text(encoding="utf-8").replace('"family-log"', f'"{token}"', 1)

    with pytest.raises(ValueError, match="scenario token"):
        refuse_published_tokens(DERIVED.name, text, tokens)


#: Sixteen lower-case hexadecimal digits, the shape of a scenario token, that no
#: committed cohort file holds.
OUTSIDE_THE_COHORT = "0123456789abcdef"


@pytest.mark.parametrize(
    ("path", "original"),
    [
        (DERIVED, '"family-log"'),
        (REGISTRY, "p3.posthoc.v1-addendum.bootstrap.clusters"),
        (RESULTS, "p3.posthoc.v1-addendum.bootstrap.clusters"),
    ],
    ids=["evidence", "registry", "results"],
)
def test_the_token_check_would_find_a_token_shaped_string_outside_the_cohort(
    path: Path, original: str
) -> None:
    """A string of the token's shape is refused even when no cohort file lists it."""

    tokens = committed_cohort_tokens(ROOT)
    assert OUTSIDE_THE_COHORT not in tokens
    text = path.read_text(encoding="utf-8")
    assert original in text
    replacement = f'"{OUTSIDE_THE_COHORT}"' if path.suffix == ".json" else OUTSIDE_THE_COHORT

    with pytest.raises(ValueError, match="shape of a scenario token"):
        refuse_published_tokens(path.name, text.replace(original, replacement, 1), tokens)


def paragraph(text: str, start: str, end: str) -> str:
    """The text from `start` through `end`, with its line breaks read as spaces."""

    begin = text.index(start)
    return " ".join(text[begin : text.index(end, begin) + len(end)].split())


def test_the_addendum_is_shared_under_the_released_evidence_terms() -> None:
    """The directory's notice uses the released evidence notice's licence wording."""

    start = "This derived evidence is shared for non-commercial use under"
    end = "Motional does not sponsor or endorse this project."
    released = (ROOT / "docs" / "evidence" / "nuplan_aeb_v2-NOTICE.md").read_text(encoding="utf-8")
    notice = (ADDENDUM / "NOTICE.md").read_text(encoding="utf-8")

    assert paragraph(notice, start, end) == paragraph(released, start, end)
    for published in ("`evidence/`", "`claims.yaml`", "`results.md`"):
        assert published in notice


def test_the_root_notice_lists_the_addendum_among_the_nuplan_derived_paths() -> None:
    root = (ROOT / "NOTICE").read_text(encoding="utf-8")
    derived = root[root.index("2. Material derived from nuPlan") : root.index("3. Third-party")]

    assert "- docs/posthoc/nuplan_aeb_v2-addendum/:" in derived
    assert paragraph(derived, "- docs/posthoc/nuplan_aeb_v2-addendum/", "NOTICE.md") == (
        "- docs/posthoc/nuplan_aeb_v2-addendum/: derived evidence JSON, claims.yaml and the "
        "values restated in results.md; see docs/posthoc/nuplan_aeb_v2-addendum/NOTICE.md"
    )


def test_every_number_on_the_results_page_is_bound_to_the_addendum_registry() -> None:
    violations, status = validate_attribution(REGISTRY, ROOT, None, [RESULTS])

    assert violations == ()
    assert status["statements"]


def test_the_results_page_with_one_changed_value_is_refused(tmp_path: Path) -> None:
    binding = "`clusters` = 198 <!-- claim: p3.posthoc.v1-addendum.bootstrap.clusters -->"
    text = RESULTS.read_text(encoding="utf-8")
    assert binding in text
    changed = tmp_path / "results.md"
    changed.write_text(text.replace(binding, binding.replace("198", "199"), 1), "utf-8")

    violations, _ = validate_attribution(REGISTRY, ROOT, None, [changed])

    assert any("`clusters` says 199" in violation for violation in violations)


def test_the_results_page_prints_the_collision_game_right_after_the_duration_game() -> None:
    """The fixed sentence stands once, on the collision game's line."""

    text = RESULTS.read_text(encoding="utf-8")
    lines = text.splitlines()
    (duration,) = [i for i, line in enumerate(lines) if line.startswith("- **Duration game")]
    (collision,) = [i for i, line in enumerate(lines) if line.startswith("- **Collision game")]

    assert lines[0] == TITLE
    assert collision == duration + 1
    assert text.count(COLLISION_GAME_SENTENCE) == 1
    assert lines[collision].endswith(COLLISION_GAME_SENTENCE)


def test_the_results_page_neither_tests_nor_ranks() -> None:
    text = RESULTS.read_text(encoding="utf-8").lower()

    for word in ("supported", "contradicted", "ranks first", "ranked"):
        assert word not in text


#: In each README, the sentence under the Shapley values that says the released
#: evidence has no interval, and the pointer that follows it directly.
README_POINTERS = {
    "README.en.md": (
        "The evidence contains no uncertainty interval for Shapley values or configuration "
        "differences and supports no channel ranking. ",
        "A post-hoc addendum, not pre-registered, gives intervals for these values and "
        "differences and makes no ranking claim: "
        "[docs/posthoc/nuplan_aeb_v2-addendum/](docs/posthoc/nuplan_aeb_v2-addendum/results.md).",
    ),
    "README.md": (
        "\u73fe\u6709\u8b49\u64da\u6c92\u6709 Shapley "
        "\u6216\u8a2d\u5b9a\u5dee\u503c\u7684\u4fe1\u8cf4\u5340\u9593\uff0c"
        "\u4e5f\u4e0d\u652f\u6301 channel \u6392\u540d\u3002",
        "\u672a\u9810\u5148\u767b\u9304\u7684\u4e8b\u5f8c\u88dc\u5145\u5206\u6790"
        "\u70ba\u9019\u4e9b\u503c\u8207\u5dee\u503c\u63d0\u4f9b\u5340\u9593\uff0c"
        "\u4f46\u4ecd\u4e0d\u505a channel \u6392\u540d\uff1a"
        "[docs/posthoc/nuplan_aeb_v2-addendum/](docs/posthoc/nuplan_aeb_v2-addendum/results.md)"
        "\u3002",
    ),
}


@pytest.mark.parametrize("readme", sorted(README_POINTERS))
def test_each_readme_points_to_the_addendum_right_after_the_no_interval_sentence(
    readme: str,
) -> None:
    """One sentence with no number, ending the paragraph the released sentence ends."""

    released, pointer = README_POINTERS[readme]
    text = (ROOT / readme).read_text(encoding="utf-8")

    assert text.count(released + pointer + "\n") == 1
    assert text.count("docs/posthoc/nuplan_aeb_v2-addendum/results.md") == 1
    assert NUMBER.findall(pointer) == []
