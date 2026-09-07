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

The validator deliberately accepts a constrained, language-independent binding:
`` `metric_key` = exact_value ``. Surrounding prose may be English or Traditional
Chinese. Unsupported numeric result syntax fails closed instead of guessing which
number belongs to which claim.

Write each Markdown result line or result-table cell in this shape:

1. Bind each stable metric key to its exact artifact value, without rounding or
   percent conversion: `` `metric_key` = exact_value ``.
2. Add one `<!-- claim: <claim_id> -->` on the same line for every binding, in
   the same order as the bindings. A marker authorizes only its paired binding.
3. State `collision_indicator` and `intervention_duration_s` on separate lines.
   Their units differ, so they do not form one sum, comparison, ranking, or predictor.
4. If the line states `oracle_aeb` collisions, include the exact
   `contacts_not_at_fault` count and both claim markers on that line.
5. Use only `common_valid_tokens` as the cohort denominator. Read it from
   `shapley.json`; do not substitute runs, replicates, or the frozen input cohort.

```markdown
Dropout's Shapley `collision_indicator` = -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
Oracle AEB recorded `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> and `contacts_not_at_fault` = 1095. <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->
```

`collisions_per_100km` is `null` in this release. Describe the rate as
unavailable without publishing a per-100 km number. That unavailable clause may
appear beside valid collision and contact bindings.

## Run the gate

For Markdown:

```bash
docker compose run --rm dev uv run --frozen python \
  .agents/skills/auditing-aeb-error-attribution/scripts/validate_attribution.py \
  --claims docs/claims.yaml --repo-root . --document README.md
```

For text not yet in a document, pass `--proposal proposal.yaml`. A proposal is
`proposals:` containing `text` and `claim_ids`; use a list because one oracle
sentence cites both collisions and `contacts_not_at_fault`. The `claim_ids` list
must follow the binding order in `text`.

Exit 0 prints the common-valid cohort and a JSON trace for every claim. Exit 1
lists every publication violation. Exit 2 means the audit did not run and is
not evidence of a pass.

## Quick reference

| Result | Required shape |
| --- | --- |
| Shapley value | `` `metric_key` = exact_value ``, one metric per line, claim from `shapley.json` |
| Cohort | `` `common_valid_tokens` = exact_value `` plus its claim marker |
| `oracle_aeb` collisions | Bound `collisions` and `contacts_not_at_fault` values plus both markers |
| Per-100 km | No numeric claim in this release |

## Common mistakes

- Rounding `-0.0021802325581395357` to `-0.00218` breaks artifact traceability.
- Calling seconds a stronger collision effect compares different estimands.
- Writing a claim ID as visible prose does not create the required HTML marker.
- Putting a valid value beside the wrong metric key or swapping two values is rejected.
- A bare numeric Markdown table row is unsupported; use the same metric binding in the cell.
- Reporting 1,032 replicate runs as the cohort replaces 344 common-valid scenarios.

## What this skill is not for

A generic explanation of how AEB works, or a wording-only edit that changes no
number, unit, cohort, comparison, or result context, does not need this audit.
