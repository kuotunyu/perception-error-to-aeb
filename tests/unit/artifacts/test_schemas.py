"""Contracts for deterministic JSON Schema generation.

The schemas are committed so a consumer can validate an artifact without
installing this project. That only helps if regenerating them produces the same
bytes, which is what makes "the committed schema still describes the model" a
check rather than an assertion.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest


def load_schemas_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.artifacts import schemas
    except ImportError:
        pytest.fail("aebrisk.artifacts.schemas is missing", pytrace=False)
    return schemas


def test_every_committed_schema_has_a_model() -> None:
    """A schema with no model behind it drifts the moment the model changes."""

    schemas = load_schemas_module()

    names = [name for name, _ in schemas.SCHEMA_MODELS]

    assert names == [
        "portfolio_artifact_envelope_v1",
        "run_record_v1",
        "aeb_result_v1",
        "aeb_result_v2",
        "aeb_evaluation_v1",
        "aeb_intervals_v1",
        "aeb_shapley_v1",
        "aeb_exclusions_v1",
    ]


def test_the_dialect_is_stated_explicitly() -> None:
    """A schema without its dialect is ambiguous to any validator that reads it."""

    from aebrisk.artifacts.run_record import RunRecordV1

    schemas = load_schemas_module()

    document = schemas.schema_document(RunRecordV1)

    assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_generation_is_byte_stable_across_calls() -> None:
    """Unstable key order would make every regeneration look like a change."""

    from aebrisk.artifacts.results import AEBScenarioResultV1

    schemas = load_schemas_module()

    assert schemas.schema_bytes(AEBScenarioResultV1) == schemas.schema_bytes(AEBScenarioResultV1)


def test_the_serialization_is_sorted_indented_and_newline_terminated() -> None:
    """These three choices are the whole of what "byte-identical" depends on."""

    from aebrisk.artifacts.results import AEBScenarioResultV1

    schemas = load_schemas_module()
    payload = schemas.schema_bytes(AEBScenarioResultV1)
    text = payload.decode("utf-8")

    assert text.endswith("}\n")
    assert '\n  "' in text, "expected two-space indentation"
    document = json.loads(text)
    assert list(document) == sorted(document)


def test_writing_schemas_produces_the_declared_files(tmp_path: Path) -> None:
    """The writer is what a maintainer runs; it must cover every committed schema."""

    schemas = load_schemas_module()

    written = schemas.write_schemas(tmp_path)

    assert sorted(path.name for path in written) == [
        "aeb_evaluation_v1.json",
        "aeb_exclusions_v1.json",
        "aeb_intervals_v1.json",
        "aeb_result_v1.json",
        "aeb_result_v2.json",
        "aeb_shapley_v1.json",
        "portfolio_artifact_envelope_v1.json",
        "run_record_v1.json",
    ]
    for path in written:
        assert path.read_bytes().endswith(b"}\n")


def test_writing_schemas_accepts_an_explicit_selection(tmp_path: Path) -> None:
    """Regenerating one schema must not depend on regenerating all of them."""

    from aebrisk.artifacts.run_record import RunRecordV1

    schemas = load_schemas_module()

    written = schemas.write_schemas(tmp_path, [("only_one", RunRecordV1)])

    assert [path.name for path in written] == ["only_one.json"]


def test_v2_schema_rejects_null_exposure_for_a_valid_result() -> None:
    """Schema-only consumers need the same valid/exposure relationship as Pydantic."""

    from aebrisk.artifacts.results import AEBScenarioResultV2

    schemas = load_schemas_module()
    conditional = schemas.schema_document(AEBScenarioResultV2)["allOf"]

    assert conditional == [
        {
            "if": {"properties": {"valid": {"const": True}}, "required": ["valid"]},
            "then": {
                "properties": {"simulated_duration_s": {"exclusiveMinimum": 0.0, "type": "number"}},
                "required": ["simulated_duration_s"],
            },
        }
    ]
