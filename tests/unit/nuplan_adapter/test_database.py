"""Contracts for locating a nuPlan installation without opening one.

Nothing here reads a database. The point is to fail early and legibly when the
dataset roots are missing or misconfigured, rather than at hour three of a
simulation, and to make it impossible to configure a sensor root by accident:
this study reads world state only, and a sensor path in the environment would
be the first sign that somebody changed that.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest


def load_database_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.nuplan_adapter import database
    except ImportError:
        pytest.fail("aebrisk.nuplan_adapter.database is missing", pytrace=False)
    return database


def build_installation(root: Path, *, logs: int = 2, maps: bool = True) -> Path:
    split = root / "nuplan-v1.1" / "splits" / "mini"
    split.mkdir(parents=True)
    for index in range(logs):
        (split / f"2021.05.12.22.0{index}.00_veh-35_00000_00001.db").write_bytes(b"sqlite")
    if maps:
        (root / "maps").mkdir()
    return root


def test_a_complete_installation_is_described(tmp_path: Path) -> None:
    """The success path must pass or no run could ever locate its data."""

    database = load_database_module()
    root = build_installation(tmp_path / "nuplan")

    layout = database.resolve_installation(root)

    assert layout.data_root == root
    assert layout.maps_root == root / "maps"
    assert len(layout.log_databases) == 2


def test_the_log_databases_are_returned_in_a_stable_order(tmp_path: Path) -> None:
    """Directory order is not reproducible; a frozen cohort must not inherit it."""

    database = load_database_module()
    root = build_installation(tmp_path / "nuplan", logs=5)

    layout = database.resolve_installation(root)

    assert [path.name for path in layout.log_databases] == sorted(
        path.name for path in layout.log_databases
    )


def test_a_missing_data_root_is_refused(tmp_path: Path) -> None:
    """Failing here costs a second; failing at simulation time costs the run."""

    database = load_database_module()

    with pytest.raises(FileNotFoundError, match=r"^nuPlan\ data\ root\ does\ not\ exist:\ "):
        database.resolve_installation(tmp_path / "absent")


def test_a_missing_split_directory_is_refused(tmp_path: Path) -> None:
    """A data root without the split is a download that stopped half way."""

    database = load_database_module()
    root = tmp_path / "nuplan"
    root.mkdir()

    with pytest.raises(
        FileNotFoundError,
        match=r"^(no\ log\ database\ in\ the\ |nuPlan\ split\ directory\ does\ not\ exist:\ )",
    ):
        database.resolve_installation(root)


def test_a_split_with_no_databases_is_refused(tmp_path: Path) -> None:
    """An empty split would produce an empty cohort and a study of nothing."""

    database = load_database_module()
    root = tmp_path / "nuplan"
    (root / "nuplan-v1.1" / "splits" / "mini").mkdir(parents=True)
    (root / "maps").mkdir()

    with pytest.raises(FileNotFoundError, match=r"^no\ log\ database\ in\ the\ "):
        database.resolve_installation(root)


def test_a_missing_maps_root_is_refused(tmp_path: Path) -> None:
    """The route follower needs the map; discovering that later wastes the run."""

    database = load_database_module()
    root = build_installation(tmp_path / "nuplan", maps=False)

    with pytest.raises(FileNotFoundError, match=r"^nuPlan\ maps\ root\ does\ not\ exist:\ "):
        database.resolve_installation(root)


def test_a_named_split_other_than_mini_can_be_resolved(tmp_path: Path) -> None:
    """The formal cohort moves off mini later; the resolver must not hard-code it."""

    database = load_database_module()
    root = tmp_path / "nuplan"
    split = root / "nuplan-v1.1" / "splits" / "trainval"
    split.mkdir(parents=True)
    (split / "log.db").write_bytes(b"sqlite")
    (root / "maps").mkdir()

    layout = database.resolve_installation(root, split="trainval")

    assert layout.split == "trainval"
    assert len(layout.log_databases) == 1


def test_a_configured_sensor_root_is_refused(tmp_path: Path) -> None:
    """A sensor root in the environment means somebody changed what this study reads.

    Refusing it is the point: the boundary is meant to be noticed when it moves,
    not quietly widened by a variable somebody exported.
    """

    database = load_database_module()
    root = build_installation(tmp_path / "nuplan")

    with pytest.raises(ValueError, match="sensor"):
        database.resolve_installation(root, environment={"NUPLAN_SENSOR_ROOT": "/data/sensors"})


def test_an_empty_sensor_root_variable_is_ignored(tmp_path: Path) -> None:
    """An exported-but-empty variable is not a configured sensor root."""

    database = load_database_module()
    root = build_installation(tmp_path / "nuplan")

    layout = database.resolve_installation(root, environment={"NUPLAN_SENSOR_ROOT": ""})

    assert layout.data_root == root


def test_the_layout_is_frozen(tmp_path: Path) -> None:
    """The resolved paths are provenance for every run that uses them."""

    import dataclasses

    database = load_database_module()
    layout = database.resolve_installation(build_installation(tmp_path / "nuplan"))

    with pytest.raises(dataclasses.FrozenInstanceError):
        layout.split = "trainval"  # type: ignore[misc]


def test_the_environment_defaults_to_the_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is worthless if it only inspects a dictionary a caller passes in."""

    database = load_database_module()
    root = build_installation(tmp_path / "nuplan")
    monkeypatch.setenv("NUPLAN_SENSOR_ROOT", "/data/sensors")

    with pytest.raises(ValueError, match="sensor"):
        database.resolve_installation(root)
