"""Contracts for reading another project's calibration error distribution.

This is the only place P3 depends on anything a sibling repository produced, and
the dependency is deliberately one-directional and optional: P3 installs, tests
and runs identically whether or not the artifact exists. Consuming one is always
an explicit request, never a side effect of opening a file.

That optionality is what keeps the portfolio's three repositories independent.
A hard dependency would mean this study could not be run, reviewed or reproduced
without first building another one.

The import is refused on every axis that could make it silently wrong: the wrong
producer, the wrong artifact type, a protocol hash that does not match the study
being run, a tampered payload, and samples that are empty, non-finite, or larger
than any real calibration error. Each of these would otherwise produce a
configuration that looked measured and was not.

An imported configuration is reported SEPARATELY and never replaces the formal
localization severity. Its numbers come from measured calibration error rather
than from this study's fixed grid, so mixing them would put two different
definitions of severity inside one attribution.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PROTOCOL_HASH = "b" * 64
DATASET_HASH = "c" * 64
COMMIT = "d" * 40


def load_import_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.artifacts import calibration_import
    except ImportError:
        pytest.fail("aebrisk.artifacts.calibration_import is missing", pytrace=False)
    return calibration_import


def payload(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "calibration-error-distribution/v1",
        "rotation_samples_rpy_deg": [[0.4, -0.2, 1.1], [0.1, 0.3, -0.7]],
        "translation_samples_xyz_m": [[0.02, -0.01, 0.03], [0.01, 0.02, -0.02]],
    }
    document.update(overrides)
    return document


def write_envelope(directory: Path, **overrides: Any) -> Path:
    from aebrisk.artifacts.envelope import canonical_json_bytes

    body = overrides.pop("payload", payload())
    envelope: dict[str, Any] = {
        "schema_version": "portfolio-artifact-envelope/v1",
        "producer_repository": "bev-calibration-lab",
        "producer_release": "v1.0.0",
        "producer_commit": COMMIT,
        "artifact_type": "calibration-error-distribution/v1",
        "protocol_hash": PROTOCOL_HASH,
        "dataset_manifest_hash": DATASET_HASH,
        "created_at_utc": "2026-09-03T00:00:00Z",
        "payload_sha256": hashlib.sha256(canonical_json_bytes(body)).hexdigest(),
        "payload": body,
    }
    envelope.update(overrides)
    path = directory / "calibration.json"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(envelope, handle, sort_keys=True, separators=(",", ":"))
    return path


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_an_approved_artifact_is_imported(tmp_path: Path) -> None:
    """The base case, so every refusal below is a refusal of something specific."""

    calibration_import = load_import_module()

    imported = calibration_import.import_calibration_distribution(
        write_envelope(tmp_path), PROTOCOL_HASH
    )

    assert imported.rotation_samples_rpy_deg == ((0.4, -0.2, 1.1), (0.1, 0.3, -0.7))
    assert imported.translation_samples_xyz_m == ((0.02, -0.01, 0.03), (0.01, 0.02, -0.02))


def test_the_import_records_where_it_came_from(tmp_path: Path) -> None:
    """A configuration built from it must be traceable to the exact producing run."""

    calibration_import = load_import_module()

    imported = calibration_import.import_calibration_distribution(
        write_envelope(tmp_path), PROTOCOL_HASH
    )

    assert imported.producer_release == "v1.0.0"
    assert imported.producer_commit == COMMIT
    assert len(imported.source_payload_sha256) == 64


def test_the_configuration_name_is_derived_from_the_payload_hash(tmp_path: Path) -> None:
    """Two imports of the same distribution name the same configuration.

    Two imports of DIFFERENT distributions must not, or two runs would be filed
    under one name.
    """

    calibration_import = load_import_module()

    imported = calibration_import.import_calibration_distribution(
        write_envelope(tmp_path), PROTOCOL_HASH
    )
    name = calibration_import.imported_configuration_name(imported)

    assert name.startswith("calibration_imported_")
    assert imported.source_payload_sha256.startswith(name.split("_")[-1])


def test_two_different_distributions_get_different_names(tmp_path: Path) -> None:
    """The pair to the test above; a constant name would satisfy it alone."""

    calibration_import = load_import_module()

    first_dir, second_dir = tmp_path / "a", tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()

    first = calibration_import.import_calibration_distribution(
        write_envelope(first_dir), PROTOCOL_HASH
    )
    second = calibration_import.import_calibration_distribution(
        write_envelope(
            second_dir,
            payload=payload(
                rotation_samples_rpy_deg=[[1.0, 1.0, 1.0]],
                translation_samples_xyz_m=[[0.5, 0.5, 0.5]],
            ),
        ),
        PROTOCOL_HASH,
    )

    assert calibration_import.imported_configuration_name(
        first
    ) != calibration_import.imported_configuration_name(second)


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_an_artifact_from_the_wrong_producer_is_refused(tmp_path: Path) -> None:
    """Any repository could otherwise publish a file claiming to be this one."""

    calibration_import = load_import_module()
    path = write_envelope(tmp_path, producer_repository="driving-risk-metrics")

    with pytest.raises(
        ValueError, match=r"^unexpected\ producer\ for\ a\ calibration\ artifact:\ "
    ):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


def test_an_artifact_of_the_wrong_type_is_refused(tmp_path: Path) -> None:
    """The per-sample calibration result is a different thing from the distribution."""

    calibration_import = load_import_module()
    path = write_envelope(tmp_path, artifact_type="bev-calibration-result/v1")

    with pytest.raises(ValueError, match=r"^unexpected artifact type: "):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


def test_the_project_one_envelope_fixture_is_refused(tmp_path: Path) -> None:
    """The canonical shared fixture is a valid envelope and the wrong artifact.

    It is the file most likely to be passed here by mistake, because all three
    repositories carry a byte-identical copy of it.
    """

    calibration_import = load_import_module()
    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures" / "portfolio_artifact_envelope_v1.json"
    )
    copied = tmp_path / "copied.json"
    copied.write_bytes(fixture.read_bytes())

    with pytest.raises(ValueError, match=r"^unexpected artifact type: "):
        calibration_import.import_calibration_distribution(copied, PROTOCOL_HASH)


def test_a_protocol_hash_mismatch_is_refused(tmp_path: Path) -> None:
    """A distribution measured under another protocol describes another study."""

    calibration_import = load_import_module()

    with pytest.raises(
        ValueError, match=r"^protocol\ hash\ mismatch:\ the\ artifact\ was\ measured\ under\ "
    ):
        calibration_import.import_calibration_distribution(write_envelope(tmp_path), "a" * 64)


def test_a_tampered_payload_is_refused(tmp_path: Path) -> None:
    """The envelope's own hash check, reached through this path too."""

    calibration_import = load_import_module()
    path = write_envelope(tmp_path, payload_sha256="e" * 64)

    with pytest.raises(ValueError, match=r"^payload SHA-256 mismatch$"):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


def test_an_empty_distribution_is_refused(tmp_path: Path) -> None:
    """A distribution with no samples has no severity to apply."""

    calibration_import = load_import_module()
    path = write_envelope(
        tmp_path,
        payload=payload(rotation_samples_rpy_deg=[], translation_samples_xyz_m=[]),
    )

    with pytest.raises(ValueError, match="empty"):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


def test_unequal_sample_counts_are_refused(tmp_path: Path) -> None:
    """Rotation and translation are measured together, one pair per sample.

    Different counts mean the producer aggregated them separately, and pairing
    them here would invent a correlation that was never measured.
    """

    calibration_import = load_import_module()
    path = write_envelope(
        tmp_path,
        payload=payload(translation_samples_xyz_m=[[0.01, 0.0, 0.0]]),
    )

    with pytest.raises(
        ValueError,
        match=r"^rotation\ and\ translation\ must\ carry\ the\ same\ number\ of\ samples,\ got\ ",
    ):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


def test_a_non_finite_sample_is_refused() -> None:
    """One NaN would propagate into every perturbed box in the study.

    Checked on the payload rather than through a file, because a FILE can never
    carry one: the shared canonical encoding refuses NaN and Infinity, so no
    conforming producer could have written it. A payload built IN MEMORY can,
    and that is the path this guard defends.
    """

    calibration_import = load_import_module()

    with pytest.raises(ValueError, match="finite"):
        calibration_import.parse_distribution_payload(
            payload(rotation_samples_rpy_deg=[[0.1, 0.2, float("nan")]])
        )


def test_a_sample_component_that_is_not_a_number_is_refused() -> None:
    """The same in-memory path, one step earlier."""

    calibration_import = load_import_module()

    with pytest.raises(ValueError, match="must be numbers"):
        calibration_import.parse_distribution_payload(
            payload(rotation_samples_rpy_deg=[[0.1, 0.2, "0.3"]])
        )


def test_an_out_of_bounds_rotation_is_refused(tmp_path: Path) -> None:
    """A calibration off by 90 degrees is a wrong sensor, not a calibration error.

    Accepting it would let this study report an AEB failure caused by an
    obviously broken installation as an effect of perception noise.
    """

    calibration_import = load_import_module()
    path = write_envelope(
        tmp_path,
        payload=payload(rotation_samples_rpy_deg=[[90.0, 0.0, 0.0], [0.1, 0.2, 0.3]]),
    )

    with pytest.raises(ValueError, match=(r"^rotation component .* exceeds the plausible bound ")):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


def test_an_out_of_bounds_translation_is_refused(tmp_path: Path) -> None:
    """The same, on the axis where metres rather than degrees give it away."""

    calibration_import = load_import_module()
    path = write_envelope(
        tmp_path,
        payload=payload(translation_samples_xyz_m=[[5.0, 0.0, 0.0], [0.01, 0.0, 0.0]]),
    )

    with pytest.raises(
        ValueError, match=(r"^translation component .* exceeds the plausible bound ")
    ):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


def test_a_sample_that_is_not_a_triple_is_refused(tmp_path: Path) -> None:
    """Two components would silently drop an axis of the error."""

    calibration_import = load_import_module()
    path = write_envelope(
        tmp_path,
        payload=payload(rotation_samples_rpy_deg=[[0.1, 0.2], [0.1, 0.2, 0.3]]),
    )

    with pytest.raises(ValueError, match=r"^each\ "):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


def test_a_payload_with_the_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    """The envelope says what the artifact is; the payload says what shape it has."""

    calibration_import = load_import_module()
    path = write_envelope(
        tmp_path, payload=payload(schema_version="calibration-error-distribution/v2")
    )

    with pytest.raises(ValueError, match=r"^payload\ schema_version\ must\ be\ "):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


def test_a_payload_missing_its_samples_is_refused(tmp_path: Path) -> None:
    """A key absent is not a distribution of zero samples."""

    calibration_import = load_import_module()
    body = payload()
    del body["rotation_samples_rpy_deg"]
    path = write_envelope(tmp_path, payload=body)

    with pytest.raises(ValueError, match="rotation_samples_rpy_deg"):
        calibration_import.import_calibration_distribution(path, PROTOCOL_HASH)


# --------------------------------------------------------------------------
# Optionality
# --------------------------------------------------------------------------


def test_the_module_imports_with_no_artifact_present() -> None:
    """P3 installs and its tests pass whether or not the sibling has released.

    A hard dependency would mean this study could not be run, reviewed or
    reproduced without first building another one.
    """

    calibration_import = load_import_module()

    assert calibration_import.CALIBRATION_DISTRIBUTION_TYPE


def test_a_missing_file_raises_rather_than_returning_a_default(tmp_path: Path) -> None:
    """Silently continuing without it would produce a configuration from nothing."""

    calibration_import = load_import_module()

    with pytest.raises(FileNotFoundError):
        calibration_import.import_calibration_distribution(tmp_path / "absent.json", PROTOCOL_HASH)


def test_the_imported_distribution_is_frozen(tmp_path: Path) -> None:
    """It is another project's measurement, and this one may not edit it."""

    import dataclasses

    calibration_import = load_import_module()
    imported = calibration_import.import_calibration_distribution(
        write_envelope(tmp_path), PROTOCOL_HASH
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        imported.producer_release = "v9.9.9"  # type: ignore[misc]
