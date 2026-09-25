"""An installed package must read the same committed files as a checkout.

`configs/` at the repository root is the reviewed record every published number
was produced under. An installed wheel has no repository root to look in, so
the package reads its defaults from copies shipped inside it. These tests hold
each copy to its original byte for byte, then build a wheel the way the release
workflow does, install it into an empty directory and use it from there, so
that a file missing from the wheel fails here rather than on a user's first run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_CONFIGS = (
    "aeb/policy_v1.yaml",
    "errors/formal_v1.yaml",
    "experiments/formal_v1.yaml",
)
BUILD_INPUTS = ("pyproject.toml", "README.md", "LICENSE", "src")
SMOKE_MARKER = "the installed package read its configs and report template"
SMOKE = f"""
import sys
from importlib import resources
from pathlib import Path

import aebrisk
from aebrisk.aeb import state_machine
from aebrisk.attribution import factorial
from aebrisk.errors import pipeline

installed = Path(sys.argv[1]).resolve() / "aebrisk"
assert Path(aebrisk.__file__).resolve().parent == installed, aebrisk.__file__
assert state_machine.load_policy()["schema_version"] == "aeb-policy/v1"
assert pipeline.load_error_config()["severities"] == ["zero", "low", "medium", "high"]
assert factorial.load_experiment_matrix()["schema_version"] == "aeb-experiment-matrix/v1"
template = resources.files("aebrisk.report").joinpath("templates").joinpath("index.html.j2")
assert template.read_text(encoding="utf-8")
print({SMOKE_MARKER!r})
"""


@pytest.mark.parametrize("relative", PACKAGED_CONFIGS)
def test_each_packaged_config_is_byte_identical_to_the_committed_file(relative: str) -> None:
    """A copy that drifted would run a different study under the same name."""

    packaged = resources.files("aebrisk").joinpath("configs")
    for part in relative.split("/"):
        packaged = packaged.joinpath(part)

    assert packaged.read_bytes() == (ROOT / "configs" / relative).read_bytes()


def test_the_package_ships_exactly_the_listed_configs() -> None:
    """A shipped config outside the list above would escape the byte comparison."""

    shipped_root = ROOT / "src" / "aebrisk" / "configs"
    shipped = sorted(
        path.relative_to(shipped_root).as_posix()
        for path in shipped_root.glob("**/*")
        if path.is_file()
    )

    assert shipped == sorted(PACKAGED_CONFIGS)


def test_a_wheel_built_from_the_sdist_installs_and_reads_its_configs(tmp_path: Path) -> None:
    """Built from a copy of the sources, so the checkout gains no build directories."""

    source = tmp_path / "source"
    source.mkdir()
    for name in BUILD_INPUTS:
        origin = ROOT / name
        if origin.is_dir():
            shutil.copytree(origin, source / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copyfile(origin, source / name)
    dist = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(dist), str(source)],
        check=True,
        capture_output=True,
    )
    [wheel] = sorted(dist.glob("*.whl"))
    target = tmp_path / "installed"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            sys.executable,
            "--target",
            str(target),
            str(wheel),
        ],
        check=True,
        capture_output=True,
    )

    # The mutation audit runs this suite with MUTANT_UNDER_TEST set, and the
    # wheel built from its mutated sources forwards every call through a hook
    # that only its own test process has configured. An empty value makes the
    # installed copy run the original functions; outside the audit it does nothing.
    result = subprocess.run(
        [sys.executable, "-c", SMOKE, str(target)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(target), "MUTANT_UNDER_TEST": ""},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert SMOKE_MARKER in result.stdout
