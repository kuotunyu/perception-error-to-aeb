# Published evidence

`nuplan_aeb_v2/` contains the compact, validated documents produced from the
locked evaluation run:

- `evaluation.json` reports safety totals and rates for all 26 configurations.
  Every row carries its own measured simulation exposure; the top-level
  `simulated_seconds` is the sum of those 26 exposures.
- `intervals.json` reports the paired, family-stratified scenario bootstrap for
  collision indicator, intervention duration, missed interventions, and false
  interventions.
- `shapley.json` reports exact four-channel Shapley attribution computed per
  scenario and then averaged.
- `exclusions.json` reports every globally excluded scenario and its available
  diagnostic metadata, including an explicit null where the historical result
  format did not persist an exception type.
- `cohort/` preserves the three frozen manifests and the development and
  evaluation eligibility records that identify and explain the selected cohort.

The command that writes this set is:

```text
aeb-risk evaluate --results-dir artifacts/formal/nuplan_aeb_v2 --manifest artifacts/manifests/nuplan_aeb_v2/evaluation.json --output-dir docs/evidence/nuplan_aeb_v2
```

These files live under `docs/` because the repository ignores `artifacts/`.
A claim audit in a clean clone can resolve only evidence that is tracked.

The software is MIT licensed. The nuPlan-derived cohort metadata and aggregate
evidence are separately covered by the attribution and non-commercial terms in
[`nuplan_aeb_v2-NOTICE.md`](nuplan_aeb_v2-NOTICE.md).
