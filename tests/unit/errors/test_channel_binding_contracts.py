"""Binding the error channels: the full imported-configuration refusal, and a named config file.

The imported-configuration refusal is the only explanation a caller gets for a
run that was never bound, so it is held word for word. And the error
configuration can be read from a file the caller names as well as from the
package's committed copy; that file is read as UTF-8 text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import aebrisk.errors.channels as channels
from aebrisk.committed_config import read_committed_config
from aebrisk.errors.pipeline import ErrorConfiguration, load_error_config

CHANNELS = ("dropout", "localization_shape", "latency", "track_instability")


def test_an_imported_configuration_is_refused_in_full() -> None:
    """The refusal says what the severities are and why this grid cannot bind them."""

    imported = ErrorConfiguration(
        configuration_id="calibration_imported_0123456789abcdef",
        severity_by_channel=dict.fromkeys(CHANNELS, "zero"),
        imported=True,
    )
    message = (
        "'calibration_imported_0123456789abcdef' is an imported configuration; its "
        "severities are measured calibration error rather than this study's fixed grid, "
        "so binding it here would read a 'medium' that means something else"
    )

    with pytest.raises(ValueError, match=rf"^{re.escape(message)}$"):
        channels.ScenarioChannels(imported)


def test_an_error_configuration_is_read_from_the_file_a_caller_names(tmp_path: Path) -> None:
    """A named file is parsed as it stands, not replaced by the committed grid."""

    committed = read_committed_config("errors", "formal_v1.yaml")
    edited = committed.replace(
        "latency_s: [0.0, 0.10, 0.20, 0.40]", "latency_s: [0.0, 0.1, 0.3, 0.5]"
    )
    assert edited != committed
    path = tmp_path / "errors.yaml"
    path.write_text("# Latency in seconds, not µs.\n" + edited, encoding="utf-8")

    document = load_error_config(path)

    assert document["channels"]["latency"]["latency_s"] == [0.0, 0.1, 0.3, 0.5]
    assert document["channels"]["dropout"] == load_error_config()["channels"]["dropout"]
