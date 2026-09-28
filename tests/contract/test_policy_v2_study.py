"""What the policy v2 study holds fixed, checked against the bytes the release published.

The study in `docs/studies/aeb-policy-v2/analysis-plan.md` reruns eight cells of
the released matrix and compares them with the v1.0.0 records token by token.
That comparison means something only while the configuration the release ran
under, and the evidence and claims it published, are the ones the release
shipped. Each file is pinned here by the SHA-256 of its bytes with line endings
normalised to LF, the form Git stores, so a pin holds on any checkout. The
protocol is pinned over its raw CRLF bytes by `test_protocol_hash.py`.

The study file, `configs/experiments/aeb_policy_v2_study.yaml`, is held here to
the released evidence it names, to the two plans, and to the filter the arms run.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path

from aebrisk.cohort.manifest import load_manifest, membership_sha256
from aebrisk.observation.tracking import CV_KALMAN_PARAMETERS
from aebrisk.study.definition import StudyDefinitionV1, load_study

ROOT = Path(__file__).resolve().parents[2]
RELEASED_EVIDENCE = "docs/evidence/nuplan_aeb_v2"
FROZEN_INPUTS = (
    "configs/aeb/policy_v1.yaml",
    "configs/experiments/formal_v1.yaml",
    "configs/errors/formal_v1.yaml",
    "docs/claims.yaml",
    "docs/evidence/nuplan_aeb_v2-NOTICE.md",
)

#: The SHA-256 of each file's LF-normalised bytes at the v1.0.0 tag. The three
#: configuration hashes are the ones the analysis plan states.
RELEASED_SHA256 = {
    "configs/aeb/policy_v1.yaml": "263c4eebf718c8d7b748e4d0ad2d93af6654f17c8ce43a3a4d1144f48547564e",
    "configs/experiments/formal_v1.yaml": "6d7935977ba4bcea6bd1428c2632ee665a30c1a865fe68ba28eadf9554b254ab",
    "configs/errors/formal_v1.yaml": "e13f26aabf749e688fa911fa300e2fa794d32be4e1805443215334398d8f0e4a",
    "docs/claims.yaml": "5b4e44ebc8017f77b835e0adf9c2bc6d8a57f01a8f32c260124ecf7ef207896e",
    "docs/evidence/nuplan_aeb_v2-NOTICE.md": "6da42018ededb6407fec774a861772a8d72c0d827f432edb8dab05a016664270",
    "docs/evidence/nuplan_aeb_v2/cohort/development-eligibility.json": "fbd7fe980b3c28191645197f301eae96277cf3dedeb029927a756a73fe881f88",
    "docs/evidence/nuplan_aeb_v2/cohort/development.json": "6a71e29b26b940a52820c294c1682bd65fc1af1286fea763f13d46b6b9399c3f",
    "docs/evidence/nuplan_aeb_v2/cohort/evaluation-eligibility.json": "b9a78c518582c6f60581b56396140a390b29e1611605262ff8bb76fe68174d84",
    "docs/evidence/nuplan_aeb_v2/cohort/evaluation.json": "76d326690b6ba0666a0db9a452bbb38307877dcf6ba730586838ab6b557762d6",
    "docs/evidence/nuplan_aeb_v2/cohort/smoke.json": "bffc1fb93eb2961a6f84eabd30073eac2059d5828f654f4a172ae6eb40ed2fe1",
    "docs/evidence/nuplan_aeb_v2/evaluation.json": "dee735e1b5bed356a9ebf2d17c5aedda7515a31e5a3e7b7be1d97ed48fa3b404",
    "docs/evidence/nuplan_aeb_v2/exclusions.json": "5a4bc25be368a3f1674204bbe6bc9871597f33f29c3d564320c5856053a9fc13",
    "docs/evidence/nuplan_aeb_v2/family-interventions.json": "417eba86942dc086780e153a07ddf678be9933329bd229930c22437b69902074",
    "docs/evidence/nuplan_aeb_v2/intervals.json": "3e4ad4f0979b7e59c4fce1c9c4cec080092ea63799f79bf1ca7c81b9f55281f2",
    "docs/evidence/nuplan_aeb_v2/replays/bicycle_or_vru--coalition-dropout+localization_shape+latency+track_instability.html": "e41b047c5d812a6a10140891a1ec815d818c412ca1deaaacc7a29efe36753a45",
    "docs/evidence/nuplan_aeb_v2/replays/bicycle_or_vru--coalition-none.html": "2ed65172559f37c438e99b0bfd0ca842c6e1a3117f53a7f1899dbf97a4afe831",
    "docs/evidence/nuplan_aeb_v2/replays/bicycle_or_vru--oracle_aeb.html": "1a87d257e8203d9b30860694cb25745dfbc78fc6bf055959b05ab3a379530921",
    "docs/evidence/nuplan_aeb_v2/replays/cut_in_or_crossing--coalition-dropout+localization_shape+latency+track_instability.html": "b1a82799e277f8f82596a5d86364bab353ecbc51ed95d619fed34dabd3f52308",
    "docs/evidence/nuplan_aeb_v2/replays/cut_in_or_crossing--coalition-none.html": "a97b2ab75d74896721e0490d35ceade7b0c85d5ac875f91b7e9376263e7ab368",
    "docs/evidence/nuplan_aeb_v2/replays/cut_in_or_crossing--oracle_aeb.html": "f9734c0d1e182c94deb9e3bd66a1e84b6c02ef0c8eab467089ec4fc206be3ff1",
    "docs/evidence/nuplan_aeb_v2/replays/lead_or_stopping--coalition-dropout+localization_shape+latency+track_instability.html": "3439fe13e37b03e60ea6574aca21eebcfd6be0351988f6f6ecd9cba0f1cd25b2",
    "docs/evidence/nuplan_aeb_v2/replays/lead_or_stopping--coalition-none.html": "82d65f42bc50d78a0a30a75f1d2f4fac434e4f778a62d764413687e18ef26133",
    "docs/evidence/nuplan_aeb_v2/replays/lead_or_stopping--oracle_aeb.html": "dd70758abb7a9055e58ba10dfd175aeca0d5530712768c0e96c5e1905685fc4d",
    "docs/evidence/nuplan_aeb_v2/replays/pedestrian_or_crosswalk--coalition-dropout+localization_shape+latency+track_instability.html": "10bdaab11c50295604390b30f91383a3beb3d454ef922e0eb543ff193d3001bc",
    "docs/evidence/nuplan_aeb_v2/replays/pedestrian_or_crosswalk--coalition-none.html": "40c5f8a96e0b821217a80d020b1f964c95598f27ddd0714ac09f15b6556f3140",
    "docs/evidence/nuplan_aeb_v2/replays/pedestrian_or_crosswalk--oracle_aeb.html": "3d3cc216197154c5275e5317525ebfc3374cae5b1e79c9dc9312ce3120c5e15e",
    "docs/evidence/nuplan_aeb_v2/shapley.json": "412a42b49ad42235682dd57b60c53876a53cccc3892c19460d0c5cd8678013f5",
}


def lf_normalised_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_frozen_inputs_keep_their_released_bytes() -> None:
    """A changed, added or removed file would compare the study with something never released."""

    evidence = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / RELEASED_EVIDENCE).glob("**/*")
        if path.is_file()
    )

    assert {
        relative: lf_normalised_sha256(ROOT / relative) for relative in (*FROZEN_INPUTS, *evidence)
    } == RELEASED_SHA256


# --------------------------------------------------------------------------
# The study file
# --------------------------------------------------------------------------

STUDY_FILE = ROOT / "configs" / "experiments" / "aeb_policy_v2_study.yaml"
ANALYSIS_PLAN = ROOT / "docs" / "studies" / "aeb-policy-v2" / "analysis-plan.md"
ADDENDUM_PLAN = ROOT / "docs" / "posthoc" / "nuplan_aeb_v2-addendum" / "addendum-plan.md"
FULL_COALITION = "coalition-dropout+localization_shape+latency+track_instability"

#: The SHA-256 of the two records of the released run that stay outside Git.
RELEASED_OUTPUT_HASHES_SHA256 = "47439aad52f112ff2d3e1142cc8530ddd678c5ae5e58a77416beccfe8c730db0"
INPUT_DATABASES_SHA256 = "ea768654fbb085d1c8e53dc01922bc2301c29d5724fffe741009e688d5206774"

#: The plan writes a contrast with a minus sign and a difference with a capital delta.
MINUS = "\u2212"
DELTA = "\u0394"
#: How the analysis plan's tables write an outcome, a test and a predicted sign.
PLAN_OUTCOMES = {
    "braking share": "braking_share",
    "not-at-fault contact rate per 1,000 simulated seconds": "not_at_fault_contact_rate",
    "collision indicator": "collision_indicator",
}
PLAN_TESTS = {"cluster bootstrap": "bootstrap", "sign flip": "sign_flip"}
PLAN_SIGNS = {f"{DELTA} < 0": -1, f"{DELTA} > 0": 1, "two-sided": 0}
#: Section 7.5's implementation check: E minus B in two cells, on five outcomes.
Q3_CHECK_TEXT = (
    f"**Implementation check.** E {MINUS} B for `dropout-medium` and the full coalition,"
    " on five outcomes:\n"
    "  - the collision indicator (sign flip);\n"
    "  - braking share, the not-at-fault contact rate, and missed and false interventions"
    " per token (cluster bootstrap).\n"
)
Q3_CHECK_OUTCOMES = (
    ("collision_indicator", "sign_flip"),
    ("braking_share", "bootstrap"),
    ("not_at_fault_contact_rate", "bootstrap"),
    ("missed_interventions", "bootstrap"),
    ("false_interventions", "bootstrap"),
)


def committed_study() -> StudyDefinitionV1:
    return load_study(STUDY_FILE)


def plan_section(heading: str) -> str:
    """One section of the analysis plan, from its heading to the next heading."""

    text = ANALYSIS_PLAN.read_text(encoding="utf-8")
    start = text.index(f"\n{heading}\n")
    return text[start : text.index("\n#", start + 1)]


def table_rows(section: str) -> list[list[str]]:
    """The body rows of the section's table, one list of stripped cells per row."""

    lines = [line for line in section.splitlines() if line.startswith("|")]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]


def plan_cell(text: str) -> str:
    """A cell as the plan writes it: an identifier in backticks, or the full coalition."""

    return FULL_COALITION if text in ("full coalition", "the full coalition") else text.strip("`")


def arm_id(study: StudyDefinitionV1, letter: str) -> str:
    """The arm the plan names by its letter; each arm id starts with it."""

    (identifier,) = [arm.id for arm in study.arms if arm.id.startswith(f"{letter}-")]
    return identifier


def contrasts(study: StudyDefinitionV1, family_id: str) -> list[tuple[object, ...]]:
    (family,) = [family for family in study.hypothesis_families if family.id == family_id]
    return [
        (
            hypothesis.plus,
            hypothesis.minus,
            hypothesis.cell,
            hypothesis.outcome,
            hypothesis.test,
            hypothesis.predicted_sign,
        )
        for hypothesis in family.hypotheses
    ]


def test_the_study_seed_namespace_is_the_released_protocol_hash() -> None:
    """The released draws are reproduced only under the hash the release was seeded with."""

    evaluation = json.loads(
        (ROOT / RELEASED_EVIDENCE / "evaluation.json").read_text(encoding="utf-8")
    )

    assert committed_study().seed_namespace_protocol_sha256 == evaluation["protocol_sha256"]


def test_the_study_names_the_released_cohort_by_both_of_its_hashes() -> None:
    study = committed_study()
    manifest = ROOT / study.cohort_manifest

    assert study.cohort_manifest == f"{RELEASED_EVIDENCE}/cohort/evaluation.json"
    assert study.cohort_manifest_file_sha256 == lf_normalised_sha256(manifest)
    assert study.cohort_membership_sha256 == membership_sha256(load_manifest(manifest))
    assert study.token_log_source == f"{RELEASED_EVIDENCE}/cohort/evaluation-eligibility.json"
    assert (ROOT / study.token_log_source).is_file()
    assert (ROOT / study.analysis_plan).resolve() == ANALYSIS_PLAN.resolve()


def test_the_study_pins_both_private_records_and_the_addendum_states_the_same_hash_list() -> None:
    study = committed_study()
    stated = re.findall(
        r"`output-hashes\.json`, SHA-256 `([0-9a-f]{64})`",
        ADDENDUM_PLAN.read_text(encoding="utf-8"),
    )

    assert study.released_output_hashes_sha256 == RELEASED_OUTPUT_HASHES_SHA256
    assert study.input_databases_sha256 == INPUT_DATABASES_SHA256
    assert stated == [study.released_output_hashes_sha256]


def test_the_study_records_the_kalman_parameters_the_filter_runs() -> None:
    assert committed_study().cv_kalman.model_dump() == dataclasses.asdict(CV_KALMAN_PARAMETERS)


def test_the_study_cells_and_arms_are_the_plans() -> None:
    """Sections 4.1 and 4.5 of the analysis plan, read from the plan itself."""

    study = committed_study()
    cells = [
        plan_cell(re.findall(r"`[^`]+`", row[0])[0])
        for row in table_rows(plan_section("### 4.1 Cells"))
    ]
    arms = {}
    for row in table_rows(plan_section("### 4.5 Arms")):
        (identifier,) = re.findall(r"`([^`]+)`", row[0])
        arm_cells = cells if row[4] == "all 8" else [plan_cell(cell) for cell in row[4].split(", ")]
        arms[identifier] = (row[1], row[2], row[3], sorted(arm_cells))

    assert len(cells) == 8
    assert sorted(study.cells) == sorted(cells)
    assert [arm.id for arm in study.arms] == list(arms)
    assert {
        arm.id: (arm.aeb_policy, arm.rng_scheme, arm.velocity_estimator, sorted(arm.cells))
        for arm in study.arms
    } == arms


def test_the_study_hypothesis_families_are_the_plans() -> None:
    """Sections 7.1, 7.4 and 7.5 of the analysis plan, with Holm's K for each family."""

    study = committed_study()
    primary_section = plan_section("### 7.1 Primary family (K = 5, Holm)")
    q2_section = plan_section("### 7.4 Q2: velocity estimate")
    q3_section = plan_section("### 7.5 Q3: keying")

    ((plus, minus),) = re.findall(
        rf"Every primary contrast is \*\*([A-E]) {MINUS} ([A-E])\*\*", primary_section
    )
    primary_rows = table_rows(primary_section)
    primary_expected = [
        (
            arm_id(study, plus),
            arm_id(study, minus),
            plan_cell(row[1]),
            PLAN_OUTCOMES[row[2]],
            PLAN_TESTS[row[3]],
            PLAN_SIGNS[row[4]],
        )
        for row in primary_rows
    ]
    q2_family = (
        "**Family Q2 (K = 4, Holm, cluster-bootstrap p).** Outcome: braking share,"
        f" predicted {DELTA} < 0 for each of:"
    )
    q2_expected = [
        (
            arm_id(study, plus),
            arm_id(study, minus),
            plan_cell(cell),
            "braking_share",
            "bootstrap",
            -1,
        )
        for plus, minus, cell in re.findall(
            rf"^  - ([A-E]) {MINUS} ([A-E]) in (`[^`]+`|the full coalition)[;.]$",
            q2_section,
            flags=re.MULTILINE,
        )
    ]
    q3_expected = [
        (arm_id(study, "E"), arm_id(study, "B"), cell, outcome, test, 0)
        for cell in ("dropout-medium", FULL_COALITION)
        for outcome, test in Q3_CHECK_OUTCOMES
    ]

    assert q2_family in q2_section
    assert Q3_CHECK_TEXT in q3_section
    assert "That gives 10 contrasts, with Holm at 0.05 over the 10." in q3_section
    assert "so the expected difference is zero" in q3_section
    assert [family.id for family in study.hypothesis_families] == ["primary", "Q2", "Q3-check"]
    assert [hypothesis.id for hypothesis in study.hypothesis_families[0].hypotheses] == [
        row[0] for row in primary_rows
    ]
    assert len(primary_expected) == 5
    assert contrasts(study, "primary") == primary_expected
    assert len(q2_expected) == 4
    assert contrasts(study, "Q2") == q2_expected
    assert len(q3_expected) == 10
    assert contrasts(study, "Q3-check") == q3_expected


def test_the_study_hashes_both_policies_the_error_configuration_and_the_formal_matrix() -> None:
    """Every config an arm reads is named, and the released three at their released bytes."""

    recorded = committed_study().input_sha256
    released = ("aeb/policy_v1.yaml", "errors/formal_v1.yaml", "experiments/formal_v1.yaml")

    assert sorted(recorded) == sorted((*released, "aeb/policy_v2.yaml"))
    assert {name: recorded[name] for name in released} == {
        name: RELEASED_SHA256[f"configs/{name}"] for name in released
    }


# --------------------------------------------------------------------------
# The addendum
# --------------------------------------------------------------------------


def test_the_addendum_pins_the_hash_list_sha256_its_plan_and_the_study_file_state() -> None:
    """The addendum refuses any other list, so its pin is the value its plan states."""

    from aebrisk.study.addendum import RELEASED_OUTPUT_HASHES_SHA256 as PINNED

    stated = re.findall(
        r"`output-hashes\.json`, SHA-256 `([0-9a-f]{64})`",
        ADDENDUM_PLAN.read_text(encoding="utf-8"),
    )

    assert stated == [PINNED]
    assert committed_study().released_output_hashes_sha256 == PINNED


def test_the_addendum_collision_game_sentence_is_the_one_its_plan_fixes() -> None:
    from aebrisk.artifacts.study_documents import COLLISION_GAME_SENTENCE

    plan = ADDENDUM_PLAN.read_text(encoding="utf-8")

    assert f'with this fixed sentence: "{COLLISION_GAME_SENTENCE}"' in plan
