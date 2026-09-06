# Verification: the nuPlan installation this study reads

What was mounted, what was in it, and what that decided. Every number below was
read by a command in this repository, inside the pinned container, on
2026-09-06; the commands are here so a reader can produce them again rather than
trust them.

The dataset itself is licensed to the account holder and is not redistributed.
Nothing here is a result: these are facts about a recording.

## Preflight

```bash
docker compose run --rm dev uv run --frozen aeb-risk data preflight \
  --db-root /data/nuplan --map-root /data/nuplan/maps \
  --split <split> --output artifacts/preflight/<split>.json
```

| Split | Log databases | Map release |
| --- | ---: | --- |
| `mini` | 64 | `nuplan-maps-v1.0` |
| `val` | 1,381 | `nuplan-maps-v1.0` |
| `train` | 3,207 | `nuplan-maps-v1.0` |

`train` is both training cities — `train_boston` (1,647 databases) and
`train_pittsburgh` (1,560) — extracted into one split directory, because the
study's development half is drawn from training logs and a city is not a split.

The map release is the one the pinned devkit expects. It is **recorded rather
than enforced**: this study reads no map, so a different release could not change
a single number it produces, and refusing on it would block a run for a reason
that cannot affect it. What a record must not do is leave a reader unable to say
which release was mounted when the cohort was frozen.

`NUPLAN_SENSOR_ROOT` is set nowhere. `resolve_installation` refuses outright if
it is set, because a configured sensor root means the boundary this study claims
has moved.

## Census

```bash
docker compose run --rm dev uv run --frozen aeb-risk data census \
  --db-root /data/nuplan --protocol configs/protocols/nuplan_aeb_v2.yaml \
  --split <split> --output artifacts/census/<split>.json
```

The census counts every `scenario_tag` row in a split, by type and by family, and
says whether each family spans enough logs to be divided. It is the gate before
any freeze: **a family confined to one log cannot be split log-disjointly however
many scenarios that log holds**, and no amount of reading the protocol settles
that question.

**These are TAG ROWS, not distinct scenarios.** One lidar frame can be tagged
with several of a family's types at once — a pedestrian on a crosswalk is often
`near_pedestrian_on_crosswalk` and `waiting_for_pedestrian_to_cross` in the same
breath — and the census counts each row. The freeze counts distinct tokens
instead, which is why its totals are smaller: measured on one validation
database, 55 tag rows of the pedestrian family belonged to 30 tokens.

### `val` — 1,381 databases read, none unreadable

| Family | Tag rows | Logs | Splittable |
| --- | ---: | ---: | --- |
| `lead_or_stopping` | 25,098 | 436 | yes |
| `cut_in_or_crossing` | 12,032 | 633 | yes |
| `pedestrian_or_crosswalk` | 677,553 | 812 | yes |
| `bicycle_or_vru` | 729 | 29 | yes |

### `train` — 3,207 databases read, none unreadable

| Family | Tag rows | Logs | Splittable |
| --- | ---: | ---: | --- |
| `lead_or_stopping` | 37,209 | 743 | yes |
| `cut_in_or_crossing` | 29,858 | 1,595 | yes |
| `pedestrian_or_crosswalk` | 337,456 | 1,329 | yes |
| `bicycle_or_vru` | 4,849 | 49 | yes |

**Every type protocol v2 pins is present in both splits** (`pinned_absent` is
empty for each). That is what the census was for: protocol v1 pinned five names
nuPlan tags nothing with, which is recorded in
[the dataset card](../dataset-card.md).

`bicycle_or_vru` is the family this gate existed to check. In `mini` it is two
scenarios in one log — unsplittable, and `behind_bike` is the only bicycle tag
nuPlan has, so no renaming could rescue it. In the official splits it is 4,849
scenarios over 49 training logs and 729 over 29 validation logs, comfortably
above both caps.

## The frozen cohorts

```bash
docker compose run --rm dev uv run --frozen aeb-risk data freeze \
  --db-root /data/nuplan --protocol configs/protocols/nuplan_aeb_v2.yaml \
  --output-dir artifacts/manifests/nuplan_aeb_v2 --split development|evaluation
```

| Family | Development, from `train` | Examined | Evaluation, from `val` | Examined |
| --- | ---: | ---: | ---: | ---: |
| `lead_or_stopping` | 50 | 291 | 100 | 899 |
| `cut_in_or_crossing` | 50 | 91 | 100 | 309 |
| `pedestrian_or_crosswalk` | 50 | 439 | 100 | 1,238 |
| `bicycle_or_vru` | 50 | 143 | **44** | 451 |
| **Total** | **200** over 146 logs | 964 | **344** over 183 logs | 2,897 |

The two halves share no log and no token, which they cannot: development is drawn
from nuPlan's training logs and locked evaluation from its validation logs.

**`bicycle_or_vru` is 44 of a possible 100 in evaluation, and is left at 44.** The
validation split holds 451 bicycle scenarios and 44 of them pass the prefilter;
the count is recorded rather than back-filled from another family or another
split, because a stratum padded to look full is not the stratum it names.

**Both cohorts were frozen a second time, into a fresh directory, and compared
byte for byte: the two manifests and both eligibility records are identical.** A
freeze that cannot be reproduced is not frozen, and the eligibility records carry
measured floats — initial speeds, times to collision — so their agreeing byte for
byte says the databases were read the same way twice, not merely that the token
lists sorted the same.

Every one of the 3,861 scenarios examined carries a verdict in the eligibility
record beside its manifest, with the rule that refused it. The commonest refusal
is not a defect: `no observed object enters the ego corridor within 4 s` means
the tag named the topic and the geometry found no hazard, which is the whole
reason the prefilter measures rather than trusting the tag.

## What the counts do NOT decide

A tag decides which family a scenario is counted in. Whether a scenario is worth
simulating is decided by the prefilter, on oracle world state, at the freeze —
initial ego speed, whether anything enters the ego corridor within four seconds,
and the oracle's minimum time to collision. The census numbers above are
therefore an upper bound on a family's cohort and not a prediction of it. The
freeze writes an eligibility record beside each manifest — under `artifacts/`,
which is outside Git because it names dataset paths — saying what every examined
recording was accepted or refused for.
