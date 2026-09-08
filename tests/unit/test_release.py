"""Behavior tests for deterministic release artifacts."""

from __future__ import annotations

import hashlib
import importlib
import io
import runpy
import sys
import tarfile
import types
import zipfile
from pathlib import Path

import pytest


def release_module() -> types.ModuleType:
    try:
        return importlib.import_module("aebrisk.release")
    except ModuleNotFoundError:
        pytest.fail("aebrisk.release is missing")


def write_wheel(
    directory: Path,
    version: str,
    *,
    package_name: str = "perception-error-to-aeb",
    metadata_version: str | None = None,
) -> Path:
    path = directory / f"perception_error_to_aeb-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"perception_error_to_aeb-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: {package_name}\n"
            f"Version: {metadata_version or version}\n",
        )
    return path


def write_sdist(
    directory: Path,
    version: str,
    *,
    package_name: str = "perception-error-to-aeb",
    nested_metadata: bool = False,
    member_mtime: int = 0,
    metadata_version: str | None = None,
) -> Path:
    path = directory / f"perception_error_to_aeb-{version}.tar.gz"
    payload = (
        f"Metadata-Version: 2.4\nName: {package_name}\nVersion: {metadata_version or version}\n"
    ).encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(f"perception_error_to_aeb-{version}/PKG-INFO")
        info.size = len(payload)
        info.mtime = member_mtime
        archive.addfile(info, io.BytesIO(payload))
        if nested_metadata:
            nested = tarfile.TarInfo(
                f"perception_error_to_aeb-{version}/src/perception_error_to_aeb.egg-info/PKG-INFO"
            )
            nested.size = len(payload)
            nested.mtime = member_mtime
            archive.addfile(nested, io.BytesIO(payload))
    return path


def test_release_versions_bind_tag_installed_package_wheel_and_sdist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale installed package or archive must stop a v1.0.0 release."""

    write_wheel(tmp_path, "1.0.0")
    write_sdist(tmp_path, "1.0.0")
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    assert release.verify_release_versions("v1.0.0", tmp_path) == {
        "installed": "1.0.0",
        "sdist": "1.0.0",
        "wheel": "1.0.0",
    }


@pytest.mark.parametrize(
    ("installed", "wheel", "sdist", "match"),
    [
        ("0.1.0", "1.0.0", "1.0.0", "installed"),
        ("1.0.0", "0.1.0", "1.0.0", "wheel"),
        ("1.0.0", "1.0.0", "0.1.0", "sdist"),
    ],
)
def test_release_versions_refuse_any_component_that_disagrees_with_the_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed: str,
    wheel: str,
    sdist: str,
    match: str,
) -> None:
    """A stale installed package or archive version must disagree with the tag."""

    write_wheel(tmp_path, wheel)
    write_sdist(tmp_path, sdist)
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: installed)

    with pytest.raises(ValueError, match=match):
        release.verify_release_versions("v1.0.0", tmp_path)


@pytest.mark.parametrize("artifact", ["wheel", "sdist"])
def test_release_versions_refuse_stale_metadata_under_canonical_archive_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    """Canonical filenames and member paths cannot conceal stale embedded versions."""

    wheel = write_wheel(
        tmp_path, "1.0.0", metadata_version="0.1.0" if artifact == "wheel" else "1.0.0"
    )
    sdist = write_sdist(
        tmp_path, "1.0.0", metadata_version="0.1.0" if artifact == "sdist" else "1.0.0"
    )
    assert wheel.name == "perception_error_to_aeb-1.0.0-py3-none-any.whl"
    assert sdist.name == "perception_error_to_aeb-1.0.0.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        assert archive.namelist() == ["perception_error_to_aeb-1.0.0.dist-info/METADATA"]
    with tarfile.open(sdist, "r:gz") as source:
        assert source.getnames() == ["perception_error_to_aeb-1.0.0/PKG-INFO"]
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    with pytest.raises(ValueError, match=f"release version 1.0.0 disagrees with {artifact}=0.1.0"):
        release.verify_release_versions("v1.0.0", tmp_path)


def test_release_versions_accept_the_canonical_sdist_metadata_when_an_egg_info_copy_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal setuptools sdist can carry nested egg-info metadata as well as PKG-INFO."""

    write_wheel(tmp_path, "1.0.0")
    write_sdist(tmp_path, "1.0.0", nested_metadata=True)
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    assert release.verify_release_versions("v1.0.0", tmp_path)["sdist"] == "1.0.0"


@pytest.mark.parametrize("artifact", ["wheel", "sdist"])
def test_release_versions_refuse_wrong_distribution_package_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    """A same-version archive for another package must never enter this release."""

    write_wheel(
        tmp_path,
        "1.0.0",
        package_name="another-package" if artifact == "wheel" else "perception-error-to-aeb",
    )
    write_sdist(
        tmp_path,
        "1.0.0",
        package_name="another-package" if artifact == "sdist" else "perception-error-to-aeb",
    )
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    with pytest.raises(ValueError, match=f"{artifact} package"):
        release.verify_release_versions("v1.0.0", tmp_path)


def test_release_versions_refuse_a_filename_that_disagrees_with_the_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Internal metadata alone cannot make a stale or renamed release asset canonical."""

    wheel = write_wheel(tmp_path, "1.0.0")
    wheel.rename(tmp_path / "renamed-1.0.0-py3-none-any.whl")
    write_sdist(tmp_path, "1.0.0")
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    with pytest.raises(ValueError, match="wheel filename"):
        release.verify_release_versions("v1.0.0", tmp_path)


def test_release_versions_refuse_a_sdist_filename_that_disagrees_with_the_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source archive name is part of the release identity too."""

    write_wheel(tmp_path, "1.0.0")
    sdist = write_sdist(tmp_path, "1.0.0")
    sdist.rename(tmp_path / "renamed-1.0.0.tar.gz")
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    with pytest.raises(ValueError, match="sdist filename"):
        release.verify_release_versions("v1.0.0", tmp_path)


def test_release_versions_refuse_an_invalid_tag_before_reading_artifacts(tmp_path: Path) -> None:
    """A branch name or bare v cannot be interpreted as a release version."""

    with pytest.raises(ValueError, match="release tag"):
        release_module().verify_release_versions("main", tmp_path)


def test_release_versions_refuse_missing_archive_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An archive without canonical package metadata cannot prove its identity."""

    wheel = tmp_path / "perception_error_to_aeb-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("payload.txt", "no metadata")
    write_sdist(tmp_path, "1.0.0")
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    with pytest.raises(ValueError, match="exactly one METADATA"):
        release.verify_release_versions("v1.0.0", tmp_path)


def test_release_versions_refuse_an_sdist_without_top_level_pkg_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested egg-info alone is not the canonical source-distribution metadata."""

    write_wheel(tmp_path, "1.0.0")
    path = tmp_path / "perception_error_to_aeb-1.0.0.tar.gz"
    payload = b"Name: perception-error-to-aeb\nVersion: 1.0.0\n"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("root/src/package.egg-info/PKG-INFO")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    with pytest.raises(ValueError, match="exactly one PKG-INFO"):
        release.verify_release_versions("v1.0.0", tmp_path)


def test_release_versions_refuse_non_file_sdist_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory named PKG-INFO cannot stand in for readable archive metadata."""

    write_wheel(tmp_path, "1.0.0")
    path = tmp_path / "perception_error_to_aeb-1.0.0.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("root/PKG-INFO")
        info.type = tarfile.DIRTYPE
        archive.addfile(info)
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    with pytest.raises(ValueError, match="not a regular file"):
        release.verify_release_versions("v1.0.0", tmp_path)


def test_checksum_manifest_contains_only_relative_distribution_basenames(tmp_path: Path) -> None:
    """A manifest must stay portable and must never hash an older manifest."""

    wheel = write_wheel(tmp_path, "1.0.0")
    sdist = write_sdist(tmp_path, "1.0.0")
    (tmp_path / "SHA256SUMS").write_text("stale", encoding="utf-8")

    release = release_module()
    manifest = release.write_distribution_checksums(tmp_path)

    expected = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted((wheel, sdist), key=lambda item: item.name)
    )
    assert manifest.read_text(encoding="utf-8") == expected
    assert str(tmp_path) not in expected
    assert "SHA256SUMS" not in expected


def test_checksum_manifest_refuses_an_ambiguous_distribution_set(tmp_path: Path) -> None:
    """Multiple wheels can silently publish a stale build unless ambiguity fails closed."""

    write_wheel(tmp_path, "1.0.0")
    write_wheel(tmp_path, "0.1.0")
    write_sdist(tmp_path, "1.0.0")

    release = release_module()
    with pytest.raises(ValueError, match="exactly one wheel and one sdist"):
        release.write_distribution_checksums(tmp_path)


def test_checksum_manifest_refuses_a_missing_distribution_set(tmp_path: Path) -> None:
    """An empty output directory cannot produce an authoritative empty manifest."""

    with pytest.raises(ValueError, match="exactly one wheel and one sdist"):
        release_module().write_distribution_checksums(tmp_path)


def test_sdist_normalization_removes_build_time_from_gzip_and_tar(tmp_path: Path) -> None:
    """Two source archives from the same source and epoch must be byte-identical."""

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_wheel(first, "1.0.0")
    write_wheel(second, "1.0.0")
    write_sdist(first, "1.0.0", member_mtime=100)
    write_sdist(second, "1.0.0", member_mtime=200)
    release = release_module()

    first_path = release.normalize_sdist(first, 123456789)
    second_path = release.normalize_sdist(second, 123456789)

    assert first_path.read_bytes() == second_path.read_bytes()
    with tarfile.open(first_path, "r:gz") as archive:
        assert {member.mtime for member in archive.getmembers()} == {123456789}


def test_sdist_normalization_preserves_payload_paths_types_modes_and_links(tmp_path: Path) -> None:
    """Canonical metadata must not change any package content or archive semantics."""

    write_wheel(tmp_path, "1.0.0")
    sdist = write_sdist(tmp_path, "1.0.0", member_mtime=100)
    with tarfile.open(sdist, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        directory = tarfile.TarInfo("root/package")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o750
        directory.mtime = 100
        archive.addfile(directory)
        payload = b"payload bytes"
        regular = tarfile.TarInfo("root/package/data.txt")
        regular.size = len(payload)
        regular.mode = 0o640
        regular.mtime = 200
        regular.pax_headers = {"atime": "9.1", "purpose": "fixture"}
        archive.addfile(regular, io.BytesIO(payload))
        link = tarfile.TarInfo("root/package/link.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "data.txt"
        link.mode = 0o777
        link.mtime = 300
        archive.addfile(link)

    normalized = release_module().normalize_sdist(tmp_path, 123456789)

    with tarfile.open(normalized, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        assert set(members) == {"root/package", "root/package/data.txt", "root/package/link.txt"}
        assert members["root/package"].isdir()
        assert members["root/package"].mode == 0o750
        regular = members["root/package/data.txt"]
        assert regular.isfile()
        assert regular.mode == 0o640
        assert regular.pax_headers == {"purpose": "fixture"}
        stream = archive.extractfile(regular)
        assert stream is not None
        assert stream.read() == b"payload bytes"
        assert members["root/package/link.txt"].issym()
        assert members["root/package/link.txt"].linkname == "data.txt"


def test_sdist_normalization_removes_overriding_pax_owners_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """PAX owner headers override TarInfo fields and must not retain builder identity."""

    normalized: list[Path] = []
    for directory_name, owner in (("first", 1001), ("second", 2002)):
        directory = tmp_path / directory_name
        directory.mkdir()
        write_wheel(directory, "1.0.0")
        sdist = directory / "perception_error_to_aeb-1.0.0.tar.gz"
        payload = b"identical payload"
        with tarfile.open(sdist, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            member = tarfile.TarInfo("root/data.txt")
            member.size = len(payload)
            member.uid = owner
            member.gid = owner + 1
            member.uname = f"builder-{owner}"
            member.gname = f"group-{owner}"
            member.pax_headers = {
                "uid": str(owner),
                "gid": str(owner + 1),
                "uname": f"builder-{owner}",
                "gname": f"group-{owner}",
                "purpose": "fixture",
            }
            archive.addfile(member, io.BytesIO(payload))
        normalized.append(release_module().normalize_sdist(directory, 123456789))

    assert normalized[0].read_bytes() == normalized[1].read_bytes()
    first_pass = normalized[0].read_bytes()
    release_module().normalize_sdist(normalized[0].parent, 123456789)
    assert normalized[0].read_bytes() == first_pass
    with tarfile.open(normalized[0], "r:gz") as archive:
        [member] = archive.getmembers()
        assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")
        assert member.pax_headers == {"purpose": "fixture"}


def test_release_cli_verifies_versions_and_writes_checksums(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The module commands used by the workflow expose both guarded operations."""

    write_wheel(tmp_path, "1.0.0")
    write_sdist(tmp_path, "1.0.0")
    release = release_module()
    monkeypatch.setattr(release.metadata, "version", lambda _name: "1.0.0")

    assert release.main(["verify-version", "--tag", "v1.0.0", "--dist-dir", str(tmp_path)]) == 0
    assert "installed=1.0.0" in capsys.readouterr().out
    assert (
        release.main(["normalize-sdist", "--epoch", "123456789", "--dist-dir", str(tmp_path)]) == 0
    )
    assert ".tar.gz" in capsys.readouterr().out
    assert release.main(["write-checksums", "--dist-dir", str(tmp_path)]) == 0
    assert "SHA256SUMS" in capsys.readouterr().out


def test_release_cli_returns_one_when_the_artifact_set_is_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Workflow-visible validation errors must be non-zero without a traceback."""

    release = release_module()

    assert release.main(["write-checksums", "--dist-dir", str(tmp_path)]) == 1
    assert "release check failed" in capsys.readouterr().err


def test_release_module_entrypoint_returns_the_cli_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m aebrisk.release` must preserve the helper's failure code."""

    release = release_module()
    assert release.__file__ is not None
    monkeypatch.setattr(
        sys,
        "argv",
        ["release.py", "write-checksums", "--dist-dir", str(tmp_path)],
    )

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(release.__file__), run_name="__main__")

    assert raised.value.code == 1
