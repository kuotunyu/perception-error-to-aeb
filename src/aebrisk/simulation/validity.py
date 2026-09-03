"""The difference between a bad outcome and a broken run.

This is the distinction most likely to be got wrong quietly, and getting it
wrong in either direction ruins the study.

A COLLISION IS A RESULT. It is what is being measured. Recording it as an
invalid scenario would drop exactly the scenarios where perception error
mattered most, and every configuration would look safer than it is — most so in
the configurations carrying the most error, which is the direction that would
confirm the hypothesis by construction.

AN EXCEPTION IS NOT A RESULT. A scenario that crashed in one configuration
produced no comparable outcome there, so counting the configurations that did
finish would compare different cohorts.

A failure therefore has to say enough to be diagnosed later. The stack is kept
as a hash rather than as text because a traceback carries absolute paths from
the machine that ran it, and those do not belong in a published artifact; the
hash still answers the question an exclusion manifest needs, which is whether
two failures are the same failure.
"""

from __future__ import annotations

import hashlib
import traceback
from dataclasses import dataclass
from typing import Optional

#: A closed vocabulary, so an exclusion manifest can be grouped by where things
#: broke rather than by free text nobody can aggregate.
SIMULATION_PHASES: tuple[str, ...] = (
    "initialization",
    "observation",
    "control",
    "step",
    "metrics",
)


@dataclass(frozen=True)
class InvalidScenario:
    """Why one scenario token produced no comparable result."""

    scenario_token: str
    reason: str
    phase: str
    exception_type: Optional[str]
    stack_sha256: Optional[str]


def stack_digest(exception: BaseException) -> str:
    """Hash the traceback's shape, without the paths that produced it."""

    frames = traceback.extract_tb(exception.__traceback__)
    shape = "\n".join(f"{frame.name}:{frame.lineno}" for frame in frames)
    return hashlib.sha256(shape.encode("utf-8")).hexdigest()


def invalid_from_exception(
    scenario_token: str,
    phase: str,
    exception: BaseException,
) -> InvalidScenario:
    """Record one infrastructure failure in a form an exclusion manifest can use."""

    if not scenario_token:
        raise ValueError("scenario_token must not be empty")
    if phase not in SIMULATION_PHASES:
        raise ValueError(f"phase must be one of {SIMULATION_PHASES}, got {phase!r}")

    return InvalidScenario(
        scenario_token=scenario_token,
        reason=f"the simulator raised during the {phase} phase: {exception}",
        phase=phase,
        exception_type=type(exception).__name__,
        stack_sha256=stack_digest(exception),
    )


def is_infrastructure_failure(invalid: Optional[InvalidScenario]) -> bool:
    """Whether a token must be excluded from every configuration.

    Only a recorded exception qualifies. A completed simulation is a result
    whatever it contains, including a collision, and this function exists so
    that rule has one place to live rather than being re-decided at each caller.
    """

    return invalid is not None
