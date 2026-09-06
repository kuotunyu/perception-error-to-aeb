"""Freezing the two cohorts out of a mounted split, once, with the evidence.

This is where a study stops being a program and starts being a claim about a set
of recordings. Three things therefore happen here and nowhere else.

THE SPLIT IS SWEPT ONCE PER FAMILY, AND ONLY THE FIRST FEW THOUSAND CANDIDATES
BY FREEZE ORDER ARE KEPT. The validation split holds 1,381 logs and its largest
family holds scenarios by the hundred thousand; holding every reference in
memory to sort them would cost hundreds of megabytes for a list whose first
hundred entries are the only ones that will ever be read. The pool is bounded
and the order is the freeze's own, so the bound changes nothing about which
scenarios are chosen — unless the pool is exhausted without filling the cap,
which is REFUSED rather than reported as a thin family, because those two look
identical in a manifest and mean opposite things.

THE ELIGIBILITY RECORD IS WRITTEN FOR EVERY SCENARIO EXAMINED, accepted or not.
A cohort that came out at 43 of a possible 100 has to be readable as "these are
the recordings that were looked at and this is the rule each one failed", or the
number 43 is just an assertion.

A MANIFEST IS WRITTEN ONCE. `save_manifest` refuses to overwrite one, because a
freeze that can be silently redone is not frozen. Regenerating to prove byte
identity is done by writing to a fresh directory and comparing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional

from aebrisk.cohort.filters import CorridorCandidate
from aebrisk.cohort.manifest import CohortManifestV1
from aebrisk.cohort.prefilter import Eligibility, candidate_of, examine_until_capped
from aebrisk.cohort.splits import (
    OFFICIAL_SPLIT_FOR,
    PER_FAMILY_CAP,
    freeze_family_cohort,
    selection_key,
)
from aebrisk.nuplan_adapter.database import NuPlanInstallation
from aebrisk.nuplan_adapter.query_scenario import ScenarioReference, scenarios_of_type

#: Named so a test can freeze a cohort without a log database.
QUERY_SCENARIOS = scenarios_of_type

#: How many candidates per family are kept from the sweep, in freeze order.
#: Exhausting it without filling the cap is a refusal, so it has to be wide
#: enough for the least accepting family: measured on mini on 2026-09-06, only
#: 3.8 percent of `lead_or_stopping` candidates pass the prefilter, because the
#: tag marks the moment the ego HAS stopped behind a lead and a stopped ego had
#: no braking decision to make. A hundred acceptances at that rate needs about
#: 2,600 examinations, so twenty thousand is a wide margin and still costs a few
#: megabytes rather than the gigabyte that holding a 677,553-scenario family
#: would.
SELECTION_POOL_PER_FAMILY = 20000

ELIGIBILITY_SCHEMA_VERSION = "aeb-cohort-eligibility/v1"


@dataclass(frozen=True)
class FrozenSplit:
    """One frozen cohort, and everything that was examined to produce it."""

    split: str
    manifest: CohortManifestV1
    eligibility: tuple[Eligibility, ...]
    scenarios_in_split_by_family: Mapping[str, int]


def _type_to_family(family_types: Mapping[str, Sequence[str]]) -> dict[str, str]:
    return {
        scenario_type: family
        for family, scenario_types in family_types.items()
        for scenario_type in scenario_types
    }


def pooled_references(
    installation: NuPlanInstallation,
    family_types: Mapping[str, Sequence[str]],
    *,
    protocol_sha256: str,
    pool_size: int = SELECTION_POOL_PER_FAMILY,
) -> dict[str, tuple[tuple[ScenarioReference, ...], int]]:
    """Sweep the split once, keeping each family's first `pool_size` in freeze order.

    Returns each family's pooled references and how many the split holds in
    total, because "the pool is full" and "the family has this many" are
    different facts and the second one belongs in the eligibility document.
    """

    if pool_size < 1:
        raise ValueError(f"pool_size must be at least one, got {pool_size}")

    families = _type_to_family(family_types)
    wanted = sorted(families)
    pools: dict[str, list[tuple[tuple[str, str], ScenarioReference]]] = {
        family: [] for family in family_types
    }
    totals: dict[str, int] = dict.fromkeys(family_types, 0)

    for database in installation.log_databases:
        for reference in QUERY_SCENARIOS(str(database), wanted):
            family = families[reference.scenario_type]
            totals[family] += 1
            pool = pools[family]
            pool.append((selection_key(protocol_sha256, reference.token), reference))
            if len(pool) > 2 * pool_size:
                pool.sort(key=lambda item: item[0])
                del pool[pool_size:]

    trimmed: dict[str, tuple[tuple[ScenarioReference, ...], int]] = {}
    for family, pool in pools.items():
        pool.sort(key=lambda item: item[0])
        del pool[pool_size:]
        trimmed[family] = (tuple(reference for _key, reference in pool), totals[family])
    return trimmed


def freeze_split(
    installation: NuPlanInstallation,
    family_types: Mapping[str, Sequence[str]],
    *,
    split: Literal["development", "evaluation", "smoke"],
    protocol_sha256: str,
    pool_size: int = SELECTION_POOL_PER_FAMILY,
) -> FrozenSplit:
    """Freeze one cohort from one mounted official split."""

    official = OFFICIAL_SPLIT_FOR[split]
    if installation.split != official:
        raise ValueError(
            f"the {split} cohort is drawn from nuPlan's {official!r} logs, and the "
            f"installation mounted is {installation.split!r}; freezing from the wrong "
            "official split would put evaluation recordings in development"
        )

    cap = PER_FAMILY_CAP[split]
    pooled = pooled_references(
        installation, family_types, protocol_sha256=protocol_sha256, pool_size=pool_size
    )

    examined: list[Eligibility] = []
    families: dict[str, tuple[str, ...]] = {}
    logs: set[str] = set()
    for family in sorted(family_types):
        references, total = pooled[family]
        records = examine_until_capped(
            references, family, official, protocol_hash=protocol_sha256, cap=cap
        )
        examined.extend(records)
        accepted = [record for record in records if record.accepted]
        if len(accepted) < cap and len(references) >= pool_size:
            raise ValueError(
                f"family {family!r} filled {len(accepted)} of {cap} from a pool of "
                f"{pool_size} candidates out of {total} in the split; raise "
                "SELECTION_POOL_PER_FAMILY and freeze again. A short cohort and an "
                "exhausted pool look the same in a manifest and mean opposite things"
            )
        candidates: list[CorridorCandidate] = [candidate_of(record) for record in accepted]
        tokens = freeze_family_cohort(candidates, split, protocol_hash=protocol_sha256)
        families[family] = tokens
        chosen = set(tokens)
        logs.update(
            candidate.log_name for candidate in candidates if candidate.scenario_token in chosen
        )

    manifest = CohortManifestV1(
        schema_version="aeb-cohort-manifest/v1",
        split=split,
        protocol_sha256=protocol_sha256,
        families=families,
        log_names=tuple(sorted(logs)),
    )
    return FrozenSplit(
        split=split,
        manifest=manifest,
        eligibility=tuple(examined),
        scenarios_in_split_by_family={family: pooled[family][1] for family in sorted(family_types)},
    )


def eligibility_json_bytes(
    records: Sequence[Eligibility], totals: Optional[Mapping[str, int]] = None
) -> bytes:
    """Serialise the eligibility evidence the same way twice.

    Sorted keys, two-space indentation and an LF ending, for the reason every
    other document in this project is written that way: a file whose bytes
    depend on the platform that wrote it cannot be compared by hash.
    """

    document = {
        "schema_version": ELIGIBILITY_SCHEMA_VERSION,
        "examined": [asdict(record) for record in records],
        "scenarios_in_split_by_family": dict(sorted((totals or {}).items())),
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_eligibility(
    records: Sequence[Eligibility], path: Path, totals: Optional[Mapping[str, int]] = None
) -> Path:
    """Write the eligibility evidence beside the manifest it explains."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(eligibility_json_bytes(records, totals))
    return path
