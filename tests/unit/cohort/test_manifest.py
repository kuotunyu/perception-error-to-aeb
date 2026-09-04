"""Contracts for the document that says exactly which scenarios were simulated.

The manifest is what a reader checks a published number against. It names the
protocol it was cut under, the split it is, and every scenario token in it, and
it carries one hash over that membership so two freezes can be compared without
reading four hundred tokens.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

PROTOCOL_HASH = "a" * 64


def load_manifest_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.cohort import manifest
    except ImportError:
        pytest.fail("aebrisk.cohort.manifest is missing", pytrace=False)
    return manifest


def manifest_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "schema_version": "aeb-cohort-manifest/v1",
        "split": "development",
        "protocol_sha256": PROTOCOL_HASH,
        "families": {
            "lead_or_stopping": ["s-0002", "s-0001"],
            "cut_in_or_crossing": ["s-0003"],
            "pedestrian_or_crosswalk": [],
            "bicycle_or_vru": ["s-0004"],
        },
        "log_names": ["log-000", "log-001"],
    }
    values.update(overrides)
    return values


def test_a_complete_manifest_validates() -> None:
    """The success path must pass or no cohort could ever be frozen."""

    manifest = load_manifest_module()

    document = manifest.CohortManifestV1.model_validate(manifest_values())

    assert document.split == "development"
    assert document.families["cut_in_or_crossing"] == ("s-0003",)


def test_scenario_tokens_are_sorted_within_each_family() -> None:
    """Two freezes of the same cohort must produce the same document, not a permutation."""

    manifest = load_manifest_module()

    document = manifest.CohortManifestV1.model_validate(manifest_values())

    assert document.families["lead_or_stopping"] == ("s-0001", "s-0002")


def test_every_declared_family_must_appear() -> None:
    """A missing stratum would be read as an empty one rather than as an omission."""

    manifest = load_manifest_module()
    values = manifest_values()
    del values["families"]["bicycle_or_vru"]

    with pytest.raises(ValidationError, match=r"Value error, missing scenario families: "):
        manifest.CohortManifestV1.model_validate(values)


def test_an_unknown_family_is_refused() -> None:
    """A family outside the frozen four cannot be reported or weighted."""

    manifest = load_manifest_module()
    values = manifest_values()
    values["families"]["highway_merge"] = ["s-0009"]

    with pytest.raises(ValidationError, match=r"Value error, unknown scenario families: "):
        manifest.CohortManifestV1.model_validate(values)


def test_a_token_appearing_in_two_families_is_refused() -> None:
    """One scenario in two strata would be simulated twice and weighted twice."""

    manifest = load_manifest_module()
    values = manifest_values()
    values["families"]["cut_in_or_crossing"] = ["s-0001"]

    with pytest.raises(ValidationError, match=r"duplicate scenario token in the cohort"):
        manifest.CohortManifestV1.model_validate(values)


def test_a_duplicate_token_within_one_family_is_refused() -> None:
    """The same failure, one level down, and just as invisible in an aggregate."""

    manifest = load_manifest_module()
    values = manifest_values()
    values["families"]["lead_or_stopping"] = ["s-0001", "s-0001"]

    with pytest.raises(ValidationError, match=r"duplicate scenario token in the cohort"):
        manifest.CohortManifestV1.model_validate(values)


def test_an_unknown_split_is_refused() -> None:
    """Only two cohorts exist; a third name would name a study nobody ran."""

    manifest = load_manifest_module()

    with pytest.raises(ValidationError):
        manifest.CohortManifestV1.model_validate(manifest_values(split="test"))


def test_the_membership_hash_covers_the_split_and_every_token() -> None:
    """One value a reader can compare instead of four hundred tokens."""

    manifest = load_manifest_module()
    document = manifest.CohortManifestV1.model_validate(manifest_values())

    digest = manifest.membership_sha256(document)

    assert len(digest) == 64
    assert digest == manifest.membership_sha256(document)


def test_the_membership_hash_changes_when_a_token_changes() -> None:
    """A hash that survives an edit certifies nothing about what was simulated."""

    manifest = load_manifest_module()
    values = manifest_values()
    original = manifest.membership_sha256(manifest.CohortManifestV1.model_validate(values))

    values["families"]["bicycle_or_vru"] = ["s-9999"]
    changed = manifest.membership_sha256(manifest.CohortManifestV1.model_validate(values))

    assert original != changed


def test_the_membership_hash_changes_between_splits() -> None:
    """Development and evaluation must never be mistaken for one another."""

    manifest = load_manifest_module()
    development = manifest.CohortManifestV1.model_validate(manifest_values())
    evaluation = manifest.CohortManifestV1.model_validate(manifest_values(split="evaluation"))

    assert manifest.membership_sha256(development) != manifest.membership_sha256(evaluation)


def test_the_membership_hash_ignores_how_the_tokens_were_ordered_on_input() -> None:
    """The hash must describe the membership, not the order somebody listed it in."""

    manifest = load_manifest_module()
    values = manifest_values()
    reversed_values = manifest_values()
    reversed_values["families"]["lead_or_stopping"] = ["s-0001", "s-0002"]

    first = manifest.membership_sha256(manifest.CohortManifestV1.model_validate(values))
    second = manifest.membership_sha256(manifest.CohortManifestV1.model_validate(reversed_values))

    assert first == second


def test_a_manifest_round_trips_through_a_file(tmp_path: Path) -> None:
    """The frozen cohort lives on disk; writing and reading must not change it."""

    manifest = load_manifest_module()
    document = manifest.CohortManifestV1.model_validate(manifest_values())
    path = tmp_path / "development.json"

    manifest.save_manifest(document, path)
    restored = manifest.load_manifest(path)

    assert restored == document
    assert manifest.membership_sha256(restored) == manifest.membership_sha256(document)


def test_the_written_manifest_is_byte_stable(tmp_path: Path) -> None:
    """A cohort cited by hash must serialize the same way on every machine."""

    manifest = load_manifest_module()
    document = manifest.CohortManifestV1.model_validate(manifest_values())
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"

    manifest.save_manifest(document, first)
    manifest.save_manifest(document, second)

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"}\n")


def test_the_written_manifest_uses_line_feeds_on_every_platform(tmp_path: Path) -> None:
    """A manifest written on Windows and rebuilt in CI must hash the same."""

    manifest = load_manifest_module()
    document = manifest.CohortManifestV1.model_validate(manifest_values())
    path = tmp_path / "development.json"

    manifest.save_manifest(document, path)

    assert b"\r\n" not in path.read_bytes()


def test_the_written_manifest_is_readable_json(tmp_path: Path) -> None:
    """Somebody will open this file to check a claim; it should not be one long line."""

    manifest = load_manifest_module()
    document = manifest.CohortManifestV1.model_validate(manifest_values())
    path = tmp_path / "development.json"

    manifest.save_manifest(document, path)
    restored = json.loads(path.read_text(encoding="utf-8"))

    assert restored["split"] == "development"
    assert path.read_text(encoding="utf-8").count("\n") > 5


def test_the_manifest_refuses_to_overwrite_an_existing_freeze(tmp_path: Path) -> None:
    """Silently replacing a frozen cohort would invalidate every result citing it."""

    manifest = load_manifest_module()
    document = manifest.CohortManifestV1.model_validate(manifest_values())
    path = tmp_path / "development.json"
    manifest.save_manifest(document, path)

    with pytest.raises(FileExistsError):
        manifest.save_manifest(document, path)


def test_the_model_is_frozen() -> None:
    """A manifest that can be edited after loading is not a frozen cohort."""

    manifest = load_manifest_module()
    document = manifest.CohortManifestV1.model_validate(manifest_values())

    with pytest.raises(ValidationError, match=r"Instance is frozen"):
        document.split = "evaluation"  # type: ignore[misc]


def test_an_empty_cohort_is_refused() -> None:
    """A manifest with no scenarios at all is a freeze that captured nothing."""

    manifest = load_manifest_module()
    values = manifest_values()
    values["families"] = {family: [] for family in values["families"]}

    with pytest.raises(
        ValidationError,
        match=r"the\ cohort\ is\ empty;\ a\ freeze\ that\ captured\ nothing\ is\ not\ a\ cohort",
    ):
        manifest.CohortManifestV1.model_validate(values)
