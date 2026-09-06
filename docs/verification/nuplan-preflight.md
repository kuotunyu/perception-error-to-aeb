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

### `val` — 1,381 databases read, none unreadable

| Family | Scenarios | Logs | Splittable |
| --- | ---: | ---: | --- |
| `lead_or_stopping` | 25,098 | 436 | yes |
| `cut_in_or_crossing` | 12,032 | 633 | yes |
| `pedestrian_or_crosswalk` | 677,553 | 812 | yes |
| `bicycle_or_vru` | 729 | 29 | yes |

### `train` — 3,207 databases read, none unreadable

| Family | Scenarios | Logs | Splittable |
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

## What the counts do NOT decide

A tag decides which family a scenario is counted in. Whether a scenario is worth
simulating is decided by the prefilter, on oracle world state, at the freeze —
initial ego speed, whether anything enters the ego corridor within four seconds,
and the oracle's minimum time to collision. The census numbers above are
therefore an upper bound on a family's cohort and not a prediction of it. The
freeze writes an eligibility record beside each manifest — under `artifacts/`,
which is outside Git because it names dataset paths — saying what every examined
recording was accepted or refused for.
