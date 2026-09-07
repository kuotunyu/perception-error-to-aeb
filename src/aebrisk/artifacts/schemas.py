"""Deterministic JSON Schema generation for the committed contracts.

The schemas under ``schemas/`` are the machine-readable form of the models in
this package, and they are committed so a consumer can validate an artifact
without installing this project. Generating them through one function, with one
fixed serialization, is what makes "regenerates byte-identically" a check a
test can perform rather than a claim in a README.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from aebrisk.artifacts.documents import (
    AEBEvaluationV1,
    AEBExclusionsV1,
    AEBIntervalsV1,
    AEBShapleyV1,
)
from aebrisk.artifacts.envelope import PortfolioArtifactEnvelopeV1
from aebrisk.artifacts.family_interventions import FamilyInterventionsV1
from aebrisk.artifacts.results import AEBScenarioResultV1, AEBScenarioResultV2
from aebrisk.artifacts.run_record import RunRecordV1

#: Every committed schema, paired with the model it is generated from.
SCHEMA_MODELS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("portfolio_artifact_envelope_v1", PortfolioArtifactEnvelopeV1),
    ("run_record_v1", RunRecordV1),
    ("aeb_result_v1", AEBScenarioResultV1),
    ("aeb_result_v2", AEBScenarioResultV2),
    ("aeb_evaluation_v1", AEBEvaluationV1),
    ("aeb_intervals_v1", AEBIntervalsV1),
    ("aeb_shapley_v1", AEBShapleyV1),
    ("aeb_exclusions_v1", AEBExclusionsV1),
    ("aeb_family_interventions_v1", FamilyInterventionsV1),
)


def schema_document(model: type[BaseModel]) -> dict[str, Any]:
    """Return the JSON Schema for one model, with the dialect stated explicitly."""

    document: dict[str, Any] = {"$schema": "https://json-schema.org/draft/2020-12/schema"}
    document.update(model.model_json_schema())
    if model is AEBScenarioResultV2:
        document["allOf"] = [
            {
                "if": {"properties": {"valid": {"const": True}}, "required": ["valid"]},
                "then": {
                    "properties": {
                        "simulated_duration_s": {
                            "exclusiveMinimum": 0.0,
                            "type": "number",
                        }
                    },
                    "required": ["simulated_duration_s"],
                },
            }
        ]
    return document


def schema_bytes(model: type[BaseModel]) -> bytes:
    """Serialize one schema the single way every regeneration must reproduce."""

    return (
        json.dumps(schema_document(model), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def write_schemas(
    schema_dir: Path, models: Iterable[tuple[str, type[BaseModel]]] = ()
) -> list[Path]:
    """Write every committed schema and return the paths that were written."""

    schema_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in models or SCHEMA_MODELS:
        path = schema_dir / f"{name}.json"
        path.write_bytes(schema_bytes(model))
        written.append(path)
    return written
