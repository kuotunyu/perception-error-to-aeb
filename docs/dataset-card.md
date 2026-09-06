# Dataset card: nuPlan v1.1 for `perception-error-to-aeb`

This study reads world state, log databases and map metadata. It reads no camera
image, no LiDAR point cloud and no sensor blob of any kind, and the environment
enforces that rather than the author's discipline: the container has neither
OpenCV nor rasterio, so the devkit modules that would open sensor data cannot be
imported at all.

## What is used

| Item | Value |
| --- | --- |
| Dataset | nuPlan v1.1 |
| Devkit | pinned at commit `e9241677997dd86bfc0bcd44817ab04fe631405b` |
| Splits | `train` for development scenarios, `val` for locked evaluation, `mini` for smoke |
| Maps | `nuplan-maps-v1.0`, present and recorded, read by nothing in this study |
| Sensor blobs | never fetched, never mounted; `NUPLAN_SENSOR_ROOT` set is a refusal |
| Tables read | `lidar_pc` for the clock, `ego_pose` and the tracked-object tables through the devkit's raw-SQL query layer, `scenario_tag` for the family census |

The map version is v1.0 rather than v1.1 because the pinned devkit says so:
`nuplan/submission/submission_planner.py` defaults `NUPLAN_MAP_VERSION` to
`nuplan-maps-v1.0`. That was read from the installed code, not assumed.

## Licence and distribution

nuPlan is licensed to the account holder by Motional and is not redistributed
here. No database, log, map or extract of one is committed to this repository,
and no absolute dataset path appears in any tracked file. The archives were
fetched from Motional's public AWS mirror, which needs no login and no AWS
account; that is a distribution convenience and changes nothing about the
licence the data carries.

## The scenario-type vocabulary, and why protocol v1 was retired

nuPlan's scenario types are not a published enumeration. They live in each log
database's `scenario_tag` table, so a family mapping written from expectation
cannot be checked against the library — it compiles, passes every unit test, and
is discovered to be wrong only when a cohort comes out empty. Protocol v1 was
written that way, and P3-04 recorded at the time that the mapping had to be
checked against real data when the portfolio order gate opened.

It was, on 2026-09-06, read-only over all 64 databases of the mini split. Five of
v1's fifteen pinned types are names nuPlan tags nothing with:
`crossed_by_vehicle`, `changing_lane_with_lead`, `changing_lane_with_trail`,
`crossed_by_bike` and `near_multiple_bikes`. Two of the four families were
therefore unfreezable:

| Family | Logs | Tagged scenarios | Verdict under v1 |
| --- | ---: | ---: | --- |
| `pedestrian_or_crosswalk` | 55 | 74,999 | ample |
| `lead_or_stopping` | 46 | 2,810 | ample |
| `cut_in_or_crossing` | 0 | 0 | empty; every pinned type absent |
| `bicycle_or_vru` | 1 | 2 | one log cannot be split log-disjointly |

## What the official splits hold under protocol v2

The census was re-run over both official splits once they were fetched. Every
type v2 pins is present in each, and every family spans enough logs to be
divided; the counts and the commands are in
[the preflight record](verification/nuplan-preflight.md).

| Family | `train` scenarios | `train` logs | `val` scenarios | `val` logs |
| --- | ---: | ---: | ---: | ---: |
| `lead_or_stopping` | 37,209 | 743 | 25,098 | 436 |
| `cut_in_or_crossing` | 29,858 | 1,595 | 12,032 | 633 |
| `pedestrian_or_crosswalk` | 337,456 | 1,329 | 677,553 | 812 |
| `bicycle_or_vru` | 4,849 | 49 | 729 | 29 |

`bicycle_or_vru` is thin in mini and ordinary in the official splits, which is
what the gate existed to find out. Nothing was dropped and no fallback city was
needed.

A family's scenario count is an upper bound on its cohort rather than a
prediction of it. Measured on mini, only 3.8 percent of `lead_or_stopping`
candidates pass the prefilter, and almost every refusal is `initial ego speed
below 2.0 m/s`: `stopping_with_lead` tags the moment the ego HAS stopped behind a
lead, and a stopped ego had no braking decision for a perception error to
change.

**Protocol v1 never produced a result, so nothing published changes.** It was
replaced by [`../configs/protocols/nuplan_aeb_v2.yaml`](../configs/protocols/nuplan_aeb_v2.yaml),
whose types all come from
[`../configs/nuplan_scenario_vocabulary.yaml`](../configs/nuplan_scenario_vocabulary.yaml) —
the 67 types the databases actually carry, recorded with their provenance. A
contract test refuses any family type that is not in that file, and a second
test refuses the protocol file and the selection code drifting apart, because
the file's bytes are the hash every run record cites while the code is what
actually selects scenarios.

Two substitutions in v2 deserve stating plainly. `cut_in_or_crossing` now uses
the lane-change tags nuPlan really has (`changing_lane`, `changing_lane_to_left`,
`changing_lane_to_right`) together with the two unprotected turns, which is where
crossing-traffic conflict occurs. `bicycle_or_vru` keeps its single real tag,
`behind_bike`; the family is thin and will be reported with its count rather than
dropped, because a study about vulnerable road users does not quietly delete the
one class it finds inconvenient.

What a tag decides is which family a scenario is counted in. Whether a threat is
present is decided by the prefilter's corridor and time-to-collision rules on
oracle world state, never by the tag.

## The mini split's layout

`nuplan-v1.1_mini.zip` unpacks to `data/cache/mini/*.db`, the 2022 path, while
this project and the devkit's discovery helper both expect
`nuplan-v1.1/splits/mini`. The databases were moved accordingly. That is this
project's own convention: `discover_log_dbs` expands whatever path it is given
and imposes none, so the adapter and `compose.yaml` simply agree on one.

## What this card does not claim

- No count here is a result. The scenario counts above describe the recording,
  not any model or controller.
- The census over the official splits found no type outside the vocabulary that
  any family claims, so nothing was added to it. The `unmapped_present` list in
  each census document names the tags nuPlan carries that no family maps, which
  is a different fact and is left as it is: this study measures four families,
  not every situation nuPlan labelled.
- No cohort is frozen by this document. Freezing, with its eligibility record for
  every accepted and rejected token, is a separate step with its own evidence.
