"""The post-hoc addendum: its reproduction gate, and the intervals the gate permits.

`docs/posthoc/nuplan_aeb_v2-addendum/addendum-plan.md` fixes both. These tests
write one small released run by hand: six tokens in three families and three
logs, one log with tokens in two families, all twenty-six configurations with
three replicates, the evidence the released `evaluate` command writes from them,
and the list of their SHA-256 in the shape of the released run's list.

In the sixteen coalition cells each token's two games are additive: every
channel adds a planted amount to the token's intervention duration and a
planted number of colliding replicates. The exact Shapley value of an additive
game is the planted amount, so each token's values are known before anything is
computed. The other cells carry small hand-built outcomes whose counts, rates
and quartiles are worked out below.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
import shutil
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pytest
from pydantic import ValidationError

from aebrisk.analysis.aggregate import load_formal_results
from aebrisk.artifacts.results import AEBScenarioResultV2, ScenarioFamily
from aebrisk.artifacts.study_documents import AttributionAddendumV1, StudyGateV1
from aebrisk.attribution.factorial import coalition_configurations, formal_configurations
from aebrisk.attribution.shapley import CHANNELS
from aebrisk.cli.evaluate import evaluate
from aebrisk.cohort.manifest import load_manifest, membership_sha256
from aebrisk.metrics.bootstrap import (
    BootstrapWeights,
    cluster_bootstrap_weights,
    percentile_interval,
    weighted_mean,
)
from aebrisk.metrics.inference import clopper_pearson
from aebrisk.simulation.orchestrate import TokenResultsV1, token_results_bytes
from aebrisk.study.addendum import (
    RELEASED_OUTPUT_HASHES_SHA256,
    analyse_addendum,
    per_token_shapley,
    reproduce_released,
)
from aebrisk.study.gates import GateResult

PROTOCOL_SHA = "bbf0b6d31943a0f99160afe1d4c18f9a181850367494004037bab98d8f2e59f9"

#: The sentence the addendum plan fixes for the collision game, word for word.
COLLISION_GAME_SENTENCE = (
    "Under v1, the lower collision indicator of the configurations with localization error "
    "coincides with braking for most of their measured exposure; it should not be read as a "
    "safety benefit."
)

FULL_COALITION = "coalition-dropout+localization_shape+latency+track_instability"
LATENCY_CELLS = ("latency-low", "latency-medium", "latency-high")
HIGH_CELLS = ("dropout-high", "localization_shape-high", "latency-high", "track_instability-high")
FORMAL_IDS = tuple(configuration.configuration_id for configuration in formal_configurations())
COALITION_OF = {
    identifier: coalition for coalition, identifier in coalition_configurations().items()
}

# --------------------------------------------------------------------------
# The cohort
# --------------------------------------------------------------------------

LOGS = (
    "2021.01.01.00.00.00_veh-01_00000_00100.db",
    "2021.01.02.00.00.00_veh-02_00000_00200.db",
    "2021.01.03.00.00.00_veh-03_00000_00300.db",
)
TOKENS_BY_FAMILY: Mapping[ScenarioFamily, tuple[str, ...]] = {
    "lead_or_stopping": ("lead-a", "lead-b", "lead-c"),
    "cut_in_or_crossing": ("cut-a", "cut-b"),
    "pedestrian_or_crosswalk": ("walk-a",),
    "bicycle_or_vru": (),
}
FAMILY_OF = {token: family for family, tokens in TOKENS_BY_FAMILY.items() for token in tokens}
TOKENS = tuple(sorted(FAMILY_OF))
#: The first log has tokens in two families, so it is two clusters: five in all.
LOG_OF = {
    "lead-a": LOGS[0],
    "lead-b": LOGS[0],
    "cut-a": LOGS[0],
    "lead-c": LOGS[1],
    "cut-b": LOGS[2],
    "walk-a": LOGS[2],
}

# --------------------------------------------------------------------------
# The planted outcomes
# --------------------------------------------------------------------------

#: Each channel's addition to a token's intervention duration, in CHANNELS order
#: (dropout, localization_shape, latency, track_instability), over a base of 6 s.
BASE_DURATION_S = 6.0
DURATION_EFFECT_S: Mapping[str, Mapping[str, float]] = {
    token: dict(zip(CHANNELS, effects))
    for token, effects in {
        "cut-a": (-0.5, 2.0, 0.25, 0.0),
        "cut-b": (0.0, 1.5, -0.25, 0.5),
        "lead-a": (-0.5, 4.0, -0.25, 0.0),
        "lead-b": (0.0, 3.0, -0.5, 0.25),
        "lead-c": (-1.0, 5.0, 0.0, -0.25),
        "walk-a": (0.25, 2.5, -0.75, 0.0),
    }.items()
}
#: Each token's colliding replicates without error, and each channel's addition
#: to them. Replicate r collides when r is below the count, so the replicate
#: mean of the collision indicator is the count in thirds.
COLLISION_BASE: Mapping[str, int] = {
    "cut-a": 1,
    "cut-b": 0,
    "lead-a": 1,
    "lead-b": 0,
    "lead-c": 2,
    "walk-a": 1,
}
COLLISION_EFFECT: Mapping[str, Mapping[str, int]] = {
    token: dict(zip(CHANNELS, effects))
    for token, effects in {
        "cut-a": (0, -1, 0, 0),
        "cut-b": (0, 0, 0, 0),
        "lead-a": (0, -1, 1, 0),
        "lead-b": (1, 0, 0, 1),
        "lead-c": (-1, -1, 0, 1),
        "walk-a": (1, -1, 1, 0),
    }.items()
}

#: `no_aeb` and `oracle_aeb` collide on the same tokens in every replicate.
#: The oracle avoids lead-a and walk-a, and induces lead-b.
NO_AEB_COLLISION_SPEED_MPS = {"lead-a": 5.0, "cut-a": 3.0, "walk-a": 4.0}
ORACLE_COLLISIONS = ("cut-a", "lead-b")
ORACLE_DURATION_S = 2.0
ORACLE_STOP_DISTANCE_M = {"lead-a": 10.0, "lead-b": 20.0}
OTHER_COLLISION_SPEED_MPS = 2.0

#: Matched onset delays, by cell and (token, replicate). Every oracle
#: intervention is matched with itself.
DELAYS_S: Mapping[str, Mapping[tuple[str, int], tuple[float, ...]]] = {
    "latency-low": {("cut-a", 0): (1.0,)},
    "latency-high": {
        ("lead-a", 0): (0.2,),
        ("lead-a", 1): (0.4,),
        ("lead-a", 2): (0.6, 0.8),
    },
}

COLLIDED_EXPOSURE_S = 12.0
FULL_EXPOSURE_S = 15.0


def planted(cell: str, token: str, replicate: int, nondeterministic: bool) -> dict[str, Any]:
    """What one replicate of one token measured in one cell."""

    duration = BASE_DURATION_S
    delays = DELAYS_S.get(cell, {}).get((token, replicate), ())
    stop: Optional[float] = None
    speed = OTHER_COLLISION_SPEED_MPS
    if cell == "no_aeb":
        collided = token in NO_AEB_COLLISION_SPEED_MPS or (
            nondeterministic and token == "lead-b" and replicate == 0
        )
        speed = NO_AEB_COLLISION_SPEED_MPS.get(token, speed)
        duration = 0.0
    elif cell == "oracle_aeb":
        collided = token in ORACLE_COLLISIONS
        duration = ORACLE_DURATION_S
        delays = (0.0,)
        stop = ORACLE_STOP_DISTANCE_M.get(token)
    elif cell in HIGH_CELLS:
        collided = False
    else:
        coalition = COALITION_OF.get(cell, frozenset())
        count = COLLISION_BASE[token] + sum(COLLISION_EFFECT[token][c] for c in coalition)
        collided = replicate < count
        duration += sum(DURATION_EFFECT_S[token][c] for c in coalition)
    contacts = int(token == "lead-b" and replicate == 0) + int(
        cell == "oracle_aeb" and token == "walk-a"
    )
    return {
        "collided": collided,
        "duration": duration,
        "delays": delays,
        "false_interventions": int(cell != "no_aeb" and token == "lead-c" and replicate == 2),
        "stop": stop,
        "speed": speed,
        "contacts": contacts,
    }


def record(cell: str, token: str, replicate: int, nondeterministic: bool) -> AEBScenarioResultV2:
    outcome = planted(cell, token, replicate, nondeterministic)
    collided = outcome["collided"]
    return AEBScenarioResultV2(
        schema_version="aeb-scenario-result/v2",
        scenario_token=token,
        family=FAMILY_OF[token],
        configuration_id=cell,
        replicate=replicate,
        valid=True,
        collision_vru=0,
        collision_vehicle=int(collided),
        collision_object=0,
        collision_energy=0.5 * 1500.0 * outcome["speed"] ** 2 if collided else 0.0,
        contacts_not_at_fault=outcome["contacts"],
        min_ttc_s=1.0,
        min_clearance_m=0.0 if collided else 1.0,
        missed_interventions=0,
        false_interventions=outcome["false_interventions"],
        matched_delay_s=outcome["delays"],
        stop_distance_m=outcome["stop"],
        max_deceleration_mps2=3.0,
        max_abs_jerk_mps3=5.0,
        intervention_duration_s=outcome["duration"],
        simulated_duration_s=COLLIDED_EXPOSURE_S if collided else FULL_EXPOSURE_S,
    )


# --------------------------------------------------------------------------
# Writing the released run
# --------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_crlf_json(path: Path, value: Any) -> Path:
    """JSON in the shape of the released run's private list: two-space indent and CRLF."""

    path.write_bytes(("\r\n".join(json.dumps(value, indent=2).split("\n")) + "\n").encode())
    return path


def manifest_document(split: str) -> dict[str, Any]:
    return {
        "schema_version": "aeb-cohort-manifest/v1",
        "split": split,
        "protocol_sha256": PROTOCOL_SHA,
        "families": {family: list(tokens) for family, tokens in TOKENS_BY_FAMILY.items()},
        "log_names": sorted(LOGS),
    }


def eligibility_document(split: str) -> dict[str, Any]:
    rows = [
        {
            "accepted": True,
            "family": FAMILY_OF[token],
            "initial_ego_speed_mps": 10.0,
            "log_name": LOG_OF[token],
            "official_split": split,
            "oracle_enters_corridor_within_4s": True,
            "oracle_min_ttc_within_4s": 2.0,
            "reason": "accepted",
            "scenario_token": token,
            "scenario_type": "following_lane_with_lead",
        }
        for token in TOKENS
    ]
    rejected = dict(rows[0], accepted=False, log_name=LOGS[1], reason="no corridor entry")
    return {
        "schema_version": "aeb-cohort-eligibility/v1",
        "examined": [*rows, rejected],
        "scenarios_in_split_by_family": dict.fromkeys(TOKENS_BY_FAMILY, 10),
    }


def write_cohort(directory: Path) -> Path:
    """The five cohort files the released `evaluate` copies beside its evidence."""

    for split in ("smoke", "development", "evaluation"):
        write_json(directory / f"{split}.json", manifest_document(split))
    for split in ("development", "evaluation"):
        write_json(directory / f"{split}-eligibility.json", eligibility_document(split))
    return directory / "evaluation.json"


def write_records(root: Path, manifest: Path, nondeterministic: bool) -> Path:
    membership = membership_sha256(load_manifest(manifest))
    for cell in FORMAL_IDS:
        for token in TOKENS:
            document = TokenResultsV1(
                schema_version="aeb-token-results/v1",
                scenario_token=token,
                family=FAMILY_OF[token],
                split="evaluation",
                configuration_id=cell,
                protocol_sha256=PROTOCOL_SHA,
                cohort_manifest_sha256=membership,
                valid=True,
                results=tuple(record(cell, token, r, nondeterministic) for r in range(3)),
            )
            path = root / cell / f"{token}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(token_results_bytes(document))
    write_json(root / "run_context.json", {"configuration_id": "all"})
    write_json(
        root / "run_complete.json",
        {
            "schema_version": "aeb-run-complete/v1",
            "cohort_manifest_sha256": membership,
            "tokens": list(TOKENS),
        },
    )
    (root / "run.log").write_text("the released run\n", encoding="utf-8")
    return root


def hash_list(root: Path) -> dict[str, Any]:
    """A list in the shape of the released run's `output-hashes.json`."""

    return {
        "schema_version": "aeb-d2-output-hashes/v2",
        "generated_at_utc": "2026-09-06T20:54:13.4306365Z",
        "formal_files": [
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ],
        "operation_files": [{"path": "write_output_hashes.ps1", "bytes": 1773, "sha256": "c" * 64}],
    }


@dataclasses.dataclass(frozen=True)
class Release:
    """One released run: its records, their hash list, its evidence and its cohort."""

    released_root: Path
    released_hashes: Path
    evidence_dir: Path
    manifest: Path
    eligibility: Path

    def gate(self, **changes: Any) -> GateResult:
        arguments: dict[str, Any] = {
            "released_root": self.released_root,
            "released_hashes": self.released_hashes,
            "evidence_dir": self.evidence_dir,
            "manifest": self.manifest,
            "expected_hashes_sha256": file_sha256(self.released_hashes),
        }
        arguments.update(changes)
        return reproduce_released(**arguments)

    def analyse(self, **changes: Any) -> AttributionAddendumV1:
        arguments: dict[str, Any] = {
            "released_root": self.released_root,
            "released_hashes": self.released_hashes,
            "evidence_dir": self.evidence_dir,
            "manifest": self.manifest,
            "eligibility": self.eligibility,
            "expected_hashes_sha256": file_sha256(self.released_hashes),
        }
        arguments.update(changes)
        return analyse_addendum(**arguments)

    def evidence(self, name: str) -> dict[str, Any]:
        document: dict[str, Any] = json.loads(
            (self.evidence_dir / name).read_text(encoding="utf-8")
        )
        return document


def build_release(directory: Path, nondeterministic: bool = False) -> Release:
    manifest = write_cohort(directory / "cohort")
    released_root = write_records(directory / "released", manifest, nondeterministic)
    evaluate(results_dir=released_root, manifest=manifest, output_dir=directory / "evidence")
    return Release(
        released_root=released_root,
        released_hashes=write_crlf_json(directory / "output-hashes.json", hash_list(released_root)),
        evidence_dir=directory / "evidence",
        manifest=manifest,
        eligibility=directory / "cohort" / "evaluation-eligibility.json",
    )


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> Release:
    return build_release(tmp_path_factory.mktemp("release"))


@pytest.fixture(scope="module")
def passing_gate(release: Release) -> GateResult:
    return release.gate()


@pytest.fixture(scope="module")
def summary(release: Release) -> AttributionAddendumV1:
    return release.analyse()


@pytest.fixture(scope="module")
def dumped(summary: AttributionAddendumV1) -> dict[str, Any]:
    return summary.model_dump(mode="json")


def copied_evidence(release: Release, directory: Path) -> Release:
    return dataclasses.replace(
        release, evidence_dir=Path(shutil.copytree(release.evidence_dir, directory / "evidence"))
    )


def rewrite(release: Release, name: str, change: Any) -> None:
    document = release.evidence(name)
    change(document)
    write_json(release.evidence_dir / name, document)


def one_ulp_up(value: float) -> float:
    return math.nextafter(value, math.inf)


# --------------------------------------------------------------------------
# Per-token Shapley values
# --------------------------------------------------------------------------


def test_per_token_shapley_recovers_the_planted_values_of_an_additive_game(
    release: Release,
) -> None:
    values = per_token_shapley(load_formal_results(release.released_root), TOKENS)

    assert list(values) == list(TOKENS)
    for token in TOKENS:
        assert set(values[token]) == {"collision_indicator", "intervention_duration_s"}
        assert values[token]["intervention_duration_s"] == pytest.approx(
            DURATION_EFFECT_S[token], abs=1e-12
        )
        assert values[token]["collision_indicator"] == pytest.approx(
            {channel: effect / 3 for channel, effect in COLLISION_EFFECT[token].items()},
            abs=1e-12,
        )


def test_the_mean_of_the_per_token_values_is_the_released_shapley_value_exactly(
    release: Release,
) -> None:
    """Summed in cohort order and divided by the cohort size, as the released value is."""

    values = per_token_shapley(load_formal_results(release.released_root), TOKENS)
    released = release.evidence("shapley.json")["metrics"]

    for game in ("collision_indicator", "intervention_duration_s"):
        for channel in CHANNELS:
            total = 0.0
            for token in TOKENS:
                total += values[token][game][channel]
            assert total / len(TOKENS) == released[game]["values"][channel]


# --------------------------------------------------------------------------
# The reproduction gate
# --------------------------------------------------------------------------

RELEASED_RECORD_COUNTS = {
    "released_listed_files": 26 * 6 + 3,
    "released_missing_files": 0,
    "released_extra_files": 0,
    "released_size_mismatches": 0,
    "released_hash_mismatches": 0,
}


def test_the_reproduction_gate_passes_on_the_records_and_evidence_of_one_release(
    passing_gate: GateResult,
) -> None:
    assert passing_gate.gate == "reproduction"
    assert passing_gate.passed
    assert dict(passing_gate.counts) == {
        **RELEASED_RECORD_COUNTS,
        # Every estimate, bound, confidence, resample count and seed.
        "intervals_values": 26 * 4 * 6,
        "intervals_differing": 0,
        # Four values and one efficiency residual per game.
        "shapley_values": 2 * 5,
        "shapley_differing": 0,
        # Four fields of every configuration.
        "evaluation_values": 26 * 4,
        "evaluation_differing": 0,
    }
    assert passing_gate.local_detail == ()


def test_the_reproduction_gate_fails_when_one_bound_in_intervals_json_moves_by_one_ulp(
    release: Release, tmp_path: Path
) -> None:
    copy = copied_evidence(release, tmp_path)
    interval = copy.evidence("intervals.json")["intervals"]["oracle_aeb"]["collision_indicator"]
    moved = one_ulp_up(interval["high"])

    def move(document: dict[str, Any]) -> None:
        document["intervals"]["oracle_aeb"]["collision_indicator"]["high"] = moved

    rewrite(copy, "intervals.json", move)

    result = copy.gate()

    assert not result.passed
    assert result.counts["intervals_differing"] == 1
    assert result.counts["shapley_differing"] == 0
    assert result.counts["evaluation_differing"] == 0
    assert result.local_detail == (
        f"intervals.json /intervals/oracle_aeb/collision_indicator/high is {moved!r}, "
        f"and the records give {interval['high']!r}",
    )
    with pytest.raises(ValueError, match=r"^the reproduction gate failed"):
        copy.analyse()


@pytest.mark.parametrize(
    ("name", "counted", "pointer"),
    [
        (
            "shapley.json",
            "shapley_differing",
            ("metrics", "collision_indicator", "values", "localization_shape"),
        ),
        (
            "shapley.json",
            "shapley_differing",
            ("metrics", "intervention_duration_s", "efficiency_max_abs_residual"),
        ),
        ("evaluation.json", "evaluation_differing", ("configurations", 1, "simulated_seconds")),
        (
            "evaluation.json",
            "evaluation_differing",
            ("configurations", 1, "mean_intervention_duration_s"),
        ),
    ],
)
def test_the_reproduction_gate_fails_on_a_shapley_value_or_an_evaluation_field_one_ulp_away(
    release: Release, tmp_path: Path, name: str, counted: str, pointer: tuple[Any, ...]
) -> None:
    copy = copied_evidence(release, tmp_path)

    def move(document: dict[str, Any]) -> None:
        parent = document
        for key in pointer[:-1]:
            parent = parent[key]
        parent[pointer[-1]] = one_ulp_up(parent[pointer[-1]])
        if name == "evaluation.json":
            # The released model holds the total to the sum of the rows; the gate
            # does not compare the total.
            document["simulated_seconds"] = sum(
                row["simulated_seconds"] for row in document["configurations"]
            )

    rewrite(copy, name, move)

    result = copy.gate()

    assert not result.passed
    assert {key: count for key, count in result.counts.items() if key.endswith("_differing")} == {
        "intervals_differing": 0,
        "shapley_differing": 0,
        "evaluation_differing": 0,
        counted: 1,
    }


@pytest.mark.parametrize("field", ["collisions", "contacts_not_at_fault"])
def test_the_reproduction_gate_fails_on_an_evaluation_count_that_differs(
    release: Release, tmp_path: Path, field: str
) -> None:
    copy = copied_evidence(release, tmp_path)

    def move(document: dict[str, Any]) -> None:
        row = document["configurations"][0]
        row[field] += 1
        if field == "collisions":
            row["collisions_vehicle"] += 1

    rewrite(copy, "evaluation.json", move)

    result = copy.gate()

    assert not result.passed
    assert result.counts["evaluation_differing"] == 1
    assert result.local_detail[0].startswith(f"evaluation.json /configurations/no_aeb/{field} is ")


def test_the_reproduction_gate_does_not_compare_evaluation_fields_the_plan_does_not_name(
    release: Release, tmp_path: Path
) -> None:
    copy = copied_evidence(release, tmp_path)

    def move(document: dict[str, Any]) -> None:
        document["configurations"][0]["max_abs_jerk_mps3"] += 1.0

    rewrite(copy, "evaluation.json", move)

    assert copy.gate().passed


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("remove", "is not in the evidence"),
        ("add", "is not recomputed from the records"),
    ],
)
def test_the_reproduction_gate_counts_a_configuration_the_evidence_lacks_or_adds(
    release: Release, tmp_path: Path, change: str, message: str
) -> None:
    copy = copied_evidence(release, tmp_path)

    def edit(document: dict[str, Any]) -> None:
        intervals = document["intervals"]
        if change == "remove":
            del intervals["latency-high"]
        else:
            intervals["latency-highest"] = intervals["latency-high"]

    rewrite(copy, "intervals.json", edit)

    result = copy.gate()

    assert not result.passed
    assert result.counts["intervals_differing"] == 4 * 6
    assert result.counts["intervals_values"] == 26 * 4 * 6 + (4 * 6 if change == "add" else 0)
    assert all(line.endswith(message) for line in result.local_detail)


def test_the_reproduction_gate_fails_when_the_hash_list_sha256_differs(release: Release) -> None:
    result = release.gate(expected_hashes_sha256="0" * 64)

    assert not result.passed
    assert dict(result.counts) == {"released_hash_list_refused": 1}
    assert file_sha256(release.released_hashes) in result.local_detail[0]
    with pytest.raises(ValueError, match=r"^the reproduction gate failed"):
        release.analyse(expected_hashes_sha256="0" * 64)


def test_the_expected_hash_list_is_the_one_the_addendum_plan_states_unless_named(
    release: Release,
) -> None:
    result = reproduce_released(
        release.released_root, release.released_hashes, release.evidence_dir, release.manifest
    )

    assert RELEASED_OUTPUT_HASHES_SHA256 == (
        "47439aad52f112ff2d3e1142cc8530ddd678c5ae5e58a77416beccfe8c730db0"
    )
    assert not result.passed
    assert dict(result.counts) == {"released_hash_list_refused": 1}
    assert RELEASED_OUTPUT_HASHES_SHA256 in result.local_detail[0]
    with pytest.raises(ValueError, match=r"^the reproduction gate failed"):
        analyse_addendum(
            release.released_root,
            release.released_hashes,
            release.evidence_dir,
            release.manifest,
            release.eligibility,
        )


def test_the_reproduction_gate_recomputes_nothing_from_records_that_differ_from_the_list(
    release: Release, tmp_path: Path
) -> None:
    root = Path(shutil.copytree(release.released_root, tmp_path / "released"))
    log = root / "run.log"
    log.write_bytes(log.read_bytes().replace(b"released", b"RELEASED"))

    result = release.gate(released_root=root)

    assert not result.passed
    assert dict(result.counts) == {**RELEASED_RECORD_COUNTS, "released_hash_mismatches": 1}
    assert result.local_detail == ("run.log does not have the listed SHA-256",)


# --------------------------------------------------------------------------
# The summary: the gate it records, and what it does not hold
# --------------------------------------------------------------------------


def test_the_summary_records_the_reproduction_gate_and_the_hash_list_sha256(
    release: Release, passing_gate: GateResult, summary: AttributionAddendumV1
) -> None:
    assert summary.schema_version == "aeb-attribution-addendum/v1"
    assert summary.reproduction_gate == StudyGateV1(
        gate="reproduction", passed=True, counts=dict(passing_gate.counts)
    )
    assert summary.released_output_hashes_sha256 == file_sha256(release.released_hashes)
    assert summary.protocol_sha256 == PROTOCOL_SHA
    assert summary.cohort_manifest_sha256 == membership_sha256(load_manifest(release.manifest))
    assert summary.cohort_size == 6
    assert summary.common_valid_tokens == 6
    assert summary.bootstrap.model_dump() == {
        "cluster": "family-log",
        "clusters": 5,
        "resamples": 5000,
        "seed": 20260831,
    }


def test_the_summary_round_trips_through_its_model(summary: AttributionAddendumV1) -> None:
    assert AttributionAddendumV1.model_validate_json(summary.model_dump_json()) == summary


def keys(value: Any) -> Iterator[str]:
    """Every key of a parsed JSON value, at every depth."""

    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys(item)


FORBIDDEN_FIELD = re.compile(
    r"(^|_)p(_|$)|p_value|pvalue|holm|reject|classif|support|contradict|rank|decision"
)


def test_the_summary_has_no_p_value_or_classification_field(dumped: dict[str, Any]) -> None:
    schema = AttributionAddendumV1.model_json_schema()
    field_names = {
        name
        for definition in (schema, *schema.get("$defs", {}).values())
        for name in definition.get("properties", {})
    }

    assert field_names
    assert not [name for name in field_names if FORBIDDEN_FIELD.search(name)]
    assert not [name for name in keys(dumped) if FORBIDDEN_FIELD.search(name)]


def test_the_collision_game_sentence_is_fixed_and_follows_the_duration_game(
    dumped: dict[str, Any],
) -> None:
    text = json.dumps(dumped, indent=2, sort_keys=True)
    games = dumped["games"]

    assert text.count(COLLISION_GAME_SENTENCE) == 1
    assert [game["game"] for game in games] == ["intervention_duration_s", "collision_indicator"]
    assert games[0]["caution"] is None
    assert games[1]["caution"] == COLLISION_GAME_SENTENCE


# --------------------------------------------------------------------------
# The Shapley values and their differences
# --------------------------------------------------------------------------

SIMULTANEOUS = 1 - 0.05 / 6


def released_values(release: Release, game: str) -> Mapping[str, float]:
    values: Mapping[str, float] = release.evidence("shapley.json")["metrics"][game]["values"]
    return values


def planted_value(game: str, token: str, channel: str) -> float:
    if game == "intervention_duration_s":
        return DURATION_EFFECT_S[token][channel]
    return COLLISION_EFFECT[token][channel] / 3


def game_entry(summary: AttributionAddendumV1, game: str) -> Any:
    (entry,) = [item for item in summary.games if item.game == game]
    return entry


@pytest.mark.parametrize("game", ["intervention_duration_s", "collision_indicator"])
def test_the_eight_shapley_values_are_the_released_values_with_95_percent_intervals(
    release: Release, summary: AttributionAddendumV1, game: str
) -> None:
    values = game_entry(summary, game).shapley_values

    assert set(values) == set(CHANNELS)
    for channel in CHANNELS:
        assert values[channel].estimate == released_values(release, game)[channel]
        assert [interval.confidence for interval in values[channel].intervals] == [0.95]
        assert values[channel].computed_before_plan is True


@pytest.mark.parametrize("game", ["intervention_duration_s", "collision_indicator"])
def test_the_localization_shape_differences_have_simultaneous_and_95_percent_intervals(
    release: Release, summary: AttributionAddendumV1, game: str
) -> None:
    released = released_values(release, game)
    differences = game_entry(summary, game).localization_shape_differences

    assert [(item.plus, item.minus) for item in differences] == [
        ("localization_shape", "dropout"),
        ("localization_shape", "latency"),
        ("localization_shape", "track_instability"),
    ]
    for item in differences:
        assert item.estimate == released[item.plus] - released[item.minus]
        assert item.estimate == pytest.approx(
            sum(
                planted_value(game, token, item.plus) - planted_value(game, token, item.minus)
                for token in TOKENS
            )
            / len(TOKENS),
            abs=1e-12,
        )
        assert [interval.confidence for interval in item.intervals] == [SIMULTANEOUS, 0.95]
        simultaneous, ordinary = item.intervals
        assert simultaneous.low <= ordinary.low <= ordinary.high <= simultaneous.high
        assert item.computed_before_plan is False


@pytest.mark.parametrize("game", ["intervention_duration_s", "collision_indicator"])
def test_the_other_three_pairs_per_game_have_95_percent_intervals(
    release: Release, summary: AttributionAddendumV1, game: str
) -> None:
    released = released_values(release, game)
    differences = game_entry(summary, game).other_differences

    assert [(item.plus, item.minus) for item in differences] == [
        ("dropout", "latency"),
        ("dropout", "track_instability"),
        ("latency", "track_instability"),
    ]
    for item in differences:
        assert item.estimate == released[item.plus] - released[item.minus]
        assert [interval.confidence for interval in item.intervals] == [0.95]
        assert item.computed_before_plan is False


def test_every_interval_is_drawn_from_one_family_stratified_log_cluster_bootstrap(
    release: Release, summary: AttributionAddendumV1
) -> None:
    """(family, log) clusters, 5,000 draws from seed 20260831, one draw for everything."""

    weights = cluster_bootstrap_weights(TOKENS, FAMILY_OF, LOG_OF, resamples=5000, seed=20260831)
    values = per_token_shapley(load_formal_results(release.released_root), TOKENS)
    game = "intervention_duration_s"
    phi = {
        channel: {token: values[token][game][channel] for token in TOKENS} for channel in CHANNELS
    }
    entry = game_entry(summary, game)
    difference = entry.localization_shape_differences[1]

    assert entry.shapley_values["latency"].intervals[0].model_dump() == dict(
        zip(
            ("confidence", "low", "high"),
            (0.95, *percentile_interval(weighted_mean(weights, phi["latency"]), 0.95)),
        )
    )
    draws = weighted_mean(
        weights,
        {token: phi["localization_shape"][token] - phi["latency"][token] for token in TOKENS},
    )
    assert [interval.model_dump() for interval in difference.intervals] == [
        dict(zip(("confidence", "low", "high"), (level, *percentile_interval(draws, level))))
        for level in (SIMULTANEOUS, 0.95)
    ]


def test_every_estimate_is_the_cohort_value_whatever_the_draws(
    release: Release, summary: AttributionAddendumV1, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Draws that hold walk-a alone move every interval onto walk-a's value, and no estimate."""

    def walk_a_alone(tokens: tuple[str, ...], *args: object, **kwargs: object) -> BootstrapWeights:
        ordered = tuple(sorted(tokens))
        weights = np.zeros((3, len(ordered)), dtype=np.int64)
        weights[:, ordered.index("walk-a")] = 1
        return BootstrapWeights(tokens=ordered, weights=weights)

    monkeypatch.setattr("aebrisk.study.addendum.cluster_bootstrap_weights", walk_a_alone)

    collapsed = release.analyse()

    for cohort_game, game in zip(summary.games, collapsed.games):
        for channel in CHANNELS:
            value = game.shapley_values[channel]
            (interval,) = value.intervals
            assert value.estimate == cohort_game.shapley_values[channel].estimate
            assert interval.low == interval.high
            assert interval.low == pytest.approx(
                planted_value(game.game, "walk-a", channel), abs=1e-12
            )
        assert [item.estimate for item in game.localization_shape_differences] == [
            item.estimate for item in cohort_game.localization_shape_differences
        ]
        assert [item.estimate for item in game.other_differences] == [
            item.estimate for item in cohort_game.other_differences
        ]
    assert [item.estimate for item in collapsed.configuration_contrasts] == [
        item.estimate for item in summary.configuration_contrasts
    ]
    # walk-a collides without AEB in every replicate and never with the oracle.
    oracle_minus_no_aeb = collapsed.configuration_contrasts[0]
    assert oracle_minus_no_aeb.metric == "collision_indicator"
    assert oracle_minus_no_aeb.estimate == pytest.approx(2 / 6 - 3 / 6, abs=1e-12)
    assert (oracle_minus_no_aeb.intervals[0].low, oracle_minus_no_aeb.intervals[0].high) == (
        -1.0,
        -1.0,
    )


# --------------------------------------------------------------------------
# The descriptive set
# --------------------------------------------------------------------------

#: Worked out from the planted outcomes. The oracle collides on two tokens and
#: no_aeb on three; the oracle brakes 2 s in every replicate and no_aeb never.
ORACLE_EXPOSURE_S = 2 * 3 * COLLIDED_EXPOSURE_S + 4 * 3 * FULL_EXPOSURE_S
NO_AEB_EXPOSURE_S = 3 * 3 * COLLIDED_EXPOSURE_S + 3 * 3 * FULL_EXPOSURE_S
ORACLE_MINUS_NO_AEB = {
    "collision_indicator": 2 / 6 - 3 / 6,
    "braking_share": 6 * 3 * ORACLE_DURATION_S / ORACLE_EXPOSURE_S,
    "not_at_fault_contact_rate": 1000 * (4 / ORACLE_EXPOSURE_S - 1 / NO_AEB_EXPOSURE_S),
}


def test_the_configuration_contrasts_are_the_four_pairs_on_three_outcomes(
    summary: AttributionAddendumV1,
) -> None:
    contrasts = summary.configuration_contrasts

    assert [(item.plus, item.minus, item.metric) for item in contrasts] == [
        (plus, minus, metric)
        for plus, minus in (
            ("oracle_aeb", "no_aeb"),
            ("coalition-none", "oracle_aeb"),
            (FULL_COALITION, "coalition-none"),
            ("localization_shape-medium", "coalition-none"),
        )
        for metric in ("collision_indicator", "braking_share", "not_at_fault_contact_rate")
    ]
    for item in contrasts:
        assert [interval.confidence for interval in item.intervals] == [0.95]
    assert {item.metric: item.estimate for item in contrasts[:3]} == pytest.approx(
        ORACLE_MINUS_NO_AEB, abs=1e-12
    )


def test_a_configuration_contrast_is_a_difference_of_ratios_of_sums(
    summary: AttributionAddendumV1,
) -> None:
    """Braking share over the full coalition: its exposure differs by token, so a mean of
    per-token shares would give another number."""

    full = {token: planted_braking(FULL_COALITION, token) for token in TOKENS}
    none = {token: planted_braking("coalition-none", token) for token in TOKENS}
    expected = sum(duration for duration, _ in full.values()) / sum(
        exposure for _, exposure in full.values()
    ) - sum(duration for duration, _ in none.values()) / sum(
        exposure for _, exposure in none.values()
    )
    (item,) = [
        item
        for item in summary.configuration_contrasts
        if (item.plus, item.metric) == (FULL_COALITION, "braking_share")
    ]

    assert item.estimate == pytest.approx(expected, abs=1e-12)
    assert item.estimate != pytest.approx(
        np.mean([d / e for d, e in full.values()]) - np.mean([d / e for d, e in none.values()]),
        abs=1e-6,
    )


def planted_braking(cell: str, token: str) -> tuple[float, float]:
    """A token's summed intervention duration and exposure over its three replicates."""

    outcomes = [planted(cell, token, replicate, False) for replicate in range(3)]
    return (
        sum(outcome["duration"] for outcome in outcomes),
        sum(
            COLLIDED_EXPOSURE_S if outcome["collided"] else FULL_EXPOSURE_S for outcome in outcomes
        ),
    )


def test_avoided_and_induced_collisions_are_token_counts_with_clopper_pearson_intervals(
    summary: AttributionAddendumV1,
) -> None:
    collisions = summary.oracle_collisions

    assert collisions.avoided.model_dump() == dict(
        zip(("events", "tokens", "confidence", "low", "high"), (2, 6, 0.95, *clopper_pearson(2, 6)))
    )
    assert collisions.induced.model_dump() == dict(
        zip(("events", "tokens", "confidence", "low", "high"), (1, 6, 0.95, *clopper_pearson(1, 6)))
    )
    # One contact on lead-b's first replicate and one in each of walk-a's three.
    assert collisions.oracle_aeb_contacts_not_at_fault == 4


def test_avoided_and_induced_that_are_not_token_counts_are_refused(tmp_path: Path) -> None:
    """The plan's counts rest on no_aeb and oracle_aeb giving one outcome per token."""

    variant = build_release(tmp_path, nondeterministic=True)

    assert variant.gate().passed
    with pytest.raises(ValueError, match="differ across replicates"):
        variant.analyse()


def test_an_induced_collision_is_one_on_a_token_where_no_aeb_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With cut-b among the oracle's collisions, lead-b and cut-b are induced. On cut-a
    both collide, so it is neither avoided nor induced."""

    monkeypatch.setitem(globals(), "ORACLE_COLLISIONS", (*ORACLE_COLLISIONS, "cut-b"))

    collisions = build_release(tmp_path).analyse().oracle_collisions

    assert (collisions.avoided.events, collisions.induced.events) == (2, 2)


def test_brake_activations_per_hour_cover_every_aeb_configuration(
    summary: AttributionAddendumV1,
) -> None:
    rates = {item.configuration_id: item for item in summary.brake_activations}

    assert list(rates) == [cell for cell in FORMAL_IDS if cell != "no_aeb"]
    # The oracle matches each of its 18 interventions with itself; lead-c's third
    # replicate adds one false intervention in every cell with an AEB.
    assert (rates["oracle_aeb"].brake_activations, rates["oracle_aeb"].simulated_seconds) == (
        19,
        ORACLE_EXPOSURE_S,
    )
    assert rates["oracle_aeb"].per_hour == 3600.0 * 19 / ORACLE_EXPOSURE_S
    assert (rates["latency-high"].brake_activations, rates["latency-high"].simulated_seconds) == (
        5,
        6 * 3 * FULL_EXPOSURE_S,
    )
    assert rates["latency-high"].per_hour == 3600.0 * 5 / (6 * 3 * FULL_EXPOSURE_S)
    assert rates["coalition-none"].brake_activations == 1


def test_zero_event_configurations_get_clopper_pearson_intervals_overall_and_per_family(
    summary: AttributionAddendumV1,
) -> None:
    zero = summary.zero_event_configurations

    assert [item.configuration_id for item in zero] == list(HIGH_CELLS)
    for item in zero:
        assert item.overall.model_dump() == dict(
            zip(
                ("events", "tokens", "confidence", "low", "high"),
                (0, 6, 0.95, *clopper_pearson(0, 6)),
            )
        )
        assert item.overall.low == 0.0
        assert item.overall.high == pytest.approx(1 - 0.025 ** (1 / 6), abs=1e-15)
        assert {family: (p.events, p.tokens) for family, p in item.by_family.items()} == {
            "lead_or_stopping": (0, 3),
            "cut_in_or_crossing": (0, 2),
            "pedestrian_or_crosswalk": (0, 1),
        }
        assert item.by_family["cut_in_or_crossing"].high == clopper_pearson(0, 2)[1]


def test_matched_onset_delays_are_described_for_coalition_none_and_the_latency_cells(
    summary: AttributionAddendumV1,
) -> None:
    delays = {
        item.configuration_id: item.matched_onset_delays_s for item in summary.matched_onset_delays
    }

    assert list(delays) == ["coalition-none", *LATENCY_CELLS]
    assert delays["coalition-none"].model_dump() == {
        "count": 0,
        "median": None,
        "lower_quartile": None,
        "upper_quartile": None,
    }
    assert delays["latency-low"].model_dump() == {
        "count": 1,
        "median": 1.0,
        "lower_quartile": 1.0,
        "upper_quartile": 1.0,
    }
    # 0.2, 0.4, 0.6 and 0.8 s, with numpy's linear interpolation.
    assert delays["latency-high"].count == 4
    assert (
        delays["latency-high"].lower_quartile,
        delays["latency-high"].median,
        delays["latency-high"].upper_quartile,
    ) == pytest.approx((0.35, 0.5, 0.65), abs=1e-12)


def test_first_stop_distances_and_ego_speeds_at_counted_collisions(
    summary: AttributionAddendumV1,
) -> None:
    rows = {item.configuration_id: item for item in summary.stops_and_collision_speeds}

    assert list(rows) == list(FORMAL_IDS)
    assert rows["oracle_aeb"].first_stop_distance_m.model_dump() == {
        "count": 6,
        "median": 15.0,
        "lower_quartile": 10.0,
        "upper_quartile": 20.0,
    }
    assert rows["no_aeb"].first_stop_distance_m.count == 0
    # sqrt(2E / 1500) returns each planted ego speed: 3, 4 and 5 m/s, three times each.
    assert rows["no_aeb"].ego_speed_at_collision_mps.model_dump() == {
        "count": 9,
        "median": 4.0,
        "lower_quartile": 3.0,
        "upper_quartile": 5.0,
    }
    assert rows["latency-high"].ego_speed_at_collision_mps.count == 0


def test_the_summary_labels_what_was_computed_before_the_plan(
    summary: AttributionAddendumV1,
) -> None:
    """Section 1 of the addendum plan lists what scratch analyses computed first."""

    for game in summary.games:
        assert {item.computed_before_plan for item in game.shapley_values.values()} == {True}
    assert {item.metric: item.computed_before_plan for item in summary.configuration_contrasts} == {
        "collision_indicator": True,
        "braking_share": True,
        "not_at_fault_contact_rate": False,
    }
    assert summary.oracle_collisions.computed_before_plan is True
    assert [
        item.configuration_id for item in summary.brake_activations if item.computed_before_plan
    ] == ["oracle_aeb"]
    assert {item.computed_before_plan for item in summary.zero_event_configurations} == {True}
    assert {item.computed_before_plan for item in summary.matched_onset_delays} == {True}
    assert {item.computed_before_plan for item in summary.stops_and_collision_speeds} == {False}


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------


def changed(dumped: dict[str, Any], change: Any) -> dict[str, Any]:
    document = json.loads(json.dumps(dumped))
    change(document)
    return document


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["games"].reverse(), "the duration game and then the collision game"),
        (lambda d: d["games"].pop(), "the duration game and then the collision game"),
        (lambda d: d["games"][0].update(caution=COLLISION_GAME_SENTENCE), "caution"),
        (lambda d: d["games"][1].update(caution=None), "caution"),
        (lambda d: d["games"][1].update(caution=COLLISION_GAME_SENTENCE[:-1]), "caution"),
    ],
)
def test_the_document_holds_the_two_games_in_order_with_the_sentence_on_the_collision_game(
    dumped: dict[str, Any], change: Any, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        AttributionAddendumV1.model_validate(changed(dumped, change))


def test_the_document_refuses_an_inverted_interval(dumped: dict[str, Any]) -> None:
    def invert(document: dict[str, Any]) -> None:
        interval = document["configuration_contrasts"][0]["intervals"][0]
        interval["low"], interval["high"] = interval["high"] + 1.0, interval["low"]

    with pytest.raises(ValidationError, match="low must not exceed high"):
        AttributionAddendumV1.model_validate(changed(dumped, invert))


@pytest.mark.parametrize(
    "distribution",
    [
        {"count": 0, "median": 1.0, "lower_quartile": None, "upper_quartile": None},
        {"count": 2, "median": 1.0, "lower_quartile": None, "upper_quartile": 1.0},
    ],
)
def test_the_document_refuses_a_distribution_whose_values_do_not_match_its_count(
    dumped: dict[str, Any], distribution: dict[str, Any]
) -> None:
    def replace(document: dict[str, Any]) -> None:
        document["matched_onset_delays"][0]["matched_onset_delays_s"] = distribution

    with pytest.raises(ValidationError, match="quartiles exactly when"):
        AttributionAddendumV1.model_validate(changed(dumped, replace))


def test_the_document_refuses_more_events_than_tokens(dumped: dict[str, Any]) -> None:
    def replace(document: dict[str, Any]) -> None:
        document["oracle_collisions"]["avoided"]["events"] = 7

    with pytest.raises(ValidationError, match="events cannot exceed tokens"):
        AttributionAddendumV1.model_validate(changed(dumped, replace))


# --------------------------------------------------------------------------
# Checks that run the analysis themselves
# --------------------------------------------------------------------------


def test_every_summary_check_holds_on_a_summary_computed_inside_the_test(
    release: Release,
) -> None:
    """The module's `summary` is computed once, inside the first test that asks for it, so
    the checks that read it afterwards never run the analysis themselves. This test runs
    the analysis and holds its own summary to every one of those checks."""

    summary = release.analyse()
    dumped = summary.model_dump(mode="json")

    test_the_summary_records_the_reproduction_gate_and_the_hash_list_sha256(
        release, release.gate(), summary
    )
    test_the_summary_round_trips_through_its_model(summary)
    test_the_summary_has_no_p_value_or_classification_field(dumped)
    test_the_collision_game_sentence_is_fixed_and_follows_the_duration_game(dumped)
    for game in ("intervention_duration_s", "collision_indicator"):
        test_the_eight_shapley_values_are_the_released_values_with_95_percent_intervals(
            release, summary, game
        )
        test_the_localization_shape_differences_have_simultaneous_and_95_percent_intervals(
            release, summary, game
        )
        test_the_other_three_pairs_per_game_have_95_percent_intervals(release, summary, game)
    test_every_interval_is_drawn_from_one_family_stratified_log_cluster_bootstrap(release, summary)
    test_the_configuration_contrasts_are_the_four_pairs_on_three_outcomes(summary)
    test_a_configuration_contrast_is_a_difference_of_ratios_of_sums(summary)
    test_avoided_and_induced_collisions_are_token_counts_with_clopper_pearson_intervals(summary)
    test_brake_activations_per_hour_cover_every_aeb_configuration(summary)
    test_zero_event_configurations_get_clopper_pearson_intervals_overall_and_per_family(summary)
    test_matched_onset_delays_are_described_for_coalition_none_and_the_latency_cells(summary)
    test_first_stop_distances_and_ego_speeds_at_counted_collisions(summary)
    test_the_summary_labels_what_was_computed_before_the_plan(summary)


def test_a_gate_that_recomputes_nothing_is_the_reproduction_gate_and_did_not_pass(
    release: Release,
) -> None:
    result = release.gate(expected_hashes_sha256="0" * 64)

    assert result.gate == "reproduction"
    assert result.passed is False


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"protocol_sha256": "a" * 64},
            "formal documents do not match the manifest protocol hash",
        ),
        (
            {"split": "development"},
            "completion marker cohort hash does not match the manifest",
        ),
    ],
)
def test_the_reproduction_gate_holds_the_records_to_the_manifest_it_is_given(
    release: Release, tmp_path: Path, change: dict[str, str], message: str
) -> None:
    """Same tokens in the same families, but another protocol or split: not the cohort the
    records were run on, and the gate refuses them."""

    other = write_json(tmp_path / "manifest.json", {**manifest_document("evaluation"), **change})

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        release.gate(manifest=other)


def test_the_gate_detail_names_the_shapley_json_path_of_each_differing_value(
    release: Release, tmp_path: Path
) -> None:
    copy = copied_evidence(release, tmp_path)
    metrics = copy.evidence("shapley.json")["metrics"]
    value = metrics["collision_indicator"]["values"]["localization_shape"]
    residual = metrics["intervention_duration_s"]["efficiency_max_abs_residual"]

    def move(document: dict[str, Any]) -> None:
        moved = document["metrics"]
        moved["collision_indicator"]["values"]["localization_shape"] = one_ulp_up(value)
        moved["intervention_duration_s"]["efficiency_max_abs_residual"] = one_ulp_up(residual)

    rewrite(copy, "shapley.json", move)

    assert copy.gate().local_detail == (
        f"shapley.json /metrics/collision_indicator/values/localization_shape is "
        f"{one_ulp_up(value)!r}, and the records give {value!r}",
        f"shapley.json /metrics/intervention_duration_s/efficiency_max_abs_residual is "
        f"{one_ulp_up(residual)!r}, and the records give {residual!r}",
    )


def test_the_addendum_refuses_a_failed_gate_in_these_words(release: Release) -> None:
    message = (
        "the reproduction gate failed with counts {'released_hash_list_refused': 1}; the "
        "addendum computes nothing from records or evidence it cannot reproduce"
    )

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        release.analyse(expected_hashes_sha256="0" * 64)


def test_avoided_and_induced_shares_between_token_counts_are_refused_in_these_words(
    tmp_path: Path,
) -> None:
    """lead-b collides in every oracle replicate and only in the first no_aeb one, so two of
    its three replicates are induced collisions."""

    variant = build_release(tmp_path, nondeterministic=True)
    message = (
        f"a token's induced collisions differ across replicates ({2 / 3}); the addendum "
        "counts them as tokens because no_aeb and oracle_aeb are deterministic"
    )

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        variant.analyse()


def test_a_replicate_counted_in_two_collision_columns_is_one_collided_replicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record may count a vehicle and an object at once, and the released evaluation
    counts that replicate as collided. Here lead-a's no_aeb collisions are counted in both
    columns: the oracle still avoids them, and each still has an ego speed."""

    one_column = record

    def two_columns(
        cell: str, token: str, replicate: int, nondeterministic: bool
    ) -> AEBScenarioResultV2:
        result = one_column(cell, token, replicate, nondeterministic)
        if cell == "no_aeb" and token == "lead-a":
            return result.model_copy(update={"collision_object": 1})
        return result

    monkeypatch.setitem(globals(), "record", two_columns)

    variant = build_release(tmp_path).analyse()
    rows = {item.configuration_id: item for item in variant.stops_and_collision_speeds}

    assert (variant.oracle_collisions.avoided.events, variant.oracle_collisions.induced.events) == (
        2,
        1,
    )
    assert rows["no_aeb"].ego_speed_at_collision_mps.model_dump() == {
        "count": 9,
        "median": 4.0,
        "lower_quartile": 3.0,
        "upper_quartile": 5.0,
    }
