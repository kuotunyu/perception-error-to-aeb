"""The envelope contract as this repository must honour it, from both sides.

P3 is the only project in the portfolio that consumes another's artifacts, and
it does so optionally: at P3-16 a calibration envelope from `bev-calibration-lab`
may be read, and everything else must be refused. The asymmetry is the point.
A consumer that accepts any envelope it can parse would read a cohort manifest
as a calibration result and produce a number that looks fine.

These tests also pin the generated JSON schemas, which are the machine-readable
form of the same contract and must regenerate byte-identically.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "portfolio_artifact_envelope_v1.json"
SCHEMA_DIR = REPO_ROOT / "schemas"
CALIBRATION_ARTIFACT_TYPE = "calibration-error-distribution/v1"


def load_envelope_module() -> ModuleType:
    try:
        from aebrisk.artifacts import envelope
    except ImportError:
        pytest.fail("aebrisk.artifacts.envelope is missing", pytrace=False)
    return envelope


def write_envelope(path: Path, **overrides: Any) -> Path:
    envelope = load_envelope_module()
    values: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    values.update(overrides)
    if "payload" in overrides:
        import hashlib

        values["payload_sha256"] = hashlib.sha256(
            envelope.canonical_json_bytes(values["payload"])
        ).hexdigest()
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_the_optional_calibration_consumer_accepts_a_p2_envelope(tmp_path: Path) -> None:
    """P3-16 reads one artifact type from P2, and this is it."""

    envelope = load_envelope_module()
    path = write_envelope(
        tmp_path / "calibration.json",
        producer_repository="bev-calibration-lab",
        artifact_type=CALIBRATION_ARTIFACT_TYPE,
        payload={"corrector": "identity", "translation_error_m": 0.0},
    )

    document = envelope.read_optional_calibration_artifact(path)

    assert document.producer_repository == "bev-calibration-lab"
    assert document.artifact_type == CALIBRATION_ARTIFACT_TYPE


def test_the_calibration_consumer_refuses_another_artifact_type(tmp_path: Path) -> None:
    """Reading a cohort manifest as a calibration result would silently succeed."""

    envelope = load_envelope_module()
    path = write_envelope(
        tmp_path / "other.json",
        producer_repository="bev-calibration-lab",
        artifact_type="bev-cohort-manifest/v1",
    )

    with pytest.raises(ValueError, match=r"^unexpected artifact type: "):
        envelope.read_optional_calibration_artifact(path)


def test_the_calibration_consumer_refuses_another_producer(tmp_path: Path) -> None:
    """Only P2 produces calibration results; anything else is a mislabelled file."""

    envelope = load_envelope_module()
    path = write_envelope(
        tmp_path / "wrong-producer.json",
        producer_repository="driving-risk-metrics",
        artifact_type=CALIBRATION_ARTIFACT_TYPE,
    )

    with pytest.raises(
        ValueError, match=r"^unexpected\ producer\ for\ a\ calibration\ artifact:\ "
    ):
        envelope.read_optional_calibration_artifact(path)


def test_consuming_a_p2_artifact_is_never_implicit(tmp_path: Path) -> None:
    """The generic verifier must not hand back a calibration artifact by accident.

    P3 must run identically whether or not P2 exists, so the only way to read
    one of its artifacts is to ask for it by name.
    """

    envelope = load_envelope_module()
    path = write_envelope(
        tmp_path / "calibration.json",
        producer_repository="bev-calibration-lab",
        artifact_type=CALIBRATION_ARTIFACT_TYPE,
    )

    with pytest.raises(ValueError, match=r"^unexpected artifact type: "):
        envelope.verify_envelope(path, "portfolio-contract-fixture/v1")


@pytest.mark.parametrize(
    "name",
    ["portfolio_artifact_envelope_v1", "run_record_v1", "aeb_result_v1"],
)
def test_every_declared_schema_exists_and_is_valid_json(name: str) -> None:
    """A schema nobody can parse is documentation, not a contract."""

    document = json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))

    assert document["$schema"].startswith("https://json-schema.org/")
    assert document["type"] == "object"
    assert document["additionalProperties"] is False


@pytest.mark.parametrize(
    ("name", "model_path"),
    [
        ("portfolio_artifact_envelope_v1", "envelope.PortfolioArtifactEnvelopeV1"),
        ("run_record_v1", "run_record.RunRecordV1"),
        ("aeb_result_v1", "results.AEBScenarioResultV1"),
    ],
)
def test_every_schema_regenerates_byte_identically(name: str, model_path: str) -> None:
    """A committed schema that has drifted from its model describes nothing."""

    import importlib

    from aebrisk.artifacts import schemas

    module_name, class_name = model_path.split(".")
    module = importlib.import_module(f"aebrisk.artifacts.{module_name}")
    model = getattr(module, class_name)

    regenerated = schemas.schema_bytes(model)

    assert (SCHEMA_DIR / f"{name}.json").read_bytes() == regenerated


def test_the_claims_registry_carries_the_shared_vocabulary() -> None:
    """Three repositories labelling evidence differently cannot be compared."""

    import yaml

    from aebrisk.artifacts import results

    registry = yaml.safe_load((REPO_ROOT / "docs" / "claims.yaml").read_text(encoding="utf-8"))

    assert tuple(registry["allowed_evidence_types"]) == results.ALLOWED_EVIDENCE_TYPES
    assert tuple(registry["allowed_statuses"]) == results.ALLOWED_STATUSES
    assert registry["claims"] == []


def test_the_claims_registry_starts_empty() -> None:
    """No simulation has run, so a claim here would be a number nobody measured."""

    import yaml

    registry = yaml.safe_load((REPO_ROOT / "docs" / "claims.yaml").read_text(encoding="utf-8"))

    assert registry["claims"] == []
