"""The committed configuration the package reads by default.

`configs/` at the repository root is the reviewed record every published number
was produced under. An installed wheel has no repository root to look in, so the
package carries byte-identical copies of the files it reads by default under
`aebrisk/configs/` and reads them through `importlib.resources`.
`tests/contract/test_package_data.py` holds every copy to its original byte for
byte, so the two cannot drift apart unnoticed.
"""

from __future__ import annotations

from importlib import resources


def read_committed_config(*parts: str) -> str:
    """Return the text of one shipped config, named by its path under `configs/`."""

    resource = resources.files("aebrisk").joinpath("configs")
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")
