---
name: running-common-cohort-aeb-studies
description: Use when asked to run, resume, check or report an AEB perception-error study in this repository — including "are these results ready", "finish the remaining runs", "why did the corrupted configuration do better", or any request to put a number from artifacts/runs into a README, a report or a message. Enforces the identical-simulation contract, the common cohort, global invalidation and the fixed 10 Hz policy before any number leaves the repository.
---

# Running common-cohort AEB studies

## What this study claims, and how it can quietly be false

Every number this project publishes rests on one sentence: **the controller,
the route, the initial state, the rate and the termination were identical
across configurations, and only the perception differed.**

The dangerous thing about that sentence is that it can be false while every
individual run looks finished. A configuration that ran at 20 Hz produces a
different trajectory for reasons that have nothing to do with perception. A
configuration that ran twenty of twenty-four scenarios is averaged over
different roads. A scenario excluded in one configuration and kept in the
others breaks the comparison most quietly of all, because each run's own
denominator stays internally consistent.

None of these produce an error. They produce **numbers**, and the numbers are
plausible, and they point in the direction the hypothesis predicts.

## Before you read any result, run the validator

```bash
docker compose run --rm dev uv run --frozen python \
  .agents/skills/running-common-cohort-aeb-studies/scripts/validate_common_cohort.py \
  --runs-dir artifacts/runs \
  --manifest artifacts/manifests/nuplan_aeb_v1.json
```

Exit 0 means the configurations are comparable. Exit 1 lists what makes them
incomparable and prints the exact commands to finish the remaining work. Exit 2
means the inputs are not there.

**If it exits non-zero, no number from these runs may be reported, quoted,
summarised or put in a README — not even with a caveat.** A caveated number is
still a number, and the caveat is not what gets repeated.

## The six things to check, and what each protects

1. **The nominal planner is perception-blind.** `aebrisk.simulation.route_follower`
   takes route and map data only; agents and the AEB are not arguments. If the
   nominal controller could see the agents, a perception error would change the
   driving as well as the braking and no result could separate the two.

2. **The cohort is common.** A scenario valid in one configuration and invalid
   in another is used in NEITHER. `common_valid_scenarios` enforces this.
   Discarding a token everywhere is deliberately wasteful; the alternative is a
   comparison that cannot be defended.

3. **Invalidation is global.** An infrastructure failure excludes the token from
   every configuration. A COLLISION IS NOT AN INFRASTRUCTURE FAILURE — it is the
   measurement. Treating a collision as invalid would drop exactly the scenarios
   where perception error mattered most.

4. **The rate is 10 Hz and the policy is the committed one.** Every counter in
   `configs/aeb/policy_v1.yaml` is a duration at 10 Hz. Never edit that file to
   make a result come out differently; the study's claim is that the controller
   was fixed.

5. **The handoff is the resume point.** Read
   `handoff/perception-error-to-aeb.md` (outside the repository) before
   resuming anything, and update it after. Never commit it and never copy it
   into the repository.

6. **No sensor blobs, ever.** This study reads world state, logs and maps.
   `NUPLAN_SENSOR_ROOT` being set is a configuration error, not an option. If
   a request needs camera or lidar data, stop and say so.

## When the input is incomplete

If any of these is missing, list exactly what is missing and STOP. Do not run a
partial study and do not infer a default:

- the container (`docker info` must answer)
- the nuPlan data root and map root (`aeb-risk data preflight` must exit 0)
- the cohort manifest under `artifacts/manifests/`
- the protocol file under `configs/protocols/`

## What to produce

- The validator's verdict, quoted, with its exit code.
- The identical-simulation contract, stated per configuration: rate, planner,
  controller, termination, initial speed, route signature.
- The global invalid manifest: which tokens left the cohort and in which phase.
- The exact resume commands for whatever is unfinished.

## What this skill is not for

A general question about what automatic emergency braking is, or how AEB works
in production vehicles, is not a request to run or check a study. Answer it
directly; do not run the validator and do not read `artifacts/`.
