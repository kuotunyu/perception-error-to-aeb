"""The committed configuration the package reads by default.

`configs/` at the repository root is the reviewed record every published number
was produced under. An installed wheel has no repository root to look in, so the
package carries byte-identical copies of the files it reads by default under
`aebrisk/configs/` and reads them through `importlib.resources`.
`tests/contract/test_package_data.py` holds every copy to its original byte for
byte, so the two cannot drift apart unnoticed.

The modules that read these files at run time do so once per process and keep
a read-only copy, built by `frozen`. Every step of every run asks for the same
values, and a copy that one caller could edit would change them for all the
callers after it.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from types import MappingProxyType
from typing import Any


def read_committed_config(*parts: str) -> str:
    """Return the text of one shipped config, named by its path under `configs/`."""

    resource = resources.files("aebrisk").joinpath("configs")
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")


def frozen(value: Any) -> Any:
    """Return a read-only deep copy of a parsed YAML document.

    Mappings become read-only views of new dictionaries and lists become
    tuples, so neither the copy nor the document it came from can change the
    other afterwards.
    """

    if isinstance(value, Mapping):
        return MappingProxyType({key: frozen(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(frozen(item) for item in value)
    return value
