"""Locate a nuPlan installation, and refuse one configured for sensor replay.

Nothing here opens a database. The job is to fail in a second, with a message
naming what is missing, rather than at hour three of a simulation, and to make
a sensor root impossible to configure by accident. This study reads world
state, logs and maps; a sensor root in the environment is the first sign that
somebody has changed what it reads, and it should be noticed rather than
quietly honoured.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: The map release the pinned devkit expects, and the name of the manifest file
#: nuPlan ships beside the four map directories. This study reads no map, so a
#: different version cannot change a single number it produces — which is
#: exactly why the version is RECORDED rather than enforced. A preflight that
#: refused here would block a run for a reason that could not affect it; a
#: preflight that said nothing would leave a reader unable to tell which map
#: release was mounted when the cohort was frozen.
EXPECTED_MAP_VERSION = "nuplan-maps-v1.0"
MAP_VERSION_GLOB = "nuplan-maps-*.json"

#: Set by anyone intending to replay camera or lidar data. This project never
#: does, so its presence is a configuration error rather than an option.
SENSOR_ROOT_VAR = "NUPLAN_SENSOR_ROOT"
DEFAULT_SPLIT = "mini"


@dataclass(frozen=True)
class NuPlanInstallation:
    """Where one nuPlan installation keeps the three things this study reads."""

    data_root: Path
    maps_root: Path
    split: str
    log_databases: tuple[Path, ...]
    #: The map release found beside the map directories, or ``None`` when the
    #: installation carries no manifest naming one.
    map_version: Optional[str] = None


def resolve_installation(
    data_root: Path,
    *,
    split: str = DEFAULT_SPLIT,
    environment: Optional[Mapping[str, str]] = None,
) -> NuPlanInstallation:
    """Describe an installation, failing closed on anything missing or out of scope."""

    source = os.environ if environment is None else environment
    if source.get(SENSOR_ROOT_VAR):
        raise ValueError(
            f"{SENSOR_ROOT_VAR} is set. This study reads world state, logs and maps "
            "only; a configured sensor root means its boundary has moved and the "
            "change should be deliberate rather than inherited from a shell."
        )

    root = Path(data_root)
    if not root.is_dir():
        raise FileNotFoundError(f"nuPlan data root does not exist: {root}")

    split_dir = root / "nuplan-v1.1" / "splits" / split
    if not split_dir.is_dir():
        raise FileNotFoundError(f"nuPlan split directory does not exist: {split_dir}")

    databases = tuple(sorted(split_dir.glob("*.db")))
    if not databases:
        raise FileNotFoundError(f"no log database in the {split!r} split: {split_dir}")

    maps_root = root / "maps"
    if not maps_root.is_dir():
        raise FileNotFoundError(f"nuPlan maps root does not exist: {maps_root}")

    versions = sorted(path.stem for path in maps_root.glob(MAP_VERSION_GLOB))
    return NuPlanInstallation(
        data_root=root,
        maps_root=maps_root,
        split=split,
        log_databases=databases,
        map_version=versions[0] if versions else None,
    )
