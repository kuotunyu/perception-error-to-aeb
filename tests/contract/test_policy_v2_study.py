"""What the policy v2 study holds fixed, checked against the bytes the release published.

The study in `docs/studies/aeb-policy-v2/analysis-plan.md` reruns eight cells of
the released matrix and compares them with the v1.0.0 records token by token.
That comparison means something only while the configuration the release ran
under, and the evidence and claims it published, are the ones the release
shipped. Each file is pinned here by the SHA-256 of its bytes with line endings
normalised to LF, the form Git stores, so a pin holds on any checkout. The
protocol is pinned over its raw CRLF bytes by `test_protocol_hash.py`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

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
