"""Contracts for the read-only copies the package keeps of its committed configs."""

from __future__ import annotations

from typing import Any

import pytest


def test_a_frozen_copy_equals_its_source_and_refuses_changes_at_every_depth() -> None:
    from aebrisk.committed_config import frozen

    source: dict[str, Any] = {"stage": {"thresholds": [1.0, {"deep": 2.0}]}, "name": "policy"}
    copy = frozen(source)

    assert copy == {"stage": {"thresholds": (1.0, {"deep": 2.0})}, "name": "policy"}
    with pytest.raises(TypeError):
        copy["name"] = "other"
    with pytest.raises(TypeError):
        copy["stage"]["thresholds"][0] = 3.0
    with pytest.raises(TypeError):
        copy["stage"]["thresholds"][1]["deep"] = 3.0


def test_a_frozen_copy_does_not_follow_later_changes_to_its_source() -> None:
    """A cached copy that aliased the parsed document could still be edited through it."""

    from aebrisk.committed_config import frozen

    source: dict[str, Any] = {"thresholds": [1.0], "stage": {"ttc": 3.0}}
    copy = frozen(source)
    source["thresholds"].append(2.0)
    source["stage"]["ttc"] = 4.0

    assert copy["thresholds"] == (1.0,)
    assert copy["stage"]["ttc"] == 3.0
