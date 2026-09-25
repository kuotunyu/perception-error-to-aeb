"""The committed figures are exactly what the committed evidence draws.

The READMEs link the SVGs under docs/figures, while the Pages workflow redraws
them from docs/evidence before it publishes the site. If the two could differ,
a reader of the repository and a reader of the site would see different
figures for the same numbers.
"""

from __future__ import annotations

from pathlib import Path

from aebrisk.report.svg import write_figures

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence" / "nuplan_aeb_v2"
FIGURE_DIR = REPO_ROOT / "docs" / "figures"


def test_committed_figures_match_a_fresh_render_byte_for_byte(tmp_path: Path) -> None:
    written = write_figures(EVIDENCE_DIR, tmp_path)

    assert sorted(path.name for path in FIGURE_DIR.glob("*.svg")) == sorted(
        path.name for path in written
    )
    for path in written:
        assert (FIGURE_DIR / path.name).read_bytes() == path.read_bytes(), path.name
