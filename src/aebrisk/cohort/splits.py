"""Cut the development and evaluation cohorts out of the candidates that passed.

Two properties carry the design.

The selection cannot depend on the order candidates arrive in. Scenarios come
off a filesystem, and a cohort that changed when the directory listing changed
would not be reproducible by anyone else.

Development and evaluation cannot share a log. Scenarios cut from one nuPlan log
are the same road, the same traffic and often the same agents a minute apart, so
a shared log leaks the split that thresholds were chosen on into the one
reported as held out. Splitting by scenario alone would look disjoint and not be.

Log assignment therefore depends only on the log name and the protocol hash.
Adding logs later extends a cohort instead of reshuffling the one already frozen.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from aebrisk.cohort.filters import CorridorCandidate, passes_prefilter

SPLITS: tuple[str, ...] = ("development", "evaluation")

#: The plan's budget per family, per split.
PER_FAMILY_CAP: dict[str, int] = {"development": 50, "evaluation": 100}

#: One log in three goes to development. The ratio follows the caps above, and
#: taking it modulo a small number rather than by sorting is what keeps a log's
#: assignment stable as the candidate set grows.
DEVELOPMENT_LOG_MODULUS = 3


def _digest(protocol_hash: str, value: str) -> str:
    return hashlib.sha256(f"{protocol_hash}:{value}".encode()).hexdigest()


def selection_key(protocol_hash: str, scenario_token: str) -> tuple[str, str]:
    """The order a family's scenarios are taken in, once the cap bites.

    The token is the tie-break so the key is a total order even in the
    astronomically unlikely event of a digest collision, which keeps the frozen
    list stable rather than dependent on a sort's stability.
    """

    return (_digest(protocol_hash, scenario_token), scenario_token)


def log_split(protocol_hash: str, log_name: str) -> str:
    """Say which cohort a log belongs to, from the log and the protocol alone."""

    bucket = int(_digest(protocol_hash, log_name), 16) % DEVELOPMENT_LOG_MODULUS
    return "development" if bucket == 0 else "evaluation"


def freeze_family_cohort(
    candidates: Iterable[CorridorCandidate],
    split: str,
    *,
    protocol_hash: str,
) -> tuple[str, ...]:
    """Freeze one family's scenario tokens for one split, deterministically.

    Every candidate must already pass the prefilter. Filtering here instead
    would hide the fact that something unmeasurable reached the freeze, and the
    cohort is exactly the place where silence is expensive.
    """

    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, got {split!r}")

    selected: list[CorridorCandidate] = []
    seen_tokens: set[str] = set()
    families: set[str] = set()
    for candidate in candidates:
        if candidate.scenario_token in seen_tokens:
            raise ValueError(
                f"duplicate scenario token in candidates: {candidate.scenario_token!r}"
            )
        seen_tokens.add(candidate.scenario_token)
        if not passes_prefilter(candidate):
            raise ValueError(
                f"candidate {candidate.scenario_token!r} does not pass the prefilter; "
                "freeze only what has already been filtered"
            )
        families.add(candidate.family)
        if log_split(protocol_hash, candidate.log_name) == split:
            selected.append(candidate)

    if len(families) > 1:
        raise ValueError(
            f"candidates span more than one family: {sorted(families)}. Each family "
            "is capped separately, so freezing a mixed set would apply one family's "
            "budget to several."
        )

    selected.sort(key=lambda c: selection_key(protocol_hash, c.scenario_token))
    return tuple(candidate.scenario_token for candidate in selected[: PER_FAMILY_CAP[split]])
