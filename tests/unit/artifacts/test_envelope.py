"""Contracts for the cross-repository portfolio artifact envelope.

The envelope is specified once in the portfolio plan and implemented separately
in each repository on purpose: three independent implementations that agree on
one canonical fixture prove the contract, while a shared package would only
prove that three projects import the same code. The fixture in
``tests/fixtures`` is therefore byte-identical to the other two repositories'.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "portfolio_artifact_envelope_v1.json"


def load_envelope_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.artifacts import envelope
    except ImportError:
        pytest.fail("aebrisk.artifacts.envelope is missing", pytrace=False)
    return envelope


def canonical_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    values.update(overrides)
    return values


def write_envelope(path: Path, values: dict[str, Any]) -> Path:
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_the_canonical_fixture_validates_unchanged() -> None:
    """Three repositories that disagree on this file do not share a contract."""

    envelope = load_envelope_module()

    document = envelope.PortfolioArtifactEnvelopeV1.model_validate(canonical_values())

    assert document.schema_version == "portfolio-artifact-envelope/v1"
    assert document.producer_repository == "driving-risk-metrics"


def test_the_fixture_is_the_canonical_compact_serialization() -> None:
    """A reformatted copy would still parse here and fail a byte comparison there.

    The file is the canonical encoding plus the trailing newline that makes it a
    well-formed text file. Both halves matter: the three repositories compare
    these bytes, so a pretty-printed copy or a missing newline is a difference.
    """

    envelope = load_envelope_module()
    values = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert FIXTURE.read_bytes() == envelope.canonical_json_bytes(values) + b"\n"


def test_the_payload_hash_in_the_fixture_is_the_hash_of_its_payload() -> None:
    """The fixture must be self-consistent or it proves nothing about hashing."""

    import hashlib

    envelope = load_envelope_module()
    values = canonical_values()

    digest = hashlib.sha256(envelope.canonical_json_bytes(values["payload"])).hexdigest()

    assert digest == values["payload_sha256"]


@pytest.mark.parametrize(
    "producer",
    ["driving-risk-metrics", "bev-calibration-lab", "perception-error-to-aeb"],
)
def test_every_portfolio_repository_may_produce_an_envelope(producer: str) -> None:
    """Dropping one producer would silently make its artifacts unreadable here."""

    envelope = load_envelope_module()

    document = envelope.PortfolioArtifactEnvelopeV1.model_validate(
        canonical_values(producer_repository=producer)
    )

    assert document.producer_repository == producer


def test_an_unknown_producer_is_refused() -> None:
    """Only the three repositories in the portfolio may claim to have produced one."""

    from pydantic import ValidationError

    envelope = load_envelope_module()

    with pytest.raises(ValidationError):
        envelope.PortfolioArtifactEnvelopeV1.model_validate(
            canonical_values(producer_repository="some-other-project")
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("schema_version", "portfolio-artifact-envelope/v2"),
        ("producer_release", "1.0.0"),
        ("producer_commit", "a" * 39),
        ("protocol_hash", "b" * 63),
        ("dataset_manifest_hash", "c" * 65),
        ("payload_sha256", "not-a-hash"),
        ("artifact_type", ""),
        ("created_at_utc", "2026-08-31T00:00:00+00:00"),
        ("created_at_utc", "not a timestamp"),
        ("created_at_utc", "definitely-not-a-dateZ"),
    ],
)
def test_a_malformed_field_is_refused(field: str, invalid_value: str) -> None:
    """Provenance that cannot be parsed is provenance nobody can check."""

    from pydantic import ValidationError

    envelope = load_envelope_module()

    with pytest.raises(ValidationError):
        envelope.PortfolioArtifactEnvelopeV1.model_validate(
            canonical_values(**{field: invalid_value})
        )


def test_an_extra_field_is_refused() -> None:
    """An unversioned field would change the shared contract without saying so."""

    from pydantic import ValidationError

    envelope = load_envelope_module()

    with pytest.raises(ValidationError):
        envelope.PortfolioArtifactEnvelopeV1.model_validate(canonical_values(confidence="high"))


def test_the_model_is_frozen() -> None:
    """An envelope that can be edited after validation is not provenance."""

    from pydantic import ValidationError

    envelope = load_envelope_module()
    document = envelope.PortfolioArtifactEnvelopeV1.model_validate(canonical_values())

    with pytest.raises(
        ValidationError,
        match=r"^1 validation error for PortfolioArtifactEnvelopeV1\nproducer_release\n  Instance is frozen",
    ):
        document.producer_release = "v9.9.9"  # type: ignore[misc]


def test_canonical_bytes_are_sorted_compact_and_utf8() -> None:
    """Two producers must serialize the same payload to the same bytes."""

    envelope = load_envelope_module()

    assert envelope.canonical_json_bytes({"b": 1, "a": "é"}) == b'{"a":"\xc3\xa9","b":1}'


def test_canonical_bytes_refuse_non_finite_numbers() -> None:
    """NaN and Infinity are not JSON, and they would hash differently per writer."""

    envelope = load_envelope_module()

    with pytest.raises(ValueError):
        envelope.canonical_json_bytes({"value": float("nan")})


def test_a_verified_envelope_is_returned(tmp_path: Path) -> None:
    """The success path must pass or every consumer would refuse valid input."""

    envelope = load_envelope_module()
    path = write_envelope(tmp_path / "envelope.json", canonical_values())

    document = envelope.verify_envelope(path, "portfolio-contract-fixture/v1")

    assert document.artifact_type == "portfolio-contract-fixture/v1"


def test_an_envelope_of_the_wrong_artifact_type_is_refused(tmp_path: Path) -> None:
    """A consumer that accepts any type would read a calibration file as a cohort."""

    envelope = load_envelope_module()
    path = write_envelope(tmp_path / "envelope.json", canonical_values())

    with pytest.raises(ValueError, match=r"^unexpected\ artifact\ type:\ "):
        envelope.verify_envelope(path, "calibration-error-distribution/v1")


def test_a_payload_that_does_not_match_its_hash_is_refused(tmp_path: Path) -> None:
    """A payload edited after signing is exactly what the hash exists to catch."""

    envelope = load_envelope_module()
    values = canonical_values()
    values["payload"] = {"fixture": "tampered", "values": [0, 1]}
    path = write_envelope(tmp_path / "envelope.json", values)

    with pytest.raises(ValueError, match=r"^payload SHA-256 mismatch$"):
        envelope.verify_envelope(path, "portfolio-contract-fixture/v1")
