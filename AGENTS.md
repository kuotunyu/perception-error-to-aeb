# Repository working agreement

## Scope

This repository contains the installable, data-independent simulation core and
adapters for `perception-error-to-aeb`. Keep nuPlan databases, maps, logs,
simulation outputs, credentials, and private progress handoffs outside this Git
repository. The only replay exception is the audited set of at most twelve
derived HTML timelines under `docs/evidence/nuplan_aeb_v2/replays/`; every other
rendered replay or simulation output remains private.

## Required workflow

1. Work on `main` locally; do not rewrite history or publish without explicit
   human authorization.
2. For every behavior change, add one focused test first and observe the intended
   failure before writing production code.
3. Every Python command runs inside the pinned container. From the host, prefix
   commands with `docker compose run --rm dev`; never create a host virtual
   environment for this project. The lock resolves for Linux only, so a host sync
   refuses on purpose.
4. Run `docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify`
   before a local checkpoint.
5. Keep first-party statement and branch coverage at 100% inside the container.
   Do not use coverage exclusions or `pragma: no cover` in first-party code.
6. Keep package code under `src/aebrisk`; never place unique production logic in
   the Dockerfile, compose file, workflows, or wrapper scripts.

## Fixed design

- nuPlan devkit is pinned to commit `e9241677997dd86bfc0bcd44817ab04fe631405b`
  by URL. Never replace it with a branch, a tag, or a fork.
- P3 trains no model, uses no GPU, and reads no sensor data. Torch, Ray, Bokeh,
  Jupyter and OpenCV are deliberately absent from the lock; a contract test keeps
  them out.
- The AEB is deterministic and its thresholds are fixed by the approved plan.
  Changing a threshold, a severity level, or the scenario-family mapping is a new
  protocol version, never an edit.

## Data and compute safety

- `/datasets/`, `/data/` and `/artifacts/` are repository-local ignore boundaries;
  package paths such as `src/aebrisk/nuplan_adapter/` must remain trackable.
- Dataset mounts are read-only. `/work/artifacts` is the only writable output
  root inside the container.
- No real nuPlan database, map or log may be read until the portfolio order gate
  is lifted; unit, contract and container tests use fakes.

## Public/private boundary

Run `docker compose run --rm dev uv run --frozen python -m aebrisk.private_guard`
before committing. The guard inspects the Git index and fails closed for private
handoff material, environment-secret files, credential-shaped text, and dataset
or model artifacts. A documented `.env.example` template is allowed.
