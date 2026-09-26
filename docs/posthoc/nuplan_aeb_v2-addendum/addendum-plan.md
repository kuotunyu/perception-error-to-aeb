# Post-hoc addendum to v1.0.0: paired intervals for the released attribution and configuration contrasts

**Status: post hoc, not pre-registered, and partly computed before this plan was written.** Drafted 2026-09-24 and revised on 2026-09-26. Repository path: `docs/posthoc/nuplan_aeb_v2-addendum/addendum-plan.md`.

- **When it was written.** It is committed in its own pull request before any number below is computed for publication. It was written after the released results had been seen, and after the scratch analyses listed in §1.
- **What it covers.** It uses only the released v1.0.0 records. It describes controller v1 as released, including the target-selection behaviour documented in the [simulation contract](../../simulation-contract.md).
- **What it is independent of.** The pre-registered policy v2 study (`docs/studies/aeb-policy-v2/`). The addendum has its own gate (the reproduction gate, §7), its own claims registry and its own publication, and it waits for none of that study's runs or gates; only the v1.1.0 tag waits for both results pull requests.
- **What it changes.** No existing number, claim, figure, tag or release. Its results are released in v1.1.0. That release is tagged after both results pull requests, this addendum's (§8) and the policy v2 study's, have merged. If that study is reported not completed, v1.1.0 carries that study's not-completed results page, with gate diagnostics and no study results. This addendum's results pull request also adds one pointer sentence to each README. The sentence links to this addendum's `results.md` and has no numbers. It is placed directly after the sentence under the Shapley values that says the evidence contains no uncertainty interval: in `README.en.md` the sentence quoted below, and in `README.md` its Chinese counterpart.

The released English README (`README.en.md`) says the evidence "contains no uncertainty interval for Shapley values or configuration differences and supports no channel ranking". This addendum supplies the intervals. It still makes no ranking claim.

## 1. What was computed before this document

After the v1.0.0 release, and before this plan was merged, scratch analyses of the local released records computed the following (with numpy outside the pinned container):

- the braking share of every released configuration;
- token-level collision counts, including the overlap between the collision tokens of `no_aeb` and those of `oracle_aeb`;
- token-level percentile intervals for all eight released Shapley values (four channels × two games);
- the distribution (count, median, quartiles) of matched onset delays in `coalition-none` and the three latency configurations;
- the brake-activation count of `oracle_aeb`;
- whether the three replicates of every token give identical results in `no_aeb`, `oracle_aeb`, `coalition-none`, `latency-medium` and `dropout-medium`;
- Clopper-Pearson bounds for zero-event configurations.

The Shapley means (Appendix A) and the configuration levels ([`evaluation.json`](../../evidence/nuplan_aeb_v2/evaluation.json)) are published in v1.0.0, so the direction of every Shapley difference and configuration contrast below is known in advance.

Nothing in this addendum is blind. Every number in `results.md` is labelled post hoc, and the items above are also labelled "computed before this plan".

## 2. Question

**Question.** How uncertain are the released Shapley values and configuration differences? How large is the localization-and-shape channel's Shapley value relative to each other channel's, in each released game?

This is estimation only. No hypothesis is tested, no p-value is computed, and no ranking is declared.

## 3. Data and frozen inputs

- **Released records.** The per-token records: 26 configurations × 344 tokens × 3 replicates, run context commit `a65ae17`. They are kept outside Git under the nuPlan licence. Only aggregates are published.
- **Cohort.** The released cohort, [`cohort/evaluation.json`](../../evidence/nuplan_aeb_v2/cohort/evaluation.json), with membership SHA-256 `65e38df2…daf9`. Its token-to-log mapping comes from [`cohort/evaluation-eligibility.json`](../../evidence/nuplan_aeb_v2/cohort/evaluation-eligibility.json): 344 tokens, 183 logs, 198 (family, log) clusters.
- **Attribution definitions**, unchanged:
  - φ is the exact per-token Shapley value over the 16 medium coalitions, computed on replicate means and averaged over tokens (`aebrisk.analysis.attribution`);
  - the two released games are `intervention_duration_s` and `collision_indicator`;
  - the Shapley reference is `coalition-none`, not the oracle. Its zero-severity tracker still differentiates positions.
- **Error keying.** In the released run one error key, which carries the configuration's dropout severity, seeds the dropout, localization/shape and track-instability draws ([simulation contract](../../simulation-contract.md#known-controller-and-modelling-choices)). Every marginal contribution of dropout to a coalition that contains localization_shape or track_instability therefore also redraws those channels' noise; at zero severity those two channels add no noise. φ(dropout), and every difference that involves it (Δ_g,dropout in §5 and the dropout pairs in §6), combines the dropout effect with a new noise realisation. The intervals include that variation and do not separate it.

## 4. Statistics

- **Resampling.** A family-stratified log-cluster bootstrap, the same scheme as the policy v2 study's, stated here in full so that this addendum does not depend on that study's plan:
  - a cluster is a (family, log) pair: all tokens of one nuPlan log in one family (198 clusters, §3);
  - within each family, that family's clusters are resampled with replacement, as many as the family has, and each drawn cluster carries all its tokens;
  - thirteen logs have tokens in more than one family (11 in two, 2 in three). Their tokens form a separate cluster in each family, so the bootstrap does not carry within-log correlation across families;
  - one draw is shared by every configuration and game;
  - 5,000 draws from `numpy.random.Generator(PCG64(20260831))`.

  The released token-level `paired_scenario_bootstrap` is used only in the reproduction gate (§7).
- **Intervals.** Two-sided percentile intervals, with numpy's linear interpolation.
  - The six differences of §5 get simultaneous Bonferroni intervals at 1 − 0.05/6 (99.17%), with the 95% interval beside them.
  - Everything else is 95%.
- **No tests.** No p-value, Holm procedure, or supported or contradicted classification appears anywhere in this addendum.
- **Zero-event configurations.** Exact two-sided 95% Clopper-Pearson intervals for the token-level probability, overall (n = 344) and per family. They are descriptive: n counts tokens, which are correlated within logs.
- **Definitions.**
  - "Full coalition" is `coalition-dropout+localization_shape+latency+track_instability`.
  - Clopper-Pearson intervals use the token-level binary "any counted collision in any replicate".
  - `no_aeb` and `oracle_aeb` are deterministic across replicates, so the avoided and induced counts of §6.3 are token counts.
  - Not-at-fault contact rate = Σ `contacts_not_at_fault` ÷ Σ `simulated_duration_s` × 1,000 over all tokens and replicates (a ratio of sums, as in `evaluation.json`). It counts every contact excluded by the frozen rule: the ego at or below 0.01 m/s, or a faster body behind the ego.

## 5. Post-hoc estimation (not a test): localization and shape against each other channel

For each game g ∈ {`intervention_duration_s`, `collision_indicator`} and each channel c ∈ {dropout, latency, track_instability}:

Δ_g,c = φ_g(localization_shape) − φ_g(c)

- `results.md` reports the six differences with their simultaneous and 95% intervals. It does not use the words "supported", "contradicted", "ranks first" or "ranked". It makes no ranking claim among any channels.
- The collision game is printed on its own line, directly after the duration game's line, with this fixed sentence: "Under v1, the lower collision indicator of the configurations with localization error coincides with braking for most of their measured exposure; it should not be read as a safety benefit."
- The sentence is fixed for two reasons:
  - the released configurations with localization error brake for 0.864–0.982 of their measured exposure, with 0–4 counted collisions (braking share = `mean_intervention_duration_s` × `scenarios` ÷ `simulated_seconds` from [`evaluation.json`](../../evidence/nuplan_aeb_v2/evaluation.json), which includes time held in braking at standstill; the exact values are the `data-braking-share` attributes of the [collisions-vs-braking figure](../../figures/collisions-vs-braking.svg));
  - a contact while the ego is at or below 0.01 m/s is never counted as a collision (`ego_at_fault` and `STOPPED_SPEED_MPS` in `src/aebrisk/simulation/step_loop.py`; simulation contract, [The ego counts as stopped at or below 0.01 m/s](../../simulation-contract.md#known-controller-and-modelling-choices)).

## 6. Descriptive (95%)

1. The eight Shapley value intervals, labelled as in §1, and the other three pairwise differences in each game.
2. Configuration contrasts on the collision indicator, braking share, and the not-at-fault contact rate per 1,000 simulated seconds (§4):
   - `oracle_aeb` − `no_aeb`;
   - `coalition-none` − `oracle_aeb`;
   - full coalition − `coalition-none`;
   - `localization_shape-medium` − `coalition-none`.
3. Avoided and induced counted collisions of the released oracle AEB, separately, with Clopper-Pearson intervals, each stated beside `oracle_aeb`'s `contacts_not_at_fault`:
   - the `no_aeb` collisions that `oracle_aeb` avoids;
   - the `oracle_aeb` collisions on tokens where `no_aeb` has none.

   Per token, avoided = mean over replicates of 1[`no_aeb` has a counted collision and `oracle_aeb` has none], and induced = mean over replicates of 1[`no_aeb` has none and `oracle_aeb` has one]. Both are taken against the same token's `no_aeb` run, and both are token counts here (§4).
4. Brake activations per simulated hour, for every AEB configuration: Σ (entries in `matched_delay_s` + `false_interventions`) ÷ simulated seconds × 3,600.
5. Clopper-Pearson intervals for every released configuration with zero counted collisions.
6. The distribution of matched onset delays (count, median, quartiles) in the three latency configurations, beside `coalition-none`. Delays are measured against `oracle_aeb` by nearest-onset matching within 1.0 s, so they also carry the tracker's effect; they are descriptive and are not a test of the configured latency. They are labelled "computed before this plan" (§1).
7. First stop distance conditional on stopping, and the ego speed at counted collisions, √(2E/1500). The latter is the ego's own speed, not the relative impact speed.

## 7. Reproduction gate before any number

First, the released records are checked against the SHA-256 list written after the formal run finished (`output-hashes.json`, SHA-256 `47439aad52f112ff2d3e1142cc8530ddd678c5ae5e58a77416beccfe8c730db0`): 8,947 files, that is the 8,944 per-token documents plus `run_context.json`, `run_complete.json` and `run.log`. The list is kept with the run's local operation records, outside Git, and is not published. If the list's own SHA-256 differs from the value above, the addendum code refuses to compute anything. The addendum summary records that SHA-256 (§8). Then the addendum code, run on the released records, must reproduce with float equality:

- every estimate, bound, confidence, resample count and seed in [`intervals.json`](../../evidence/nuplan_aeb_v2/intervals.json) (26 configurations × 4 metrics), using the released family-stratified, token-level `paired_scenario_bootstrap` over per-token replicate means (`configuration_intervals` in `src/aebrisk/analysis/aggregate.py`);
- every value and efficiency residual in [`shapley.json`](../../evidence/nuplan_aeb_v2/shapley.json);
- `collisions`, `contacts_not_at_fault`, `simulated_seconds` and `mean_intervention_duration_s` of all 26 configurations in [`evaluation.json`](../../evidence/nuplan_aeb_v2/evaluation.json).

A failure stops the addendum.

## 8. Publication and licence

- `docs/posthoc/nuplan_aeb_v2-addendum/` holds:
  - this plan;
  - `results.md`, titled "Post-hoc addendum to v1.0.0 (not pre-registered; partly computed before writing)";
  - `claims.yaml`, separate from `docs/claims.yaml`, with claim identifiers beginning `p3.posthoc.v1-addendum.`;
  - `evidence/`: the addendum summary `addendum-summary.json`, which carries the reproduction gate's outcome and the SHA-256 of the released records' hash list (§7), and the derived evidence `attribution-addendum-evidence.json`, both documents whose schemas are registered in the repository;
  - `NOTICE.md`: the nuPlan-derived material in this directory is shared under CC BY-NC-SA 4.0 and the Motional dataset terms, worded as in `docs/evidence/nuplan_aeb_v2-NOTICE.md`.
- In this addendum's results pull request, the root `NOTICE`, section 2, gains `docs/posthoc/nuplan_aeb_v2-addendum/` (derived evidence JSON, `claims.yaml`, and the values restated in `results.md`).
- `results.md` passes `validate_attribution` against this registry:
  - every number is written as `` `metric_key` = value `` with a claim marker (`<!-- claim: <id>; rounded: N -->` for a shortened display, with the exact value kept in the evidence);
  - the two games are on separate lines;
  - every line that states `oracle_aeb` collisions also binds `collisions` = 39 and `contacts_not_at_fault` = 1095 through registry claims on `docs/evidence/nuplan_aeb_v2/evaluation.json` `/configurations/1/…`, with ids containing `oracle_aeb`;
  - the addendum's Shapley numbers trace to the addendum's derived evidence, `attribution-addendum-evidence.json`, under the Shapley-source rule as extended by the study tooling pull request (branch `study/aeb-policy-v2-tooling`).
- This plan merges first, in its own pull request, before the addendum code exists. `results.md`, `claims.yaml`, `evidence/` and `NOTICE.md` merge later in a separate results pull request, at any time after the study tooling pull request (branch `study/aeb-policy-v2-tooling`), which adds the addendum code, has merged and the reproduction gate (§7) has passed. Neither waits for the policy v2 study's runs or gates.
- Only aggregates are published: no per-token table, token identifier or scenario list.

## 9. What this addendum does not show

- It describes controller v1 as released, including target selection that is not path-gated. It says nothing about policy v2.
- φ(dropout) and every difference involving dropout combine the dropout effect with a redraw of the localization/shape and track-instability noise (§3).
- Every cohort token was admitted by a straight-line, current-heading predicate on oracle tracks (`src/aebrisk/cohort/prefilter.py`). The cohort is not a sample of all driving.
- This is a simulation of non-reactive logged traffic; logged actors do not react to the ego, and a contact while the ego is stopped is recorded in `contacts_not_at_fault`, not as a counted collision. It says nothing about real-world AEB performance.
- It gives no channel ranking, no real-world AEB claim and no regulatory claim.

## 10. Deviations

None at merge. Each later change is a numbered entry:

| No. | Date | Change | Reason | Had any addendum number been computed for publication? |
| --- | --- | --- | --- | --- |

## Appendix A. Released values quoted

From [`shapley.json`](../../evidence/nuplan_aeb_v2/shapley.json), at `/metrics/<game>/values/<channel>`:

| Channel | `intervention_duration_s` (s) | `collision_indicator` |
| --- | ---: | ---: |
| dropout | −0.104 | −0.0022 |
| latency | −0.267 | +0.0027 |
| localization_shape | 4.067 | −0.0274 |
| track_instability | −0.034 | +0.0007 |

- **Full precision** for `localization_shape`: 4.0670219638242875 and −0.027374031007751935.
- **Cohort:** `/cohort_size` = 344 and `/common_valid_tokens` = 344.
