"""The shape of artifact this project will accept from `bev-calibration-lab`.

The fixture beside this test is committed BEFORE that project has released
anything, and that is the point. It states, in bytes, what P3 will accept, so
the producer can be built against a fixed target rather than the two projects
negotiating a format after both have results. If the real artifact ever fails
this test, the disagreement is visible here rather than in a run that quietly
imported the wrong numbers.

It is a fixture, not a measurement. Its three samples are made up, and no figure
derived from them may be reported as anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aebrisk.artifacts.calibration_import import (
    CALIBRATION_DISTRIBUTION_TYPE,
    import_calibration_distribution,
)

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / ("calibration_error_distribution_v1.json")
)
FIXTURE_PROTOCOL_HASH = "2" * 64


def test_the_committed_fixture_is_accepted() -> None:
    """The contract, stated positively."""

    imported = import_calibration_distribution(FIXTURE, FIXTURE_PROTOCOL_HASH)

    assert len(imported.rotation_samples_rpy_deg) == 3
    assert len(imported.translation_samples_xyz_m) == 3
    assert imported.producer_release == "v1.0.0"


def test_the_fixture_declares_the_artifact_type_this_project_consumes() -> None:
    """Named in the envelope and in the payload, and they must agree."""

    document = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert document["artifact_type"] == CALIBRATION_DISTRIBUTION_TYPE
    assert document["payload"]["schema_version"] == CALIBRATION_DISTRIBUTION_TYPE


def test_the_fixture_is_stored_canonically() -> None:
    """It is a byte-level contract, so its bytes must be the canonical ones.

    A fixture that was merely valid JSON could be reformatted without anyone
    noticing, and then two repositories would be comparing different files
    while believing they shared one.
    """

    from aebrisk.artifacts.envelope import canonical_json_bytes

    raw = FIXTURE.read_bytes()

    assert raw == canonical_json_bytes(json.loads(raw.decode("utf-8")))
    assert b"\r" not in raw


def test_the_fixture_is_rejected_under_another_protocol() -> None:
    """The same bytes describe another study if the protocol differs."""

    with pytest.raises(
        ValueError, match=r"^protocol hash mismatch: the artifact was measured under "
    ):
        import_calibration_distribution(FIXTURE, "9" * 64)


def test_an_extra_payload_field_does_not_break_the_import() -> None:
    """The fixture carries `sample_count`, which this project does not read.

    The producer must be free to publish more than P3 consumes, or every field
    it adds would break this project. What P3 requires is checked; what it does
    not require is ignored.
    """

    document = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert "sample_count" in document["payload"]
    assert import_calibration_distribution(FIXTURE, FIXTURE_PROTOCOL_HASH)
