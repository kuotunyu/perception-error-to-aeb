"""Cut the development and evaluation cohorts out of the candidates that passed.

Two properties carry the design.

The selection cannot depend on the order candidates arrive in. Scenarios come
off a filesystem, and a cohort that changed when the directory listing changed
would not be reproducible by anyone else. Within a family the order is a hash of
the protocol and the token, so it is fixed before any scenario is looked at.

Development and evaluation cannot share a log. Scenarios cut from one nuPlan log
are the same road, the same traffic and often the same agents a minute apart, so
a shared log leaks the split that thresholds were chosen on into the one
reported as held out. Splitting by scenario alone would look disjoint and not be.

**The split a scenario belongs to is the official split its log was published
in**: development comes from nuPlan's training logs and locked evaluation from
its validation logs, which is what the specification requires and what makes the
two halves disjoint by construction rather than by arithmetic.

An earlier version of this module assigned logs to cohorts by hashing the log
name modulo three, because at that point everything was expected to come from
one pool — the mini split. That rule survived into a design where the two halves
already come from different official splits, where it would have silently
discarded two thirds of the training logs and one third of the validation logs,
and could have emptied a thin family for no reason anybody could defend. It is
gone; the official split decides, and nothing here needs to reshuffle when logs
are added.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from aebrisk.cohort.filters import CorridorCandidate, passes_prefilter

SPLITS: tuple[str, ...] = ("development", "evaluation")

#: The plan's budget per family, per split.
PER_FAMILY_CAP: dict[str, int] = {"development": 50, "evaluation": 100}

#: Which official nuPlan split each cohort is drawn from. Development scenarios
#: come from the training logs and locked evaluation from the validation logs,
#: so the two can never share a log, a road or an agent.
OFFICIAL_SPLIT_FOR: dict[str, str] = {"development": "train", "evaluation": "val"}


def _digest(protocol_hash: str, value: str) -> str:
    return hashlib.sha256(f"{protocol_hash}:{value}".encode()).hexdigest()


def selection_key(protocol_hash: str, scenario_token: str) -> tuple[str, str]:
    """The order a family's scenarios are taken in, once the cap bites.

    The token is the tie-break so the key is a total order even in the
    astronomically unlikely event of a digest collision, which keeps the frozen
    list stable rather than dependent on a sort's stability.
    """

    return (_digest(protocol_hash, scenario_token), scenario_token)


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
        if candidate.official_split == OFFICIAL_SPLIT_FOR[split]:
            selected.append(candidate)

    if len(families) > 1:
        raise ValueError(
            f"candidates span more than one family: {sorted(families)}. Each family "
            "is capped separately, so freezing a mixed set would apply one family's "
            "budget to several."
        )

    selected.sort(key=lambda c: selection_key(protocol_hash, c.scenario_token))
    return tuple(candidate.scenario_token for candidate in selected[: PER_FAMILY_CAP[split]])
