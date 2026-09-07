"""Building the static report from claims and artifacts.

A report is a set of CLAIMS ABOUT ARTIFACTS. Every number on the page is there
because a claim in a committed file says it should be, and every claim names the
artifact it rests on. A page assembled from whatever happened to be on disk
would be a page nobody had taken responsibility for.

The page separates the configuration groups — no AEB, oracle AEB, single
channel, factorial coalitions, and imported — because they answer different
questions and mixing them invites a reader to compare a measured calibration
error against a chosen severity as though they were the same kind of number.

It always shows the GLOBAL INVALID RATE and its reasons. A study that reported
only what succeeded would be reporting a cohort it chose after seeing results.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aebrisk.analysis.claims import load_registry, validate_supplied_evidence_claims
from aebrisk.artifacts.documents import AEBEvaluationV1
from aebrisk.artifacts.family_interventions import FamilyInterventionsV1

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

#: The groups the report keeps apart, in the order it presents them.
CONFIGURATION_GROUPS: tuple[str, ...] = (
    "baseline",
    "single_channel",
    "coalition",
    "imported",
)
FEATURED_CONFIGURATION_IDS: tuple[str, ...] = (
    "no_aeb",
    "oracle_aeb",
    "coalition-none",
    "coalition-dropout+localization_shape+latency+track_instability",
)


def classify_configuration(configuration_id: str) -> str:
    """Which section of the report a configuration belongs in."""

    if configuration_id in ("no_aeb", "oracle_aeb"):
        return "baseline"
    if configuration_id.startswith("calibration_imported_"):
        return "imported"
    if configuration_id.startswith("coalition-"):
        return "coalition"
    return "single_channel"


def load_claims(path: Path) -> list[dict[str, Any]]:
    """Read the claims this report is allowed to make, on the registry's own terms.

    The required fields, the evidence types and the statuses all come from the
    registry rather than from this loader. Hard-coding them here would create a
    second vocabulary that could disagree with the committed one, and the
    disagreement would be a claim the registry accepted and the report rejected.
    """

    registry = load_registry(path)
    return [claim.model_dump(mode="json") for claim in registry.claims]


def _validated_report_document(
    path: Path,
    model: type[AEBEvaluationV1] | type[FamilyInterventionsV1],
    claims_path: Path,
    label: str,
) -> dict[str, Any]:
    payload = path.read_bytes()
    try:
        document = model.model_validate_json(payload)
    except ValueError as exc:
        raise ValueError(f"{label} evidence is invalid: {exc}") from exc
    validate_supplied_evidence_claims(claims_path, path.name, payload)
    return document.model_dump(mode="json")


def invalid_summary(artifacts_dir: Path) -> dict[str, Any]:
    """How much of the cohort was excluded, and why.

    Reported whether or not anything failed. A study that showed this section
    only when it was non-empty would let a reader assume it was always empty.
    """

    path = artifacts_dir / "exclusions.json"
    if not path.is_file():
        return {"excluded": 0, "reasons": {}}

    document = json.loads(path.read_text(encoding="utf-8"))
    reasons: dict[str, int] = {}
    for entry in document.get("excluded", []):
        reasons[entry["phase"]] = reasons.get(entry["phase"], 0) + 1
    return {"excluded": len(document.get("excluded", [])), "reasons": reasons}


def build_site(claims_path: Path, artifacts_dir: Path, output_dir: Path) -> Path:
    """Render the report and return the page it wrote."""

    claims = load_claims(claims_path)

    grouped: dict[str, list[dict[str, Any]]] = {group: [] for group in CONFIGURATION_GROUPS}
    summary_path = artifacts_dir / "evaluation.json"
    evaluation: dict[str, Any] = {}
    if summary_path.is_file():
        evaluation = _validated_report_document(
            summary_path, AEBEvaluationV1, claims_path, "evaluation"
        )
        for row in evaluation.get("configurations", []):
            grouped[classify_configuration(row["configuration_id"])].append(row)

    family_path = artifacts_dir / "family-interventions.json"
    family_rows: list[dict[str, Any]] = []
    family_shortfall: dict[str, int] | None = None
    if family_path.is_file():
        family_document = _validated_report_document(
            family_path, FamilyInterventionsV1, claims_path, "family intervention"
        )
        family_rows = family_document["rows"]
        bicycle_oracle = next(
            row
            for row in family_rows
            if row["family"] == "bicycle_or_vru" and row["configuration_id"] == "oracle_aeb"
        )
        family_shortfall = {
            "valid_tokens": bicycle_oracle["valid_tokens"],
            "evaluation_per_family": family_document["evaluation_per_family"],
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    figure_names = (
        "shapley-contributions.svg",
        "intervention-rates-by-family.svg",
    )
    figure_roots = (artifacts_dir.parent / "figures", artifacts_dir.parent.parent / "figures")
    source_figures = next((root for root in figure_roots if root.is_dir()), None)
    available_figures: list[str] = []
    if source_figures is not None:
        destination = output_dir / "figures"
        destination.mkdir(exist_ok=True)
        for name in figure_names:
            source = source_figures / name
            if source.is_file():
                shutil.copyfile(source, destination / name)
                available_figures.append(name)

    replay_source = artifacts_dir / "replays"
    replay_names: list[str] = []
    if replay_source.is_dir():
        replay_destination = output_dir / "replays"
        replay_destination.mkdir(exist_ok=True)
        for source in sorted(replay_source.glob("*.html"), key=lambda path: path.name):
            shutil.copyfile(source, replay_destination / source.name)
            replay_names.append(source.name)

    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        keep_trailing_newline=True,
    )
    rendered = environment.get_template("index.html.j2").render(
        claims=claims,
        groups=CONFIGURATION_GROUPS,
        grouped=grouped,
        invalid=invalid_summary(artifacts_dir),
        common_valid_tokens=evaluation.get("common_valid_tokens"),
        observed=[
            row
            for configuration_id in FEATURED_CONFIGURATION_IDS
            for row in evaluation.get("configurations", [])
            if row["configuration_id"] == configuration_id
        ],
        family_rows=family_rows,
        family_shortfall=family_shortfall,
        figures=available_figures,
        replays=replay_names,
    )

    page = output_dir / "index.html"
    with page.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    return page
