"""The report built from the committed evidence says what that evidence supports.

The Pages workflow builds the site from docs/evidence and docs/figures. This
builds the same page from copies of those files (without the large replay
pages) and checks the statements that depend on the released numbers: the
numberless braking comparisons in the lede, the READMEs and the experiment
card, and the rounded summary table.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

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


def configurations() -> list[dict[str, Any]]:
    evaluation = json.loads((EVIDENCE / "evaluation.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = evaluation["configurations"]
    return rows


def braking_share(row: dict[str, Any]) -> float:
    """The share of measured exposure spent braking, as the hero figure plots it."""

    share: float = row["mean_intervention_duration_s"] * row["scenarios"] / row["simulated_seconds"]
    return share


def test_the_lede_holds_for_the_committed_evidence() -> None:
    """The fewest counted collisions come with the largest braking share, strictly.

    Every AEB configuration, oracle AEB included, brakes for more than half of
    its exposure, so only the ordering distinguishes the fewest-collision
    configurations; that ordering is what the lede and both READMEs state.
    """

    rows = configurations()
    fewest = min(row["collisions"] for row in rows)
    least = [braking_share(row) for row in rows if row["collisions"] == fewest]
    rest = [braking_share(row) for row in rows if row["collisions"] > fewest]

    assert least
    assert rest
    assert min(least) > max(rest)


def test_the_braking_paragraph_holds_for_the_committed_evidence() -> None:
    """Localization/shape error separates a configuration from every AEB one without it.

    docs/experiment-card.md ("Reading the braking numbers") says each
    configuration with localization/shape error brakes for a larger share of
    its exposure, and records fewer counted collisions, than every AEB
    configuration without it, oracle AEB included.
    """

    rows = [row for row in configurations() if row["configuration_id"] != "no_aeb"]
    with_error = [row for row in rows if "localization_shape" in row["configuration_id"]]
    without = [row for row in rows if "localization_shape" not in row["configuration_id"]]

    assert with_error
    assert "oracle_aeb" in {row["configuration_id"] for row in without}
    assert min(braking_share(row) for row in with_error) > max(
        braking_share(row) for row in without
    )
    assert max(row["collisions"] for row in with_error) < min(row["collisions"] for row in without)


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
