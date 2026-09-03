"""The cross-repository artifact envelope, implemented independently here.

The portfolio specifies one envelope and each of the three repositories writes
its own implementation of it. That is deliberate: three implementations that
agree on one canonical fixture demonstrate the contract, whereas a shared
package would only demonstrate that three projects import the same code. If
this file and the fixture ever disagree, the contract is what changed and every
repository has to be updated together.

This project is the only consumer in the portfolio, and it consumes exactly one
artifact type from exactly one producer, optionally. Everything else is refused.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The one artifact this project may read from another. P3 runs identically
#: whether or not it is present, so consuming one is always an explicit request
#: and never a side effect of opening a file.
#:
#: It is the aggregated error DISTRIBUTION, not `bev-calibration-result/v1`,
#: which is that project's PER-SAMPLE record. Reading per-sample results would
#: mean re-deriving here the distribution the producer already computed, and the
#: two projects could then disagree about the same measurements. This corrects
#: the value used before P3-16; no artifact of either type exists yet.
CALIBRATION_ARTIFACT_TYPE = "calibration-error-distribution/v1"
CALIBRATION_PRODUCER = "bev-calibration-lab"


class PortfolioArtifactEnvelopeV1(BaseModel):
    """Immutable provenance wrapper shared by the three portfolio repositories."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["portfolio-artifact-envelope/v1"]
    producer_repository: Literal[
        "driving-risk-metrics",
        "bev-calibration-lab",
        "perception-error-to-aeb",
    ]
    producer_release: str = Field(pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+$")
    producer_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifact_type: str = Field(min_length=1)
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at_utc: str
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any]

    @field_validator("created_at_utc")
    @classmethod
    def validate_created_at_utc(cls, value: str) -> str:
        """Require an unambiguous UTC timestamp in the canonical trailing-Z form.

        Offsets are refused rather than converted: two producers writing the
        same instant in different notations would sort and compare differently
        for a reader doing the obvious string comparison.
        """

        if not value.endswith("Z"):
            raise ValueError("created_at_utc must end in Z")
        try:
            datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as error:
            raise ValueError("created_at_utc must be a valid ISO 8601 timestamp") from error
        return value


def canonical_json_bytes(value: object) -> bytes:
    """Serialize to the one byte sequence every repository must agree on.

    Sorted keys and no whitespace make the encoding independent of how the
    producer happened to build the object; refusing NaN and Infinity keeps it
    inside what JSON can actually represent, so the hash means the same thing
    to whoever recomputes it.
    """

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def verify_envelope(path: Path, expected_artifact_type: str) -> PortfolioArtifactEnvelopeV1:
    """Load one envelope, failing closed on the wrong type or a tampered payload."""

    document = PortfolioArtifactEnvelopeV1.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
    if document.artifact_type != expected_artifact_type:
        raise ValueError(
            "unexpected artifact type: "
            f"expected {expected_artifact_type!r}, got {document.artifact_type!r}"
        )

    recomputed = hashlib.sha256(canonical_json_bytes(document.payload)).hexdigest()
    if not secrets.compare_digest(recomputed, document.payload_sha256):
        raise ValueError("payload SHA-256 mismatch")
    return document


def read_optional_calibration_artifact(path: Path) -> PortfolioArtifactEnvelopeV1:
    """Read the one artifact this project may consume from `bev-calibration-lab`.

    Both the producer and the type are checked. Checking only the type would
    let any repository publish a file claiming to be a calibration result, and
    this study would then attribute another project's error to its own AEB.
    """

    document = verify_envelope(path, CALIBRATION_ARTIFACT_TYPE)
    if document.producer_repository != CALIBRATION_PRODUCER:
        raise ValueError(
            "unexpected producer for a calibration artifact: "
            f"expected {CALIBRATION_PRODUCER!r}, got {document.producer_repository!r}"
        )
    return document
