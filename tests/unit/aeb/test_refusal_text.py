"""The complete text of each AEB refusal.

A refusal is all the author of a rejected policy file, limiter call or gate input
gets to read: it names the rule that failed, the value it was given, and why the
rule exists. The contract tests beside this file match the start of each
message, which is enough to tell one refusal from another. These pin the whole
text, so a reworded, truncated or re-cased refusal is a change someone reviewed
rather than one nobody saw.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

from aebrisk.aeb import controller, state_machine, threat
from aebrisk.aeb.threat import EgoKinematicState
from aebrisk.committed_config import read_committed_config
from aebrisk.observation.models import TrackState

#: The tail every refusal of a recorded limiter or step-rate value ends with.
LIMITER_REASON = "the policy file would describe a controller the simulation does not run"

#: The tail both refusals of a declared gate the step loop does not apply end with.
GATE_REASON = "the policy file would describe a gate the simulation does not apply"


def refused_exactly(message: str) -> Any:
    """Expect a ValueError whose text is `message`, whole and nothing else."""

    return pytest.raises(ValueError, match=f"^{re.escape(message)}$")


def committed_document(name: str) -> dict[str, Any]:
    """A fresh, editable parse of one shipped policy file."""

    document: dict[str, Any] = yaml.safe_load(read_committed_config("aeb", name))
    return document


def car(track_id: str, center_xy_m: tuple[float, float]) -> TrackState:
    return TrackState(
        track_id=track_id,
        category="vehicle",
        center_xy_m=center_xy_m,
        yaw_rad=0.0,
        size_lw_m=(4.5, 1.9),
        velocity_xy_mps=(0.0, 0.0),
        visible=True,
        source_timestamp_us=1_600_000_000_000_000,
        covariance_xy=(0.0, 0.0, 0.0, 0.0),
    )


def ego() -> EgoKinematicState:
    return EgoKinematicState(
        center_xy_m=(0.0, 0.0),
        yaw_rad=0.0,
        size_lw_m=(4.8, 2.0),
        speed_mps=10.0,
        velocity_xy_mps=(10.0, 0.0),
        acceleration_mps2=0.0,
    )


# --------------------------------------------------------------------------
# The limiter
# --------------------------------------------------------------------------


def test_inverted_limiter_bounds_are_refused_in_full() -> None:
    with refused_exactly(
        "min_mps2 2.0 exceeds max_mps2 -6.0; there is no correct clamp for inverted "
        "bounds, only a silent one"
    ):
        controller.limit_acceleration(0.0, -1.0, min_mps2=2.0, max_mps2=-6.0)


# --------------------------------------------------------------------------
# The policy validator
# --------------------------------------------------------------------------


def test_an_unknown_schema_version_is_refused_in_full() -> None:
    policy = committed_document("policy_v2.yaml")
    policy["schema_version"] = "aeb-policy/v3"

    with refused_exactly(
        "schema_version must be one of ('aeb-policy/v1', 'aeb-policy/v2'), got "
        "'aeb-policy/v3'; the version decides which rules the rest of the policy is held to"
    ):
        state_machine.validate_policy(policy)


def test_a_braking_target_that_is_not_a_deceleration_is_refused_in_full() -> None:
    policy = committed_document("policy_v1.yaml")
    policy["full"]["target_accel_mps2"] = 1.0

    with refused_exactly(
        "full target_accel_mps2 must be a deceleration, got 1.0; a positive target would "
        "make the AEB accelerate into the threat"
    ):
        state_machine.validate_policy(policy)


def test_stage_thresholds_out_of_order_are_refused_in_full() -> None:
    policy = committed_document("policy_v1.yaml")
    policy["full"]["ttc_lt_s"] = 4.0

    with refused_exactly(
        "stage ttc_lt_s thresholds must decrease with urgency, got [3.0, 2.5, 4.0]; "
        "otherwise the priority order is a claim the thresholds do not support"
    ):
        state_machine.validate_policy(policy)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "nominal_step_s",
            0.2,
            "nominal_step_s must be 0.1, the step consecutive_steps are counted in, got 0.2",
        ),
        (
            "acceleration_bounds_mps2",
            [-8.0, 2.0],
            "acceleration_bounds_mps2 must be (-6.0, 2.0), the bounds the limiter applies, "
            "got [-8.0, 2.0]",
        ),
        (
            "jerk_limit_mps3",
            10.0,
            "jerk_limit_mps3 must be 5.0, the jerk limit the limiter applies, got 10.0",
        ),
    ],
)
def test_a_recorded_limiter_value_the_code_does_not_apply_is_refused_in_full(
    field: str, value: Any, message: str
) -> None:
    """The refusal quotes the value the file records, not a placeholder."""

    policy = committed_document("policy_v1.yaml")
    policy[field] = value

    with refused_exactly(f"{message}; {LIMITER_REASON}"):
        state_machine.validate_policy(policy)


# --------------------------------------------------------------------------
# The declared target selection
# --------------------------------------------------------------------------


def test_target_selection_in_a_v1_policy_is_refused_in_full() -> None:
    policy = committed_document("policy_v1.yaml")
    policy["target_selection"] = committed_document("policy_v2.yaml")["target_selection"]

    with refused_exactly(
        "an aeb-policy/v1 policy must not carry target_selection; v1 selects among every "
        "visible track, and a gate in its file would describe a controller the released "
        "runs did not use"
    ):
        state_machine.validate_policy(policy)


def test_a_v2_policy_without_its_gate_block_is_refused_in_full() -> None:
    policy = committed_document("policy_v2.yaml")
    del policy["target_selection"]

    with refused_exactly(
        "an aeb-policy/v2 policy must carry target_selection with exactly gate, "
        "predicted_overlap, not_behind, got None; without its gate the policy would run "
        "as v1 under the v2 name"
    ):
        state_machine.validate_policy(policy)


def test_a_rollout_the_assessment_does_not_run_is_refused_in_full() -> None:
    policy = committed_document("policy_v2.yaml")
    policy["target_selection"]["predicted_overlap"]["horizon_s"] = 5.0

    with refused_exactly(
        "target_selection predicted_overlap must be {'horizon_s': 4.0, 'step_s': 0.1, "
        "'corridor_margin_m': 0.5}, the rollout assess_threat runs, got {'horizon_s': 5.0, "
        f"'step_s': 0.1, 'corridor_margin_m': 0.5}}; {GATE_REASON}"
    ):
        state_machine.validate_policy(policy)


def test_a_not_behind_condition_the_gate_does_not_apply_is_refused_in_full() -> None:
    policy = committed_document("policy_v2.yaml")
    policy["target_selection"]["not_behind"]["minimum_signed_separation_m"] = -1.0

    with refused_exactly(
        "target_selection not_behind must be {'reference': 'centre', "
        "'minimum_signed_separation_m': 0.0, 'inclusive': True}, got {'reference': "
        "'centre', 'minimum_signed_separation_m': -1.0, 'inclusive': True}; "
        f"{GATE_REASON}"
    ):
        state_machine.validate_policy(policy)


# --------------------------------------------------------------------------
# The collision-course gate's inputs
# --------------------------------------------------------------------------


def test_a_track_without_its_assessment_is_refused_in_full() -> None:
    with refused_exactly(
        "collision_course_candidates needs one threat assessment per track, got 1 tracks "
        "and 0 assessments"
    ):
        threat.collision_course_candidates(ego(), (car("lead", (25.0, 0.0)),), (), None)


def test_an_assessment_of_another_track_is_refused_in_full() -> None:
    parked = car("parked", (18.0, 4.0))

    with refused_exactly(
        "threat assessment 0 describes track 'parked', not 'lead'; the gate would judge "
        "one body by another's position"
    ):
        threat.collision_course_candidates(
            ego(), (car("lead", (25.0, 0.0)),), (threat.assess_threat(ego(), parked),), None
        )
