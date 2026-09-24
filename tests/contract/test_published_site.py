"""The report built from the committed evidence says what that evidence supports.

The Pages workflow builds the site from docs/evidence and docs/figures. This
builds the same page from copies of those files (without the large replay
pages) and checks the statements that depend on the released numbers: the lede
and the rounded summary table.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "evidence" / "nuplan_aeb_v2"


def build(tmp_path: Path) -> str:
    from aebrisk.report.builder import build_site

    evidence = tmp_path / "docs" / "evidence" / "nuplan_aeb_v2"
    evidence.mkdir(parents=True)
    for name in ("evaluation.json", "family-interventions.json", "exclusions.json"):
        shutil.copyfile(EVIDENCE / name, evidence / name)
    shutil.copytree(ROOT / "docs" / "figures", tmp_path / "docs" / "figures")
    page = build_site(ROOT / "docs" / "claims.yaml", evidence, tmp_path / "site")
    return page.read_text(encoding="utf-8")


def test_the_lede_holds_for_the_committed_evidence() -> None:
    """The fewest counted collisions come with braking for most of the exposure."""

    evaluation = json.loads((EVIDENCE / "evaluation.json").read_text(encoding="utf-8"))
    rows = evaluation["configurations"]
    fewest = min(row["collisions"] for row in rows)
    shares = [
        row["mean_intervention_duration_s"] * row["scenarios"] / row["simulated_seconds"]
        for row in rows
        if row["collisions"] == fewest
    ]

    assert shares
    assert all(share > 0.5 for share in shares)


def test_the_committed_site_leads_with_the_figure_and_rounds_its_summary(
    tmp_path: Path,
) -> None:
    page = build(tmp_path)

    finding = page[page.index('<section id="finding">') : page.index('<section id="question">')]
    assert 'src="figures/collisions-vs-braking.svg"' in finding
    observed = page[
        page.index('<section id="observations">') : page.index('<section id="figures">')
    ]
    assert '<td class="number">14901.9</td>' in observed
    assert '<td class="number">9.2</td>' in observed
    assert "14901.900000000001" not in observed
    appendix = page[page.index('<details id="complete-trace">') :]
    assert "14901.900000000001" in appendix
