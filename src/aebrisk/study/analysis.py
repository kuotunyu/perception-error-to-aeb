"""The analysis of the policy v2 study, as its pre-registered plan fixes it.

`docs/studies/aeb-policy-v2/analysis-plan.md` fixed this analysis before any
arm ran. It runs only after gates G1-G3 and G5 have passed, and after G4 has
reproduced the released numbers:

- OUTCOMES PER TOKEN (section 5). `token_outcome` reads one token's three
  records in one cell, beside the same arm's `no_aeb` records, into the values
  every statistic uses. The records carry no command trace, so brake
  activations are counted from the matched delays and the false interventions:
  matching against the arm's own oracle is one to one, so together they count
  every episode of braking.
- CONTRASTS (section 6). One draw of (family, log) clusters, resampled within
  each family, is shared by every arm and cell; that sharing is the pairing. A
  contrast's value in a draw is the sum of its signed terms, and each term is a
  mean of per-token values, a ratio of sums or a sum over the drawn tokens. A
  collision-indicator contrast is tested by flipping whole logs, and every other
  contrast by the cluster-bootstrap p.
- THE ANALYSIS (section 7). `analyse_study` decides the primary, Q2 and Q3-check
  families by Holm, words the verdict by the plan's fixed rules, and reports the
  sensitivity, secondary and descriptive analyses beside them.
- THE REPRODUCTION GATE (section 8). `reproduce_study_cells` runs the released
  loader and the released interval code on the released records of the eight
  cells, and requires the released evidence back with float equality.

Missed and false interventions and matched delays are measured against each
arm's own oracle, so they are compared only between arms of one policy.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Optional, Union

import numpy as np

from aebrisk.analysis.aggregate import (
    FormalResults,
    common_cohort,
    configuration_intervals,
    configuration_rows,
    load_formal_results,
    validate_formal_set,
)
from aebrisk.artifacts.documents import INTERVAL_METRICS, AEBEvaluationV1, AEBIntervalsV1
from aebrisk.artifacts.results import AEBScenarioResult, AEBScenarioResultV2, ScenarioFamily
from aebrisk.artifacts.study_documents import (
    POLICY_V2_SUMMARY_SCHEMA_VERSION,
    PolicyV2SummaryV1,
    Reference,
    StudyClassification,
    StudyCluster,
    StudyContrastV1,
    StudyDistributionV1,
    StudyGatesV1,
    StudyGateV1,
    StudyHypothesisResultV1,
    StudyIntervalV1,
    StudyKeyingRatioV1,
    StudyLevelV1,
    StudyOutcome,
    StudyProportionV1,
    StudyReportedAnalysis,
    StudyReportedContrastV1,
    StudySensitivity,
    StudySensitivityV1,
    StudyStatementsV1,
    StudyTermV1,
    StudyTest,
)
from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import load_manifest, membership_sha256
from aebrisk.metrics.bootstrap import (
    DEFAULT_CONFIDENCE,
    DEFAULT_SEED,
    BootstrapWeights,
    bootstrap_p_value,
    cluster_bootstrap_weights,
    percentile_interval,
    weighted_mean,
    weighted_ratio,
)
from aebrisk.metrics.inference import (
    classify,
    clopper_pearson,
    h5_qualifier,
    holm,
    holm_step_confidence,
    q1_label,
    sign_flip_p_value,
    simultaneous_confidence,
)
from aebrisk.metrics.safety import measured_simulated_seconds
from aebrisk.simulation.orchestrate import FAMILIES, TokenResultsV1
from aebrisk.simulation.step_loop import COLLIDING_MASS_KG
from aebrisk.study.definition import (
    ORACLE_CELL,
    STUDY_REPLICATES,
    StudyDefinitionV1,
    StudyHypothesisFamilyV1,
    StudyHypothesisV1,
    drive_of,
    load_study,
    study_sha256,
    token_log_map,
)
from aebrisk.study.gates import (
    ARM_A,
    NO_AEB_CELL,
    REFERENCES,
    REPLICATION_ARM,
    GateResult,
    gates_document,
    write_gates,
)

#: The arms by their roles in section 4.5 of the analysis plan. Arm A runs the
#: released controller and is the v1 arm of every contrast.
GATED_ARM = "B-v2-gated"
V1_KALMAN_ARM = "C-v1-kalman"
V2_KALMAN_ARM = "D-v2-kalman"
KEYING_ARM = "E-v2-channel-rng"

#: The eight cells of section 4.1, in the order of the formal matrix.
DROPOUT_CELL = "dropout-medium"
LOCALIZATION_CELL = "localization_shape-medium"
LATENCY_CELL = "latency-medium"
TRACK_INSTABILITY_CELL = "track_instability-medium"
COALITION_NONE_CELL = "coalition-none"
FULL_COALITION_CELL = "coalition-dropout+localization_shape+latency+track_instability"
STUDY_CELLS: tuple[str, ...] = (
    NO_AEB_CELL,
    ORACLE_CELL,
    DROPOUT_CELL,
    LOCALIZATION_CELL,
    LATENCY_CELL,
    TRACK_INSTABILITY_CELL,
    COALITION_NONE_CELL,
    FULL_COALITION_CELL,
)
#: The cells with corrupted observation, and the four single-channel cells.
CORRUPTED_CELLS: tuple[str, ...] = STUDY_CELLS[2:]
SINGLE_CHANNEL_CELLS: tuple[str, ...] = STUDY_CELLS[2:6]

#: A run that is not cut short lasts the protocol's 15.0 s; one that ends
#: earlier without a counted collision ran out of logged route.
SCENARIO_DURATION_S = 15.0

#: The hypothesis families of the study file.
PRIMARY_FAMILY = "primary"
Q2_FAMILY = "Q2"
Q3_CHECK_FAMILY = "Q3-check"

#: The gates a formal gate file must hold before any contrast is computed.
REQUIRED_GATES: tuple[str, ...] = ("G1", "G2", "G3", "G5")

#: The fixed sentences of section 7.2 of the analysis plan. None of them says
#: that policy v2 is safer, or that it fixes anything.
H3_SENTENCE = (
    "Excluded contacts and counted collisions are coupled by the stopped-ego rule, so less "
    "braking can move contacts from the excluded class into counted collisions."
)
H4_SUPPORTED_SENTENCE = (
    "Under collision-course gating, the all-channel configuration no longer has zero counted "
    "collisions."
)
H4_OTHER_SENTENCE = (
    "This study does not show that the zero depends on the released target selection."
)
H5_INCREASE_SENTENCE = "Under oracle perception, gating increased counted collisions."
H5_DECREASE_SENTENCE = "Under oracle perception, gating reduced counted collisions."
H5_NO_CHANGE_SENTENCE = (
    "This study does not detect a change in counted collisions under oracle perception."
)
FIXED_SENTENCES: tuple[str, ...] = (
    H3_SENTENCE,
    H4_SUPPORTED_SENTENCE,
    H4_OTHER_SENTENCE,
    H5_INCREASE_SENTENCE,
    H5_DECREASE_SENTENCE,
    H5_NO_CHANGE_SENTENCE,
)

_REPLICATES = len(STUDY_REPLICATES)
_TESTS: tuple[StudyTest, ...] = ("bootstrap", "sign_flip")
_EVALUATION_FIELDS = (
    "collisions",
    "contacts_not_at_fault",
    "simulated_seconds",
    "mean_intervention_duration_s",
)
_INTERVAL_FIELDS = ("estimate", "low", "high", "confidence", "resamples", "seed")


# --------------------------------------------------------------------------
# Outcomes per token
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenOutcome:
    """One token's outcomes in one cell of one arm, over its three replicates.

    Each field is section 5 of the analysis plan. The collision indicator, any
    contact, avoided and induced collisions, and missed and false interventions
    are means over the replicates; the others are sums over them, so that a rate
    over tokens is a ratio of sums.
    """

    collision_indicator: float
    any_collision: bool
    braking_s: float
    exposure_s: float
    not_at_fault: int
    any_contact: float
    avoided: float
    induced: float
    brake_activations: int
    early_ends: int
    missed_interventions: float
    false_interventions: float


def _counted(record: AEBScenarioResult) -> bool:
    return record.collision_vru + record.collision_vehicle + record.collision_object > 0


def _replicates(document: TokenResultsV1) -> tuple[tuple[AEBScenarioResultV2, float], ...]:
    """A valid document's records in replicate order, each with its simulated duration."""

    name = f"{document.configuration_id}/{document.scenario_token}"
    if not document.valid:
        raise ValueError(f"{name} is invalid; the analysis reads only valid documents")
    records = sorted(document.results, key=lambda record: record.replicate)
    if tuple(record.replicate for record in records) != STUDY_REPLICATES:
        raise ValueError(f"{name} must hold exactly the replicates {STUDY_REPLICATES}")
    runs = []
    for record in records:
        if not isinstance(record, AEBScenarioResultV2) or record.simulated_duration_s is None:
            raise ValueError(f"{name} holds a record without its simulated duration")
        runs.append((record, record.simulated_duration_s))
    return tuple(runs)


def token_outcome(document: TokenResultsV1, no_aeb_document: TokenResultsV1) -> TokenOutcome:
    """One token's outcomes in one cell, against the same arm's `no_aeb` run of the token.

    A replicate is paired with the `no_aeb` replicate of the same number for the
    avoided and induced collisions. Brake activations are the matched delays
    plus the false interventions of each replicate. A run ends early when it is
    shorter than 15.0 s and has no counted collision.
    """

    if no_aeb_document.configuration_id != NO_AEB_CELL:
        raise ValueError(
            f"avoided and induced collisions are defined against {NO_AEB_CELL!r}, not "
            f"{no_aeb_document.configuration_id!r}"
        )
    if no_aeb_document.scenario_token != document.scenario_token:
        raise ValueError(
            f"{document.scenario_token!r} cannot be paired with the {NO_AEB_CELL} run of "
            f"{no_aeb_document.scenario_token!r}"
        )
    runs = _replicates(document)
    baseline = _replicates(no_aeb_document)
    counted = [_counted(record) for record, _ in runs]
    counted_without_aeb = [_counted(record) for record, _ in baseline]
    return TokenOutcome(
        collision_indicator=sum(counted) / _REPLICATES,
        any_collision=any(counted),
        braking_s=sum(record.intervention_duration_s for record, _ in runs),
        exposure_s=sum(duration for _, duration in runs),
        not_at_fault=sum(record.contacts_not_at_fault for record, _ in runs),
        any_contact=sum(
            collided or record.contacts_not_at_fault > 0
            for collided, (record, _) in zip(counted, runs)
        )
        / _REPLICATES,
        avoided=sum(before and not after for before, after in zip(counted_without_aeb, counted))
        / _REPLICATES,
        induced=sum(after and not before for before, after in zip(counted_without_aeb, counted))
        / _REPLICATES,
        brake_activations=sum(
            len(record.matched_delay_s) + record.false_interventions for record, _ in runs
        ),
        early_ends=sum(
            duration < SCENARIO_DURATION_S and not collided
            for collided, (_, duration) in zip(counted, runs)
        ),
        missed_interventions=sum(record.missed_interventions for record, _ in runs) / _REPLICATES,
        false_interventions=sum(record.false_interventions for record, _ in runs) / _REPLICATES,
    )


def load_arm(
    arm_root: Path, cells: Sequence[str], tokens: Sequence[str]
) -> Mapping[str, Mapping[str, TokenResultsV1]]:
    """Every document of a completed arm, by cell and then by token.

    The arm must have written `run_complete.json` and hold exactly the cell
    directories of `cells`. Each document must be the one its path names.
    """

    if not (arm_root / "run_complete.json").is_file():
        raise ValueError(f"{arm_root} has no run_complete.json; only a completed arm is analysed")
    directories = sorted(path.name for path in arm_root.iterdir() if path.is_dir())
    if directories != sorted(cells):
        raise ValueError(
            f"{arm_root} holds the cell directories {directories}, not exactly {sorted(cells)}"
        )
    loaded: dict[str, Mapping[str, TokenResultsV1]] = {}
    for cell in cells:
        documents: dict[str, TokenResultsV1] = {}
        for token in sorted(tokens):
            path = arm_root / cell / f"{token}.json"
            if not path.is_file():
                raise ValueError(f"{cell}/{token}.json is missing from {arm_root}")
            document = TokenResultsV1.model_validate_json(path.read_bytes())
            if (document.configuration_id, document.scenario_token) != (cell, token):
                raise ValueError(
                    f"{cell}/{token}.json in {arm_root} holds the document of "
                    f"{document.configuration_id}/{document.scenario_token}"
                )
            documents[token] = document
        loaded[cell] = MappingProxyType(documents)
    return MappingProxyType(loaded)


def arm_outcomes(
    documents: Mapping[str, Mapping[str, TokenResultsV1]],
) -> Mapping[str, Mapping[str, TokenOutcome]]:
    """Each token's outcomes in each of an arm's cells, against the arm's own `no_aeb`."""

    if NO_AEB_CELL not in documents:
        raise ValueError(
            f"the arm has no {NO_AEB_CELL!r} cell, which avoided and induced collisions are "
            "measured against"
        )
    baseline = documents[NO_AEB_CELL]
    return MappingProxyType(
        {
            cell: MappingProxyType(
                {
                    token: token_outcome(document, baseline[token])
                    for token, document in cell_documents.items()
                }
            )
            for cell, cell_documents in documents.items()
        }
    )


# --------------------------------------------------------------------------
# Contrasts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmCell:
    """One level a contrast compares: an arm's outcomes in one cell."""

    arm: str
    cell: str


#: One side of a contrast: a level, or the difference of two levels.
Term = Union[ArmCell, tuple[ArmCell, ArmCell]]

#: Every arm's per-token outcomes, by arm, then cell, then token.
Outcomes = Mapping[str, Mapping[str, Mapping[str, TokenOutcome]]]


@dataclass(frozen=True)
class ContrastResult:
    """A contrast's full-sample value, its intervals and its p.

    `terms` is the contrast as signed levels: the plus side with its sign, then
    the minus side with the opposite one. The simultaneous and Holm-step
    intervals are present only when the contrast's family size and Holm step
    were given.
    """

    terms: tuple[tuple[int, ArmCell], ...]
    metric: StudyOutcome
    test: StudyTest
    estimate: float
    p_value: float
    interval: StudyIntervalV1
    simultaneous_interval: Optional[StudyIntervalV1]
    holm_step_interval: Optional[StudyIntervalV1]


_PER_TOKEN_MEANS: Mapping[str, Callable[[TokenOutcome], float]] = MappingProxyType(
    {
        "collision_indicator": lambda outcome: outcome.collision_indicator,
        "any_contact": lambda outcome: outcome.any_contact,
        "avoided": lambda outcome: outcome.avoided,
        "induced": lambda outcome: outcome.induced,
        "missed_interventions": lambda outcome: outcome.missed_interventions,
        "false_interventions": lambda outcome: outcome.false_interventions,
        "braking_seconds_per_run": lambda outcome: outcome.braking_s / _REPLICATES,
        "not_at_fault_contacts_per_run": lambda outcome: outcome.not_at_fault / _REPLICATES,
    }
)

#: A rate is its numerator summed over the drawn tokens, divided by their summed
#: simulated time, times its scale.
_RATES: Mapping[str, tuple[Callable[[TokenOutcome], float], float]] = MappingProxyType(
    {
        "braking_share": (lambda outcome: outcome.braking_s, 1.0),
        "not_at_fault_contact_rate": (lambda outcome: outcome.not_at_fault, 1000.0),
        "activation_rate": (lambda outcome: outcome.brake_activations, 3600.0),
    }
)

_TOTALS: Mapping[str, Callable[[TokenOutcome], float]] = MappingProxyType(
    {
        "simulated_seconds": lambda outcome: outcome.exposure_s,
        "early_ends": lambda outcome: outcome.early_ends,
    }
)

_METRICS = frozenset((*_PER_TOKEN_MEANS, *_RATES, *_TOTALS))


def _cell_outcomes(outcomes: Outcomes, level: ArmCell) -> Mapping[str, TokenOutcome]:
    try:
        return outcomes[level.arm][level.cell]
    except KeyError:
        raise ValueError(f"there are no outcomes of arm {level.arm!r} in {level.cell!r}") from None


def _values(
    weights: BootstrapWeights,
    outcomes: Outcomes,
    level: ArmCell,
    value: Callable[[TokenOutcome], float],
) -> dict[str, float]:
    """One value per resampled token, in the order of the draws' columns."""

    cell = _cell_outcomes(outcomes, level)
    missing = sorted(set(weights.tokens) - set(cell))
    if missing:
        raise ValueError(f"arm {level.arm!r} has no outcome in {level.cell!r} for tokens {missing}")
    return {token: value(cell[token]) for token in weights.tokens}


def _level_draws(
    weights: BootstrapWeights, outcomes: Outcomes, level: ArmCell, metric: str
) -> np.ndarray:
    """One level's value in each draw: a mean, a ratio of sums, or a sum."""

    if metric in _PER_TOKEN_MEANS:
        return weighted_mean(weights, _values(weights, outcomes, level, _PER_TOKEN_MEANS[metric]))
    if metric in _RATES:
        numerator, scale = _RATES[metric]
        return scale * weighted_ratio(
            weights,
            _values(weights, outcomes, level, numerator),
            _values(weights, outcomes, level, _TOTALS["simulated_seconds"]),
        )
    column = np.array(
        list(_values(weights, outcomes, level, _TOTALS[metric]).values()), dtype=np.float64
    )
    return (weights.weights * column).sum(axis=1)


def _combination(
    weights: BootstrapWeights,
    outcomes: Outcomes,
    terms: Sequence[tuple[int, ArmCell]],
    metric: str,
) -> np.ndarray:
    """The contrast in each draw: the sum of its signed levels."""

    total = np.zeros(weights.weights.shape[0], dtype=np.float64)
    for sign, level in terms:
        total = total + sign * _level_draws(weights, outcomes, level, metric)
    return total


def _full_sample(weights: BootstrapWeights) -> BootstrapWeights:
    """One draw that holds every token once, whose value is the point estimate."""

    return BootstrapWeights(
        tokens=weights.tokens, weights=np.ones((1, len(weights.tokens)), dtype=np.int64)
    )


def _side(term: Term, sign: int) -> tuple[tuple[int, ArmCell], ...]:
    if isinstance(term, ArmCell):
        return ((sign, term),)
    first, second = term
    return ((sign, first), (-sign, second))


def _unit_differences(
    tokens: Sequence[str],
    outcomes: Outcomes,
    terms: Sequence[tuple[int, ArmCell]],
    unit_by_token: Mapping[str, str],
) -> list[int]:
    """Each unit's summed difference in collision indicator, in thirds of a collision."""

    by_unit: dict[str, int] = {}
    for token in tokens:
        thirds = sum(
            sign * round(_REPLICATES * _cell_outcomes(outcomes, level)[token].collision_indicator)
            for sign, level in terms
        )
        unit = unit_by_token[token]
        by_unit[unit] = by_unit.get(unit, 0) + thirds
    return [by_unit[unit] for unit in sorted(by_unit)]


def _interval(draws: np.ndarray, confidence: float) -> StudyIntervalV1:
    low, high = percentile_interval(draws, confidence)
    return StudyIntervalV1(confidence=confidence, low=low, high=high)


def contrast(
    weights: BootstrapWeights,
    outcomes: Outcomes,
    plus: Term,
    minus: Term,
    metric: StudyOutcome,
    test: StudyTest,
    log_by_token: Mapping[str, str],
    k: Optional[int],
    holm_step: Optional[int],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    enumerate_up_to: int = 20,
    random_flips: int = 100_000,
    flip_seed: int = DEFAULT_SEED,
) -> ContrastResult:
    """The contrast `plus` minus `minus` on `metric`, over the tokens of `weights`.

    Each side is a level, or the difference of two levels, so a difference in
    differences is one contrast. Both sides are computed from the same draw. The
    estimate is the full-sample value, and the interval is at `confidence`. With
    `k`, the simultaneous 1 - 0.05/k interval is added, and with `holm_step`
    too, the interval at that step's Holm level. `p_value` is the
    cluster-bootstrap p, or for `sign_flip` the exact paired sign flip over the
    units `log_by_token` names, which only the collision indicator can take.
    """

    if metric not in _METRICS:
        raise ValueError(f"unknown outcome {metric!r}; expected one of {sorted(_METRICS)}")
    if test not in _TESTS:
        raise ValueError(f"unknown test {test!r}; expected one of {list(_TESTS)}")
    if test == "sign_flip" and metric != "collision_indicator":
        raise ValueError(
            f"a sign flip tests only the collision indicator, which is counted in whole "
            f"collisions; {metric!r} takes the cluster-bootstrap p"
        )
    if holm_step is not None and k is None:
        raise ValueError("a Holm step is a step of a family, so its size k is needed too")

    terms = (*_side(plus, 1), *_side(minus, -1))
    draws = _combination(weights, outcomes, terms, metric)
    estimate = float(_combination(_full_sample(weights), outcomes, terms, metric)[0])
    if test == "bootstrap":
        p_value = bootstrap_p_value(draws)
    else:
        p_value = sign_flip_p_value(
            _unit_differences(weights.tokens, outcomes, terms, log_by_token),
            enumerate_up_to=enumerate_up_to,
            random_flips=random_flips,
            seed=flip_seed,
        )
    return ContrastResult(
        terms=terms,
        metric=metric,
        test=test,
        estimate=estimate,
        p_value=p_value,
        interval=_interval(draws, confidence),
        simultaneous_interval=None if k is None else _interval(draws, simultaneous_confidence(k)),
        holm_step_interval=(
            None
            if holm_step is None or k is None
            else _interval(draws, holm_step_confidence(k, holm_step))
        ),
    )


def _term_documents(terms: Sequence[tuple[int, ArmCell]]) -> tuple[StudyTermV1, ...]:
    return tuple(
        StudyTermV1(sign=1 if sign > 0 else -1, arm=level.arm, cell=level.cell)
        for sign, level in terms
    )


def keying_ratio(weights: BootstrapWeights, outcomes: Outcomes) -> StudyKeyingRatioV1:
    """The answer to Q3: SD_B / SD_E of the braking-share contrast full - localization.

    SD_B is taken under B's keying, and SD_E under E's, where the two cells
    share their localization draws. E runs `localization_shape-medium` only if
    its arm has the cell; otherwise E's is B's, which its keying makes the same
    (section 7.5 of the analysis plan). The standard deviations are over the
    draws, with one degree of freedom removed. The ratio is absent when E's
    contrast does not vary.
    """

    localization_arm = KEYING_ARM if LOCALIZATION_CELL in outcomes[KEYING_ARM] else GATED_ARM
    b_terms = (
        (1, ArmCell(GATED_ARM, FULL_COALITION_CELL)),
        (-1, ArmCell(GATED_ARM, LOCALIZATION_CELL)),
    )
    e_terms = (
        (1, ArmCell(KEYING_ARM, FULL_COALITION_CELL)),
        (-1, ArmCell(localization_arm, LOCALIZATION_CELL)),
    )
    sd_b = float(np.std(_combination(weights, outcomes, b_terms, "braking_share"), ddof=1))
    sd_e = float(np.std(_combination(weights, outcomes, e_terms, "braking_share"), ddof=1))
    return StudyKeyingRatioV1(
        outcome="braking_share",
        b_terms=_term_documents(b_terms),
        e_terms=_term_documents(e_terms),
        sd_b=sd_b,
        sd_e=sd_e,
        ratio=sd_b / sd_e if sd_e > 0.0 else None,
    )


# --------------------------------------------------------------------------
# G4: the analysis reproduces the released numbers
# --------------------------------------------------------------------------


def reproduce_study_cells(
    released_root: Path,
    evidence_dir: Path,
    manifest: Path,
    cells: Sequence[str] = STUDY_CELLS,
) -> GateResult:
    """G4: the released loader and interval code reproduce the released evidence.

    The released records of `cells` are loaded and validated as the released
    `evaluate` command does, and their intervals are computed by the released
    `configuration_intervals`, which calls the released
    `paired_scenario_bootstrap`. Every estimate, bound, confidence, resample
    count and seed in `intervals.json`, and `collisions`,
    `contacts_not_at_fault`, `simulated_seconds` and
    `mean_intervention_duration_s` in `evaluation.json`, must come back with
    float equality. A value the evidence lacks is a mismatch.
    """

    cohort = load_manifest(manifest)
    tokens = tuple(token for family_tokens in cohort.families.values() for token in family_tokens)
    released = load_formal_results(released_root)
    loaded = FormalResults(
        marker_tokens=released.marker_tokens,
        cohort_manifest_sha256=released.cohort_manifest_sha256,
    )
    loaded.update({cell: released[cell] for cell in cells if cell in released})
    configurations = [
        configuration
        for configuration in formal_configurations()
        if configuration.configuration_id in cells
    ]
    validate_formal_set(loaded, configurations, tokens, manifest=cohort)
    common = common_cohort(loaded)
    rows = {str(row["configuration_id"]): row for row in configuration_rows(loaded, common)}
    families = {
        token: family
        for family, family_tokens in cohort.families.items()
        for token in family_tokens
    }
    recomputed = configuration_intervals(loaded, common, families)

    evaluation = AEBEvaluationV1.model_validate_json(
        (evidence_dir / "evaluation.json").read_text(encoding="utf-8")
    )
    intervals = AEBIntervalsV1.model_validate_json(
        (evidence_dir / "intervals.json").read_text(encoding="utf-8")
    )
    evaluation_rows = {row.configuration_id: row for row in evaluation.configurations}
    detail: list[str] = []
    interval_mismatches = evaluation_mismatches = 0
    for cell in cells:
        for metric in INTERVAL_METRICS:
            published = intervals.intervals.get(cell, {}).get(metric)
            for field in _INTERVAL_FIELDS:
                ours = getattr(recomputed[cell][metric], field)
                theirs = None if published is None else getattr(published, field)
                if theirs != ours:
                    interval_mismatches += 1
                    detail.append(
                        f"intervals.json {cell}/{metric}/{field}: released {theirs!r}, "
                        f"recomputed {ours!r}"
                    )
        row = evaluation_rows.get(cell)
        for field in _EVALUATION_FIELDS:
            ours = rows[cell][field]
            theirs = None if row is None else getattr(row, field)
            if theirs != ours:
                evaluation_mismatches += 1
                detail.append(
                    f"evaluation.json {cell}/{field}: released {theirs!r}, recomputed {ours!r}"
                )
    counts = {
        "cells": len(cells),
        "fields_compared": len(cells)
        * (len(INTERVAL_METRICS) * len(_INTERVAL_FIELDS) + len(_EVALUATION_FIELDS)),
        "interval_mismatches": interval_mismatches,
        "evaluation_mismatches": evaluation_mismatches,
    }
    passed = interval_mismatches == 0 and evaluation_mismatches == 0
    return GateResult("G4", passed, counts, tuple(detail))


# --------------------------------------------------------------------------
# The analysis
# --------------------------------------------------------------------------


def _refuse_unless_gates_passed(
    definition: StudyDefinitionV1, study_hash: str, gates: StudyGatesV1, reference: Reference
) -> None:
    """Refuse a gate file that does not show G1, G2, G3 and G5 passed over every arm.

    In arm-A reference mode the gate file must have been written in that mode,
    and G2 may have failed.
    """

    if gates.study_sha256 != study_hash:
        raise ValueError(
            f"the gate file checked the study file {gates.study_sha256}, not this one, {study_hash}"
        )
    arms = [arm.id for arm in definition.arms]
    if sorted(gates.arms_checked) != sorted(arms):
        raise ValueError(
            f"the gate file checks the arms {list(gates.arms_checked)}; the analysis needs every "
            f"arm of the study checked together, {arms}"
        )
    names = {gate.gate for gate in gates.gates}
    missing = [name for name in REQUIRED_GATES if name not in names]
    if missing:
        raise ValueError(f"the gate file does not hold {missing}; it is not a check of the arms")
    if gates.reference != reference:
        raise ValueError(
            f"the gate file was written with the reference {gates.reference!r}, and the "
            f"analysis runs with the reference {reference!r}"
        )
    failed = [
        gate.gate
        for gate in gates.gates
        if not gate.passed and not (reference == ARM_A and gate.gate == "G2")
    ]
    if failed:
        raise ValueError(f"the gates {failed} failed; no contrast is computed after a failure")


def _distribution(values: Sequence[float]) -> StudyDistributionV1:
    if not values:
        return StudyDistributionV1(count=0)
    array = np.asarray(values, dtype=np.float64)
    lower, median, upper = (float(value) for value in np.percentile(array, (25.0, 50.0, 75.0)))
    return StudyDistributionV1(
        count=len(values),
        median=median,
        lower_quartile=lower,
        upper_quartile=upper,
        maximum=float(array.max()),
    )


#: Which event of a token a Clopper-Pearson interval counts, by the outcome it goes with.
_EVENTS: Mapping[str, Literal["collision", "avoided", "induced"]] = MappingProxyType(
    {"collision_indicator": "collision", "avoided": "avoided", "induced": "induced"}
)
_HAPPENED: Mapping[str, Callable[[TokenOutcome], bool]] = MappingProxyType(
    {
        "collision": lambda outcome: outcome.any_collision,
        "avoided": lambda outcome: outcome.avoided > 0,
        "induced": lambda outcome: outcome.induced > 0,
    }
)

#: The sensitivity outcome that section 7.1 reports beside a primary outcome.
#: Each is both an outcome a contrast takes and the name of its analysis.
_SensitivityOutcome = Literal["braking_seconds_per_run", "not_at_fault_contacts_per_run"]
_SENSITIVITY_OUTCOMES: Mapping[str, _SensitivityOutcome] = MappingProxyType(
    {
        "braking_share": "braking_seconds_per_run",
        "not_at_fault_contact_rate": "not_at_fault_contacts_per_run",
    }
)

#: The outcomes sections 7.3 and 7.4 compare cell by cell.
_CELL_OUTCOMES: tuple[StudyOutcome, ...] = (
    "collision_indicator",
    "braking_share",
    "not_at_fault_contact_rate",
)

#: The exposure outcomes of section 7.3.7.
_EXPOSURE_OUTCOMES: tuple[StudyOutcome, ...] = ("simulated_seconds", "early_ends")


def _test_of(metric: StudyOutcome) -> StudyTest:
    return "sign_flip" if metric == "collision_indicator" else "bootstrap"


@dataclass(frozen=True)
class _Decided:
    hypothesis: StudyHypothesisV1
    family: str
    result: ContrastResult
    step: int
    rejected: bool
    classification: StudyClassification


@dataclass(frozen=True)
class _Study:
    """The study's outcomes, resampling and settings, shared by every contrast."""

    definition: StudyDefinitionV1
    outcomes: Outcomes
    documents: Mapping[str, Mapping[str, Mapping[str, TokenResultsV1]]]
    tokens: tuple[str, ...]
    families: tuple[ScenarioFamily, ...]
    family_by_token: Mapping[str, str]
    log_by_token: Mapping[str, str]
    drive_by_token: Mapping[str, str]
    weights: Mapping[str, BootstrapWeights]
    family_weights: Mapping[str, BootstrapWeights]

    @property
    def confidence(self) -> float:
        return self.definition.bootstrap.secondary_confidence

    def tokens_of(self, family: Optional[str]) -> tuple[str, ...]:
        if family is None:
            return self.tokens
        return tuple(token for token in self.tokens if self.family_by_token[token] == family)

    def contrast(
        self,
        plus: Term,
        minus: Term,
        metric: StudyOutcome,
        test: StudyTest,
        *,
        cluster: StudyCluster = "family-log",
        unit: Literal["log", "drive"] = "log",
        family: Optional[str] = None,
        k: Optional[int] = None,
        holm_step: Optional[int] = None,
    ) -> ContrastResult:
        flips = self.definition.sign_flip
        return contrast(
            self.weights[cluster] if family is None else self.family_weights[family],
            self.outcomes,
            plus,
            minus,
            metric,
            test,
            self.drive_by_token if unit == "drive" else self.log_by_token,
            k,
            holm_step,
            confidence=self.confidence,
            enumerate_up_to=flips.enumerate_up_to,
            random_flips=flips.random_flips,
            flip_seed=flips.seed,
        )

    def proportion(
        self,
        event: Literal["collision", "avoided", "induced"],
        level: ArmCell,
        family: Optional[ScenarioFamily],
    ) -> StudyProportionV1:
        tokens = self.tokens_of(family)
        cell = self.outcomes[level.arm][level.cell]
        successes = sum(_HAPPENED[event](cell[token]) for token in tokens)
        low, high = clopper_pearson(successes, len(tokens), self.confidence)
        return StudyProportionV1(
            event=event,
            arm=level.arm,
            cell=level.cell,
            family=family,
            successes=successes,
            trials=len(tokens),
            confidence=self.confidence,
            low=low,
            high=high,
        )

    def document(
        self,
        result: ContrastResult,
        *,
        cluster: Optional[StudyCluster] = "family-log",
        unit: Literal["log", "drive"] = "log",
        family: Optional[ScenarioFamily] = None,
        tested: bool = True,
        clopper: bool = False,
    ) -> StudyContrastV1:
        """A contrast as the summary reports it: intervals only with a cluster, p only if tested."""

        event = _EVENTS.get(result.metric)
        return StudyContrastV1(
            outcome=result.metric,
            terms=_term_documents(result.terms),
            family=family,
            estimate=result.estimate,
            cluster=cluster,
            interval=None if cluster is None else result.interval,
            simultaneous_interval=None if cluster is None else result.simultaneous_interval,
            holm_step_interval=None if cluster is None else result.holm_step_interval,
            test=result.test if tested else None,
            test_unit=unit if tested and result.test == "sign_flip" else None,
            p_value=result.p_value if tested else None,
            clopper_pearson=(
                tuple(self.proportion(event, level, family) for _, level in result.terms)
                if clopper and event is not None
                else ()
            ),
        )

    # ----------------------------------------------------------------------
    # The hypothesis families (sections 7.1, 7.4 and 7.5)
    # ----------------------------------------------------------------------

    def decide(self, family: StudyHypothesisFamilyV1, primary: bool) -> list[_Decided]:
        """A family's contrasts, Holm's decision on them, and each one's classification.

        A primary hypothesis is computed again at its Holm step, for the
        simultaneous and Holm-step intervals.
        """

        first = {
            hypothesis.id: self.contrast(
                ArmCell(hypothesis.plus, hypothesis.cell),
                ArmCell(hypothesis.minus, hypothesis.cell),
                hypothesis.outcome,
                hypothesis.test,
            )
            for hypothesis in family.hypotheses
        }
        decisions = holm({name: result.p_value for name, result in first.items()})
        k = len(family.hypotheses)
        decided = []
        for hypothesis in family.hypotheses:
            decision = decisions[hypothesis.id]
            result = (
                self.contrast(
                    ArmCell(hypothesis.plus, hypothesis.cell),
                    ArmCell(hypothesis.minus, hypothesis.cell),
                    hypothesis.outcome,
                    hypothesis.test,
                    k=k,
                    holm_step=decision.step,
                )
                if primary
                else first[hypothesis.id]
            )
            decided.append(
                _Decided(
                    hypothesis=hypothesis,
                    family=family.id,
                    result=result,
                    step=decision.step,
                    rejected=decision.rejected,
                    classification=classify(
                        result.estimate, decision.rejected, hypothesis.predicted_sign
                    ),
                )
            )
        return decided

    def hypothesis_document(self, decided: _Decided) -> StudyHypothesisResultV1:
        return StudyHypothesisResultV1(
            id=decided.hypothesis.id,
            family=decided.family,
            predicted_sign=decided.hypothesis.predicted_sign,
            contrast=self.document(decided.result, clopper=True),
            holm_step=decided.step,
            rejected=decided.rejected,
            classification=decided.classification,
        )

    def sensitivity(self, primary: Sequence[_Decided]) -> list[StudySensitivityV1]:
        """Section 7.1's sensitivity analyses. None of them enters a decision.

        Braking seconds per scenario-replicate for a braking-share hypothesis,
        and the per-token mean of not-at-fault contacts for the contact-rate
        one; every interval again with (family, drive) clusters; the sign-flip p
        again over drives; and every bootstrap hypothesis again with an
        unstratified bootstrap over whole logs.
        """

        k = len(primary)
        rows: list[StudySensitivityV1] = []
        for decided in primary:
            hypothesis = decided.hypothesis
            plus = ArmCell(hypothesis.plus, hypothesis.cell)
            minus = ArmCell(hypothesis.minus, hypothesis.cell)
            step = decided.step
            analyses: list[tuple[StudySensitivity, StudyContrastV1]] = []
            alternative = _SENSITIVITY_OUTCOMES.get(hypothesis.outcome)
            if alternative is not None:
                analyses.append(
                    (
                        alternative,
                        self.document(
                            self.contrast(
                                plus, minus, alternative, "bootstrap", k=k, holm_step=step
                            )
                        ),
                    )
                )
            analyses.append(
                (
                    "family_drive_clusters",
                    self.document(
                        self.contrast(
                            plus,
                            minus,
                            hypothesis.outcome,
                            hypothesis.test,
                            cluster="family-drive",
                            k=k,
                            holm_step=step,
                        ),
                        cluster="family-drive",
                        tested=False,
                    ),
                )
            )
            if hypothesis.test == "sign_flip":
                analyses.append(
                    (
                        "drive_sign_flip",
                        self.document(
                            self.contrast(
                                plus, minus, hypothesis.outcome, "sign_flip", unit="drive"
                            ),
                            cluster=None,
                            unit="drive",
                        ),
                    )
                )
            else:
                analyses.append(
                    (
                        "whole_log_bootstrap",
                        self.document(
                            self.contrast(
                                plus,
                                minus,
                                hypothesis.outcome,
                                "bootstrap",
                                cluster="log",
                                k=k,
                                holm_step=step,
                            ),
                            cluster="log",
                        ),
                    )
                )
            rows.extend(
                StudySensitivityV1(hypothesis=hypothesis.id, analysis=analysis, contrast=reported)
                for analysis, reported in analyses
            )
        return rows

    # ----------------------------------------------------------------------
    # Secondary and descriptive contrasts (sections 7.3, 7.4 and 7.6)
    # ----------------------------------------------------------------------

    def reported(
        self,
        analysis: StudyReportedAnalysis,
        plus: Term,
        minus: Term,
        metric: StudyOutcome,
        *,
        tested: bool,
        family: Optional[ScenarioFamily] = None,
        hypothesis: Optional[str] = None,
    ) -> StudyReportedContrastV1:
        result = self.contrast(
            plus, minus, metric, _test_of(metric) if tested else "bootstrap", family=family
        )
        return StudyReportedContrastV1(
            analysis=analysis,
            hypothesis=hypothesis,
            contrast=self.document(result, family=family, tested=tested, clopper=tested),
        )

    def secondary(self) -> list[StudyReportedContrastV1]:
        """Section 7.3, at 95% with p, and not a family of tests."""

        a, b = REPLICATION_ARM, GATED_ARM
        rows = [
            self.reported("other_cells", ArmCell(b, cell), ArmCell(a, cell), metric, tested=True)
            for cell in (COALITION_NONE_CELL, *SINGLE_CHANNEL_CELLS)
            for metric in _CELL_OUTCOMES
        ]
        rows.extend(
            self.reported(
                "difference_in_differences",
                (ArmCell(b, cell), ArmCell(b, COALITION_NONE_CELL)),
                (ArmCell(a, cell), ArmCell(a, COALITION_NONE_CELL)),
                metric,
                tested=True,
            )
            for cell in (FULL_COALITION_CELL, LOCALIZATION_CELL)
            for metric in _CELL_OUTCOMES[:2]
        )
        rows.extend(
            self.reported(
                "combined_remedy",
                ArmCell(V2_KALMAN_ARM, cell),
                ArmCell(a, cell),
                metric,
                tested=True,
            )
            for cell in (LOCALIZATION_CELL, FULL_COALITION_CELL)
            for metric in _CELL_OUTCOMES
        )
        both = (ORACLE_CELL, FULL_COALITION_CELL)
        outcome_rows: tuple[tuple[StudyReportedAnalysis, tuple[StudyOutcome, ...]], ...] = (
            ("benefit_and_harm", ("induced", "avoided")),
            ("any_contact", ("any_contact",)),
        )
        for analysis, metrics in outcome_rows:
            rows.extend(
                self.reported(analysis, ArmCell(b, cell), ArmCell(a, cell), metric, tested=True)
                for cell in both
                for metric in metrics
            )
        rows.extend(
            self.reported(
                "activation_rate",
                ArmCell(plus, cell),
                ArmCell(minus, cell),
                "activation_rate",
                tested=True,
            )
            for cell in both
            for plus, minus in ((b, a), (V1_KALMAN_ARM, a), (V2_KALMAN_ARM, b))
        )
        rows.extend(
            self.reported("exposure", ArmCell(b, cell), ArmCell(a, cell), metric, tested=True)
            for cell in both
            for metric in _EXPOSURE_OUTCOMES
        )
        return rows

    def descriptive_contrasts(self, primary: Sequence[_Decided]) -> list[StudyReportedContrastV1]:
        """Section 7.4's descriptive contrasts, and the primary contrasts per family (7.6.5).

        C - A and D - B share an oracle, and the interaction (D - B) - (C - A)
        is taken in every corrupted cell. All are at 95%, without a test.
        """

        a, b, c, d = REPLICATION_ARM, GATED_ARM, V1_KALMAN_ARM, V2_KALMAN_ARM
        rows = [
            self.reported(
                "velocity_by_cell", ArmCell(plus, cell), ArmCell(minus, cell), metric, tested=False
            )
            for cell in CORRUPTED_CELLS
            for metric in _CELL_OUTCOMES
            for plus, minus in ((c, a), (d, b))
        ]
        rows.extend(
            self.reported(
                "velocity_interaction",
                (ArmCell(d, cell), ArmCell(b, cell)),
                (ArmCell(c, cell), ArmCell(a, cell)),
                metric,
                tested=False,
            )
            for cell in CORRUPTED_CELLS
            for metric in _CELL_OUTCOMES
        )
        rows.extend(
            self.reported(
                "primary_by_family",
                ArmCell(decided.hypothesis.plus, decided.hypothesis.cell),
                ArmCell(decided.hypothesis.minus, decided.hypothesis.cell),
                decided.hypothesis.outcome,
                tested=False,
                family=family,
                hypothesis=decided.hypothesis.id,
            )
            for family in self.families
            for decided in primary
        )
        return rows

    # ----------------------------------------------------------------------
    # Descriptive levels (section 7.6)
    # ----------------------------------------------------------------------

    def level(self, arm: str, cell: str, family: Optional[ScenarioFamily]) -> StudyLevelV1:
        """One arm's cell over the cohort or one family.

        Simulated and braking seconds are summed record by record, in the order
        the released aggregation sums them, so arm A's totals are the released
        ones to the last digit.
        """

        tokens = self.tokens_of(family)
        outcomes = [self.outcomes[arm][cell][token] for token in tokens]
        records = tuple(
            record for token in tokens for record in self.documents[arm][cell][token].results
        )
        simulated = measured_simulated_seconds(records, cohort=tokens)
        braking = sum(record.intervention_duration_s for record in records)
        contacts = sum(outcome.not_at_fault for outcome in outcomes)
        activations = sum(outcome.brake_activations for outcome in outcomes)
        return StudyLevelV1(
            arm=arm,
            cell=cell,
            family=family,
            tokens=len(tokens),
            records=len(records),
            collisions=sum(
                record.collision_vru + record.collision_vehicle + record.collision_object
                for record in records
            ),
            collision_tokens=sum(outcome.any_collision for outcome in outcomes),
            collision_indicator=sum(outcome.collision_indicator for outcome in outcomes)
            / len(tokens),
            collision_clopper_pearson=self.proportion("collision", ArmCell(arm, cell), family),
            contacts_not_at_fault=contacts,
            any_contact=sum(outcome.any_contact for outcome in outcomes) / len(tokens),
            simulated_seconds=simulated,
            braking_seconds=braking,
            braking_share=braking / simulated,
            braking_seconds_per_run=braking / len(records),
            not_at_fault_contact_rate=contacts / simulated * 1000.0,
            brake_activations=activations,
            activation_rate=activations / simulated * 3600.0,
            early_ends=sum(outcome.early_ends for outcome in outcomes),
            max_deceleration_mps2=max(record.max_deceleration_mps2 for record in records),
            missed_interventions=sum(record.missed_interventions for record in records),
            false_interventions=sum(record.false_interventions for record in records),
            matched_delays_s=_distribution(
                [delay for record in records for delay in record.matched_delay_s]
            ),
            stop_distance_m=_distribution(
                [record.stop_distance_m for record in records if record.stop_distance_m is not None]
            ),
            runs_never_stopped=sum(record.stop_distance_m is None for record in records),
            collision_speed_mps=_distribution(
                [
                    math.sqrt(2.0 * record.collision_energy / COLLIDING_MASS_KG)
                    for record in records
                    if _counted(record)
                ]
            ),
        )

    def levels(self) -> list[StudyLevelV1]:
        return [
            self.level(arm.id, cell, family)
            for arm in self.definition.arms
            for cell in arm.cells
            for family in (None, *self.families)
        ]


def _statements(by_id: Mapping[str, _Decided]) -> StudyStatementsV1:
    """The H3, H4 and H5 sentences, chosen by the rules of section 7.2."""

    h4, h5 = by_id["H4"], by_id["H5"]
    if h5_qualifier(h5.result.p_value, h5.result.estimate):
        h5_sentence = H5_INCREASE_SENTENCE
    elif h5.classification == "decrease":
        h5_sentence = H5_DECREASE_SENTENCE
    else:
        h5_sentence = H5_NO_CHANGE_SENTENCE
    return StudyStatementsV1(
        h3=H3_SENTENCE,
        h4=H4_SUPPORTED_SENTENCE if h4.classification == "supported" else H4_OTHER_SENTENCE,
        h5=h5_sentence,
    )


def analyse_study(
    study: Path,
    arms_root: Path,
    released_root: Path,
    evidence_dir: Path,
    manifest: Path,
    gates: StudyGatesV1,
    reference: Reference = "released",
    g4_path: Optional[Path] = None,
) -> PolicyV2SummaryV1:
    """The pre-registered analysis of the five arms under `arms_root`.

    Refused before any number is computed: an unknown reference; a gate file of
    another study, of fewer arms, without G1, G2, G3 and G5, of the other
    reference, or with a failed gate (G2 may fail in arm-A reference mode); and
    a manifest of another cohort. Then G4 runs; its result is written to
    `g4_path`, when given, whether it passes or fails, and a failure refuses the
    analysis. Section 7 follows in order: the primary family with its
    sensitivity analyses, Q2, the Q3 ratio and check, the secondary analyses and
    the descriptive set. Each token's log is read from the study file's
    `token_log_source`, relative to the working directory.
    """

    if reference not in REFERENCES:
        raise ValueError(f"unknown reference {reference!r}; expected one of {list(REFERENCES)}")
    definition = load_study(study)
    study_hash = study_sha256(study)
    _refuse_unless_gates_passed(definition, study_hash, gates, reference)
    cohort = load_manifest(manifest)
    if membership_sha256(cohort) != definition.cohort_membership_sha256:
        raise ValueError(
            f"the manifest's membership hash {membership_sha256(cohort)} is not the cohort "
            f"membership hash {definition.cohort_membership_sha256} the study file records"
        )

    g4 = reproduce_study_cells(released_root, evidence_dir, manifest, definition.cells)
    if g4_path is not None:
        write_gates(g4_path, gates_document(study, (), (g4,), reference))
    if not g4.passed:
        raise ValueError(
            f"G4 failed: the released records do not reproduce the released evidence "
            f"{dict(g4.counts)}; no number of the study is computed"
        )

    family_by_token = {token: family for family in FAMILIES for token in cohort.families[family]}
    tokens = tuple(sorted(family_by_token))
    log_by_token = token_log_map(Path.cwd() / definition.token_log_source, manifest)
    drive_by_token = {token: drive_of(log) for token, log in log_by_token.items()}
    documents = {arm.id: load_arm(arms_root / arm.id, arm.cells, tokens) for arm in definition.arms}
    settings = definition.bootstrap
    families = tuple(family for family in FAMILIES if cohort.families[family])
    single_stratum = dict.fromkeys(tokens, "all")

    def draws(
        drawn: Sequence[str], strata: Mapping[str, str], clusters: Mapping[str, str]
    ) -> BootstrapWeights:
        return cluster_bootstrap_weights(
            drawn, strata, clusters, resamples=settings.resamples, seed=settings.seed
        )

    analysis = _Study(
        definition=definition,
        outcomes=MappingProxyType(
            {arm_id: arm_outcomes(arm_documents) for arm_id, arm_documents in documents.items()}
        ),
        documents=MappingProxyType(documents),
        tokens=tokens,
        families=families,
        family_by_token=family_by_token,
        log_by_token=log_by_token,
        drive_by_token=drive_by_token,
        weights=MappingProxyType(
            {
                "family-log": draws(tokens, family_by_token, log_by_token),
                "family-drive": draws(tokens, family_by_token, drive_by_token),
                "log": draws(tokens, single_stratum, log_by_token),
            }
        ),
        family_weights=MappingProxyType(
            {
                family: draws(
                    [token for token in tokens if family_by_token[token] == family],
                    family_by_token,
                    log_by_token,
                )
                for family in families
            }
        ),
    )

    by_family = {family.id: family for family in definition.hypothesis_families}
    primary = analysis.decide(by_family[PRIMARY_FAMILY], primary=True)
    decided = [
        *primary,
        *analysis.decide(by_family[Q2_FAMILY], primary=False),
        *analysis.decide(by_family[Q3_CHECK_FAMILY], primary=False),
    ]
    by_id = {item.hypothesis.id: item for item in primary}
    h1, h2, h5 = by_id["H1"], by_id["H2"], by_id["H5"]
    return PolicyV2SummaryV1(
        schema_version=POLICY_V2_SUMMARY_SCHEMA_VERSION,
        study_sha256=study_hash,
        protocol_sha256=definition.seed_namespace_protocol_sha256,
        cohort_manifest_sha256=definition.cohort_membership_sha256,
        common_valid_tokens=len(tokens),
        reference=reference,
        exploratory=reference == ARM_A,
        g4=StudyGateV1(gate=g4.gate, passed=g4.passed, counts=dict(g4.counts)),
        hypotheses=tuple(analysis.hypothesis_document(item) for item in decided),
        q1_label=q1_label(
            h1.classification, h2.classification, h1.result.estimate, h2.result.estimate
        ),
        h5_qualifier=h5_qualifier(h5.result.p_value, h5.result.estimate),
        statements=_statements(by_id),
        sensitivity=tuple(analysis.sensitivity(primary)),
        q3_ratio=keying_ratio(analysis.weights["family-log"], analysis.outcomes),
        secondary=tuple(analysis.secondary()),
        descriptive_contrasts=tuple(analysis.descriptive_contrasts(primary)),
        levels=tuple(analysis.levels()),
    )
