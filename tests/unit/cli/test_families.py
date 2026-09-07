"""The family-summary CLI wires validated inputs to one deterministic document."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from aebrisk.artifacts.family_interventions import FamilyInterventionsV1


def _empty_summary() -> FamilyInterventionsV1:
    rows = [
        {
            "family": family,
            "configuration_id": configuration,
            "valid_tokens": 0,
            "replicate_count": 3,
            "scenario_replicates": 0,
            "missed_interventions": 0,
            "false_interventions": 0,
            "missed_per_1000_scenario_replicates": None,
            "false_per_1000_scenario_replicates": None,
        }
        for family in (
            "lead_or_stopping",
            "cut_in_or_crossing",
            "pedestrian_or_crosswalk",
            "bicycle_or_vru",
        )
        for configuration in (
            "oracle_aeb",
            "coalition-none",
            "coalition-dropout+localization_shape+latency+track_instability",
        )
    ]
    return FamilyInterventionsV1.model_validate(
        {
            "schema_version": "aeb-family-interventions/v1",
            "protocol_sha256": "a" * 64,
            "cohort_manifest_sha256": "b" * 64,
            "common_valid_tokens": 0,
            "evaluation_per_family": 100,
            "rows": rows,
        }
    )


def test_summarize_families_writes_the_registered_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aebrisk.cli import families
    from aebrisk.cli.app import app

    results = tmp_path / "formal"
    manifest = tmp_path / "manifest.json"
    protocol = tmp_path / "protocol.yaml"
    results.mkdir()
    manifest.write_text("{}", encoding="utf-8")
    protocol.write_text("cohort: {}", encoding="utf-8")
    monkeypatch.setattr(families, "load_formal_results", lambda path: object())
    monkeypatch.setattr(
        families,
        "load_manifest",
        lambda path: SimpleNamespace(
            protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest()
        ),
    )
    monkeypatch.setattr(families, "family_interventions", lambda *args: _empty_summary())
    output = tmp_path / "evidence"

    result = CliRunner().invoke(
        app,
        [
            "summarize-families",
            "--results-dir",
            str(results),
            "--manifest",
            str(manifest),
            "--protocol",
            str(protocol),
            "--output-dir",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    written = json.loads((output / "family-interventions.json").read_text(encoding="utf-8"))
    assert written["schema_version"] == "aeb-family-interventions/v1"


def test_summarize_families_turns_invalid_input_into_a_nonzero_exit(tmp_path: Path) -> None:
    from aebrisk.cli.app import app

    result = CliRunner().invoke(
        app,
        [
            "summarize-families",
            "--results-dir",
            str(tmp_path / "absent"),
            "--manifest",
            str(tmp_path / "absent.json"),
            "--protocol",
            str(tmp_path / "absent.yaml"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 1
    assert "summarize-families failed" in result.output


def test_summarize_families_rejects_changed_protocol_before_loading_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aebrisk.cli import families
    from aebrisk.cli.app import app

    results = tmp_path / "formal"
    manifest = tmp_path / "manifest.json"
    protocol = tmp_path / "changed.yaml"
    results.mkdir()
    manifest.write_text("{}", encoding="utf-8")
    protocol.write_text("cohort:\n  evaluation_per_family: 999\n", encoding="utf-8")
    monkeypatch.setattr(
        families,
        "load_manifest",
        lambda path: SimpleNamespace(protocol_sha256="a" * 64),
    )

    def fail_if_loaded(path: Path) -> None:
        raise AssertionError("formal results were loaded before protocol provenance was checked")

    monkeypatch.setattr(families, "load_formal_results", fail_if_loaded)
    result = CliRunner().invoke(
        app,
        [
            "summarize-families",
            "--results-dir",
            str(results),
            "--manifest",
            str(manifest),
            "--protocol",
            str(protocol),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 1
    assert "protocol hash does not match" in result.output


def test_summarize_families_rejects_a_non_mapping_protocol(tmp_path: Path) -> None:
    from aebrisk.cli.app import app

    protocol = tmp_path / "protocol.yaml"
    protocol.write_text("- not-a-mapping\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "summarize-families",
            "--results-dir",
            str(tmp_path / "formal"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--protocol",
            str(protocol),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 1
    assert "protocol root must be a mapping" in result.output
