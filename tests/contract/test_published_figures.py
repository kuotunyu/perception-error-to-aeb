"""The committed figures are exactly what the figures command draws from committed evidence.

The READMEs embed the SVGs in docs/figures, and the Pages workflow regenerates
them before building the site. Without this check the two could drift apart
and a README reader would see a figure the evidence no longer produces.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_committed_figures_are_regenerated_byte_for_byte(tmp_path: Path) -> None:
    from aebrisk.report.svg import write_figures

    written = write_figures(ROOT / "docs" / "evidence" / "nuplan_aeb_v2", tmp_path)

    committed = sorted(path.name for path in (ROOT / "docs" / "figures").glob("*.svg"))
    assert sorted(path.name for path in written) == committed
    for path in written:
        assert path.read_bytes() == (ROOT / "docs" / "figures" / path.name).read_bytes(), path.name
