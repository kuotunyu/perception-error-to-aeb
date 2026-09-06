"""The formal result set is whole, or it is not used.

A missing coalition would leave a Shapley game with a hole, and a cohort that
differs between configurations would compare different roads.  These tests use
the complete 26-cell matrix and three replicates per token, matching the formal
reader's actual contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aebrisk.analysis.aggregate import load_formal_results, validate_formal_set
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import CohortManifestV1, membership_sha256

TOKENS = ("t-0001", "t-0002")


def _result(configuration_id: str, token: str, replicate: int) -> dict:
    return {
        "schema_version": "aeb-scenario-result/v2",
        "scenario_token": token,
        "family": "lead_or_stopping",
        "configuration_id": configuration_id,
        "replicate": replicate,
        "valid": True,
        "invalid_reason": None,
        "collision_vru": 0,
        "collision_vehicle": 0,
        "collision_object": 0,
        "collision_energy": 0.0,
        "contacts_not_at_fault": 0,
        "min_ttc_s": None,
        "min_clearance_m": 5.0,
        "missed_interventions": 0,
        "false_interventions": 0,
        "matched_delay_s": [],
        "stop_distance_m": None,
        "max_deceleration_mps2": 0.0,
        "max_abs_jerk_mps3": 0.0,
        "intervention_duration_s": 0.0,
        "simulated_duration_s": 1.0,
    }


def write_fixture(root: Path, configurations, tokens: tuple[str, ...] = TOKENS) -> None:
    for configuration in configurations:
        directory = root / configuration.configuration_id
        directory.mkdir(parents=True)
        for token in tokens:
            payload = {
                "schema_version": "aeb-token-results/v1",
                "scenario_token": token,
                "family": "lead_or_stopping",
                "split": "evaluation",
                "configuration_id": configuration.configuration_id,
                "protocol_sha256": "a" * 64,
                "cohort_manifest_sha256": "b" * 64,
                "valid": True,
                "invalid_reason": None,
                "invalid_phase": None,
                "results": [
                    _result(configuration.configuration_id, token, replicate)
                    for replicate in range(3)
                ],
            }
            (directory / f"{token}.json").write_text(json.dumps(payload), encoding="utf-8")
    (root / "run_complete.json").write_text(
        json.dumps(
            {
                "schema_version": "aeb-run-complete/v1",
                "cohort_manifest_sha256": "b" * 64,
                "tokens": sorted(tokens),
            }
        ),
        encoding="utf-8",
    )


def test_a_set_missing_one_coalition_is_refused(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations[:-1])

    with pytest.raises(ValueError, match=r"^missing configuration "):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def test_a_cohort_that_differs_between_configurations_is_refused(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    (tmp_path / "dropout-high" / "t-0002.json").unlink()

    with pytest.raises(
        ValueError, match=r"^configuration 'dropout-high' covers a different cohort"
    ):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def test_a_whole_set_passes(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)

    validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def _rewrite(path: Path, change) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_the_completion_marker_must_name_the_expected_cohort(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    _rewrite(tmp_path / "run_complete.json", lambda payload: payload["tokens"].pop())

    with pytest.raises(ValueError, match=r"^completion marker covers a different cohort$"):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def test_the_completion_marker_cannot_repeat_a_token(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    _rewrite(
        tmp_path / "run_complete.json",
        lambda payload: payload["tokens"].append(payload["tokens"][0]),
    )

    with pytest.raises(ValueError, match=r"completion marker repeats a token"):
        load_formal_results(tmp_path)


def test_an_extra_configuration_is_refused(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    extra = tmp_path / "extra"
    extra.mkdir()

    with pytest.raises(ValueError, match=r"^unexpected configuration 'extra'$"):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def test_an_unexpected_file_inside_a_configuration_is_refused(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    (tmp_path / "no_aeb" / "partial.json.tmp").write_text("partial", encoding="utf-8")

    with pytest.raises(ValueError, match=r"^unexpected formal artifact "):
        load_formal_results(tmp_path)


def test_file_and_document_identities_must_match(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    path = tmp_path / "no_aeb" / "t-0001.json"
    _rewrite(path, lambda payload: payload.__setitem__("configuration_id", "oracle_aeb"))

    with pytest.raises(ValueError, match=r"is filed under 'no_aeb'"):
        load_formal_results(tmp_path)

    write_fixture(tmp_path / "second", configurations)
    source = tmp_path / "second" / "no_aeb" / "t-0001.json"
    source.rename(source.with_name("wrong-token.json"))
    with pytest.raises(ValueError, match=r"names scenario_token 't-0001'$"):
        load_formal_results(tmp_path / "second")


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("protocol_sha256", "c" * 64, "do not share one protocol hash"),
        ("cohort_manifest_sha256", "c" * 64, "do not match the completion marker"),
        ("split", "development", "do not share one split"),
    ],
)
def test_formal_provenance_must_be_identical(
    tmp_path: Path, field: str, replacement: str, message: str
) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    path = tmp_path / "no_aeb" / "t-0001.json"
    _rewrite(path, lambda payload: payload.__setitem__(field, replacement))

    with pytest.raises(ValueError, match=message):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("scenario_token", "different", "record token differs"),
        ("configuration_id", "oracle_aeb", "record configuration differs"),
        ("family", "bicycle_or_vru", "record family differs"),
        ("valid", False, "record validity differs"),
    ],
)
def test_nested_record_identity_must_match_its_document(
    tmp_path: Path, field: str, replacement, message: str
) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    path = tmp_path / "no_aeb" / "t-0001.json"

    def change(payload) -> None:
        payload["results"][0][field] = replacement
        if field == "valid":
            payload["results"][0]["invalid_reason"] = "failed"

    _rewrite(path, change)
    with pytest.raises(ValueError, match=message):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def test_every_valid_token_has_the_predeclared_replicates(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    path = tmp_path / "no_aeb" / "t-0001.json"
    _rewrite(path, lambda payload: payload["results"].pop())

    with pytest.raises(ValueError, match=r"must have replicates \[0, 1, 2\]$"):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def test_an_invalid_token_in_only_one_configuration_is_refused(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    path = tmp_path / "no_aeb" / "t-0001.json"

    def invalidate(payload) -> None:
        payload.update(
            {"valid": False, "invalid_reason": "failed", "invalid_phase": "step", "results": []}
        )

    _rewrite(path, invalidate)
    with pytest.raises(ValueError, match=r"^invalid scenarios are not excluded globally$"):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def test_a_token_cannot_change_family_between_configurations(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    path = tmp_path / "oracle_aeb" / "t-0001.json"

    def change_family(payload) -> None:
        payload["family"] = "bicycle_or_vru"
        for record in payload["results"]:
            record["family"] = "bicycle_or_vru"

    _rewrite(path, change_family)
    with pytest.raises(ValueError, match=r"changes family across configurations$"):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


@pytest.mark.parametrize("field", ["invalid_reason", "invalid_phase"])
def test_valid_documents_cannot_carry_failure_metadata(tmp_path: Path, field: str) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    path = tmp_path / "no_aeb" / "t-0001.json"
    _rewrite(path, lambda payload: payload.__setitem__(field, "failed"))

    with pytest.raises(ValueError, match=r"^valid token document carries invalid diagnostics$"):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


@pytest.mark.parametrize(
    ("reason", "phase"),
    [(None, "step"), ("failed", None)],
)
def test_invalid_documents_require_both_failure_diagnostics(tmp_path: Path, reason, phase) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    path = tmp_path / "no_aeb" / "t-0001.json"

    def invalidate(payload) -> None:
        payload.update(
            {"valid": False, "invalid_reason": reason, "invalid_phase": phase, "results": []}
        )

    _rewrite(path, invalidate)
    with pytest.raises(ValueError, match=r"^invalid token document requires a reason and phase$"):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def test_global_exclusion_requires_one_reason_and_phase_everywhere(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)

    for configuration in configurations:
        path = tmp_path / configuration.configuration_id / "t-0001.json"

        def invalidate(payload) -> None:
            payload.update(
                {
                    "valid": False,
                    "invalid_reason": "failed",
                    "invalid_phase": "step",
                    "results": [],
                }
            )

        _rewrite(path, invalidate)
    divergent = tmp_path / "oracle_aeb" / "t-0001.json"
    _rewrite(divergent, lambda payload: payload.__setitem__("invalid_reason", "different"))

    with pytest.raises(ValueError, match=r"^invalid diagnostics differ across configurations$"):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def test_one_consistent_global_exclusion_is_accepted_and_reported(tmp_path: Path) -> None:
    from aebrisk.analysis.aggregate import common_cohort, exclusion_rows

    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    for configuration in configurations:
        path = tmp_path / configuration.configuration_id / "t-0001.json"

        def invalidate(payload) -> None:
            payload.update(
                {
                    "valid": False,
                    "invalid_reason": "failed",
                    "invalid_phase": "step",
                    "results": [],
                }
            )

        _rewrite(path, invalidate)

    loaded = load_formal_results(tmp_path)
    validate_formal_set(loaded, configurations, TOKENS)
    cohort = common_cohort(loaded)

    assert cohort == ("t-0002",)
    assert exclusion_rows(loaded, cohort) == [
        {
            "scenario_token": "t-0001",
            "phase": "step",
            "reason": "failed",
            "exception_type": None,
        }
    ]


def test_duplicate_invalid_diagnostic_replicates_are_refused(tmp_path: Path) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    path = tmp_path / "no_aeb" / "t-0001.json"

    def invalidate(payload) -> None:
        for record in payload["results"][:2]:
            record.update({"valid": False, "invalid_reason": "failed", "replicate": 0})
        payload.update(
            {
                "valid": False,
                "invalid_reason": "failed",
                "invalid_phase": "step",
                "results": payload["results"][:2],
            }
        )

    _rewrite(path, invalidate)
    with pytest.raises(ValueError, match=r"repeats an invalid replicate$"):
        validate_formal_set(load_formal_results(tmp_path), configurations, TOKENS)


def _manifest(
    *,
    protocol: str = "a" * 64,
    split: str = "evaluation",
    lead: tuple[str, ...] = TOKENS,
    bicycle: tuple[str, ...] = (),
) -> CohortManifestV1:
    return CohortManifestV1.model_validate(
        {
            "schema_version": "aeb-cohort-manifest/v1",
            "split": split,
            "protocol_sha256": protocol,
            "families": {
                "lead_or_stopping": lead,
                "cut_in_or_crossing": (),
                "pedestrian_or_crosswalk": (),
                "bicycle_or_vru": bicycle,
            },
            "log_names": ("one.db",),
        }
    )


def _bind_manifest_hash(loaded, manifest: CohortManifestV1) -> None:
    digest = membership_sha256(manifest)
    loaded.cohort_manifest_sha256 = digest
    for documents in loaded.values():
        for token, document in tuple(documents.items()):
            documents[token] = document.model_copy(update={"cohort_manifest_sha256": digest})


@pytest.mark.parametrize(
    ("manifest", "bind_hash", "message"),
    [
        (_manifest(lead=(*TOKENS, "t-0003")), True, "manifest covers a different cohort"),
        (_manifest(), False, "completion marker cohort hash does not match the manifest"),
        (_manifest(protocol="c" * 64), True, "do not match the manifest protocol hash"),
        (_manifest(split="development"), True, "do not match the manifest split"),
        (
            _manifest(lead=("t-0001",), bicycle=("t-0002",)),
            True,
            "formal document families do not match the manifest",
        ),
    ],
)
def test_manifest_identity_is_checked_field_by_field(
    tmp_path: Path, manifest: CohortManifestV1, bind_hash: bool, message: str
) -> None:
    configurations = formal_configurations()
    write_fixture(tmp_path, configurations)
    loaded = load_formal_results(tmp_path)
    if bind_hash:
        _bind_manifest_hash(loaded, manifest)

    with pytest.raises(ValueError, match=message):
        validate_formal_set(loaded, configurations, TOKENS, manifest=manifest)
