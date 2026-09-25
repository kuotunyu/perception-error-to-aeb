"""The policy that turns geometry into a brake command.

This is the part of the study that must not move. The claim is that the
CONTROLLER was identical across every configuration and only the perception
changed, so every threshold is read from a committed file rather than written
here, every comparison is strict so a boundary belongs to exactly one side, and
the transition table is pinned by a golden sequence rather than described.

A stage fires when ANY of its configured conditions holds. An object four
seconds away that would need 8 m/s^2 to avoid is already an emergency however
comfortable its time to collision looks, and requiring both conditions would
miss it.

Two asymmetries are deliberate. Warning waits for two consecutive qualifying
steps, because one frame of corrupted perception should not put a vehicle into
an intervention; the braking stages do not wait, because a real system that
hesitated there would be worse than one that occasionally brakes early. And
returning to monitoring takes five clear steps while entering takes one, because
a threat that flickers is still a threat.

What this module does NOT do is decide what the vehicle applies. It reports the
AEB's braking demand and the state that owns the command; the nominal controller
and the jerk limiter run afterwards, so `previous_acceleration_mps2` is carried
through untouched for the closed loop to write.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from aebrisk.aeb.controller import ACCELERATION_BOUNDS_MPS2, JERK_LIMIT_MPS3
from aebrisk.aeb.threat import ThreatAssessment, select_highest_required_deceleration
from aebrisk.committed_config import frozen, read_committed_config

#: The rate the policy's `consecutive_steps` are expressed at. They are
#: durations, not raw counts: reading them as counts would silently halve the
#: warning delay at 20 Hz without changing any number in the committed file.
#: The policy records the same value as `nominal_step_s`, and the validator
#: refuses a policy file that records a different one.
POLICY_NOMINAL_DT_S = 0.1

STAGES = ("warning", "partial", "full")


class AEBState(str, Enum):
    """What the controller is doing, and who owns the acceleration command."""

    MONITOR = "monitor"
    WARNING = "warning"
    PARTIAL = "partial_brake"
    FULL = "full_brake"


@dataclass(frozen=True)
class AEBMemory:
    """The controller's state between steps."""

    state: AEBState
    warning_qualifying_steps: int
    release_clear_steps: int
    previous_acceleration_mps2: float


@dataclass(frozen=True)
class AEBCommand:
    """What the AEB asks for at one step."""

    state: AEBState
    target_acceleration_mps2: float
    selected_track_id: Optional[str]


def load_policy(path: Optional[Path] = None) -> dict[str, Any]:
    """Read the committed AEB policy, or the policy file at `path`.

    The committed policy is the package's copy of `configs/aeb/policy_v1.yaml`,
    so an installed wheel reads the same thresholds as a checkout.
    """

    text = (
        read_committed_config("aeb", "policy_v1.yaml")
        if path is None
        else path.read_text(encoding="utf-8")
    )
    document: dict[str, Any] = yaml.safe_load(text)
    return document


def validate_policy(policy: Mapping[str, Any]) -> None:
    """Refuse a policy that would silently change what the study measures."""

    for stage in STAGES:
        if stage not in policy:
            raise ValueError(f"policy is missing the {stage!r} stage")
    if "release" not in policy:
        raise ValueError("policy is missing the 'release' stage")

    for stage in ("partial", "full"):
        target = policy[stage]["target_accel_mps2"]
        if target >= 0.0:
            raise ValueError(
                f"{stage} target_accel_mps2 must be a deceleration, got {target!r}; "
                "a positive target would make the AEB accelerate into the threat"
            )

    ordering = [policy[stage]["ttc_lt_s"] for stage in STAGES]
    if not ordering[0] > ordering[1] > ordering[2]:
        raise ValueError(
            f"stage ttc_lt_s thresholds must decrease with urgency, got {ordering}; "
            "otherwise the priority order is a claim the thresholds do not support"
        )

    for stage in ("warning", "release"):
        steps = policy[stage]["consecutive_steps"]
        if not isinstance(steps, int) or isinstance(steps, bool) or steps < 1:
            raise ValueError(f"{stage} consecutive_steps must be a positive integer, got {steps!r}")

    # The limiter and the step rate are applied by code, not read from the file.
    # The file records them so a reader can see the whole controller in one
    # place, which is only true while the two agree.
    recorded = (
        ("nominal_step_s", POLICY_NOMINAL_DT_S, "the step consecutive_steps are counted in"),
        ("acceleration_bounds_mps2", ACCELERATION_BOUNDS_MPS2, "the bounds the limiter applies"),
        ("jerk_limit_mps3", JERK_LIMIT_MPS3, "the jerk limit the limiter applies"),
    )
    for name, applied, meaning in recorded:
        if frozen(policy.get(name)) != applied:
            raise ValueError(
                f"{name} must be {applied!r}, {meaning}, got {policy.get(name)!r}; "
                "the policy file would describe a controller the simulation does not run"
            )


@lru_cache(maxsize=1)
def committed_policy() -> Mapping[str, Any]:
    """The committed policy, read and validated once per process, then read-only.

    Every step of every run asks for the same thresholds, so the file is parsed
    once rather than once per step. Validating here makes the rules above hold
    for the policy the simulation actually uses, and the read-only copy means
    no caller can change a threshold for the steps that follow.
    """

    policy = load_policy()
    validate_policy(policy)
    result: Mapping[str, Any] = frozen(policy)
    return result


def _required_steps(configured: int, dt_s: float) -> int:
    """Convert a duration expressed in nominal steps into steps at this rate."""

    return max(1, round(configured * POLICY_NOMINAL_DT_S / dt_s))


def _below(value: Optional[float], threshold: float) -> bool:
    """Strictly below, treating an absent time to collision as not below anything."""

    return value is not None and value < threshold


def _demanded_stage(threat: Optional[ThreatAssessment], policy: Mapping[str, Any]) -> Optional[str]:
    """The most urgent stage this threat qualifies for, or ``None``."""

    if threat is None:
        return None
    if _below(threat.ttc_s, policy["full"]["ttc_lt_s"]) or (
        threat.required_deceleration_mps2 > policy["full"]["required_decel_gt_mps2"]
    ):
        return "full"
    if _below(threat.ttc_s, policy["partial"]["ttc_lt_s"]):
        return "partial"
    if _below(threat.ttc_s, policy["warning"]["ttc_lt_s"]) or (
        threat.required_deceleration_mps2 > policy["warning"]["required_decel_gt_mps2"]
    ):
        return "warning"
    return None


def update_aeb(
    memory: AEBMemory,
    threats: tuple[ThreatAssessment, ...],
    dt_s: float = 0.1,
) -> tuple[AEBMemory, AEBCommand]:
    """Advance the policy by one step and report what the AEB asks for."""

    if not isinstance(dt_s, (int, float)) or isinstance(dt_s, bool):
        raise ValueError("dt_s must be a number")
    if not math.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError(f"dt_s must be finite and positive, got {dt_s!r}")

    policy = committed_policy()
    selected = select_highest_required_deceleration(threats)
    demanded = _demanded_stage(selected, policy)

    warning_steps = memory.warning_qualifying_steps + 1 if demanded else 0
    clear = not _below(
        selected.ttc_s if selected is not None else None,
        policy["release"]["no_qualifying_ttc_lt_s"],
    )

    state = memory.state
    release_steps = memory.release_clear_steps

    if demanded == "full":
        state, release_steps = AEBState.FULL, 0
    elif demanded == "partial":
        state, release_steps = AEBState.PARTIAL, 0
    elif demanded == "warning":
        release_steps = 0
        if warning_steps >= _required_steps(policy["warning"]["consecutive_steps"], dt_s):
            state = AEBState.WARNING
    elif state is AEBState.MONITOR:
        release_steps = 0
    elif clear:
        release_steps += 1
        if release_steps >= _required_steps(policy["release"]["consecutive_steps"], dt_s):
            state, release_steps = AEBState.MONITOR, 0
    else:
        # A threat inside the release window that demands no stage. This is the
        # hysteresis band: it neither escalates nor lets the intervention go.
        release_steps = 0

    if state is AEBState.FULL:
        target = float(policy["full"]["target_accel_mps2"])
    elif state is AEBState.PARTIAL:
        target = float(policy["partial"]["target_accel_mps2"])
    else:
        # Monitoring and warning make no braking demand: the nominal controller
        # owns the acceleration this step, and the state field is what says so.
        target = 0.0

    return (
        AEBMemory(
            state=state,
            warning_qualifying_steps=warning_steps,
            release_clear_steps=release_steps,
            previous_acceleration_mps2=memory.previous_acceleration_mps2,
        ),
        AEBCommand(
            state=state,
            target_acceleration_mps2=target,
            selected_track_id=None
            if state is AEBState.MONITOR or selected is None
            else selected.track_id,
        ),
    )
