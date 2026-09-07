"""Fail-closed helpers for versioned distribution artifacts."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import sys
import tarfile
import zipfile
from collections.abc import Sequence
from email.parser import BytesParser
from importlib import metadata
from pathlib import Path

PACKAGE_NAME = "perception-error-to-aeb"


def _distribution_paths(distribution_dir: Path) -> tuple[Path, Path]:
    wheels = sorted(distribution_dir.glob("*.whl"))
    sdists = sorted(distribution_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("distribution directory must contain exactly one wheel and one sdist")
    return wheels[0], sdists[0]


def _wheel_metadata(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError(f"wheel {path.name} must contain exactly one METADATA file")
        message = BytesParser().parsebytes(archive.read(names[0]))
    return str(message["Name"]), str(message["Version"])


def _sdist_metadata(path: Path) -> tuple[str, str]:
    with tarfile.open(path, "r:gz") as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.name.endswith("/PKG-INFO") and member.name.count("/") == 1
        ]
        if len(members) != 1:
            raise ValueError(f"sdist {path.name} must contain exactly one PKG-INFO file")
        stream = archive.extractfile(members[0])
        if stream is None:
            raise ValueError(f"sdist {path.name} PKG-INFO is not a regular file")
        message = BytesParser().parsebytes(stream.read())
    return str(message["Name"]), str(message["Version"])


def verify_release_versions(tag: str, distribution_dir: Path) -> dict[str, str]:
    """Require the future tag, installed metadata and both archives to agree."""

    if not tag.startswith("v") or len(tag) == 1:
        raise ValueError("release tag must start with v and contain a version")
    expected = tag[1:]
    wheel, sdist = _distribution_paths(distribution_dir)
    expected_wheel = f"perception_error_to_aeb-{expected}-py3-none-any.whl"
    expected_sdist = f"perception_error_to_aeb-{expected}.tar.gz"
    if wheel.name != expected_wheel:
        raise ValueError(f"wheel filename must be {expected_wheel}, got {wheel.name}")
    if sdist.name != expected_sdist:
        raise ValueError(f"sdist filename must be {expected_sdist}, got {sdist.name}")
    wheel_name, wheel_version = _wheel_metadata(wheel)
    sdist_name, sdist_version = _sdist_metadata(sdist)
    if wheel_name != PACKAGE_NAME:
        raise ValueError(f"wheel package must be {PACKAGE_NAME}, got {wheel_name}")
    if sdist_name != PACKAGE_NAME:
        raise ValueError(f"sdist package must be {PACKAGE_NAME}, got {sdist_name}")
    versions = {
        "installed": metadata.version(PACKAGE_NAME),
        "sdist": sdist_version,
        "wheel": wheel_version,
    }
    disagreements = [name for name, version in versions.items() if version != expected]
    if disagreements:
        detail = ", ".join(f"{name}={versions[name]}" for name in disagreements)
        raise ValueError(f"release version {expected} disagrees with {detail}")
    return versions


def write_distribution_checksums(distribution_dir: Path) -> Path:
    """Write portable SHA-256 lines for exactly one wheel and one sdist."""

    distributions = sorted(_distribution_paths(distribution_dir), key=lambda path: path.name)
    manifest = distribution_dir / "SHA256SUMS"
    contents = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in distributions
    )
    with manifest.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(contents)
    return manifest


def normalize_sdist(distribution_dir: Path, epoch: int) -> Path:
    """Rewrite the sdist with fixed gzip and tar metadata."""

    _, path = _distribution_paths(distribution_dir)
    with tarfile.open(path, "r:gz") as source:
        entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
        for original in sorted(source.getmembers(), key=lambda member: member.name):
            stream = source.extractfile(original) if original.isfile() else None
            data = stream.read() if stream is not None else None
            member = copy.copy(original)
            member.mtime = epoch
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.pax_headers = {
                key: value
                for key, value in member.pax_headers.items()
                if key not in {"atime", "ctime", "mtime"}
            }
            entries.append((member, data))

    temporary = path.with_name(f".{path.name}.tmp")
    with (
        temporary.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as target,
    ):
        for member, data in entries:
            target.addfile(member, io.BytesIO(data) if data is not None else None)
    temporary.replace(path)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    version_parser = subparsers.add_parser("verify-version")
    version_parser.add_argument("--tag", required=True)
    version_parser.add_argument("--dist-dir", type=Path, required=True)
    checksum_parser = subparsers.add_parser("write-checksums")
    checksum_parser.add_argument("--dist-dir", type=Path, required=True)
    normalize_parser = subparsers.add_parser("normalize-sdist")
    normalize_parser.add_argument("--dist-dir", type=Path, required=True)
    normalize_parser.add_argument("--epoch", type=int, required=True)
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "verify-version":
            versions = verify_release_versions(arguments.tag, arguments.dist_dir)
            print(
                "release versions: "
                + ", ".join(f"{key}={value}" for key, value in versions.items())
            )
        elif arguments.command == "write-checksums":
            print(write_distribution_checksums(arguments.dist_dir))
        else:
            print(normalize_sdist(arguments.dist_dir, arguments.epoch))
    except (OSError, ValueError, metadata.PackageNotFoundError) as error:
        print(f"release check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
