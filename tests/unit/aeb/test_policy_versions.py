"""Contracts for the two committed AEB policies and the loader that picks one.

Policy v1 is the released controller and does not change. Policy v2 is the
policy of the study pre-registered in `docs/studies/aeb-policy-v2/analysis-plan.md`.
It keeps every v1 value and adds one thing: a gate on which visible observed
bodies may be selected at all. The gate is declared in the committed file
rather than written in code, and the validator holds the file to the gate the
simulation applies, so a reader of `configs/aeb/policy_v2.yaml` reads the
controller that ran.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest
import yaml

from aebrisk.aeb.threat import EgoKinematicState, ThreatAssessment
from aebrisk.committed_config import read_committed_config
from aebrisk.observation.models import TrackState

#: The gate the analysis plan fixed before any code that applies it existed.
PRE_REGISTERED_GATE: dict[str, Any] = {
    "gate": "predicted_overlap_not_behind",
    "predicted_overlap": {"horizon_s": 4.0, "step_s": 0.1, "corridor_margin_m": 0.5},
    "not_behind": {"reference": "centre", "minimum_signed_separation_m": 0.0, "inclusive": True},
}


def load_state_machine_module() -> ModuleType:
    from aebrisk.aeb import state_machine

    return state_machine


def committed_document(name: str) -> dict[str, Any]:
    """A fresh, editable parse of one shipped policy file."""

    document: dict[str, Any] = yaml.safe_load(read_committed_config("aeb", name))
    return document


@pytest.fixture
def uncached_policies() -> Iterator[ModuleType]:
    """Clear both per-process policies around a test that substitutes what they read."""

    state_machine = load_state_machine_module()
    state_machine.committed_policy.cache_clear()
    state_machine._committed_policy_v2.cache_clear()
    yield state_machine
    state_machine.committed_policy.cache_clear()
    state_machine._committed_policy_v2.cache_clear()


# --------------------------------------------------------------------------
# The committed files
# --------------------------------------------------------------------------


def test_policy_v2_copies_every_v1_value() -> None:
    """The study varies target selection only; every threshold and limit stays v1's."""

    v1 = committed_document("policy_v1.yaml")
    v2 = committed_document("policy_v2.yaml")
    added = ("schema_version", "target_selection")

    assert {key: value for key, value in v2.items() if key not in added} == {
        key: value for key, value in v1.items() if key != "schema_version"
    }


def test_policy_v2_declares_the_pre_registered_gate() -> None:
    policy = committed_document("policy_v2.yaml")

    assert policy["schema_version"] == "aeb-policy/v2"
    assert policy["target_selection"] == PRE_REGISTERED_GATE


def test_the_committed_v2_policy_passes_its_own_validator() -> None:
    state_machine = load_state_machine_module()

    state_machine.validate_policy(committed_document("policy_v2.yaml"))


# --------------------------------------------------------------------------
# What the validator refuses
# --------------------------------------------------------------------------


def test_validator_refuses_an_unknown_gate() -> None:
    """The step loop applies one gate; a file naming another would describe a policy that never ran."""

    state_machine = load_state_machine_module()
    policy = committed_document("policy_v2.yaml")
    policy["target_selection"]["gate"] = "predicted_overlap"

    with pytest.raises(
        ValueError,
        match=r"^target_selection gate must be 'predicted_overlap_not_behind', got 'predicted_overlap'",
    ):
        state_machine.validate_policy(policy)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("horizon_s", 5.0),
        ("step_s", 0.05),
        ("corridor_margin_m", 0.0),
        ("yaw_rate_rad_s", 0.0),
    ],
)
def test_validator_refuses_a_rollout_value_the_assessment_does_not_use(
    field: str, value: float
) -> None:
    """The gate asks the unchanged assessment, so the file may only restate its rollout."""

    state_machine = load_state_machine_module()
    policy = committed_document("policy_v2.yaml")
    policy["target_selection"]["predicted_overlap"][field] = value

    with pytest.raises(ValueError, match=r"^target_selection predicted_overlap must be "):
        state_machine.validate_policy(policy)


def test_the_rollout_is_the_one_the_assessment_signature_declares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An assessment whose defaults moved would make the committed rollout a false statement."""

    state_machine = load_state_machine_module()

    def assess_threat_with_a_longer_horizon(
        ego_state: EgoKinematicState,
        track: TrackState,
        horizon_s: float = 5.0,
        step_s: float = 0.1,
        corridor_margin_m: float = 0.5,
    ) -> ThreatAssessment:
        raise AssertionError("the validator reads the signature and never calls the assessment")

    monkeypatch.setattr(state_machine, "assess_threat", assess_threat_with_a_longer_horizon)

    with pytest.raises(
        ValueError, match=r"^target_selection predicted_overlap must be \{'horizon_s': 5\.0, "
    ):
        state_machine.validate_policy(committed_document("policy_v2.yaml"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reference", "front"),
        ("minimum_signed_separation_m", -1.0),
        ("inclusive", False),
    ],
)
def test_validator_refuses_a_not_behind_condition_other_than_the_pre_registered_one(
    field: str, value: Any
) -> None:
    state_machine = load_state_machine_module()
    policy = committed_document("policy_v2.yaml")
    policy["target_selection"]["not_behind"][field] = value

    with pytest.raises(ValueError, match=r"^target_selection not_behind must be "):
        state_machine.validate_policy(policy)


@pytest.mark.parametrize(
    "target_selection",
    [
        None,
        ["predicted_overlap_not_behind"],
        {key: value for key, value in PRE_REGISTERED_GATE.items() if key != "not_behind"},
        {**PRE_REGISTERED_GATE, "lateral_margin_m": 0.0},
    ],
    ids=["absent", "not-a-mapping", "without-not-behind", "with-an-extra-key"],
)
def test_validator_refuses_a_v2_policy_without_exactly_the_gate_block(
    target_selection: Any,
) -> None:
    """A v2 policy without its gate would run as v1 under the v2 name."""

    state_machine = load_state_machine_module()
    policy = committed_document("policy_v2.yaml")
    policy["target_selection"] = target_selection

    with pytest.raises(
        ValueError, match=r"^an aeb-policy/v2 policy must carry target_selection with exactly "
    ):
        state_machine.validate_policy(policy)


def test_validator_refuses_target_selection_in_a_v1_policy() -> None:
    """Policy v1 selects among every visible track; a gate in its file would misdescribe the release."""

    state_machine = load_state_machine_module()
    policy = committed_document("policy_v1.yaml")
    policy["target_selection"] = committed_document("policy_v2.yaml")["target_selection"]

    with pytest.raises(
        ValueError, match=r"^an aeb-policy/v1 policy must not carry target_selection"
    ):
        state_machine.validate_policy(policy)


@pytest.mark.parametrize("version", ["aeb-policy/v3", "aeb-policy/V2", "v2", None])
def test_validator_refuses_an_unknown_schema_version(version: Any) -> None:
    """The schema version decides which rules the rest of the file is held to."""

    state_machine = load_state_machine_module()
    policy = committed_document("policy_v2.yaml")
    policy["schema_version"] = version

    with pytest.raises(ValueError, match=r"^schema_version must be one of "):
        state_machine.validate_policy(policy)


# --------------------------------------------------------------------------
# The loader
# --------------------------------------------------------------------------


def test_policy_for_returns_one_read_only_mapping_per_version() -> None:
    """Every step asks for the same thresholds, and no caller may change them for the next."""

    state_machine = load_state_machine_module()
    policies = {version: state_machine.policy_for(version) for version in ("v1", "v2")}

    for version, policy in policies.items():
        assert state_machine.policy_for(version) is policy
        assert policy["schema_version"] == f"aeb-policy/{version}"
        with pytest.raises(TypeError):
            policy["schema_version"] = "aeb-policy/v3"
        with pytest.raises(TypeError):
            policy["full"]["target_accel_mps2"] = 1.0
    with pytest.raises(TypeError):
        policies["v2"]["target_selection"]["not_behind"]["minimum_signed_separation_m"] = -1.0
    assert policies["v1"] is not policies["v2"]
    assert policies["v2"]["target_selection"] == PRE_REGISTERED_GATE


def test_committed_policy_is_policy_v1() -> None:
    """The released path reads the same object the study's v1 arm reads."""

    state_machine = load_state_machine_module()

    assert state_machine.committed_policy() is state_machine.policy_for("v1")
    assert state_machine.committed_policy() == state_machine.frozen(state_machine.load_policy())
    assert state_machine.committed_policy()["schema_version"] == "aeb-policy/v1"
    assert "target_selection" not in state_machine.committed_policy()


@pytest.mark.parametrize("version", ["v3", "V2", "aeb-policy/v2", ""])
def test_an_unknown_version_is_refused(
    version: str, uncached_policies: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused before any file is read, so a misspelt arm cannot fall back to a policy."""

    state_machine = uncached_policies
    reads: list[tuple[str, ...]] = []

    def recording_read(*parts: str) -> str:
        reads.append(parts)
        raise AssertionError("an unknown version must be refused before any read")

    monkeypatch.setattr(state_machine, "read_committed_config", recording_read)

    with pytest.raises(ValueError, match=r"^unknown AEB policy version "):
        state_machine.policy_for(version)
    assert reads == []


def test_a_policy_v2_file_that_fails_validation_is_refused(
    uncached_policies: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validator's rules must hold for the v2 policy the runs read, not only in tests."""

    state_machine = uncached_policies
    reads: list[tuple[str, ...]] = []
    text = read_committed_config("aeb", "policy_v2.yaml").replace(
        "horizon_s: 4.0", "horizon_s: 5.0"
    )

    def edited_read(*parts: str) -> str:
        reads.append(parts)
        return text

    monkeypatch.setattr(state_machine, "read_committed_config", edited_read)

    with pytest.raises(ValueError, match=r"^target_selection predicted_overlap must be "):
        state_machine.policy_for("v2")
    assert reads == [("aeb", "policy_v2.yaml")]
