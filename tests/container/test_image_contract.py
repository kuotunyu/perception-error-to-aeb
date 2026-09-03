"""Contracts the pinned container must satisfy before any study code runs in it.

Every P3 result will be produced inside this image. If the interpreter, the
nuPlan commit, the user, the mounts or the base image were allowed to drift, a
number could be reproduced only by whoever happened to build the image that
day. These tests run inside the container and pin each of those facts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import sys
from importlib import metadata
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED_NUPLAN_COMMIT = "e9241677997dd86bfc0bcd44817ab04fe631405b"
NUPLAN_URL = "https://github.com/motional/nuplan-devkit"
DATASET_MOUNT = Path("/data/nuplan")
ARTIFACT_ROOT = Path("/work/artifacts")
FROM_LINE = re.compile(r"FROM python:3\.9\.19-slim-bookworm@sha256:[0-9a-f]{64}( AS \w+)?")


def test_interpreter_is_python_3_9_on_linux() -> None:
    """nuPlan at the pinned commit targets 3.9; any other interpreter is a different study."""

    assert sys.version_info[:2] == (3, 9), sys.version
    assert platform.system() == "Linux"


def test_nuplan_is_installed_from_the_pinned_commit() -> None:
    """A branch or tag can move; only a commit id is a reproducible source."""

    direct_url = metadata.distribution("nuplan-devkit").read_text("direct_url.json")

    assert direct_url is not None, "nuplan-devkit was not installed from a VCS URL"
    info = json.loads(direct_url)
    assert info["url"] == NUPLAN_URL
    assert info["vcs_info"]["vcs"] == "git"
    assert info["vcs_info"]["commit_id"] == PINNED_NUPLAN_COMMIT


def test_nuplan_package_is_importable() -> None:
    """Installed metadata without importable code would pass the commit check for nothing."""

    assert importlib.util.find_spec("nuplan") is not None
    assert importlib.util.find_spec("nuplan.database") is not None


def test_process_is_not_root_and_the_artifact_root_is_writable() -> None:
    """Results are written as an unprivileged user into the one writable root."""

    assert os.geteuid() != 0
    probe = ARTIFACT_ROOT / ".write-probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()


def test_sensor_blob_root_is_neither_required_nor_mounted() -> None:
    """P3 is world-state only; a sensor mount would be a silent scope change."""

    assert "NUPLAN_SENSOR_ROOT" not in os.environ
    assert not (DATASET_MOUNT / "sensor_blobs").exists()


def test_dataset_mount_is_read_only() -> None:
    """A study that can write into its dataset can also corrupt it."""

    assert DATASET_MOUNT.is_dir()
    with pytest.raises(OSError):
        (DATASET_MOUNT / ".write-probe").write_text("no", encoding="utf-8")


def test_base_image_is_pinned_by_digest() -> None:
    """A tag is re-pointable; the digest is the image."""

    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]

    assert from_lines, "Dockerfile has no FROM line"
    assert FROM_LINE.fullmatch(from_lines[0]), from_lines[0]
