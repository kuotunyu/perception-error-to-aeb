"""The analysis of the policy v2 study, on arm trees written by hand.

The tests build small studies of their own: sixteen tokens in four families,
drawn from ten logs of five drives, with one log holding tokens of two families.
Each arm writes its cells with outcomes planted by a rule, and the released
records are arm A's. The released evidence for the eight cells is computed from
them by the released functions, so the reproduction gate has something exact to
reproduce. The statistics are also checked on four hand-built tokens, where each
value can be worked out by hand.

Three plantings are used:

- NULL: every arm measures what arm A does, so no contrast can be established;
- PLANTED: policy v2 brakes far less, has no excluded contacts in the oracle and
  collides in logs where policy v1 does not, and the Kalman estimate lowers
  braking under localization error. Each primary and Q2 hypothesis is then
  decided the way the planting predicts;
- DECREASE: as planted, but the oracle collisions are policy v1's.

The bootstrap draws 400 times rather than 5,000, which keeps each analysis to a
few seconds and leaves every decision the plantings target unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Optional, cast

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from aebrisk.aeb.state_machine import AEBState
from aebrisk.analysis.aggregate import (
    common_cohort,
    configuration_intervals,
    configuration_rows,
    load_formal_results,
)
from aebrisk.artifacts.documents import AEBEvaluationV1, AEBIntervalsV1, write_document
from aebrisk.artifacts.envelope import canonical_json_bytes
from aebrisk.artifacts.results import AEBScenarioResultV1, AEBScenarioResultV2, ScenarioFamily
from aebrisk.artifacts.study_documents import (
    PolicyV2SummaryV1,
    Reference,
    StudyContrastV1,
    StudyDistributionV1,
    StudyGatesV1,
    StudyIntervalV1,
    StudyOutcome,
    StudyTest,
)
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import load_manifest, membership_sha256
from aebrisk.metrics.bootstrap import (
    DEFAULT_SEED,
    BootstrapWeights,
    bootstrap_p_value,
    cluster_bootstrap_weights,
    percentile_interval,
    weighted_ratio,
)
from aebrisk.metrics.events import extract_interventions
from aebrisk.metrics.inference import clopper_pearson, holm_step_confidence, sign_flip_p_value
from aebrisk.simulation.orchestrate import (
    TokenResultsV1,
    TokenRun,
    documents_for,
    token_results_bytes,
)
from aebrisk.simulation.runner import run_common_scenario
from aebrisk.simulation.step_loop import COLLIDING_MASS_KG, StepLoopOutcome
from aebrisk.simulation.synthetic import SyntheticLeadScenario
from aebrisk.study import analysis as study_analysis
from aebrisk.study.analysis import (
    FIXED_SENTENCES,
    H3_SENTENCE,
    H4_OTHER_SENTENCE,
    H4_SUPPORTED_SENTENCE,
    H5_DECREASE_SENTENCE,
    H5_INCREASE_SENTENCE,
    H5_NO_CHANGE_SENTENCE,
    SCENARIO_DURATION_S,
    ArmCell,
    TokenOutcome,
    analyse_study,
    arm_outcomes,
    contrast,
    keying_ratio,
    load_arm,
    reproduce_study_cells,
    token_outcome,
)
from aebrisk.study.definition import drive_of
from aebrisk.study.gates import GateResult, gates_document, load_gates

ROOT = Path(__file__).resolve().parents[3]
COMMITTED_STUDY = ROOT / "configs" / "experiments" / "aeb_policy_v2_study.yaml"
PROTOCOL_SHA = "bbf0b6d31943a0f99160afe1d4c18f9a181850367494004037bab98d8f2e59f9"
RESAMPLES = 400

A = "A-v1-replication"
B = "B-v2-gated"
C = "C-v1-kalman"
D = "D-v2-kalman"
E = "E-v2-channel-rng"
ARMS = (A, B, C, D, E)
POLICY = {A: "v1", B: "v2", C: "v1", D: "v2", E: "v2"}
KALMAN = (C, D)

NO_AEB = "no_aeb"
ORACLE = "oracle_aeb"
DROPOUT = "dropout-medium"
LOCALIZATION = "localization_shape-medium"
LATENCY = "latency-medium"
TRACK = "track_instability-medium"
NONE = "coalition-none"
FULL = "coalition-dropout+localization_shape+latency+track_instability"
STUDY_CELLS = (NO_AEB, ORACLE, DROPOUT, LOCALIZATION, LATENCY, TRACK, NONE, FULL)
E_CELLS = (NO_AEB, ORACLE, DROPOUT, FULL)

TOKENS_BY_FAMILY: Mapping[ScenarioFamily, tuple[str, ...]] = {
    "lead_or_stopping": tuple(f"lead-0{index}" for index in range(1, 7)),
    "cut_in_or_crossing": tuple(f"cut-0{index}" for index in range(1, 5)),
    "pedestrian_or_crosswalk": tuple(f"walk-0{index}" for index in range(1, 5)),
    "bicycle_or_vru": ("bike-01", "bike-02"),
}
FAMILY_OF = {token: family for family, tokens in TOKENS_BY_FAMILY.items() for token in tokens}
TOKENS = tuple(sorted(FAMILY_OF))


def log_name(drive: int, segment: int) -> str:
    return f"2021.0{drive}.01.00.00.00_veh-0{drive}_0{segment}000_0{segment}999.db"


#: Ten logs, two per drive. L1 holds a lead token and a cut-in token.
LOGS = tuple(log_name(drive, segment) for drive in range(1, 6) for segment in (1, 2))
LOG_OF = {
    **{f"lead-0{index}": LOGS[index - 1] for index in range(1, 7)},
    "cut-01": LOGS[6],
    "cut-02": LOGS[7],
    "cut-03": LOGS[0],
    "cut-04": LOGS[8],
    "walk-01": LOGS[9],
    "walk-02": LOGS[1],
    "walk-03": LOGS[2],
    "walk-04": LOGS[9],
    "bike-01": LOGS[6],
    "bike-02": LOGS[8],
}
#: The planted collisions: six lead tokens in six logs, and two cut-in tokens in two more.
LEAD = TOKENS_BY_FAMILY["lead_or_stopping"]
H4_TOKENS = (*LEAD, "cut-01", "cut-02")

#: An ego at 5 m/s when it collides.
COLLISION_ENERGY_J = 0.5 * COLLIDING_MASS_KG * 5.0 * 5.0


# --------------------------------------------------------------------------
# What each replicate measured
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Measured:
    braking: float = 0.0
    duration: float = SCENARIO_DURATION_S
    collision: bool = False
    contacts: int = 0
    missed: int = 0
    false: int = 0
    delays: tuple[float, ...] = ()
    stop: Optional[float] = None
    max_decel: float = 0.0


Plant = Callable[[str, str, str, int], Measured]


def baseline(cell: str, token: str) -> Measured:
    """What every arm measures unless a planting says otherwise."""

    if cell == NO_AEB:
        if token in ("walk-01", "walk-02"):
            return Measured(duration=6.0, collision=True)
        return Measured()
    if cell == ORACLE:
        return Measured(braking=6.0, contacts=1, delays=(0.0,), stop=30.0, max_decel=5.0)
    return Measured(
        braking=9.0,
        duration=14.0 if token == "cut-04" else SCENARIO_DURATION_S,
        contacts=1,
        false=1,
        delays=(0.2,),
        stop=None if token.startswith("bike") else 25.0,
        max_decel=6.0,
    )


def null_plant(arm: str, cell: str, token: str, replicate: int) -> Measured:
    return baseline(cell, token)


def planted(
    arm: str, cell: str, token: str, replicate: int, *, v1_collides: bool = False
) -> Measured:
    measured = baseline(cell, token)
    v2 = POLICY[arm] == "v2"
    kalman = 0.8 if arm in KALMAN else 1.0
    if cell == ORACLE:
        if v2:
            measured = replace(measured, braking=1.5, contacts=0)
        if token in LEAD and v2 != v1_collides:
            measured = replace(measured, duration=7.0, collision=True, stop=None)
    elif cell == FULL:
        measured = replace(measured, braking=(3.0 if v2 else 13.5) * kalman)
        if v2 and token in H4_TOKENS:
            measured = replace(measured, duration=7.0, collision=True, stop=None)
    elif cell == LOCALIZATION:
        measured = replace(measured, braking=(12.0 if v2 else 13.5) * kalman)
    return measured


def decrease(arm: str, cell: str, token: str, replicate: int) -> Measured:
    return planted(arm, cell, token, replicate, v1_collides=True)


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")
    return path


def record(token: str, cell: str, replicate: int, measured: Measured) -> AEBScenarioResultV2:
    return AEBScenarioResultV2(
        schema_version="aeb-scenario-result/v2",
        scenario_token=token,
        family=FAMILY_OF[token],
        configuration_id=cell,
        replicate=replicate,
        valid=True,
        collision_vru=0,
        collision_vehicle=int(measured.collision),
        collision_object=0,
        collision_energy=COLLISION_ENERGY_J if measured.collision else 0.0,
        contacts_not_at_fault=measured.contacts,
        min_clearance_m=0.0 if measured.collision else 1.0,
        missed_interventions=measured.missed,
        false_interventions=measured.false,
        matched_delay_s=measured.delays,
        stop_distance_m=measured.stop,
        max_deceleration_mps2=measured.max_decel,
        max_abs_jerk_mps3=1.0,
        intervention_duration_s=measured.braking,
        simulated_duration_s=measured.duration,
    )


def document_of(
    token: str, cell: str, records: Sequence[Any], manifest: Path, valid: bool = True
) -> TokenResultsV1:
    return TokenResultsV1(
        schema_version="aeb-token-results/v1",
        scenario_token=token,
        family=FAMILY_OF[token],
        split="evaluation",
        configuration_id=cell,
        protocol_sha256=PROTOCOL_SHA,
        cohort_manifest_sha256=membership_sha256(load_manifest(manifest)),
        valid=valid,
        invalid_reason=None if valid else "the simulator raised",
        invalid_phase=None if valid else "step",
        results=tuple(records),
    )


def planted_document(
    arm: str, cell: str, token: str, plant: Plant, manifest: Path
) -> TokenResultsV1:
    return document_of(
        token,
        cell,
        [
            record(token, cell, replicate, plant(arm, cell, token, replicate))
            for replicate in (0, 1, 2)
        ],
        manifest,
    )


def write_token_document(path: Path, document: TokenResultsV1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(token_results_bytes(document))


def run_complete(manifest: Path) -> dict[str, Any]:
    return {
        "schema_version": "aeb-run-complete/v1",
        "cohort_manifest_sha256": membership_sha256(load_manifest(manifest)),
        "tokens": list(TOKENS),
    }


def write_cells(root: Path, arm: str, cells: Sequence[str], plant: Plant, manifest: Path) -> Path:
    for cell in cells:
        for token in TOKENS:
            write_token_document(
                root / cell / f"{token}.json", planted_document(arm, cell, token, plant, manifest)
            )
    write_json(root / "run_complete.json", run_complete(manifest))
    return root


def write_manifest(path: Path) -> Path:
    return write_json(
        path,
        {
            "schema_version": "aeb-cohort-manifest/v1",
            "split": "evaluation",
            "protocol_sha256": PROTOCOL_SHA,
            "families": {family: list(tokens) for family, tokens in TOKENS_BY_FAMILY.items()},
            "log_names": sorted(set(LOG_OF.values())),
        },
    )


def write_eligibility(path: Path) -> Path:
    return write_json(
        path,
        {
            "schema_version": "aeb-cohort-eligibility/v1",
            "examined": [
                {
                    "accepted": True,
                    "family": FAMILY_OF[token],
                    "initial_ego_speed_mps": 10.0,
                    "log_name": LOG_OF[token],
                    "official_split": "val",
                    "oracle_enters_corridor_within_4s": True,
                    "oracle_min_ttc_within_4s": 3.0,
                    "reason": "accepted",
                    "scenario_token": token,
                    "scenario_type": "stopping_with_lead",
                }
                for token in TOKENS
            ],
            "scenarios_in_split_by_family": {
                family: len(tokens) for family, tokens in TOKENS_BY_FAMILY.items()
            },
        },
    )


def write_study(path: Path, manifest: Path, eligibility: Path, e_cells: Sequence[str]) -> Path:
    """The committed study file, recording the fixture cohort and drawing 400 times."""

    document = yaml.safe_load(COMMITTED_STUDY.read_text(encoding="utf-8"))
    document["cohort_manifest_file_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    document["cohort_membership_sha256"] = membership_sha256(load_manifest(manifest))
    document["token_log_source"] = str(eligibility)
    document["bootstrap"]["resamples"] = RESAMPLES
    for arm in document["arms"]:
        arm["cells"] = list(STUDY_CELLS if arm["id"] != E else e_cells)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def write_evidence(released: Path, evidence: Path) -> Path:
    """The released evidence for the eight cells, written the way `evaluate` writes it."""

    loaded = load_formal_results(released)
    common = common_cohort(loaded)
    rows = configuration_rows(loaded, common)
    intervals = configuration_intervals(loaded, common, FAMILY_OF)
    shared = {
        "protocol_sha256": PROTOCOL_SHA,
        "cohort_manifest_sha256": loaded.cohort_manifest_sha256,
        "cohort_size": len(TOKENS),
        "common_valid_tokens": len(common),
    }
    write_document(
        AEBEvaluationV1.model_validate(
            {
                "schema_version": "aeb-evaluation/v1",
                **shared,
                "simulated_seconds": sum(cast(float, row["simulated_seconds"]) for row in rows),
                "configurations": rows,
            }
        ),
        evidence / "evaluation.json",
    )
    write_document(
        AEBIntervalsV1.model_validate(
            {
                "schema_version": "aeb-intervals/v1",
                **shared,
                "intervals": {
                    cell: {metric: asdict(interval) for metric, interval in metrics.items()}
                    for cell, metrics in intervals.items()
                },
            }
        ),
        evidence / "intervals.json",
    )
    return evidence


@dataclass(frozen=True)
class Tree:
    """One study's files: the study file, its cohort, five arms, and the released run."""

    study: Path
    manifest: Path
    eligibility: Path
    arms_root: Path
    released_root: Path
    evidence_dir: Path

    def gates(
        self,
        reference: str = "released",
        failed: Sequence[str] = (),
        arms: Sequence[str] = ARMS,
        names: Sequence[str] = ("G1", "G2", "G3", "G5"),
        study: Optional[Path] = None,
    ) -> StudyGatesV1:
        results = [GateResult(name, name not in failed, {"checked": 1}, ()) for name in names]
        return gates_document(study or self.study, arms, results, cast(Reference, reference))

    def analyse(
        self,
        gates: Optional[StudyGatesV1] = None,
        reference: str = "released",
        g4_path: Optional[Path] = None,
        manifest: Optional[Path] = None,
    ) -> PolicyV2SummaryV1:
        return analyse_study(
            self.study,
            self.arms_root,
            self.released_root,
            self.evidence_dir,
            manifest or self.manifest,
            gates if gates is not None else self.gates(reference),
            cast(Reference, reference),
            g4_path=g4_path,
        )


def build_tree(root: Path, plant: Plant, e_cells: Sequence[str] = E_CELLS) -> Tree:
    manifest = write_manifest(root / "cohort" / "evaluation.json")
    eligibility = write_eligibility(root / "cohort" / "evaluation-eligibility.json")
    study = write_study(root / "study.yaml", manifest, eligibility, e_cells)
    arms_root = root / "attempt-1"
    for arm in ARMS:
        write_cells(arms_root / arm, arm, STUDY_CELLS if arm != E else e_cells, plant, manifest)
    released = write_cells(root / "released", A, STUDY_CELLS, plant, manifest)
    return Tree(
        study=study,
        manifest=manifest,
        eligibility=eligibility,
        arms_root=arms_root,
        released_root=released,
        evidence_dir=write_evidence(released, root / "evidence"),
    )


@pytest.fixture(scope="module")
def null_tree(tmp_path_factory: pytest.TempPathFactory) -> Tree:
    return build_tree(tmp_path_factory.mktemp("null"), null_plant)


@pytest.fixture(scope="module")
def null_summary(null_tree: Tree) -> PolicyV2SummaryV1:
    return null_tree.analyse()


@pytest.fixture(scope="module")
def planted_tree(tmp_path_factory: pytest.TempPathFactory) -> Tree:
    return build_tree(tmp_path_factory.mktemp("planted"), planted)


@pytest.fixture(scope="module")
def planted_summary(planted_tree: Tree) -> PolicyV2SummaryV1:
    return planted_tree.analyse()


def hypotheses(summary: PolicyV2SummaryV1) -> dict[str, Any]:
    return {result.id: result for result in summary.hypotheses}


def level(summary: PolicyV2SummaryV1, arm: str, cell: str, family: Optional[str] = None) -> Any:
    (found,) = (
        row for row in summary.levels if (row.arm, row.cell, row.family) == (arm, cell, family)
    )
    return found


def every_contrast(summary: PolicyV2SummaryV1) -> Iterator[StudyContrastV1]:
    yield from (result.contrast for result in summary.hypotheses)
    yield from (row.contrast for row in summary.sensitivity)
    yield from (row.contrast for row in summary.secondary)
    yield from (row.contrast for row in summary.descriptive_contrasts)


# --------------------------------------------------------------------------
# Hand-built outcomes for the statistics
# --------------------------------------------------------------------------


def outcome(
    braking: float = 0.0,
    exposure: float = 45.0,
    collisions: int = 0,
    contacts: int = 0,
    activations: int = 0,
) -> TokenOutcome:
    return TokenOutcome(
        collision_indicator=collisions / 3,
        any_collision=collisions > 0,
        braking_s=braking,
        exposure_s=exposure,
        not_at_fault=contacts,
        any_contact=max(collisions, int(contacts > 0)) / 3,
        avoided=0.0,
        induced=0.0,
        brake_activations=activations,
        early_ends=0,
        missed_interventions=0.0,
        false_interventions=0.0,
    )


HAND_TOKENS = ("token-1", "token-2", "token-3", "token-4")
HAND_FAMILY = {"token-1": "a", "token-2": "a", "token-3": "b", "token-4": "b"}
HAND_LOG = {"token-1": "log-1", "token-2": "log-2", "token-3": "log-2", "token-4": "log-3"}


def hand_weights(resamples: int = 200) -> BootstrapWeights:
    return cluster_bootstrap_weights(HAND_TOKENS, HAND_FAMILY, HAND_LOG, resamples=resamples)


def cells_of(cells: Mapping[str, Sequence[TokenOutcome]]) -> dict[str, dict[str, TokenOutcome]]:
    """One arm's hand-built outcomes: each cell's values for the four tokens, in order."""

    return {cell: dict(zip(HAND_TOKENS, values)) for cell, values in cells.items()}


def estimate_of(
    outcomes: Mapping[str, Mapping[str, Mapping[str, TokenOutcome]]], metric: str
) -> float:
    return contrast(
        hand_weights(),
        outcomes,
        ArmCell(B, ORACLE),
        ArmCell(A, ORACLE),
        cast(StudyOutcome, metric),
        "bootstrap",
        HAND_LOG,
        None,
        None,
    ).estimate


# --------------------------------------------------------------------------
# Loading an arm
# --------------------------------------------------------------------------


def test_load_arm_reads_each_cell_and_token_of_a_completed_arm(null_tree: Tree) -> None:
    loaded = load_arm(null_tree.arms_root / E, E_CELLS, TOKENS)

    assert tuple(loaded) == E_CELLS
    assert tuple(loaded[ORACLE]) == TOKENS
    assert loaded[FULL]["lead-01"].configuration_id == FULL
    assert loaded[FULL]["lead-01"].scenario_token == "lead-01"


def test_load_arm_requires_the_completion_marker(tmp_path: Path, null_tree: Tree) -> None:
    manifest = null_tree.manifest
    arm = write_cells(tmp_path / E, E, E_CELLS, null_plant, manifest)
    (arm / "run_complete.json").unlink()

    with pytest.raises(ValueError, match=r"run_complete\.json"):
        load_arm(arm, E_CELLS, TOKENS)


@pytest.mark.parametrize(
    ("cells_written", "cells_expected"),
    [(E_CELLS[:3], E_CELLS), ((*E_CELLS, LOCALIZATION), E_CELLS)],
    ids=["a missing cell", "an extra cell"],
)
def test_load_arm_requires_exactly_the_expected_cell_directories(
    tmp_path: Path, null_tree: Tree, cells_written: Sequence[str], cells_expected: Sequence[str]
) -> None:
    arm = write_cells(tmp_path / E, E, cells_written, null_plant, null_tree.manifest)

    with pytest.raises(ValueError, match="cell directories"):
        load_arm(arm, cells_expected, TOKENS)


def test_load_arm_refuses_a_missing_document(tmp_path: Path, null_tree: Tree) -> None:
    arm = write_cells(tmp_path / E, E, E_CELLS, null_plant, null_tree.manifest)
    (arm / ORACLE / "walk-03.json").unlink()

    with pytest.raises(ValueError, match="walk-03"):
        load_arm(arm, E_CELLS, TOKENS)


def test_load_arm_refuses_a_document_filed_under_another_token_or_cell(
    tmp_path: Path, null_tree: Tree
) -> None:
    arm = write_cells(tmp_path / E, E, E_CELLS, null_plant, null_tree.manifest)
    other = planted_document(E, DROPOUT, "walk-04", null_plant, null_tree.manifest)
    write_token_document(arm / ORACLE / "walk-03.json", other)

    with pytest.raises(ValueError, match="walk-03"):
        load_arm(arm, E_CELLS, TOKENS)


# --------------------------------------------------------------------------
# One token's outcomes
# --------------------------------------------------------------------------


def test_token_outcome_reads_every_outcome_from_the_records(null_tree: Tree) -> None:
    manifest = null_tree.manifest
    runs = [
        Measured(braking=2.0, duration=15.0, contacts=2, delays=(0.1, -0.2), false=1),
        Measured(braking=1.0, duration=7.5, collision=True, delays=(0.0,)),
        Measured(braking=0.5, duration=11.0, contacts=0, false=2),
    ]
    baseline_runs = [Measured(duration=6.0, collision=True), Measured(), Measured()]
    cell = document_of(
        "walk-01", FULL, [record("walk-01", FULL, r, m) for r, m in enumerate(runs)], manifest
    )
    no_aeb = document_of(
        "walk-01",
        NO_AEB,
        [record("walk-01", NO_AEB, r, m) for r, m in enumerate(baseline_runs)],
        manifest,
    )

    assert token_outcome(cell, no_aeb) == TokenOutcome(
        collision_indicator=1 / 3,
        any_collision=True,
        braking_s=3.5,
        exposure_s=33.5,
        not_at_fault=2,
        any_contact=2 / 3,
        avoided=1 / 3,
        induced=1 / 3,
        brake_activations=6,
        early_ends=1,
        missed_interventions=0.0,
        false_interventions=1.0,
    )


def test_early_ends_counts_short_runs_without_a_counted_collision(null_tree: Tree) -> None:
    manifest = null_tree.manifest
    runs = [
        Measured(duration=SCENARIO_DURATION_S),
        Measured(duration=12.3),
        Measured(duration=8.0, collision=True),
    ]
    cell = document_of(
        "cut-01", ORACLE, [record("cut-01", ORACLE, r, m) for r, m in enumerate(runs)], manifest
    )
    no_aeb = planted_document(A, NO_AEB, "cut-01", null_plant, manifest)

    assert SCENARIO_DURATION_S == 15.0
    assert token_outcome(cell, no_aeb).early_ends == 1


class RecordingScenario:
    """The synthetic lead scenario, keeping every outcome the runner simulates."""

    def __init__(self) -> None:
        self.inner = SyntheticLeadScenario()
        self.token = self.inner.token
        self.outcomes: list[StepLoopOutcome] = []

    def build_setup(self, protocol: Any) -> Any:
        return self.inner.build_setup(protocol)

    def simulate(self, setup: Any, configuration: Any, replicate: int) -> StepLoopOutcome:
        simulated = self.inner.simulate(setup, configuration, replicate)
        self.outcomes.append(simulated)
        return simulated


def test_brake_activations_from_the_written_records_equal_the_interventions_of_the_runs(
    tmp_path: Path,
) -> None:
    scenario = RecordingScenario()
    cells = (NO_AEB, ORACLE, LOCALIZATION, FULL)
    configurations = tuple(
        configuration
        for configuration in formal_configurations()
        if configuration.configuration_id in cells
    )
    records, invalid = run_common_scenario(scenario, configurations, protocol=object())
    assert invalid is None
    documents = documents_for(
        TokenRun(token=scenario.token, family="lead_or_stopping", results=records, invalid=None),
        split="evaluation",
        written_configurations=cells,
        protocol_sha256=PROTOCOL_SHA,
        cohort_manifest_sha256="c" * 64,
    )
    written: dict[str, TokenResultsV1] = {}
    for document in documents:
        path = tmp_path / f"{document.configuration_id}.json"
        path.write_bytes(token_results_bytes(document))
        written[document.configuration_id] = TokenResultsV1.model_validate_json(path.read_bytes())

    counted = {}
    for cell in cells:
        extracted = sum(
            len(extract_interventions(simulated.commands, dt_s=0.1))
            for simulated in scenario.outcomes
            if simulated.configuration_id == cell
        )
        counted[cell] = extracted
        assert token_outcome(written[cell], written[NO_AEB]).brake_activations == extracted
    assert counted[NO_AEB] == 0
    assert counted[ORACLE] > 0
    assert all(
        any(command.state in (AEBState.PARTIAL, AEBState.FULL) for command in simulated.commands)
        for simulated in scenario.outcomes
        if simulated.configuration_id == ORACLE
    )


@pytest.mark.parametrize(
    "damage",
    [
        "invalid",
        "other token",
        "not no_aeb",
        "two replicates",
        "a repeated replicate",
        "no duration",
    ],
)
def test_token_outcome_refuses_documents_it_cannot_pair(null_tree: Tree, damage: str) -> None:
    manifest = null_tree.manifest
    cell = planted_document(A, ORACLE, "lead-01", null_plant, manifest)
    no_aeb = planted_document(A, NO_AEB, "lead-01", null_plant, manifest)
    if damage == "invalid":
        cell = document_of("lead-01", ORACLE, [], manifest, valid=False)
    elif damage == "other token":
        no_aeb = planted_document(A, NO_AEB, "lead-02", null_plant, manifest)
    elif damage == "not no_aeb":
        no_aeb = planted_document(A, DROPOUT, "lead-01", null_plant, manifest)
    elif damage == "two replicates":
        cell = document_of("lead-01", ORACLE, cell.results[:2], manifest)
    elif damage == "a repeated replicate":
        cell = document_of("lead-01", ORACLE, (*cell.results[:2], cell.results[0]), manifest)
    else:
        legacy = [
            AEBScenarioResultV1.model_validate(
                {
                    **result.model_dump(exclude={"simulated_duration_s"}),
                    "schema_version": "aeb-scenario-result/v1",
                }
            )
            for result in cell.results
        ]
        cell = document_of("lead-01", ORACLE, legacy, manifest)

    with pytest.raises(ValueError):
        token_outcome(cell, no_aeb)


def test_an_arm_without_no_aeb_has_no_baseline_for_avoided_and_induced(null_tree: Tree) -> None:
    loaded = load_arm(null_tree.arms_root / E, E_CELLS, TOKENS)

    with pytest.raises(ValueError, match="no_aeb"):
        arm_outcomes({cell: documents for cell, documents in loaded.items() if cell != NO_AEB})


def test_arm_outcomes_pairs_every_cell_with_the_arms_own_no_aeb(null_tree: Tree) -> None:
    loaded = load_arm(null_tree.arms_root / A, STUDY_CELLS, TOKENS)

    outcomes = arm_outcomes(loaded)

    assert tuple(outcomes) == STUDY_CELLS
    assert outcomes[ORACLE]["walk-01"].avoided == 1.0
    assert outcomes[NO_AEB]["walk-01"].avoided == 0.0
    assert outcomes[ORACLE]["walk-01"] == token_outcome(
        loaded[ORACLE]["walk-01"], loaded[NO_AEB]["walk-01"]
    )


def test_token_outcome_counts_object_collisions_single_contacts_and_short_runs(
    null_tree: Tree,
) -> None:
    """Each count follows its rule, on runs where a looser rule would count differently.

    A collision with an object is a counted collision; one excluded contact is a
    contact; a short run that ends in a counted collision is not an early end;
    and missed interventions are a mean over the three replicates.
    """

    manifest = null_tree.manifest
    object_collision = AEBScenarioResultV2.model_validate(
        {
            **record("walk-03", FULL, 1, Measured(duration=9.0)).model_dump(),
            "collision_object": 1,
            "collision_energy": COLLISION_ENERGY_J,
            "min_clearance_m": 0.0,
        }
    )
    runs = [
        record("walk-03", FULL, 0, Measured(duration=12.0, contacts=1, missed=1)),
        object_collision,
        record("walk-03", FULL, 2, Measured(duration=11.0, missed=2)),
    ]
    cell = document_of("walk-03", FULL, runs, manifest)
    no_aeb = planted_document(A, NO_AEB, "walk-03", null_plant, manifest)

    assert token_outcome(cell, no_aeb) == TokenOutcome(
        collision_indicator=1 / 3,
        any_collision=True,
        braking_s=0.0,
        exposure_s=32.0,
        not_at_fault=1,
        any_contact=2 / 3,
        avoided=0.0,
        induced=1 / 3,
        brake_activations=0,
        early_ends=2,
        missed_interventions=1.0,
        false_interventions=0.0,
    )


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("invalid", "oracle_aeb/lead-01 is invalid; the analysis reads only valid documents"),
        ("two_replicates", "oracle_aeb/lead-01 must hold exactly the replicates (0, 1, 2)"),
        ("no_duration", "oracle_aeb/lead-01 holds a record without its simulated duration"),
        (
            "not_no_aeb",
            "avoided and induced collisions are defined against 'no_aeb', not 'dropout-medium'",
        ),
        ("other_token", "'lead-01' cannot be paired with the no_aeb run of 'lead-02'"),
    ],
)
def test_token_outcome_names_the_document_it_refuses_and_why(
    null_tree: Tree, damage: str, message: str
) -> None:
    manifest = null_tree.manifest
    cell = planted_document(A, ORACLE, "lead-01", null_plant, manifest)
    no_aeb = planted_document(A, NO_AEB, "lead-01", null_plant, manifest)
    if damage == "invalid":
        cell = document_of("lead-01", ORACLE, [], manifest, valid=False)
    elif damage == "two_replicates":
        cell = document_of("lead-01", ORACLE, cell.results[:2], manifest)
    elif damage == "no_duration":
        legacy = [
            AEBScenarioResultV1.model_validate(
                {
                    **result.model_dump(exclude={"simulated_duration_s"}),
                    "schema_version": "aeb-scenario-result/v1",
                }
            )
            for result in cell.results
        ]
        cell = document_of("lead-01", ORACLE, legacy, manifest)
    elif damage == "not_no_aeb":
        no_aeb = planted_document(A, DROPOUT, "lead-01", null_plant, manifest)
    else:
        no_aeb = planted_document(A, NO_AEB, "lead-02", null_plant, manifest)

    with pytest.raises(ValueError) as refused:
        token_outcome(cell, no_aeb)

    assert str(refused.value) == message


def test_an_arm_without_no_aeb_is_refused_with_the_reason() -> None:
    with pytest.raises(ValueError) as refused:
        arm_outcomes({})

    assert str(refused.value) == (
        "the arm has no 'no_aeb' cell, which avoided and induced collisions are measured against"
    )


# --------------------------------------------------------------------------
# Contrasts
# --------------------------------------------------------------------------


def test_braking_share_and_every_rate_are_ratios_of_sums() -> None:
    minus = cells_of(
        {
            ORACLE: [outcome(1.0, 10.0, contacts=1, activations=2)]
            + [outcome(9.0, 30.0, contacts=3, activations=4)] * 3
        }
    )
    plus = cells_of(
        {
            ORACLE: [outcome(1.0, 2.0, contacts=2, activations=1)]
            + [outcome(3.0, 30.0, contacts=0, activations=0)] * 3
        }
    )
    outcomes = {A: minus, B: plus}

    assert estimate_of(outcomes, "braking_share") == pytest.approx(10.0 / 92.0 - 28.0 / 100.0)
    assert estimate_of(outcomes, "not_at_fault_contact_rate") == pytest.approx(
        1000.0 * (2.0 / 92.0 - 10.0 / 100.0)
    )
    assert estimate_of(outcomes, "activation_rate") == pytest.approx(
        3600.0 * (1.0 / 92.0 - 14.0 / 100.0)
    )
    mean_of_ratios = (1.0 / 2.0 + 3 * 0.1) / 4 - (0.1 + 3 * 0.3) / 4
    assert estimate_of(outcomes, "braking_share") != pytest.approx(mean_of_ratios)


def test_each_draw_of_a_contrast_is_a_ratio_of_sums_over_the_drawn_tokens() -> None:
    minus = cells_of(
        {ORACLE: [outcome(1.0, 10.0), outcome(9.0, 30.0), outcome(2.0, 20.0), outcome(3.0, 40.0)]}
    )
    plus = cells_of(
        {ORACLE: [outcome(1.0, 2.0), outcome(3.0, 30.0), outcome(2.0, 25.0), outcome(0.0, 40.0)]}
    )
    weights = hand_weights()
    draws = braking_share_draws(weights, plus[ORACLE]) - braking_share_draws(weights, minus[ORACLE])

    result = contrast(
        weights,
        {A: minus, B: plus},
        ArmCell(B, ORACLE),
        ArmCell(A, ORACLE),
        "braking_share",
        "bootstrap",
        HAND_LOG,
        5,
        2,
    )

    low, high = percentile_interval(draws, 0.95)
    assert result.interval == StudyIntervalV1(confidence=0.95, low=low, high=high)
    assert result.simultaneous_interval is not None
    assert result.simultaneous_interval.confidence == 0.99
    assert (
        result.simultaneous_interval.low,
        result.simultaneous_interval.high,
    ) == percentile_interval(draws, 0.99)
    assert result.holm_step_interval is not None
    assert result.holm_step_interval.confidence == holm_step_confidence(5, 2)
    assert (result.holm_step_interval.low, result.holm_step_interval.high) == percentile_interval(
        draws, holm_step_confidence(5, 2)
    )
    assert result.p_value == bootstrap_p_value(draws)
    assert result.estimate == pytest.approx(6.0 / 97.0 - 15.0 / 100.0)


def test_a_contrast_without_its_holm_step_reports_the_95_percent_interval_only() -> None:
    arm = cells_of({ORACLE: [outcome(1.0), outcome(2.0), outcome(3.0), outcome(4.0)]})

    result = contrast(
        hand_weights(),
        {A: arm, B: arm},
        ArmCell(B, ORACLE),
        ArmCell(A, ORACLE),
        "braking_share",
        "bootstrap",
        HAND_LOG,
        None,
        None,
    )

    assert result.estimate == 0.0
    assert result.p_value == 1.0
    assert result.test == "bootstrap"
    assert result.simultaneous_interval is None
    assert result.holm_step_interval is None


def test_a_collision_contrast_is_tested_by_flipping_whole_logs() -> None:
    minus = cells_of({ORACLE: [outcome(collisions=0)] * 4})
    plus = cells_of(
        {
            ORACLE: [
                outcome(collisions=3),
                outcome(collisions=1),
                outcome(collisions=2),
                outcome(collisions=0),
            ]
        }
    )

    result = contrast(
        hand_weights(),
        {A: minus, B: plus},
        ArmCell(B, ORACLE),
        ArmCell(A, ORACLE),
        "collision_indicator",
        "sign_flip",
        HAND_LOG,
        None,
        None,
    )

    # log-1 holds token-1 (3 thirds), log-2 token-2 and token-3 (1 + 2), log-3 token-4 (0).
    assert result.p_value == sign_flip_p_value([3, 3, 0]) == 0.5
    assert result.estimate == pytest.approx(0.5)
    assert result.test == "sign_flip"


def test_the_sign_flip_settings_reach_the_test() -> None:
    minus = cells_of({ORACLE: [outcome(collisions=0)] * 4})
    plus = cells_of(
        {
            ORACLE: [
                outcome(collisions=3),
                outcome(collisions=1),
                outcome(collisions=2),
                outcome(collisions=1),
            ]
        }
    )

    result = contrast(
        hand_weights(),
        {A: minus, B: plus},
        ArmCell(B, ORACLE),
        ArmCell(A, ORACLE),
        "collision_indicator",
        "sign_flip",
        HAND_LOG,
        None,
        None,
        enumerate_up_to=1,
        random_flips=50,
        flip_seed=7,
    )

    assert result.p_value == sign_flip_p_value(
        [3, 3, 1], enumerate_up_to=1, random_flips=50, seed=7
    )
    assert result.p_value != sign_flip_p_value([3, 3, 1])


def test_the_flip_seed_is_the_seed_of_the_drawn_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three logs and fifty flips leave p only a few values, so a draw from another
    seed often gives the same p. The arguments the draw receives are checked instead."""

    received: list[dict[str, Any]] = []

    def recording(*args: Any, **kwargs: Any) -> float:
        received.append(kwargs)
        return sign_flip_p_value(*args, **kwargs)

    monkeypatch.setattr("aebrisk.study.analysis.sign_flip_p_value", recording)
    minus = cells_of({ORACLE: [outcome(collisions=0)] * 4})
    plus = cells_of(
        {
            ORACLE: [
                outcome(collisions=3),
                outcome(collisions=1),
                outcome(collisions=2),
                outcome(collisions=1),
            ]
        }
    )

    result = contrast(
        hand_weights(),
        {A: minus, B: plus},
        ArmCell(B, ORACLE),
        ArmCell(A, ORACLE),
        "collision_indicator",
        "sign_flip",
        HAND_LOG,
        None,
        None,
        enumerate_up_to=1,
        random_flips=50,
        flip_seed=7,
    )

    (arguments,) = received
    assert arguments == {"enumerate_up_to": 1, "random_flips": 50, "seed": 7}
    assert result.p_value == sign_flip_p_value(
        [3, 3, 1], enumerate_up_to=1, random_flips=50, seed=7
    )


def test_a_difference_in_differences_combines_four_terms_in_each_draw() -> None:
    minus = cells_of({NONE: [outcome(collisions=0)] * 4, ORACLE: [outcome(collisions=1)] * 4})
    plus = cells_of(
        {
            NONE: [outcome(collisions=1)] * 4,
            ORACLE: [
                outcome(collisions=3),
                outcome(collisions=1),
                outcome(collisions=1),
                outcome(collisions=1),
            ],
        }
    )
    outcomes = {A: minus, B: plus}

    result = contrast(
        hand_weights(),
        outcomes,
        (ArmCell(B, ORACLE), ArmCell(B, NONE)),
        (ArmCell(A, ORACLE), ArmCell(A, NONE)),
        "collision_indicator",
        "sign_flip",
        HAND_LOG,
        None,
        None,
    )

    # Per token: (plus oracle - plus none) - (minus oracle - minus none), in thirds.
    assert result.estimate == pytest.approx((2 / 3 + 0 + 0 + 0) / 4 - 1 / 3)
    assert result.p_value == sign_flip_p_value([2 - 1, -1 + -1, -1])
    assert [(sign, cell.arm, cell.cell) for sign, cell in result.terms] == [
        (1, B, ORACLE),
        (-1, B, NONE),
        (-1, A, ORACLE),
        (1, A, NONE),
    ]


def test_a_contrast_refuses_what_the_plan_does_not_define() -> None:
    arm = cells_of({ORACLE: [outcome(1.0)] * 4})
    outcomes = {A: arm, B: arm}
    plus, minus = ArmCell(B, ORACLE), ArmCell(A, ORACLE)

    with pytest.raises(ValueError, match="sign flip"):
        contrast(
            hand_weights(),
            outcomes,
            plus,
            minus,
            "braking_share",
            "sign_flip",
            HAND_LOG,
            None,
            None,
        )
    with pytest.raises(ValueError, match="Holm step"):
        contrast(
            hand_weights(), outcomes, plus, minus, "braking_share", "bootstrap", HAND_LOG, None, 1
        )
    with pytest.raises(ValueError, match="test"):
        contrast(
            hand_weights(),
            outcomes,
            plus,
            minus,
            "braking_share",
            cast(StudyTest, "t-test"),
            HAND_LOG,
            None,
            None,
        )
    with pytest.raises(ValueError, match="outcome"):
        contrast(
            hand_weights(),
            outcomes,
            plus,
            minus,
            cast(StudyOutcome, "comfort"),
            "bootstrap",
            HAND_LOG,
            None,
            None,
        )
    with pytest.raises(ValueError, match="no outcomes of arm"):
        contrast(
            hand_weights(),
            outcomes,
            ArmCell(C, ORACLE),
            minus,
            "braking_share",
            "bootstrap",
            HAND_LOG,
            None,
            None,
        )
    with pytest.raises(ValueError, match="token-4"):
        contrast(
            hand_weights(),
            {A: arm, B: {ORACLE: dict(list(arm[ORACLE].items())[:3])}},
            plus,
            minus,
            "braking_share",
            "bootstrap",
            HAND_LOG,
            None,
            None,
        )


def test_totals_sum_over_the_drawn_tokens() -> None:
    minus = cells_of({ORACLE: [outcome(exposure=45.0)] * 4})
    plus = cells_of({ORACLE: [outcome(exposure=30.0)] * 2 + [outcome(exposure=45.0)] * 2})
    weights = hand_weights()

    result = contrast(
        weights,
        {A: minus, B: plus},
        ArmCell(B, ORACLE),
        ArmCell(A, ORACLE),
        "simulated_seconds",
        "bootstrap",
        HAND_LOG,
        None,
        None,
    )

    draws = (weights.weights * np.array([-15.0, -15.0, 0.0, 0.0])).sum(axis=1)
    assert result.estimate == -30.0
    assert (result.interval.low, result.interval.high) == percentile_interval(draws, 0.95)


def test_a_contrast_with_its_family_size_but_no_holm_step_adds_the_simultaneous_interval_only() -> (
    None
):
    minus = cells_of({ORACLE: [outcome(1.0), outcome(2.0), outcome(3.0), outcome(4.0)]})
    plus = cells_of({ORACLE: [outcome(2.0), outcome(2.0), outcome(5.0), outcome(1.0)]})

    result = contrast(
        hand_weights(),
        {A: minus, B: plus},
        ArmCell(B, ORACLE),
        ArmCell(A, ORACLE),
        "braking_share",
        "bootstrap",
        HAND_LOG,
        5,
        None,
    )

    assert result.simultaneous_interval is not None
    assert result.simultaneous_interval.confidence == 0.99
    assert result.holm_step_interval is None


def test_a_holm_step_without_its_family_size_is_refused_with_the_reason() -> None:
    arm = cells_of({ORACLE: [outcome(1.0)] * 4})

    with pytest.raises(ValueError) as refused:
        contrast(
            hand_weights(),
            {A: arm, B: arm},
            ArmCell(B, ORACLE),
            ArmCell(A, ORACLE),
            "braking_share",
            "bootstrap",
            HAND_LOG,
            None,
            1,
        )

    assert str(refused.value) == "a Holm step is a step of a family, so its size k is needed too"


def test_a_contrast_without_sign_flip_settings_takes_the_committed_study_files() -> None:
    """Twenty-one logs is one more than the study file enumerates, so p comes from random flips."""

    tokens = tuple(f"token-{index:02d}" for index in range(1, 22))
    logs = {token: f"log-{token}" for token in tokens}
    weights = cluster_bootstrap_weights(tokens, dict.fromkeys(tokens, "a"), logs, resamples=50)
    outcomes = {
        A: {ORACLE: {token: outcome(collisions=0) for token in tokens}},
        B: {ORACLE: {token: outcome(collisions=1) for token in tokens}},
    }
    settings = yaml.safe_load(COMMITTED_STUDY.read_text(encoding="utf-8"))["sign_flip"]

    result = contrast(
        weights,
        outcomes,
        ArmCell(B, ORACLE),
        ArmCell(A, ORACLE),
        "collision_indicator",
        "sign_flip",
        logs,
        None,
        None,
    )

    assert result.p_value == sign_flip_p_value(
        [1] * 21,
        enumerate_up_to=settings["enumerate_up_to"],
        random_flips=settings["random_flips"],
        seed=settings["seed"],
    )
    assert (settings["enumerate_up_to"], settings["random_flips"]) == (20, 100_000)
    # No random pattern reaches |sum| = 21, and enumerating all 2^21 would give 2 / 2^21.
    assert result.p_value == 1 / 100_001
    assert sign_flip_p_value([1] * 21, enumerate_up_to=21) == 2 / 2**21


def test_a_contrast_runs_clean_when_warnings_are_errors() -> None:
    """A caller that turns every warning into an error still gets the contrast."""

    minus = cells_of({ORACLE: [outcome(1.0), outcome(2.0), outcome(3.0), outcome(4.0)]})
    plus = cells_of({ORACLE: [outcome(2.0), outcome(2.0), outcome(5.0), outcome(3.0)]})

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = contrast(
            hand_weights(),
            {A: minus, B: plus},
            ArmCell(B, ORACLE),
            ArmCell(A, ORACLE),
            "braking_share",
            "bootstrap",
            HAND_LOG,
            None,
            None,
        )

    assert result.estimate == pytest.approx(12.0 / 180.0 - 10.0 / 180.0)


# --------------------------------------------------------------------------
# The Q3 ratio
# --------------------------------------------------------------------------


def braking_share_draws(weights: BootstrapWeights, cells: Mapping[str, TokenOutcome]) -> np.ndarray:
    return weighted_ratio(
        weights,
        {t: o.braking_s for t, o in cells.items()},
        {t: o.exposure_s for t, o in cells.items()},
    )


def test_the_q3_ratio_takes_localization_from_b_when_e_does_not_run_it() -> None:
    weights = hand_weights()
    b_arm = {
        FULL: dict(zip(HAND_TOKENS, [outcome(9.0), outcome(3.0), outcome(12.0), outcome(6.0)])),
        LOCALIZATION: dict(
            zip(HAND_TOKENS, [outcome(8.0), outcome(1.0), outcome(2.0), outcome(7.0)])
        ),
    }
    e_arm = {
        FULL: dict(zip(HAND_TOKENS, [outcome(9.5), outcome(2.0), outcome(11.0), outcome(6.5)]))
    }

    ratio = keying_ratio(weights, {B: b_arm, E: e_arm})

    sd_b = float(
        np.std(
            braking_share_draws(weights, b_arm[FULL])
            - braking_share_draws(weights, b_arm[LOCALIZATION]),
            ddof=1,
        )
    )
    sd_e = float(
        np.std(
            braking_share_draws(weights, e_arm[FULL])
            - braking_share_draws(weights, b_arm[LOCALIZATION]),
            ddof=1,
        )
    )
    assert [(term.sign, term.arm, term.cell) for term in ratio.e_terms] == [
        (1, E, FULL),
        (-1, B, LOCALIZATION),
    ]
    assert [(term.sign, term.arm, term.cell) for term in ratio.b_terms] == [
        (1, B, FULL),
        (-1, B, LOCALIZATION),
    ]
    assert ratio.sd_b == sd_b
    assert ratio.sd_e == sd_e
    assert ratio.ratio == sd_b / sd_e


def test_the_q3_ratio_uses_es_own_localization_cell_when_e_runs_it() -> None:
    cells = {
        FULL: dict(zip(HAND_TOKENS, [outcome(9.0), outcome(3.0), outcome(12.0), outcome(6.0)])),
        LOCALIZATION: dict(
            zip(HAND_TOKENS, [outcome(8.0), outcome(1.0), outcome(2.0), outcome(7.0)])
        ),
    }

    ratio = keying_ratio(hand_weights(), {B: cells, E: cells})

    assert [(term.arm, term.cell) for term in ratio.e_terms] == [(E, FULL), (E, LOCALIZATION)]
    assert ratio.ratio == 1.0


def test_the_q3_ratio_is_undefined_when_e_has_no_spread() -> None:
    same = dict(zip(HAND_TOKENS, [outcome(9.0), outcome(3.0), outcome(12.0), outcome(6.0)]))
    other = dict(zip(HAND_TOKENS, [outcome(8.0), outcome(1.0), outcome(2.0), outcome(7.0)]))

    ratio = keying_ratio(hand_weights(), {B: {FULL: other, LOCALIZATION: same}, E: {FULL: same}})

    assert ratio.sd_e == 0.0
    assert ratio.sd_b > 0.0
    assert ratio.ratio is None


# --------------------------------------------------------------------------
# The reproduction gate
# --------------------------------------------------------------------------


def test_the_reproduction_gate_passes_on_the_released_evidence_of_the_eight_cells(
    null_tree: Tree,
) -> None:
    result = reproduce_study_cells(
        null_tree.released_root, null_tree.evidence_dir, null_tree.manifest
    )

    assert result.gate == "G4"
    assert result.passed
    assert dict(result.counts) == {
        "cells": 8,
        "fields_compared": 8 * (4 * 6 + 4),
        "interval_mismatches": 0,
        "evaluation_mismatches": 0,
    }
    assert result.local_detail == ()


def copy_evidence(tree: Tree, root: Path) -> Path:
    evidence = root / "evidence"
    evidence.mkdir()
    for name in ("evaluation.json", "intervals.json"):
        (evidence / name).write_bytes((tree.evidence_dir / name).read_bytes())
    return evidence


def edit_json(path: Path, edit: Callable[[Any], None]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    edit(document)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_the_reproduction_gate_fails_when_one_bound_moves_by_one_ulp(
    tmp_path: Path, null_tree: Tree
) -> None:
    evidence = copy_evidence(null_tree, tmp_path)

    def move(document: Any) -> None:
        interval = document["intervals"][FULL]["intervention_duration_s"]
        interval["high"] = float(np.nextafter(interval["high"], math.inf))

    edit_json(evidence / "intervals.json", move)

    result = reproduce_study_cells(null_tree.released_root, evidence, null_tree.manifest)

    assert not result.passed
    assert result.counts["interval_mismatches"] == 1
    assert result.counts["evaluation_mismatches"] == 0
    assert len(result.local_detail) == 1
    assert "intervention_duration_s" in result.local_detail[0]


def test_the_reproduction_gate_fails_on_an_evaluation_field_and_on_a_missing_cell(
    tmp_path: Path, null_tree: Tree
) -> None:
    evidence = copy_evidence(null_tree, tmp_path)

    def change_braking(document: Any) -> None:
        (row,) = (row for row in document["configurations"] if row["configuration_id"] == ORACLE)
        row["mean_intervention_duration_s"] += 1.0

    def drop_row(document: Any) -> None:
        document["configurations"] = [
            row for row in document["configurations"] if row["configuration_id"] != LATENCY
        ]
        document["simulated_seconds"] = sum(
            row["simulated_seconds"] for row in document["configurations"]
        )

    def drop_intervals(document: Any) -> None:
        del document["intervals"][TRACK]

    edit_json(evidence / "evaluation.json", change_braking)
    edit_json(evidence / "evaluation.json", drop_row)
    edit_json(evidence / "intervals.json", drop_intervals)

    result = reproduce_study_cells(null_tree.released_root, evidence, null_tree.manifest)

    assert not result.passed
    assert result.counts["evaluation_mismatches"] == 1 + 4
    assert result.counts["interval_mismatches"] == 4 * 6
    assert result.counts["fields_compared"] == 8 * 28


def test_a_failed_reproduction_gate_is_written_and_refuses_the_analysis(
    tmp_path: Path, null_tree: Tree
) -> None:
    evidence = copy_evidence(null_tree, tmp_path)
    edit_json(
        evidence / "intervals.json",
        lambda document: document["intervals"][ORACLE]["collision_indicator"].update(
            low=float(
                np.nextafter(document["intervals"][ORACLE]["collision_indicator"]["low"], -math.inf)
            )
        ),
    )
    tree = replace(null_tree, evidence_dir=evidence)
    g4_path = tmp_path / "analysis" / "g4.json"

    with pytest.raises(ValueError, match="G4"):
        tree.analyse(g4_path=g4_path)

    written = load_gates(g4_path)
    assert [(gate.gate, gate.passed) for gate in written.gates] == [("G4", False)]
    assert written.gates[0].counts["interval_mismatches"] == 1
    assert written.arms_checked == ()
    assert "G4" in written.artifacts_only_detail


def test_a_passed_reproduction_gate_is_written_beside_the_summary(
    tmp_path: Path, null_tree: Tree
) -> None:
    g4_path = tmp_path / "g4.json"

    summary = null_tree.analyse(g4_path=g4_path)

    written = load_gates(g4_path)
    assert [(gate.gate, gate.passed) for gate in written.gates] == [("G4", True)]
    assert written.reference == "released"
    assert summary.g4.gate == "G4"
    assert summary.g4.passed
    assert summary.g4.counts == written.gates[0].counts


def test_the_reproduction_gate_names_each_evaluation_field_it_cannot_reproduce(
    tmp_path: Path, null_tree: Tree
) -> None:
    evidence = copy_evidence(null_tree, tmp_path)

    def change_braking(document: Any) -> None:
        (row,) = (row for row in document["configurations"] if row["configuration_id"] == ORACLE)
        row["mean_intervention_duration_s"] = 7.5

    edit_json(evidence / "evaluation.json", change_braking)

    result = reproduce_study_cells(null_tree.released_root, evidence, null_tree.manifest)

    assert result.local_detail == (
        f"evaluation.json {ORACLE}/mean_intervention_duration_s: released 7.5, recomputed 6.0",
    )


def test_the_reproduction_gate_holds_the_released_records_to_the_manifest(
    tmp_path: Path, null_tree: Tree
) -> None:
    """A manifest of the same members but another protocol is not the released run's."""

    document = json.loads(null_tree.manifest.read_text(encoding="utf-8"))
    document["protocol_sha256"] = "0" * 64
    other = write_json(tmp_path / "evaluation.json", document)
    assert membership_sha256(load_manifest(other)) == membership_sha256(
        load_manifest(null_tree.manifest)
    )

    with pytest.raises(ValueError, match="manifest protocol hash"):
        reproduce_study_cells(null_tree.released_root, null_tree.evidence_dir, other)


def test_the_reproduction_gate_compares_the_cells_the_study_file_names(
    tmp_path: Path, null_tree: Tree
) -> None:
    document = yaml.safe_load(null_tree.study.read_text(encoding="utf-8"))
    document["cells"] = [cell for cell in STUDY_CELLS if cell != LATENCY]
    study = tmp_path / "study.yaml"
    study.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    evidence = copy_evidence(null_tree, tmp_path)
    edit_json(
        evidence / "intervals.json",
        lambda released: released["intervals"][ORACLE]["collision_indicator"].update(
            low=float(
                np.nextafter(released["intervals"][ORACLE]["collision_indicator"]["low"], -math.inf)
            )
        ),
    )
    tree = replace(null_tree, study=study, evidence_dir=evidence)
    g4_path = tmp_path / "g4.json"

    with pytest.raises(ValueError, match="G4"):
        tree.analyse(g4_path=g4_path)

    counts = load_gates(g4_path).gates[0].counts
    assert counts["cells"] == 7
    assert counts["fields_compared"] == 7 * (4 * 6 + 4)
    assert counts["interval_mismatches"] == 1


# --------------------------------------------------------------------------
# The gates the analysis requires
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"failed": ("G1",)}, "G1"),
        ({"failed": ("G2",)}, "G2"),
        ({"names": ("G1", "G2", "G5")}, "G3"),
        ({"names": ("preflight",)}, "G1"),
        ({"arms": (A,)}, "every arm"),
        ({"reference": "arm-a"}, "reference"),
    ],
    ids=[
        "failed G1",
        "failed G2",
        "missing G3",
        "a preflight file",
        "a per-arm file",
        "arm-a file",
    ],
)
def test_a_failed_or_missing_gate_is_refused(
    null_tree: Tree, options: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        null_tree.analyse(gates=null_tree.gates(**options))


def test_a_gate_file_of_another_study_is_refused(tmp_path: Path, null_tree: Tree) -> None:
    other = tmp_path / "other.yaml"
    other.write_text(
        null_tree.study.read_text(encoding="utf-8") + "# another study\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="study file"):
        null_tree.analyse(gates=null_tree.gates(study=other))


def test_a_manifest_of_another_cohort_is_refused(tmp_path: Path, null_tree: Tree) -> None:
    document = json.loads(null_tree.manifest.read_text(encoding="utf-8"))
    document["families"]["bicycle_or_vru"] = ["bike-01"]
    other = write_json(tmp_path / "other.json", document)

    with pytest.raises(ValueError, match="membership"):
        null_tree.analyse(manifest=other)


def test_an_unknown_reference_is_refused(null_tree: Tree) -> None:
    with pytest.raises(ValueError, match="reference"):
        null_tree.analyse(gates=null_tree.gates(), reference="arm-b")


def test_arm_a_reference_mode_accepts_a_failed_g2_and_labels_the_summary_exploratory(
    tmp_path: Path, null_tree: Tree
) -> None:
    g4_path = tmp_path / "g4.json"

    summary = null_tree.analyse(
        gates=null_tree.gates(reference="arm-a", failed=("G2",)), reference="arm-a", g4_path=g4_path
    )

    assert summary.reference == "arm-a"
    assert summary.exploratory is True
    assert load_gates(g4_path).exploratory is True


@pytest.mark.parametrize(
    "gates",
    [
        {"reference": "released", "failed": ("G2",)},
        {"reference": "arm-a", "failed": ("G2", "G3")},
        {"reference": "arm-a", "failed": ("G5",)},
    ],
    ids=["a released gate file", "a failed G3", "a failed G5"],
)
def test_arm_a_reference_mode_still_requires_its_gate_file_and_every_other_gate(
    null_tree: Tree, gates: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        null_tree.analyse(gates=null_tree.gates(**gates), reference="arm-a")


def test_without_the_flag_a_released_summary_is_not_exploratory(
    null_summary: PolicyV2SummaryV1,
) -> None:
    assert null_summary.reference == "released"
    assert null_summary.exploratory is False


# --------------------------------------------------------------------------
# The pre-registered analysis
# --------------------------------------------------------------------------


def test_the_summary_names_the_study_the_protocol_and_the_cohort(
    null_tree: Tree, null_summary: PolicyV2SummaryV1
) -> None:
    assert null_summary.schema_version == "aeb-policy-v2-summary/v1"
    assert null_summary.study_sha256 == hashlib.sha256(null_tree.study.read_bytes()).hexdigest()
    assert null_summary.protocol_sha256 == PROTOCOL_SHA
    assert null_summary.cohort_manifest_sha256 == membership_sha256(
        load_manifest(null_tree.manifest)
    )
    assert null_summary.common_valid_tokens == len(TOKENS)
    assert PolicyV2SummaryV1.model_validate_json(null_summary.model_dump_json()) == null_summary


def test_planted_effects_give_the_expected_classifications_and_q1_label(
    planted_summary: PolicyV2SummaryV1,
) -> None:
    results = hypotheses(planted_summary)

    assert [results[name].classification for name in ("H1", "H2", "H3", "H4", "H5")] == [
        "supported",
        "supported",
        "supported",
        "supported",
        "increase",
    ]
    assert [results[f"Q2.{index}"].classification for index in range(1, 5)] == ["supported"] * 4
    assert all(results[f"Q3.{index}"].classification == "not_established" for index in range(1, 11))
    assert all(results[f"Q3.{index}"].contrast.p_value == 1.0 for index in range(1, 11))
    assert planted_summary.q1_label == "support"
    assert results["H1"].contrast.estimate == pytest.approx(0.125 - 0.4)
    assert results["H1"].contrast.estimate <= -0.10
    assert results["H2"].contrast.estimate <= -0.10
    assert results["H4"].contrast.p_value == 2.0**-7
    assert results["H4"].contrast.test == "sign_flip"
    assert results["H1"].contrast.test == "bootstrap"
    assert planted_summary.statements.h4 == H4_SUPPORTED_SENTENCE
    assert planted_summary.statements.h3 == H3_SENTENCE


def test_the_primary_hypotheses_carry_three_intervals_and_their_holm_steps(
    planted_summary: PolicyV2SummaryV1,
) -> None:
    primary = [result for result in planted_summary.hypotheses if result.family == "primary"]

    assert [result.id for result in primary] == ["H1", "H2", "H3", "H4", "H5"]
    assert sorted(result.holm_step for result in primary) == [1, 2, 3, 4, 5]
    for result in primary:
        assert result.rejected
        assert result.contrast.interval is not None
        assert result.contrast.interval.confidence == 0.95
        assert result.contrast.simultaneous_interval is not None
        assert result.contrast.simultaneous_interval.confidence == 0.99
        assert result.contrast.holm_step_interval is not None
        assert result.contrast.holm_step_interval.confidence == holm_step_confidence(
            5, result.holm_step
        )
        assert [(term.sign, term.arm) for term in result.contrast.terms] == [(1, B), (-1, A)]
    others = [result for result in planted_summary.hypotheses if result.family != "primary"]
    assert [result.family for result in others] == ["Q2"] * 4 + ["Q3-check"] * 10
    assert all(result.contrast.simultaneous_interval is None for result in others)


def test_a_planted_oracle_collision_increase_sets_the_qualifier_and_the_h5_sentence(
    planted_summary: PolicyV2SummaryV1,
) -> None:
    h5 = hypotheses(planted_summary)["H5"]

    assert h5.contrast.p_value == 2.0**-5
    assert h5.contrast.p_value < 0.05
    assert h5.contrast.estimate > 0
    assert planted_summary.h5_qualifier is True
    assert planted_summary.statements.h5 == H5_INCREASE_SENTENCE


def test_an_oracle_collision_decrease_rejected_by_holm_gives_the_decrease_sentence(
    tmp_path: Path,
) -> None:
    summary = build_tree(tmp_path, decrease).analyse()
    h5 = hypotheses(summary)["H5"]

    assert h5.classification == "decrease"
    assert h5.contrast.estimate < 0
    assert summary.h5_qualifier is False
    assert summary.statements.h5 == H5_DECREASE_SENTENCE


def test_when_both_arms_are_zero_p_is_one_k_stays_five_and_two_intervals_are_reported(
    null_summary: PolicyV2SummaryV1,
) -> None:
    results = hypotheses(null_summary)
    h4 = results["H4"]
    zero = clopper_pearson(0, len(TOKENS))

    assert h4.contrast.p_value == 1.0
    assert h4.classification == "not_established"
    assert len([result for result in null_summary.hypotheses if result.family == "primary"]) == 5
    assert sorted(
        result.holm_step for result in null_summary.hypotheses if result.family == "primary"
    ) == [1, 2, 3, 4, 5]
    assert [
        (
            proportion.arm,
            proportion.cell,
            proportion.successes,
            proportion.trials,
            proportion.low,
            proportion.high,
        )
        for proportion in h4.contrast.clopper_pearson
    ] == [(B, FULL, 0, 16, *zero), (A, FULL, 0, 16, *zero)]
    assert zero[1] == pytest.approx(1 - 0.025 ** (1 / 16))
    assert null_summary.q1_label == "no_support"
    assert null_summary.h5_qualifier is False
    assert null_summary.statements.h5 == H5_NO_CHANGE_SENTENCE
    assert null_summary.statements.h4 == H4_OTHER_SENTENCE


def test_the_sensitivity_analyses_of_the_primary_family_are_reported(
    planted_summary: PolicyV2SummaryV1,
) -> None:
    rows = {(row.hypothesis, row.analysis): row.contrast for row in planted_summary.sensitivity}
    results = hypotheses(planted_summary)

    assert set(rows) == {
        ("H1", "braking_seconds_per_run"),
        ("H2", "braking_seconds_per_run"),
        ("H3", "not_at_fault_contacts_per_run"),
        *((name, "family_drive_clusters") for name in ("H1", "H2", "H3", "H4", "H5")),
        ("H4", "drive_sign_flip"),
        ("H5", "drive_sign_flip"),
        *((name, "whole_log_bootstrap") for name in ("H1", "H2", "H3")),
    }
    assert rows[("H1", "braking_seconds_per_run")].estimate == pytest.approx(1.5 - 6.0)
    assert rows[("H3", "not_at_fault_contacts_per_run")].estimate == pytest.approx(-1.0)
    assert rows[("H1", "family_drive_clusters")].cluster == "family-drive"
    assert rows[("H1", "family_drive_clusters")].estimate == results["H1"].contrast.estimate
    assert rows[("H1", "family_drive_clusters")].p_value is None
    assert rows[("H1", "whole_log_bootstrap")].cluster == "log"
    assert rows[("H1", "whole_log_bootstrap")].p_value is not None
    # Six lead tokens in six logs of three drives: the drive flips see three units.
    assert rows[("H5", "drive_sign_flip")].p_value == sign_flip_p_value([6, 6, 6])
    assert rows[("H5", "drive_sign_flip")].test_unit == "drive"
    assert rows[("H5", "drive_sign_flip")].interval is None


def test_the_secondary_and_descriptive_contrasts_are_all_reported(
    planted_summary: PolicyV2SummaryV1,
) -> None:
    secondary: dict[str, int] = {}
    for row in planted_summary.secondary:
        secondary[row.analysis] = secondary.get(row.analysis, 0) + 1
    descriptive: dict[str, int] = {}
    for row in planted_summary.descriptive_contrasts:
        descriptive[row.analysis] = descriptive.get(row.analysis, 0) + 1

    assert secondary == {
        "other_cells": 15,
        "difference_in_differences": 4,
        "combined_remedy": 6,
        "benefit_and_harm": 4,
        "any_contact": 2,
        "activation_rate": 6,
        "exposure": 4,
    }
    assert descriptive == {
        "velocity_by_cell": 36,
        "velocity_interaction": 18,
        "primary_by_family": 20,
    }
    assert all(row.contrast.interval is not None for row in planted_summary.secondary)
    assert all(row.contrast.p_value is not None for row in planted_summary.secondary)
    assert all(row.contrast.p_value is None for row in planted_summary.descriptive_contrasts)
    by_family = [
        row for row in planted_summary.descriptive_contrasts if row.analysis == "primary_by_family"
    ]
    assert {(row.hypothesis, row.contrast.family) for row in by_family} == {
        (name, family) for name in ("H1", "H2", "H3", "H4", "H5") for family in TOKENS_BY_FAMILY
    }


def test_benefit_and_harm_report_the_clopper_pearson_interval_of_each_arm(
    planted_summary: PolicyV2SummaryV1,
) -> None:
    rows = [
        row.contrast
        for row in planted_summary.secondary
        if row.analysis == "benefit_and_harm"
        and row.contrast.outcome == "avoided"
        and row.contrast.terms[0].cell == ORACLE
    ]

    (avoided,) = rows
    # no_aeb collides in walk-01 and walk-02; A's oracle avoids both, and B's too.
    assert [(p.event, p.arm, p.successes, p.trials) for p in avoided.clopper_pearson] == [
        ("avoided", B, 2, 16),
        ("avoided", A, 2, 16),
    ]


def test_the_exposure_contrasts_count_early_ends(planted_summary: PolicyV2SummaryV1) -> None:
    rows = {
        (row.contrast.outcome, row.contrast.terms[0].cell): row.contrast
        for row in planted_summary.secondary
        if row.analysis == "exposure"
    }

    # B's six lead tokens collide at 7 s in the oracle, where A runs to 15 s.
    assert rows[("simulated_seconds", ORACLE)].estimate == pytest.approx(6 * 3 * (7.0 - 15.0))
    assert rows[("early_ends", ORACLE)].estimate == 0.0
    assert rows[("early_ends", FULL)].estimate == 0.0


def test_no_field_compares_missed_or_false_interventions_across_policies(
    planted_summary: PolicyV2SummaryV1,
) -> None:
    compared = [
        contrast_
        for contrast_ in every_contrast(planted_summary)
        if contrast_.outcome in ("missed_interventions", "false_interventions")
    ]

    assert len(compared) == 4
    for contrast_ in compared:
        assert len({POLICY[term.arm] for term in contrast_.terms}) == 1
    fields = json.dumps(planted_summary.model_dump(mode="json"))
    assert "delay_difference" not in fields


def test_the_descriptive_levels_carry_the_released_count_names_and_ratios_of_sums(
    planted_summary: PolicyV2SummaryV1,
) -> None:
    b_oracle = level(planted_summary, B, ORACLE)
    a_oracle = level(planted_summary, A, ORACLE)
    walks = level(planted_summary, A, ORACLE, "pedestrian_or_crosswalk")

    assert len(planted_summary.levels) == (4 * 8 + 4) * 5
    assert (b_oracle.tokens, b_oracle.records) == (16, 48)
    assert (b_oracle.collisions, b_oracle.collision_tokens) == (18, 6)
    assert b_oracle.contacts_not_at_fault == 0
    assert a_oracle.contacts_not_at_fault == 48
    assert b_oracle.simulated_seconds == 6 * 21.0 + 10 * 45.0
    assert b_oracle.braking_share == 72.0 / 576.0
    assert b_oracle.braking_seconds_per_run == 1.5
    assert a_oracle.not_at_fault_contact_rate == pytest.approx(48 / 720 * 1000)
    assert a_oracle.activation_rate == pytest.approx(48 / 720 * 3600)
    assert b_oracle.collision_clopper_pearson.successes == 6
    assert (
        b_oracle.collision_clopper_pearson.low,
        b_oracle.collision_clopper_pearson.high,
    ) == clopper_pearson(6, 16)
    assert b_oracle.collision_speed_mps.count == 18
    assert b_oracle.collision_speed_mps.median == pytest.approx(5.0)
    assert b_oracle.stop_distance_m.count == 30
    assert b_oracle.runs_never_stopped == 18
    assert a_oracle.matched_delays_s.count == 48
    assert a_oracle.matched_delays_s.median == 0.0
    assert (walks.tokens, walks.records) == (4, 12)
    assert level(planted_summary, A, LOCALIZATION).early_ends == 3
    assert level(planted_summary, A, NO_AEB).matched_delays_s.median is None
    assert {row.cell for row in planted_summary.levels if row.arm == E} == set(E_CELLS)


def test_arm_a_levels_carry_the_released_totals_of_its_cells(
    planted_tree: Tree, planted_summary: PolicyV2SummaryV1
) -> None:
    evaluation = AEBEvaluationV1.model_validate_json(
        (planted_tree.evidence_dir / "evaluation.json").read_text(encoding="utf-8")
    )

    assert len(evaluation.configurations) == len(STUDY_CELLS)
    for row in evaluation.configurations:
        replicated = level(planted_summary, A, row.configuration_id)
        assert (
            replicated.simulated_seconds,
            replicated.collisions,
            replicated.contacts_not_at_fault,
            replicated.records,
        ) == (row.simulated_seconds, row.collisions, row.contacts_not_at_fault, row.scenarios)
        assert replicated.braking_seconds_per_run == row.mean_intervention_duration_s


def test_a_level_distribution_is_the_count_quartiles_and_maximum_of_its_values() -> None:
    """The quartiles are numpy's linearly interpolated 25th, 50th and 75th percentiles."""

    assert study_analysis._distribution([4.0, 0.0, 3.0, 1.0, 2.0]) == StudyDistributionV1(
        count=5, median=2.0, lower_quartile=1.0, upper_quartile=3.0, maximum=4.0
    )
    assert study_analysis._distribution([]) == StudyDistributionV1(count=0)


def test_no_fixed_sentence_says_safer_or_fixes() -> None:
    assert set(FIXED_SENTENCES) == {
        H3_SENTENCE,
        H4_SUPPORTED_SENTENCE,
        H4_OTHER_SENTENCE,
        H5_INCREASE_SENTENCE,
        H5_DECREASE_SENTENCE,
        H5_NO_CHANGE_SENTENCE,
    }
    for sentence in FIXED_SENTENCES:
        assert "safer" not in sentence.lower()
        assert "fixes" not in sentence.lower()
    assert H5_INCREASE_SENTENCE == "Under oracle perception, gating increased counted collisions."
    assert H5_DECREASE_SENTENCE == "Under oracle perception, gating reduced counted collisions."
    assert H5_NO_CHANGE_SENTENCE == (
        "This study does not detect a change in counted collisions under oracle perception."
    )
    assert H4_SUPPORTED_SENTENCE == (
        "Under collision-course gating, the all-channel configuration no longer has zero "
        "counted collisions."
    )
    assert H4_OTHER_SENTENCE == (
        "This study does not show that the zero depends on the released target selection."
    )
    assert H3_SENTENCE == (
        "Excluded contacts and counted collisions are coupled by the stopped-ego rule, so less "
        "braking can move contacts from the excluded class into counted collisions."
    )


def test_the_summary_ties_the_exploratory_label_to_the_reference(
    null_summary: PolicyV2SummaryV1,
) -> None:
    document = null_summary.model_dump(mode="json")

    with pytest.raises(ValidationError, match="exploratory"):
        PolicyV2SummaryV1.model_validate({**document, "exploratory": True})
    with pytest.raises(ValidationError, match="low"):
        StudyIntervalV1(confidence=0.95, low=1.0, high=0.0)


def test_each_analysis_resamples_the_clusters_the_plan_names(
    planted_tree: Tree, planted_summary: PolicyV2SummaryV1
) -> None:
    """The primary family resamples (family, log) clusters, and each other analysis its own.

    The (family, drive) sensitivity resamples drives within families, the
    whole-log sensitivity resamples logs in one stratum, and the per-family
    contrasts resample the logs of one family. Each expected interval is drawn
    here from the cohort's own maps, so a draw of the wrong unit is caught.
    """

    outcomes = {
        arm: arm_outcomes(load_arm(planted_tree.arms_root / arm, STUDY_CELLS, TOKENS))
        for arm in (A, B)
    }
    drives = {token: drive_of(log) for token, log in LOG_OF.items()}
    weights: dict[str, BootstrapWeights] = {
        "family-log": cluster_bootstrap_weights(TOKENS, FAMILY_OF, LOG_OF, resamples=RESAMPLES),
        "family-drive": cluster_bootstrap_weights(TOKENS, FAMILY_OF, drives, resamples=RESAMPLES),
        "log": cluster_bootstrap_weights(
            TOKENS, dict.fromkeys(TOKENS, "all"), LOG_OF, resamples=RESAMPLES
        ),
        **{
            family: cluster_bootstrap_weights(tokens, FAMILY_OF, LOG_OF, resamples=RESAMPLES)
            for family, tokens in TOKENS_BY_FAMILY.items()
        },
    }
    results = hypotheses(planted_summary)
    sensitivity = {
        (row.hypothesis, row.analysis): row.contrast for row in planted_summary.sensitivity
    }
    by_family = {
        (row.hypothesis, row.contrast.family): row.contrast
        for row in planted_summary.descriptive_contrasts
        if row.analysis == "primary_by_family"
    }

    def expected(name: str, resampled: str, test: StudyTest, step: Optional[int]) -> Any:
        hypothesis = results[name]
        cell = hypothesis.contrast.terms[0].cell
        return contrast(
            weights[resampled],
            outcomes,
            ArmCell(B, cell),
            ArmCell(A, cell),
            hypothesis.contrast.outcome,
            test,
            LOG_OF,
            None if step is None else 5,
            step,
        )

    def intervals(reported: Any) -> tuple[Any, Any, Any]:
        return (reported.interval, reported.simultaneous_interval, reported.holm_step_interval)

    for name in ("H1", "H2", "H3", "H4", "H5"):
        step = results[name].holm_step
        test = results[name].contrast.test
        primary = expected(name, "family-log", test, step)
        assert intervals(results[name].contrast) == intervals(primary)
        assert results[name].contrast.p_value == primary.p_value
        drive = expected(name, "family-drive", test, step)
        assert intervals(sensitivity[(name, "family_drive_clusters")]) == intervals(drive)
        for family in TOKENS_BY_FAMILY:
            assert by_family[(name, family)].interval == expected(name, family, test, None).interval
    for name in ("H1", "H2", "H3"):
        step = results[name].holm_step
        whole_log = expected(name, "log", "bootstrap", step)
        assert intervals(sensitivity[(name, "whole_log_bootstrap")]) == intervals(whole_log)
        assert sensitivity[(name, "whole_log_bootstrap")].p_value == whole_log.p_value
    for (name, _), reported in sensitivity.items():
        if reported.holm_step_interval is not None:
            assert reported.holm_step_interval.confidence == holm_step_confidence(
                5, results[name].holm_step
            )
    assert results["H2"].holm_step == 2
    spread = {
        (row.low, row.high)
        for row in (
            results["H2"].contrast.interval,
            sensitivity[("H2", "family_drive_clusters")].interval,
            sensitivity[("H2", "whole_log_bootstrap")].interval,
        )
        if row is not None
    }
    assert len(spread) == 3


def test_every_tested_collision_contrast_flips_whole_logs(
    planted_summary: PolicyV2SummaryV1,
) -> None:
    """A tested collision-indicator contrast flips logs; every other takes the bootstrap p."""

    tested = [
        *(result.contrast for result in planted_summary.hypotheses),
        *(row.contrast for row in planted_summary.secondary),
    ]
    collisions = [row for row in tested if row.outcome == "collision_indicator"]

    assert len(collisions) == 2 + 2 + 5 + 2 + 2
    assert all((row.test, row.test_unit) == ("sign_flip", "log") for row in collisions)
    assert all(
        (row.test, row.test_unit) == ("bootstrap", None)
        for row in tested
        if row.outcome != "collision_indicator"
    )


def test_an_analysis_called_without_a_reference_is_the_released_one(null_tree: Tree) -> None:
    summary = analyse_study(
        null_tree.study,
        null_tree.arms_root,
        null_tree.released_root,
        null_tree.evidence_dir,
        null_tree.manifest,
        null_tree.gates(),
    )

    assert summary.reference == "released"
    assert summary.exploratory is False


def test_only_primary_hypotheses_carry_family_intervals_and_secondary_collisions_flip_logs(
    null_tree: Tree,
) -> None:
    """The simultaneous and Holm-step intervals belong to the primary family alone.

    Q2 and the Q3 check report the 95% interval only, and every secondary
    collision-indicator contrast takes the sign flip over logs.
    """

    summary = null_tree.analyse()

    families = [result.family for result in summary.hypotheses]
    assert families == ["primary"] * 5 + ["Q2"] * 4 + ["Q3-check"] * 10
    for result in summary.hypotheses:
        primary = result.family == "primary"
        assert result.contrast.interval is not None
        assert (result.contrast.simultaneous_interval is not None) is primary
        assert (result.contrast.holm_step_interval is not None) is primary
    collisions = [
        row.contrast for row in summary.secondary if row.contrast.outcome == "collision_indicator"
    ]
    assert len(collisions) == 5 + 2 + 2
    assert all((row.test, row.test_unit) == ("sign_flip", "log") for row in collisions)


def test_the_draws_come_from_the_study_files_seed_and_resample_count(
    tmp_path: Path, planted_tree: Tree
) -> None:
    document = yaml.safe_load(planted_tree.study.read_text(encoding="utf-8"))
    document["bootstrap"]["seed"] = 7
    study = tmp_path / "study.yaml"
    study.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    summary = replace(planted_tree, study=study).analyse()

    outcomes = {
        arm: arm_outcomes(load_arm(planted_tree.arms_root / arm, STUDY_CELLS, TOKENS))
        for arm in (A, B)
    }
    h2 = hypotheses(summary)["H2"]

    def drawn_with(seed: int) -> Any:
        return contrast(
            cluster_bootstrap_weights(TOKENS, FAMILY_OF, LOG_OF, resamples=RESAMPLES, seed=seed),
            outcomes,
            ArmCell(B, FULL),
            ArmCell(A, FULL),
            "braking_share",
            "bootstrap",
            LOG_OF,
            5,
            h2.holm_step,
        )

    expected = drawn_with(7)
    assert (
        h2.contrast.interval,
        h2.contrast.simultaneous_interval,
        h2.contrast.holm_step_interval,
        h2.contrast.p_value,
    ) == (
        expected.interval,
        expected.simultaneous_interval,
        expected.holm_step_interval,
        expected.p_value,
    )
    assert expected.interval != drawn_with(DEFAULT_SEED).interval
