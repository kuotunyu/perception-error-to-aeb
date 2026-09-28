"""The study file of the policy v2 study, and the loader that holds it to the plan.

`configs/experiments/aeb_policy_v2_study.yaml` records what
`docs/studies/aeb-policy-v2/analysis-plan.md` fixed before any arm ran. These
tests load the committed file, show that the replication arm is the released
matrix restricted to the study's cells, and show that the loader refuses a file
that describes a study the plan does not.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from pydantic import ValidationError

from aebrisk.attribution.factorial import formal_configurations
from aebrisk.cohort.manifest import load_manifest
from aebrisk.study.definition import (
    StudyArmV1,
    StudyDefinitionV1,
    arm_configurations,
    drive_of,
    load_study,
    study_sha256,
    token_log_map,
)

ROOT = Path(__file__).resolve().parents[3]
STUDY = ROOT / "configs" / "experiments" / "aeb_policy_v2_study.yaml"
COHORT = ROOT / "docs" / "evidence" / "nuplan_aeb_v2" / "cohort"
MANIFEST = COHORT / "evaluation.json"
ELIGIBILITY = COHORT / "evaluation-eligibility.json"

FULL_COALITION = "coalition-dropout+localization_shape+latency+track_instability"
#: The eight cells of the analysis plan's section 4.1, in the formal matrix's order.
STUDY_CELLS = (
    "no_aeb",
    "oracle_aeb",
    "dropout-medium",
    "localization_shape-medium",
    "latency-medium",
    "track_instability-medium",
    "coalition-none",
    FULL_COALITION,
)
#: Each arm's policy, keying and velocity estimate, and the cells it runs.
ARMS = {
    "A-v1-replication": ("v1", "dropout-keyed", "finite-difference", STUDY_CELLS),
    "B-v2-gated": ("v2", "dropout-keyed", "finite-difference", STUDY_CELLS),
    "C-v1-kalman": ("v1", "dropout-keyed", "cv-kalman", STUDY_CELLS),
    "D-v2-kalman": ("v2", "dropout-keyed", "cv-kalman", STUDY_CELLS),
    "E-v2-channel-rng": (
        "v2",
        "channel-independent",
        "finite-difference",
        ("no_aeb", "oracle_aeb", "dropout-medium", FULL_COALITION),
    ),
}
UNKNOWN_CELL = "dropout-extreme"


def committed_document() -> dict[str, Any]:
    """A fresh, editable parse of the committed study file."""

    document: dict[str, Any] = yaml.safe_load(STUDY.read_text(encoding="utf-8"))
    return document


def write_study(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def edited_study(tmp_path: Path, edit: Callable[[dict[str, Any]], object]) -> Path:
    document = committed_document()
    edit(document)
    return write_study(tmp_path, document)


# --------------------------------------------------------------------------
# The committed file
# --------------------------------------------------------------------------


def test_the_committed_study_file_loads() -> None:
    study = load_study(STUDY)

    assert isinstance(study, StudyDefinitionV1)
    assert study.schema_version == "aeb-study/v1"
    assert study.study_id == "aeb-policy-v2"
    assert study.analysis_plan == "docs/studies/aeb-policy-v2/analysis-plan.md"
    assert study.replicates == (0, 1, 2)
    assert study.cells == STUDY_CELLS
    assert {
        arm.id: (arm.aeb_policy, arm.rng_scheme, arm.velocity_estimator, arm.cells)
        for arm in study.arms
    } == ARMS
    assert [arm.id for arm in study.arms] == list(ARMS)
    assert (study.environment.python_version, study.environment.numpy_version) == (
        "3.9.19",
        "1.23.4",
    )
    assert study.bootstrap.model_dump() == {
        "seed": 20260831,
        "resamples": 5000,
        "cluster": "family-log",
        "primary_confidence": 0.99,
        "secondary_confidence": 0.95,
    }
    assert study.sign_flip.model_dump() == {
        "unit": "log",
        "enumerate_up_to": 20,
        "random_flips": 100000,
        "seed": 20260831,
    }
    assert [family.id for family in study.hypothesis_families] == ["primary", "Q2", "Q3-check"]
    assert [len(family.hypotheses) for family in study.hypothesis_families] == [5, 4, 10]
    assert [hypothesis.id for hypothesis in study.hypothesis_families[0].hypotheses] == [
        "H1",
        "H2",
        "H3",
        "H4",
        "H5",
    ]


def test_arm_a_equals_the_formal_cells_with_defaults() -> None:
    """The replication arm is the released matrix restricted to the study's cells."""

    formal = {
        configuration.configuration_id: configuration for configuration in formal_configurations()
    }

    configurations = arm_configurations(load_study(STUDY), "A-v1-replication")

    assert configurations == tuple(formal[cell] for cell in STUDY_CELLS)
    assert {
        (configuration.aeb_policy, configuration.rng_scheme, configuration.velocity_estimator)
        for configuration in configurations
    } == {("v1", "dropout-keyed", "finite-difference")}


@pytest.mark.parametrize("arm_id", list(ARMS))
def test_each_arm_changes_only_its_factors_of_the_formal_cells_in_formal_order(
    arm_id: str,
) -> None:
    aeb_policy, rng_scheme, velocity_estimator, cells = ARMS[arm_id]
    formal = [
        configuration
        for configuration in formal_configurations()
        if configuration.configuration_id in cells
    ]

    assert arm_configurations(load_study(STUDY), arm_id) == tuple(
        dataclasses.replace(
            configuration,
            aeb_policy=aeb_policy,
            rng_scheme=rng_scheme,
            velocity_estimator=velocity_estimator,
        )
        for configuration in formal
    )


def test_an_unknown_arm_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown arm 'F-v3'"):
        arm_configurations(load_study(STUDY), "F-v3")


def test_study_sha256_is_the_hash_of_the_file_bytes(tmp_path: Path) -> None:
    """The bytes as they are on disk, line endings included."""

    path = tmp_path / "study.yaml"
    path.write_bytes(b"schema_version: aeb-study/v1\r\n")

    assert study_sha256(path) == hashlib.sha256(b"schema_version: aeb-study/v1\r\n").hexdigest()
    assert study_sha256(STUDY) == hashlib.sha256(STUDY.read_bytes()).hexdigest()


def test_the_definition_is_strict_and_frozen(tmp_path: Path) -> None:
    study = load_study(STUDY)

    with pytest.raises(ValidationError):
        study.study_id = "another-study"
    with pytest.raises(ValidationError):
        study.arms[0].aeb_policy = "v2"
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_study(edited_study(tmp_path, lambda document: document.update(notes="later")))
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_study(edited_study(tmp_path, lambda document: document["arms"][1].update(seed=1)))


def test_each_setting_accepts_the_edge_of_its_range(tmp_path: Path) -> None:
    document = committed_document()
    document["study_id"] = "s"
    document["bootstrap"].update(seed=0, resamples=1)
    document["sign_flip"].update(seed=0, enumerate_up_to=1, random_flips=1)
    document["hypothesis_families"][2]["hypotheses"] = document["hypothesis_families"][2][
        "hypotheses"
    ][:1]

    study = load_study(write_study(tmp_path, document))

    assert study.study_id == "s"
    assert (study.bootstrap.seed, study.bootstrap.resamples) == (0, 1)
    assert (
        study.sign_flip.seed,
        study.sign_flip.enumerate_up_to,
        study.sign_flip.random_flips,
    ) == (
        0,
        1,
        1,
    )
    assert len(study.hypothesis_families[2].hypotheses) == 1


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        pytest.param(
            lambda document: document["bootstrap"].update(seed=-1),
            "greater than or equal to 0",
            id="negative-seed",
        ),
        pytest.param(
            lambda document: document["sign_flip"].update(seed="20260831"),
            "valid integer",
            id="seed-as-text",
        ),
        pytest.param(
            lambda document: document["bootstrap"].update(resamples=0),
            "greater than or equal to 1",
            id="no-resamples",
        ),
        pytest.param(
            lambda document: document["bootstrap"].update(primary_confidence=1.0),
            "less than 1",
            id="certain-confidence",
        ),
        pytest.param(
            lambda document: document["cv_kalman"].update(measurement_std_m=0.0),
            "greater than 0",
            id="zero-deviation",
        ),
        pytest.param(
            lambda document: document["cv_kalman"].update(process_accel_std_mps2=float("inf")),
            "finite number",
            id="infinite-deviation",
        ),
        pytest.param(
            lambda document: document.update(replicates=["0", "1", "2"]),
            "valid integer",
            id="replicates-as-text",
        ),
        pytest.param(
            lambda document: document["environment"].update(numpy_version="1.23"),
            "should match pattern",
            id="short-version",
        ),
        pytest.param(
            lambda document: document.update(input_databases_sha256="EA" * 32),
            "should match pattern",
            id="upper-case-hash",
        ),
        pytest.param(
            lambda document: document["arms"][0].update(id=""),
            "at least 1 character",
            id="empty-arm-id",
        ),
        pytest.param(
            lambda document: document["hypothesis_families"][2].update(hypotheses=[]),
            "at least 1 item",
            id="empty-family",
        ),
        pytest.param(
            lambda document: document["hypothesis_families"][0]["hypotheses"][4].update(
                predicted_sign=2
            ),
            "Input should be -1, 0 or 1",
            id="unknown-sign",
        ),
        pytest.param(
            lambda document: document["hypothesis_families"][0]["hypotheses"][0].update(
                outcome="braking_seconds"
            ),
            "Input should be 'collision_indicator'",
            id="unknown-outcome",
        ),
        pytest.param(
            lambda document: document["hypothesis_families"][0]["hypotheses"][0].update(
                test="t_test"
            ),
            "Input should be 'bootstrap' or 'sign_flip'",
            id="unknown-test",
        ),
        pytest.param(
            lambda document: document["bootstrap"].update(cluster="token"),
            "Input should be 'family-log'",
            id="token-clusters",
        ),
        pytest.param(
            lambda document: document["sign_flip"].update(unit="family-log"),
            "Input should be 'log'",
            id="cluster-flips",
        ),
        pytest.param(
            lambda document: document.update(schema_version="aeb-study/v2"),
            "Input should be 'aeb-study/v1'",
            id="another-schema",
        ),
    ],
)
def test_each_setting_refuses_a_value_outside_its_range(
    tmp_path: Path, edit: Callable[[dict[str, Any]], object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_study(edited_study(tmp_path, edit))


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "edit",
    [
        # A new list: the arms that run every cell share the study's list through
        # a YAML alias, and this case changes the study's cells alone.
        pytest.param(
            lambda document: document.update(cells=[*document["cells"], UNKNOWN_CELL]),
            id="study-cells",
        ),
        pytest.param(
            lambda document: document["arms"][4]["cells"].append(UNKNOWN_CELL), id="arm-cells"
        ),
        pytest.param(
            lambda document: document["hypothesis_families"][1]["hypotheses"][0].update(
                cell=UNKNOWN_CELL
            ),
            id="hypothesis-cell",
        ),
    ],
)
def test_a_cell_outside_the_formal_matrix_is_refused(
    tmp_path: Path, edit: Callable[[dict[str, Any]], object]
) -> None:
    with pytest.raises(ValueError, match=f"not cells of the formal matrix: \\['{UNKNOWN_CELL}'\\]"):
        load_study(edited_study(tmp_path, edit))


def test_an_arm_without_oracle_aeb_is_refused(tmp_path: Path) -> None:
    """Missed and false interventions are measured against the oracle of the same run."""

    path = edited_study(
        tmp_path, lambda document: document["arms"][4]["cells"].remove("oracle_aeb")
    )

    with pytest.raises(ValueError, match="arm 'E-v2-channel-rng' does not run 'oracle_aeb'"):
        load_study(path)


def test_duplicate_arm_ids_are_refused(tmp_path: Path) -> None:
    path = edited_study(tmp_path, lambda document: document["arms"][4].update(id="D-v2-kalman"))

    with pytest.raises(ValueError, match=r"duplicate arm ids: \['D-v2-kalman'\]"):
        load_study(path)


@pytest.mark.parametrize("replicates", [[0, 1], [0, 1, 2, 3], [1, 2, 3], [2, 1, 0], [0, 1, 1], []])
def test_replicates_other_than_zero_one_two_are_refused(
    tmp_path: Path, replicates: list[int]
) -> None:
    path = edited_study(tmp_path, lambda document: document.update(replicates=replicates))

    with pytest.raises(ValueError, match=r"replicates must be exactly \(0, 1, 2\)"):
        load_study(path)


@pytest.mark.parametrize(
    "name",
    [
        "aeb/policy_v1.yaml",
        "aeb/policy_v2.yaml",
        "errors/formal_v1.yaml",
        "experiments/formal_v1.yaml",
    ],
)
def test_an_input_hash_that_differs_from_the_file_is_refused(tmp_path: Path, name: str) -> None:
    path = edited_study(
        tmp_path, lambda document: document["input_sha256"].update({name: "0" * 64})
    )

    with pytest.raises(ValueError, match=f"{name} has SHA-256 [0-9a-f]{{64}}, but the study"):
        load_study(path)


def test_an_input_hash_refusal_states_both_hashes_and_what_the_arms_would_read(
    tmp_path: Path,
) -> None:
    """The whole message: the file, the hash it has, the hash recorded, and why it matters.

    The committed study file loads, so the hash it records for the matrix is the
    hash the loader computes for it.
    """

    name = "experiments/formal_v1.yaml"
    actual = committed_document()["input_sha256"][name]
    path = edited_study(
        tmp_path, lambda document: document["input_sha256"].update({name: "0" * 64})
    )
    message = (
        f"{name} has SHA-256 {actual}, but the study file records {'0' * 64}; "
        "the arms would not read the inputs the study names"
    )

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        load_study(path)


def test_an_input_named_by_an_absolute_path_is_looked_up_under_the_packaged_configs(
    tmp_path: Path,
) -> None:
    """An input is named by its path under `configs/`, so no name reaches a file outside it.

    The file exists and the study records its true hash, so only where the loader
    looks decides the outcome: the name's parts are joined under the package's
    configs, where no such file exists.
    """

    outside = tmp_path / "policy_v3.yaml"
    outside.write_text("stage: outside\n", encoding="utf-8")
    name = outside.as_posix()
    recorded = hashlib.sha256(outside.read_bytes()).hexdigest()
    path = edited_study(
        tmp_path, lambda document: document["input_sha256"].update({name: recorded})
    )

    assert outside.is_absolute()
    with pytest.raises(FileNotFoundError):
        load_study(path)


@pytest.mark.parametrize(
    ("factor", "value"),
    [("aeb_policy", "v3"), ("rng_scheme", "per-channel"), ("velocity_estimator", "ukf")],
)
def test_an_arm_with_an_unknown_factor_is_refused(tmp_path: Path, factor: str, value: str) -> None:
    """A misspelt factor would file one arm's run under another arm's settings."""

    path = edited_study(tmp_path, lambda document: document["arms"][1].update({factor: value}))

    with pytest.raises(ValueError, match=f"arm 'B-v2-gated': {factor} must be one of"):
        load_study(path)


def test_an_arm_is_validated_on_its_own() -> None:
    with pytest.raises(ValueError, match="does not run 'oracle_aeb'"):
        StudyArmV1(
            id="A-v1-replication",
            aeb_policy="v1",
            rng_scheme="dropout-keyed",
            velocity_estimator="finite-difference",
            cells=("no_aeb",),
        )


# --------------------------------------------------------------------------
# Tokens, logs and drives
# --------------------------------------------------------------------------


def test_the_committed_cohort_maps_344_tokens_to_183_logs_198_clusters_and_101_drives() -> None:
    """The counts section 3 of the analysis plan states for the resampling units."""

    log_by_token = token_log_map(ELIGIBILITY, MANIFEST)
    manifest = load_manifest(MANIFEST)
    family_by_token = {
        token: family for family, tokens in manifest.families.items() for token in tokens
    }
    clusters = {(family_by_token[token], log) for token, log in log_by_token.items()}
    families_per_log = Counter(log for _, log in clusters)

    assert set(log_by_token) == set(family_by_token)
    assert len(log_by_token) == 344
    assert set(log_by_token.values()) == set(manifest.log_names)
    assert len(set(log_by_token.values())) == 183
    assert len(clusters) == 198
    assert len({drive_of(log) for log in log_by_token.values()}) == 101
    assert Counter(family for family, _ in clusters) == {
        "bicycle_or_vru": 8,
        "cut_in_or_crossing": 78,
        "lead_or_stopping": 45,
        "pedestrian_or_crosswalk": 67,
    }
    assert Counter(count for count in families_per_log.values() if count > 1) == {2: 11, 3: 2}
    with pytest.raises(TypeError):
        log_by_token[next(iter(log_by_token))] = "another.db"  # type: ignore[index]


def _eligibility_rows() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(ELIGIBILITY.read_text(encoding="utf-8"))
    return document


def _first_cohort_token() -> str:
    return load_manifest(MANIFEST).families["lead_or_stopping"][0]


def test_a_token_without_an_accepted_row_is_refused(tmp_path: Path) -> None:
    token = _first_cohort_token()
    document = _eligibility_rows()
    for row in document["examined"]:
        if row["scenario_token"] == token:
            row["accepted"] = False
    eligibility = tmp_path / "eligibility.json"
    eligibility.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=f"token '{token}' has 0 accepted rows"):
        token_log_map(eligibility, MANIFEST)


def test_a_token_with_two_accepted_rows_is_refused(tmp_path: Path) -> None:
    token = _first_cohort_token()
    document = _eligibility_rows()
    accepted = next(
        row for row in document["examined"] if row["scenario_token"] == token and row["accepted"]
    )
    document["examined"].append(
        dict(accepted, log_name="2021.06.07.12.01.13_veh-47_00001_00002.db")
    )
    eligibility = tmp_path / "eligibility.json"
    eligibility.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=f"token '{token}' has 2 accepted rows"):
        token_log_map(eligibility, MANIFEST)


def test_a_token_refusal_names_the_file_and_that_exactly_one_row_is_required(
    tmp_path: Path,
) -> None:
    token = _first_cohort_token()
    document = _eligibility_rows()
    for row in document["examined"]:
        if row["scenario_token"] == token:
            row["accepted"] = False
    eligibility = tmp_path / "eligibility.json"
    eligibility.write_text(json.dumps(document), encoding="utf-8")
    message = f"token {token!r} has 0 accepted rows in eligibility.json; exactly one is required"

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        token_log_map(eligibility, MANIFEST)


@pytest.mark.parametrize(
    "log_name",
    ["2021.06.07.12.01.13_veh-47_00730_00915.db", "2021.06.07.12.01.13_veh-47_04492_05024"],
)
def test_drive_of_keeps_the_log_name_up_to_the_vehicle_id(log_name: str) -> None:
    assert drive_of(log_name) == "2021.06.07.12.01.13_veh-47"


@pytest.mark.parametrize(
    "log_name",
    ["2021.06.07.12.01.13_veh-47", "2021.06.07.12.01.13_00730_00915.db", "evaluation.json", ""],
)
def test_drive_of_refuses_a_name_that_is_not_a_log(log_name: str) -> None:
    with pytest.raises(ValueError, match="is not a nuPlan log name"):
        drive_of(log_name)


def test_drive_of_refuses_a_log_name_followed_by_a_line_break() -> None:
    """The whole string must be a log name; a trailing line break is not one."""

    with pytest.raises(ValueError, match="is not a nuPlan log name"):
        drive_of("2021.06.07.12.01.13_veh-47_00730_00915.db\n")
