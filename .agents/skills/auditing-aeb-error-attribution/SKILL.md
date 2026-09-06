---
name: auditing-aeb-error-attribution
description: Use when writing or checking this project's Shapley attribution, collision, intervention-duration, or cohort results for a README, report, slide, release note, or proposed publication text.
---

# Auditing AEB error attribution

## Overview

Published AEB results must preserve the evidence's exact number, unit, cohort,
and context. The claims registry proves the artifacts; this skill binds each
result line to that registry and keeps unlike Shapley estimands separate.

## The result-line contract

Write each Markdown result line in this shape:

1. State one estimand and its exact artifact value, without rounding or percent conversion.
2. Add `<!-- claim: <claim_id> -->` on the same line for every number stated.
3. State `collision_indicator` and `intervention_duration_s` on separate lines.
   Their units differ, so they do not form one sum, comparison, ranking, or predictor.
4. If the line states `oracle_aeb` collisions, include the exact
   `contacts_not_at_fault` count and both claim markers on that line.
5. Use only `common_valid_tokens` as the cohort denominator. Read it from
   `shapley.json`; do not substitute runs, replicates, or the frozen input cohort.

```markdown
Dropout's Shapley collision_indicator contribution is -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
Oracle AEB recorded 39 collisions and 1095 contacts_not_at_fault. <!-- claim: p3.baseline.collisions.oracle_aeb --> <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->
```

`collisions_per_100km` is `null` in this release. Describe the rate as
unavailable without publishing a per-100 km number.

## Run the gate

For Markdown:

```bash
docker compose run --rm dev uv run --frozen python \
  .agents/skills/auditing-aeb-error-attribution/scripts/validate_attribution.py \
  --claims docs/claims.yaml --repo-root . --document README.md
```

For text not yet in a document, pass `--proposal proposal.yaml`. A proposal is
`proposals:` containing `text` and `claim_ids`; use a list because one oracle
sentence cites both collisions and `contacts_not_at_fault`.

Exit 0 prints the common-valid cohort and a JSON trace for every claim. Exit 1
lists every publication violation. Exit 2 means the audit did not run and is
not evidence of a pass.

## Quick reference

| Result | Required shape |
| --- | --- |
| Shapley value | Exact value, one metric per line, claim from `shapley.json` |
| Cohort | Exact `common_valid_tokens` |
| `oracle_aeb` collisions | Collision and `contacts_not_at_fault` values plus both markers |
| Per-100 km | No numeric claim in this release |

## Common mistakes

- Rounding `-0.0021802325581395357` to `-0.00218` breaks artifact traceability.
- Calling seconds a stronger collision effect compares different estimands.
- Writing a claim ID as visible prose does not create the required HTML marker.
- Reporting 1,032 replicate runs as the cohort replaces 344 common-valid scenarios.

## What this skill is not for

A generic explanation of how AEB works, or a wording-only edit that changes no
number, unit, cohort, comparison, or result context, does not need this audit.
