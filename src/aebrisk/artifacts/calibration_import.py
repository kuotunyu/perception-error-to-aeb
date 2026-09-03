"""Reading another project's calibration error distribution, optionally.

This is the only place P3 depends on anything a sibling repository produced, and
the dependency is deliberately one-directional and optional: P3 installs, tests
and runs identically whether or not the artifact exists. Consuming one is always
an explicit request, never a side effect of opening a file. A hard dependency
would mean this study could not be run, reviewed or reproduced without first
building another one.

The artifact is the aggregated error DISTRIBUTION, not the producer's per-sample
result. Reading per-sample results would mean re-deriving here the distribution
the producer already computed, and the two projects could then disagree about
the same measurements.

Every refusal below names a way an import would otherwise produce a
configuration that looked measured and was not: the wrong producer, the wrong
artifact type, a protocol hash from a different study, a tampered payload, and
samples that are empty, mismatched, non-finite, or larger than any real
calibration error.

An imported configuration is reported SEPARATELY and never replaces a formal
localization severity. Its numbers come from measured calibration error rather
than from this study's fixed grid, so mixing them would put two definitions of
severity inside one attribution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aebrisk.artifacts.envelope import (
    CALIBRATION_ARTIFACT_TYPE,
    read_optional_calibration_artifact,
)
from aebrisk.errors.pipeline import imported_configuration_id

CALIBRATION_DISTRIBUTION_TYPE = CALIBRATION_ARTIFACT_TYPE

#: A calibration off by more than this is a wrong sensor, not a calibration
#: error. Accepting it would let this study report an AEB failure caused by an
#: obviously broken installation as an effect of perception noise.
MAX_ROTATION_DEG = 30.0
MAX_TRANSLATION_M = 2.0

#: Enough of the payload hash to identify the distribution in a configuration
#: name without making the name unreadable. Sixteen hex characters is far more
#: collision resistance than a handful of imported artifacts needs.
NAME_HASH_PREFIX_LENGTH = 16


@dataclass(frozen=True)
class ImportedCalibrationDistribution:
    """One measured calibration error distribution, and where it came from."""

    producer_release: str
    producer_commit: str
    rotation_samples_rpy_deg: tuple[tuple[float, float, float], ...]
    translation_samples_xyz_m: tuple[tuple[float, float, float], ...]
    source_payload_sha256: str


def parse_distribution_payload(
    payload: dict[str, Any],
) -> tuple[tuple[tuple[float, float, float], ...], tuple[tuple[float, float, float], ...]]:
    """Validate a distribution payload and return its two sample sets.

    Public rather than private, because the two ways a payload can reach this
    project have different failure modes. A payload read from a FILE can never
    carry a non-finite number: the shared canonical encoding refuses NaN and
    Infinity, so such a file could not have been written by a conforming
    producer and would fail the payload hash here anyway. A payload built IN
    MEMORY can carry one, and this is the function that refuses it.
    """

    version = payload.get("schema_version")
    if version != CALIBRATION_DISTRIBUTION_TYPE:
        raise ValueError(
            f"payload schema_version must be {CALIBRATION_DISTRIBUTION_TYPE!r}, got {version!r}"
        )

    rotations = _triples(payload, "rotation_samples_rpy_deg", MAX_ROTATION_DEG, "rotation")
    translations = _triples(payload, "translation_samples_xyz_m", MAX_TRANSLATION_M, "translation")
    if len(rotations) != len(translations):
        raise ValueError(
            f"rotation and translation must carry the same number of samples, got "
            f"{len(rotations)} and {len(translations)}; different counts mean the "
            "producer aggregated them separately, and pairing them here would invent "
            "a correlation that was never measured"
        )
    return rotations, translations


def _triples(payload: dict[str, Any], key: str, limit: float, name: str) -> tuple:
    if key not in payload:
        raise ValueError(f"the payload does not carry {key!r}")

    samples = payload[key]
    if not samples:
        raise ValueError(f"{key!r} is empty; a distribution with no samples has no severity")

    collected: list[tuple[float, float, float]] = []
    for sample in samples:
        if len(sample) != 3:
            raise ValueError(
                f"each {name} sample must have three components, got {len(sample)}; "
                "fewer would silently drop an axis of the error"
            )
        for component in sample:
            if not isinstance(component, (int, float)) or isinstance(component, bool):
                raise ValueError(f"{name} components must be numbers")
            if not math.isfinite(component):
                raise ValueError(
                    f"{name} components must be finite; one NaN would propagate into "
                    "every perturbed box in the study"
                )
            if abs(component) > limit:
                raise ValueError(
                    f"{name} component {component} exceeds the plausible bound {limit}; "
                    "an error this large is a wrong sensor rather than a calibration "
                    "error, and attributing it to perception noise would be false"
                )
        collected.append((float(sample[0]), float(sample[1]), float(sample[2])))

    return tuple(collected)


def import_calibration_distribution(
    envelope_path: Path,
    expected_protocol_hash: str,
) -> ImportedCalibrationDistribution:
    """Read and check one calibration error distribution, or refuse it."""

    document = read_optional_calibration_artifact(envelope_path)

    if document.protocol_hash != expected_protocol_hash:
        raise ValueError(
            f"protocol hash mismatch: the artifact was measured under "
            f"{document.protocol_hash!r} and this study runs {expected_protocol_hash!r}; "
            "a distribution from another protocol describes another study"
        )

    rotations, translations = parse_distribution_payload(document.payload)

    return ImportedCalibrationDistribution(
        producer_release=document.producer_release,
        producer_commit=document.producer_commit,
        rotation_samples_rpy_deg=rotations,
        translation_samples_xyz_m=translations,
        source_payload_sha256=document.payload_sha256,
    )


def imported_configuration_name(imported: ImportedCalibrationDistribution) -> str:
    """Name the configuration this distribution produces.

    Derived from the payload hash, so two imports of the same distribution name
    the same configuration and two imports of different ones never collide.
    """

    return imported_configuration_id(imported.source_payload_sha256[:NAME_HASH_PREFIX_LENGTH])
