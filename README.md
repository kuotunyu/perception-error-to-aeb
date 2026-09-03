# perception-error-to-aeb

P3: how perception errors propagate through a deterministic automatic emergency
braking (AEB) controller on a common nuPlan scenario cohort.

[繁體中文](README.zh-TW.md)

## Status

The pinned container, the locked environment, the private-file guard and the
deterministic verification gate exist. **No simulation, no cohort and no result
exists yet.** Every number that will eventually appear here must trace to a
frozen cohort manifest, a run record and an artifact hash; until then there are
none.

## The question

Under identical nuPlan scenarios, initialization, route, nominal controller,
simulation step and termination: how do dropout, localization and shape error,
observation latency, and track fragmentation, alone and in interaction, change
AEB collision avoidance, missed and false interventions, comfort and
intervention duration?

The study trains no model on purpose. A learned planner would confound exactly
the attribution the project exists to compute.

## Fixed runtime

- Linux container from `python:3.9.19-slim-bookworm`, pinned by digest.
- nuPlan devkit at commit `e9241677997dd86bfc0bcd44817ab04fe631405b`, installed
  from that commit by URL.
- World state, database and maps only. No sensor blobs, no camera replay, no GPU.
- 10 Hz simulation, 4 s prediction horizon, closed-loop ego with non-reactive
  logged agents.

## Working with the repository

Every command runs inside the container.

```bash
docker compose build
docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify
docker compose run --rm dev aeb-risk --help
```

The verification gate runs, in order: the private guard, format check, lint,
type check, the test suite, 100% statement and branch coverage, JSON schema
contracts, and documentation link checks. If it fails, fix the first failing
stage; do not skip or lower a threshold.

## Boundaries

- nuPlan data is licensed to the account holder and never enters this
  repository. Dataset mounts are read-only inside the container.
- The AEB thresholds, error severities and scenario-family mapping are fixed by
  the approved protocol. Changing any of them is a new protocol version.
