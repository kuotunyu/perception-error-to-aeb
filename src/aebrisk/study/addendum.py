"""The post-hoc addendum to v1.0.0: intervals for the released attribution and contrasts.

`docs/posthoc/nuplan_aeb_v2-addendum/addendum-plan.md` fixes everything here
before it runs. The addendum reads only the released per-token records and the
evidence published from them, and it estimates: it computes no p-value, no test
decision and no ranking.

THE REPRODUCTION GATE COMES FIRST (`reproduce_released`, plan section 7). The
records are held to the list of their SHA-256 written after the released run
finished, and a list whose own SHA-256 is not the one the plan states is
refused before anything is read. Then the released functions, run on the
records, must give back with float equality every number of `intervals.json`,
every Shapley value and efficiency residual of `shapley.json`, and the four
fields of `evaluation.json` the addendum uses, for all 26 configurations. Only
then does `analyse_addendum` compute anything.

EVERY INTERVAL IS READ FROM ONE RESAMPLING (plan section 4): 5,000 draws of a
family-stratified bootstrap over (family, log) clusters, shared by every
configuration and both games. A token's Shapley value is the released
attribution run on that token alone, so the released value is the mean of
those values. The six differences between localization and shape and each
other channel carry a simultaneous interval at 1 - 0.05/6 beside the 95% one;
everything else is at 95%. The Shapley values and their differences take the
released values as their estimates, which the gate has reproduced; a
configuration contrast is the difference of two ratios of sums over the cohort.

WHAT WAS SEEN FIRST IS LABELLED. Section 1 of the plan lists what scratch
analyses computed before the plan was written. Each item of the summary that
reports one of those quantities carries `computed_before_plan: true`: the eight
Shapley value intervals; the configuration contrasts on the collision indicator
and on braking share, whose estimates follow from the token-level collision
counts and braking shares computed then; the oracle's avoided and induced
collisions; the oracle's brake activations; the Clopper-Pearson bounds of the
configurations without a counted collision; and the matched onset delays.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
import math
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

import numpy as np
from pydantic import BaseModel

from aebrisk.analysis.aggregate import (
    FormalResults,
    _replicate_metric_mean,
    common_cohort,
    configuration_intervals,
    configuration_rows,
    load_formal_results,
    validate_formal_set,
)
from aebrisk.analysis.attribution import AttributionMetric, attribution_by_metric
from aebrisk.artifacts.documents import AEBEvaluationV1, AEBIntervalsV1, AEBShapleyV1
from aebrisk.artifacts.results import AEBScenarioResult
from aebrisk.artifacts.study_documents import (
    ADDENDUM_GAMES,
    ATTRIBUTION_ADDENDUM_SCHEMA_VERSION,
    COLLISION_GAME_SENTENCE,
    AddendumActivationRateV1,
    AddendumBootstrapV1,
    AddendumContrastV1,
    AddendumDifferenceV1,
    AddendumDistributionV1,
    AddendumEstimateV1,
    AddendumGame,
    AddendumGameV1,
    AddendumIntervalV1,
    AddendumOnsetDelaysV1,
    AddendumOracleCollisionsV1,
    AddendumProportionV1,
    AddendumStopsAndSpeedsV1,
    AddendumZeroEventV1,
    AttributionAddendumV1,
    ContrastMetric,
    StudyGateV1,
)
from aebrisk.attribution.factorial import (
    EMPTY_COALITION_ID,
    SWEPT_SEVERITIES,
    coalition_configuration_id,
    formal_configurations,
)
from aebrisk.attribution.shapley import CHANNELS
from aebrisk.cohort.filters import SCENARIO_FAMILIES
from aebrisk.cohort.manifest import CohortManifestV1, load_manifest
from aebrisk.errors.pipeline import configuration_id
from aebrisk.metrics.bootstrap import (
    DEFAULT_CONFIDENCE,
    DEFAULT_RESAMPLES,
    DEFAULT_SEED,
    BootstrapWeights,
    cluster_bootstrap_weights,
    percentile_interval,
    weighted_mean,
    weighted_ratio,
)
from aebrisk.metrics.inference import clopper_pearson, simultaneous_confidence
from aebrisk.metrics.safety import SECONDS_PER_HOUR, measured_simulated_seconds
from aebrisk.simulation.orchestrate import TokenResultsV1
from aebrisk.simulation.step_loop import COLLIDING_MASS_KG
from aebrisk.study.definition import ORACLE_CELL, token_log_map
from aebrisk.study.gates import NO_AEB_CELL, GateResult, check_released_records

#: The SHA-256 of the released run's output hash list, as section 7 of the
#: addendum plan states it. Any other list is refused.
RELEASED_OUTPUT_HASHES_SHA256 = "47439aad52f112ff2d3e1142cc8530ddd678c5ae5e58a77416beccfe8c730db0"

REPRODUCTION_GATE = "reproduction"

#: The fields of each `evaluation.json` configuration the addendum uses, and
#: therefore reproduces.
REPRODUCED_EVALUATION_FIELDS: tuple[str, ...] = (
    "collisions",
    "contacts_not_at_fault",
    "simulated_seconds",
    "mean_intervention_duration_s",
)

#: The channel every other channel is compared with.
FOCAL_CHANNEL = "localization_shape"

FULL_COALITION = coalition_configuration_id(frozenset(CHANNELS))

#: Section 6.2: plus minus minus, on each of three outcomes.
CONFIGURATION_CONTRASTS: tuple[tuple[str, str], ...] = (
    (ORACLE_CELL, NO_AEB_CELL),
    (EMPTY_COALITION_ID, ORACLE_CELL),
    (FULL_COALITION, EMPTY_COALITION_ID),
    (coalition_configuration_id(frozenset({FOCAL_CHANNEL})), EMPTY_COALITION_ID),
)
CONTRAST_METRICS: tuple[ContrastMetric, ...] = (
    "collision_indicator",
    "braking_share",
    "not_at_fault_contact_rate",
)

#: The not-at-fault contact rate is per this many simulated seconds.
CONTACT_RATE_PER_S = 1000.0

#: Section 6.6: the three latency configurations, beside the empty coalition.
ONSET_DELAY_CONFIGURATIONS: tuple[str, ...] = (
    EMPTY_COALITION_ID,
    *(configuration_id("latency", severity) for severity in SWEPT_SEVERITIES),
)

#: Quantities that section 1 of the plan lists as computed before the plan.
COMPUTED_BEFORE_PLAN_CONTRAST_METRICS = frozenset({"collision_indicator", "braking_share"})
COMPUTED_BEFORE_PLAN_ACTIVATIONS = frozenset({ORACLE_CELL})


@dataclasses.dataclass(frozen=True)
class _Release:
    """The released records the gate reproduced the evidence from, and what it computed."""

    loaded: FormalResults
    cohort: CohortManifestV1
    common: tuple[str, ...]
    family_by_token: Mapping[str, str]
    shapley: Mapping[str, AttributionMetric]
    rows: Mapping[str, Mapping[str, Any]]


# --------------------------------------------------------------------------
# The reproduction gate
# --------------------------------------------------------------------------


def _leaves(value: object, pointer: str = "") -> Iterator[tuple[str, object]]:
    """Every value of nested mappings, by the path of keys that reaches it."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _leaves(item, f"{pointer}/{key}")
    else:
        yield pointer, value


def _difference(
    pointer: str, expected: Mapping[str, object], found: Mapping[str, object]
) -> Optional[str]:
    """Why one published value is not the recomputed one, or `None` when it is."""

    if pointer not in found:
        return "is not in the evidence"
    if pointer not in expected:
        return "is not recomputed from the records"
    if found[pointer] != expected[pointer]:
        return f"is {found[pointer]!r}, and the records give {expected[pointer]!r}"
    return None


def _compare(
    name: str, recomputed: object, published: object, detail: list[str]
) -> tuple[int, int]:
    """How many values were compared, and how many differ, a missing or extra one included."""

    expected = dict(_leaves(recomputed))
    found = dict(_leaves(published))
    pointers = sorted(set(expected) | set(found))
    differing = 0
    for pointer in pointers:
        reason = _difference(pointer, expected, found)
        if reason is not None:
            differing += 1
            detail.append(f"{name} {pointer} {reason}")
    return len(pointers), differing


def _read_evidence(evidence_dir: Path, name: str, model: type[BaseModel]) -> dict[str, Any]:
    """A published document as parsed JSON, after its released model has accepted it."""

    document: dict[str, Any] = json.loads((evidence_dir / name).read_text(encoding="utf-8"))
    model.model_validate(document)
    return document


def _shapley_part(metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, object]:
    return {
        "metrics": {
            metric: {
                "values": fields["values"],
                "efficiency_max_abs_residual": fields["efficiency_max_abs_residual"],
            }
            for metric, fields in metrics.items()
        }
    }


def _evaluation_part(rows: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    return {
        "configurations": {
            str(row["configuration_id"]): {
                field: row[field] for field in REPRODUCED_EVALUATION_FIELDS
            }
            for row in rows
        }
    }


def _reproduce(
    released_root: Path,
    released_hashes: Path,
    evidence_dir: Path,
    manifest: Path,
    expected_hashes_sha256: str,
) -> tuple[GateResult, Optional[_Release]]:
    records = check_released_records(released_root, released_hashes, expected_hashes_sha256)
    counts = {f"released_{name}": count for name, count in records.counts.items()}
    detail = list(records.local_detail)
    if not records.passed:
        return GateResult(REPRODUCTION_GATE, False, counts, tuple(detail)), None

    cohort = load_manifest(manifest)
    family_by_token = {
        token: family for family in SCENARIO_FAMILIES for token in cohort.families[family]
    }
    loaded = load_formal_results(released_root)
    validate_formal_set(loaded, formal_configurations(), tuple(family_by_token), manifest=cohort)
    common = common_cohort(loaded)
    shapley = attribution_by_metric(loaded, common)
    rows = configuration_rows(loaded, common)
    intervals = configuration_intervals(loaded, common, family_by_token)

    recomputed = {
        "intervals": {
            "intervals": {
                configuration: {
                    metric: dataclasses.asdict(interval) for metric, interval in metrics.items()
                }
                for configuration, metrics in intervals.items()
            }
        },
        "shapley": _shapley_part(shapley),
        "evaluation": _evaluation_part(rows),
    }
    published = {
        "intervals": {
            "intervals": _read_evidence(evidence_dir, "intervals.json", AEBIntervalsV1)["intervals"]
        },
        "shapley": _shapley_part(
            _read_evidence(evidence_dir, "shapley.json", AEBShapleyV1)["metrics"]
        ),
        "evaluation": _evaluation_part(
            _read_evidence(evidence_dir, "evaluation.json", AEBEvaluationV1)["configurations"]
        ),
    }
    for name, document in recomputed.items():
        compared, differing = _compare(f"{name}.json", document, published[name], detail)
        counts[f"{name}_values"] = compared
        counts[f"{name}_differing"] = differing
    passed = not any(counts[f"{name}_differing"] for name in recomputed)
    release = _Release(
        loaded=loaded,
        cohort=cohort,
        common=common,
        family_by_token=family_by_token,
        shapley=shapley,
        rows={str(row["configuration_id"]): row for row in rows},
    )
    return GateResult(REPRODUCTION_GATE, passed, counts, tuple(detail)), release


def reproduce_released(
    released_root: Path,
    released_hashes: Path,
    evidence_dir: Path,
    manifest: Path,
    expected_hashes_sha256: str = RELEASED_OUTPUT_HASHES_SHA256,
) -> GateResult:
    """The addendum's reproduction gate.

    It holds the records under `released_root` to the hash list
    `released_hashes` (`check_released_records`), which refuses a list whose
    SHA-256 is not `expected_hashes_sha256`, and recomputes nothing if they
    differ. It then recomputes, from the records and with float equality:
    `intervals.json` with the released `configuration_intervals`,
    `shapley.json`'s values and efficiency residuals with the released
    attribution, and `collisions`, `contacts_not_at_fault`, `simulated_seconds`
    and `mean_intervention_duration_s` of every configuration of
    `evaluation.json`. The counts give how many values were compared and how
    many differ; the detail names each difference.

    A caller that means the released list passes `RELEASED_OUTPUT_HASHES_SHA256`
    explicitly, read from this module when it calls, because a default is bound
    once, at import.
    """

    gate, _ = _reproduce(
        released_root, released_hashes, evidence_dir, manifest, expected_hashes_sha256
    )
    return gate


# --------------------------------------------------------------------------
# Per-token values
# --------------------------------------------------------------------------


def per_token_shapley(
    loaded: Mapping[str, Mapping[str, TokenResultsV1]], cohort: Sequence[str]
) -> Mapping[str, Mapping[str, Mapping[str, float]]]:
    """Each token's exact Shapley value of each channel, in each game.

    It is the released attribution run on a cohort of that one token, with its
    efficiency check, so the released value is these values summed in cohort
    order and divided by the cohort size. The mapping is token, then game, then
    channel.
    """

    return {
        token: {
            game: dict(attributed["values"])
            for game, attributed in attribution_by_metric(loaded, (token,)).items()
        }
        for token in cohort
    }


def _collided(record: AEBScenarioResult) -> bool:
    return bool(record.collision_vru + record.collision_vehicle + record.collision_object)


def _summed(document: TokenResultsV1, field: str) -> float:
    return float(sum(getattr(record, field) for record in document.results))


def _exposure(document: TokenResultsV1) -> float:
    return measured_simulated_seconds(document.results, cohort=(document.scenario_token,))


def _statistic(
    weights: BootstrapWeights, documents: Mapping[str, TokenResultsV1], metric: str
) -> np.ndarray:
    """One configuration's outcome over each draw's tokens: a mean or a ratio of sums."""

    tokens = weights.tokens
    if metric == "collision_indicator":
        return weighted_mean(
            weights, {token: _replicate_metric_mean(documents[token], metric) for token in tokens}
        )
    exposure = {token: _exposure(documents[token]) for token in tokens}
    if metric == "braking_share":
        return weighted_ratio(
            weights,
            {token: _summed(documents[token], "intervention_duration_s") for token in tokens},
            exposure,
        )
    return CONTACT_RATE_PER_S * weighted_ratio(
        weights,
        {token: _summed(documents[token], "contacts_not_at_fault") for token in tokens},
        exposure,
    )


def _intervals(draws: np.ndarray, levels: Sequence[float]) -> tuple[AddendumIntervalV1, ...]:
    intervals = []
    for level in levels:
        low, high = percentile_interval(draws, level)
        intervals.append(AddendumIntervalV1(confidence=level, low=low, high=high))
    return tuple(intervals)


def _distribution(values: Sequence[float]) -> AddendumDistributionV1:
    if not values:
        return AddendumDistributionV1(
            count=0, median=None, lower_quartile=None, upper_quartile=None
        )
    lower, median, upper = np.percentile(np.asarray(values, dtype=np.float64), [25.0, 50.0, 75.0])
    return AddendumDistributionV1(
        count=len(values),
        median=float(median),
        lower_quartile=float(lower),
        upper_quartile=float(upper),
    )


def _proportion(events: int, tokens: int) -> AddendumProportionV1:
    low, high = clopper_pearson(events, tokens)
    return AddendumProportionV1(
        events=events, tokens=tokens, confidence=DEFAULT_CONFIDENCE, low=low, high=high
    )


# --------------------------------------------------------------------------
# The analysis
# --------------------------------------------------------------------------


def _game(
    game: AddendumGame,
    released: Mapping[str, float],
    phi: Mapping[str, Mapping[str, float]],
    weights: BootstrapWeights,
) -> AddendumGameV1:
    """One game's Shapley values and their pairwise differences, all read from one set of draws.

    The three differences between localization and shape and each other channel
    carry the simultaneous interval for all six of both games beside the 95% one.
    """

    simultaneous = simultaneous_confidence(len(ADDENDUM_GAMES) * (len(CHANNELS) - 1))

    def difference(plus: str, minus: str, levels: Sequence[float]) -> AddendumDifferenceV1:
        draws = weighted_mean(
            weights, {token: phi[plus][token] - phi[minus][token] for token in weights.tokens}
        )
        return AddendumDifferenceV1(
            plus=plus,
            minus=minus,
            estimate=released[plus] - released[minus],
            intervals=_intervals(draws, levels),
            computed_before_plan=False,
        )

    others = [channel for channel in CHANNELS if channel != FOCAL_CHANNEL]
    return AddendumGameV1(
        game=game,
        shapley_values={
            channel: AddendumEstimateV1(
                estimate=released[channel],
                intervals=_intervals(weighted_mean(weights, phi[channel]), (DEFAULT_CONFIDENCE,)),
                computed_before_plan=True,
            )
            for channel in CHANNELS
        },
        localization_shape_differences=tuple(
            difference(FOCAL_CHANNEL, channel, (simultaneous, DEFAULT_CONFIDENCE))
            for channel in others
        ),
        other_differences=tuple(
            difference(plus, minus, (DEFAULT_CONFIDENCE,))
            for plus, minus in itertools.combinations(others, 2)
        ),
        caution=COLLISION_GAME_SENTENCE if game == "collision_indicator" else None,
    )


def _contrast(
    weights: BootstrapWeights, release: _Release, plus: str, minus: str, metric: str
) -> np.ndarray:
    return _statistic(weights, release.loaded[plus], metric) - _statistic(
        weights, release.loaded[minus], metric
    )


def _configuration_contrasts(
    release: _Release, weights: BootstrapWeights
) -> tuple[AddendumContrastV1, ...]:
    """Section 6.2. The estimate is the same contrast over the cohort itself, each token once."""

    observed = BootstrapWeights(
        tokens=weights.tokens, weights=np.ones((1, len(weights.tokens)), dtype=np.int64)
    )
    contrasts = []
    for plus, minus in CONFIGURATION_CONTRASTS:
        for metric in CONTRAST_METRICS:
            estimate = _contrast(observed, release, plus, minus, metric)
            contrasts.append(
                AddendumContrastV1(
                    plus=plus,
                    minus=minus,
                    metric=metric,
                    estimate=float(estimate[0]),
                    intervals=_intervals(
                        _contrast(weights, release, plus, minus, metric), (DEFAULT_CONFIDENCE,)
                    ),
                    computed_before_plan=metric in COMPUTED_BEFORE_PLAN_CONTRAST_METRICS,
                )
            )
    return tuple(contrasts)


def _oracle_collisions(release: _Release) -> AddendumOracleCollisionsV1:
    """Per token, the mean over replicates of avoided and of induced counted collisions.

    `no_aeb` and `oracle_aeb` are deterministic across replicates, so each mean
    is 0 or 1 and the sums are token counts; a mean between them is refused.
    """

    counts = {"avoided": 0, "induced": 0}
    for token in release.common:
        without = {
            record.replicate: _collided(record)
            for record in release.loaded[NO_AEB_CELL][token].results
        }
        oracle = {
            record.replicate: _collided(record)
            for record in release.loaded[ORACLE_CELL][token].results
        }
        shares = {
            "avoided": sum(without[r] and not oracle[r] for r in without) / len(without),
            "induced": sum(oracle[r] and not without[r] for r in without) / len(without),
        }
        for name, share in shares.items():
            if not share.is_integer():
                raise ValueError(
                    f"a token's {name} collisions differ across replicates ({share}); the "
                    "addendum counts them as tokens because no_aeb and oracle_aeb are "
                    "deterministic"
                )
            counts[name] += int(share)
    tokens = len(release.common)
    return AddendumOracleCollisionsV1(
        avoided=_proportion(counts["avoided"], tokens),
        induced=_proportion(counts["induced"], tokens),
        oracle_aeb_contacts_not_at_fault=release.rows[ORACLE_CELL]["contacts_not_at_fault"],
        computed_before_plan=True,
    )


def _records(release: _Release, configuration: str) -> list[AEBScenarioResult]:
    return [
        record
        for token in release.common
        for record in release.loaded[configuration][token].results
    ]


def _brake_activations(release: _Release) -> tuple[AddendumActivationRateV1, ...]:
    """Section 6.4: every intervention matched with the oracle's, and every false one."""

    rates = []
    for configuration in formal_configurations():
        if not configuration.aeb_enabled:
            continue
        identifier = configuration.configuration_id
        activations = sum(
            len(record.matched_delay_s) + record.false_interventions
            for record in _records(release, identifier)
        )
        seconds = release.rows[identifier]["simulated_seconds"]
        rates.append(
            AddendumActivationRateV1(
                configuration_id=identifier,
                brake_activations=activations,
                simulated_seconds=seconds,
                per_hour=SECONDS_PER_HOUR * activations / seconds,
                computed_before_plan=identifier in COMPUTED_BEFORE_PLAN_ACTIVATIONS,
            )
        )
    return tuple(rates)


def _zero_event_configurations(release: _Release) -> tuple[AddendumZeroEventV1, ...]:
    """Section 6.5, on the token-level "any counted collision in any replicate"."""

    zero = []
    for configuration in formal_configurations():
        identifier = configuration.configuration_id
        documents = release.loaded[identifier]
        events = {
            token: any(_collided(record) for record in documents[token].results)
            for token in release.common
        }
        if any(events.values()):
            continue
        by_family: dict[str, list[bool]] = {}
        for token, event in events.items():
            by_family.setdefault(release.family_by_token[token], []).append(event)
        zero.append(
            AddendumZeroEventV1(
                configuration_id=identifier,
                overall=_proportion(sum(events.values()), len(events)),
                by_family={
                    family: _proportion(sum(family_events), len(family_events))
                    for family, family_events in by_family.items()
                },
                computed_before_plan=True,
            )
        )
    return tuple(zero)


def _matched_onset_delays(release: _Release) -> tuple[AddendumOnsetDelaysV1, ...]:
    return tuple(
        AddendumOnsetDelaysV1(
            configuration_id=identifier,
            matched_onset_delays_s=_distribution(
                [
                    delay
                    for record in _records(release, identifier)
                    for delay in record.matched_delay_s
                ]
            ),
            computed_before_plan=True,
        )
        for identifier in ONSET_DELAY_CONFIGURATIONS
    )


def _stops_and_collision_speeds(release: _Release) -> tuple[AddendumStopsAndSpeedsV1, ...]:
    """Section 6.7. The speed is the ego's own, sqrt(2E / m), not the relative impact speed."""

    rows = []
    for configuration in formal_configurations():
        records = _records(release, configuration.configuration_id)
        rows.append(
            AddendumStopsAndSpeedsV1(
                configuration_id=configuration.configuration_id,
                first_stop_distance_m=_distribution(
                    [
                        record.stop_distance_m
                        for record in records
                        if record.stop_distance_m is not None
                    ]
                ),
                ego_speed_at_collision_mps=_distribution(
                    [
                        math.sqrt(2.0 * record.collision_energy / COLLIDING_MASS_KG)
                        for record in records
                        if _collided(record)
                    ]
                ),
                computed_before_plan=False,
            )
        )
    return tuple(rows)


def analyse_addendum(
    released_root: Path,
    released_hashes: Path,
    evidence_dir: Path,
    manifest: Path,
    eligibility: Path,
    expected_hashes_sha256: str = RELEASED_OUTPUT_HASHES_SHA256,
) -> AttributionAddendumV1:
    """The addendum summary, computed only after the reproduction gate passes.

    It refuses when the gate fails, including for a hash list whose SHA-256 is
    not `expected_hashes_sha256`. The summary records the gate's outcome and
    counts, without its detail, and the SHA-256 of `released_hashes`. Clusters
    are (family, log) pairs, each token's log read from `eligibility` with
    `token_log_map`.
    """

    gate, release = _reproduce(
        released_root, released_hashes, evidence_dir, manifest, expected_hashes_sha256
    )
    if release is None or not gate.passed:
        raise ValueError(
            f"the reproduction gate failed with counts {dict(gate.counts)}; the addendum "
            "computes nothing from records or evidence it cannot reproduce"
        )

    log_by_token = token_log_map(eligibility, manifest)
    weights = cluster_bootstrap_weights(
        release.common,
        release.family_by_token,
        log_by_token,
        resamples=DEFAULT_RESAMPLES,
        seed=DEFAULT_SEED,
    )
    values = per_token_shapley(release.loaded, release.common)
    games = tuple(
        _game(
            game,
            release.shapley[game]["values"],
            {
                channel: {token: values[token][game][channel] for token in release.common}
                for channel in CHANNELS
            },
            weights,
        )
        for game in ADDENDUM_GAMES
    )
    return AttributionAddendumV1(
        schema_version=ATTRIBUTION_ADDENDUM_SCHEMA_VERSION,
        protocol_sha256=release.cohort.protocol_sha256,
        cohort_manifest_sha256=release.loaded.cohort_manifest_sha256,
        cohort_size=len(release.family_by_token),
        common_valid_tokens=len(release.common),
        released_output_hashes_sha256=hashlib.sha256(released_hashes.read_bytes()).hexdigest(),
        reproduction_gate=StudyGateV1(gate=gate.gate, passed=gate.passed, counts=dict(gate.counts)),
        bootstrap=AddendumBootstrapV1(
            cluster="family-log",
            clusters=len(
                {(release.family_by_token[token], log_by_token[token]) for token in release.common}
            ),
            resamples=DEFAULT_RESAMPLES,
            seed=DEFAULT_SEED,
        ),
        games=games,
        configuration_contrasts=_configuration_contrasts(release, weights),
        oracle_collisions=_oracle_collisions(release),
        brake_activations=_brake_activations(release),
        zero_event_configurations=_zero_event_configurations(release),
        matched_onset_delays=_matched_onset_delays(release),
        stops_and_collision_speeds=_stops_and_collision_speeds(release),
    )
