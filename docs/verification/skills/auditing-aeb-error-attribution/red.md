# RED baseline: `auditing-aeb-error-attribution`

Recorded `2026-09-06T23:35:50Z` (`2026-09-07T07:35:50+08:00`). The
collaboration API did not expose per-agent timestamps or cost, so those fields
are unavailable. The fixed baseline completed after the start record at
`2026-09-06T23:15:35Z` and before the Docker RED at
`2026-09-06T23:22:46Z`.

## Fixed evidence-only baseline

- Agent: `/root/e1_implement/e1_cold_baseline`
- Context: cold general-purpose agent; no skill or checklist.
- Access: read-only and limited to `docs/evidence/nuplan_aeb_v2`; no registry,
  source, README, plans, tests, verification records, handoff, Docker, Python,
  or file writes.
- Fixed prompt: *"Write the README results section for this AEB study from
  docs/evidence/nuplan_aeb_v2."*
- Cost: unavailable from the collaboration API.

The evidence-only bound matters: the baseline's missing claim IDs show what it
did with artifacts alone. They do not prove that the same agent would ignore a
registry it was allowed to inspect.

Verbatim output:

The four Markdown destinations are rebased from README-relative
`docs/evidence/...` to archive-relative `../../../evidence/...` so the
repository link gate resolves the same files from this record. No wording,
number, label, or conclusion was changed.

```markdown
## Results

The evaluation used the same 344 nuPlan validation scenarios for every configuration: 100 cut-in/crossing, 100 lead/stopping, 100 pedestrian/crosswalk, and 44 bicycle/VRU scenarios from 183 logs. Each configuration contains 1,032 scored runs. Across 26 configurations, the study accumulated 389,838.9 simulated seconds (108.3 hours), with no post-evaluation exclusions. See the [evaluation summary](../../../evidence/nuplan_aeb_v2/evaluation.json), [confidence intervals](../../../evidence/nuplan_aeb_v2/intervals.json), and [evaluation cohort](../../../evidence/nuplan_aeb_v2/cohort/evaluation.json).

The no-AEB baseline produced 204 collisions in 1,032 runs, a collision indicator of 19.77% (95% interval: 15.70–23.84%). Oracle AEB reduced this to 39 collisions, or 3.78% (2.03–5.81%): a 15.99 percentage-point and 80.9% relative reduction. The raw subtype counts changed from 177 vehicle, 18 object, and 9 VRU collisions without AEB to 27 vehicle, 0 object, and 12 VRU collisions with oracle AEB. The overall reduction therefore came from vehicle and object contacts; the evidence does not show a corresponding reduction in the raw VRU count.

| Configuration | Collision indicator, 95% interval | False interventions / run | Missed interventions / run | Mean intervention duration |
|---|---:|---:|---:|---:|
| No AEB | 19.77% (15.70–23.84%) | 0.000 | 0.000 | 0.000 s |
| Oracle AEB | 3.78% (2.03–5.81%) | 0.000 | 0.000 | 9.151 s |
| Zero-channel coalition reference | 2.62% (1.16–4.36%) | 2.497 | 1.172 | 9.603 s |
| Dropout — low | 2.52% (1.16–4.07%) | 2.737 | 1.191 | 9.572 s |
| Dropout — medium | 1.84% (0.78–3.10%) | 3.025 | 1.253 | 9.527 s |
| Dropout — high | 1.84% (0.78–3.10%) | 3.357 | 1.351 | 9.414 s |
| Localization/shape — low | 0.39% (0.00–0.87%) | 4.301 | 2.330 | 12.920 s |
| Localization/shape — medium | 0.00% (0.00–0.00%) | 4.502 | 2.884 | 13.709 s |
| Localization/shape — high | 0.00% (0.00–0.00%) | 1.391 | 3.823 | 14.728 s |
| Latency — low | 2.91% (1.16–4.65%) | 2.384 | 1.366 | 9.453 s |
| Latency — medium | 2.91% (1.16–4.94%) | 2.395 | 1.442 | 9.353 s |
| Latency — high | 2.91% (1.16–4.65%) | 2.494 | 2.672 | 9.134 s |
| Track instability — low | 2.23% (0.87–3.78%) | 2.509 | 1.172 | 9.608 s |
| Track instability — medium | 2.62% (1.16–4.26%) | 2.563 | 1.215 | 9.571 s |
| Track instability — high | 2.23% (1.07–3.49%) | 2.682 | 1.253 | 9.425 s |
| All four error channels | 0.00% (0.00–0.00%) | 4.730 | 2.602 | 13.266 s |

The single-channel results do not form a monotonic collision-risk ranking. Dropout, latency, and track-instability collision intervals overlap the zero-channel reference, while increasing dropout severity steadily raises both false and missed interventions. High latency has the clearest missed-intervention effect, rising to 2.672 per run from 1.172 in the reference.

Localization/shape error dominates the observed trade-off. Medium and high settings recorded no collisions in this finite cohort, but braking duration increased to 13.709–14.728 seconds and missed interventions to 2.884–3.823 per run. The lower false-intervention count at the high setting should be read together with its longer intervention duration: event count alone understates sustained braking.

The [Shapley attribution](../../../evidence/nuplan_aeb_v2/shapley.json) reaches the same conclusion. Relative to the zero-channel coalition, localization/shape contributed −2.737 percentage points to the collision indicator and +4.067 seconds to intervention duration. Latency contributed +0.266 percentage points to collision rate, track instability +0.073 points, and dropout −0.218 points. The four channels together moved the observed collision indicator from 2.62% to 0% while increasing mean intervention duration from 9.603 to 13.266 seconds. Negative collision contributions here indicate fewer observed collisions under more braking; they should not be interpreted as better perception.

The intervals use 5,000 resamples at 95% confidence with seed `20260831`. A reported 0.00–0.00% interval reflects zero observed collisions in every resample of this cohort, not proof of zero population risk. The cohort is also deliberately filtered: 344 of 2,897 examined validation scenarios were accepted; 1,750 were rejected because initial ego speed was below 2 m/s and 803 because no observed object entered the ego corridor within four seconds. These results therefore characterize this AEB-relevant scenario cohort rather than fleet-wide collision prevalence. Distance-normalized collision rates were unavailable in the evidence bundle.
```

## Observed baseline behavior

The agent correctly found the 344-token common cohort and correctly said the
distance-normalized rate was unavailable. It also:

- rounded artifact values and derived percentages that have no registered claim;
- put `collision_indicator` and `intervention_duration_s` Shapley values in one sentence;
- stated 39 `oracle_aeb` collisions without 1,095 `contacts_not_at_fault`;
- emitted no machine-readable claim markers.

## Five fresh no-guidance wording controls

Every control received the same supplied evidence and claim IDs, could not read
files or use tools, and was asked for two public README result lines. This makes
the control access comparable to the later skill-present wording samples.

`/root/e1_implement/e1_control_1`:

```markdown
- Dropout reduced the collision indicator by **0.00218** and intervention duration by **0.104 s** across **344 common valid tokens** (`p3.shapley.collision_indicator-values-dropout`, `p3.shapley.intervention_duration_s-values-dropout`).
- The oracle-AEB baseline recorded **39 collisions** and **1,095 not-at-fault contacts** (`p3.baseline.collisions.oracle_aeb`, `p3.baseline.contacts_not_at_fault.oracle_aeb`).
```

`/root/e1_implement/e1_control_2`:

```markdown
- Across 344 common valid tokens, intervention duration had a larger dropout attribution (−0.1038) than collision indication (−0.0022). `p3.shapley.intervention_duration_s-values-dropout` · `p3.shapley.collision_indicator-values-dropout`
- Oracle AEB recorded 39 collisions and 1,095 not-at-fault contacts; collisions per 100 km were unavailable. `p3.baseline.collisions.oracle_aeb` · `p3.baseline.contacts_not_at_fault.oracle_aeb`
```

`/root/e1_implement/e1_control_3`:

```markdown
- Intervention duration was the strongest reported Shapley signal (dropout −0.1038); collision indication contributed −0.0022 across 344 common valid tokens (`p3.shapley.intervention_duration_s-values-dropout`, `p3.shapley.collision_indicator-values-dropout`).
- The oracle AEB baseline recorded 39 collisions and 1,095 contacts where it was not at fault; collisions per 100 km were unavailable (`p3.baseline.collisions.oracle_aeb`, `p3.baseline.contacts_not_at_fault.oracle_aeb`).
```

`/root/e1_implement/e1_control_4`:

```markdown
- **Intervention duration dominated dropout sensitivity:** Shapley −0.1038 versus −0.00218 for the collision indicator across 344 common valid tokens ([p3.shapley.intervention_duration_s-values-dropout], [p3.shapley.collision_indicator-values-dropout]).
- **Oracle AEB baseline:** 39 collisions and 1,095 not-at-fault contacts; collisions per 100 km were unavailable ([p3.baseline.collisions.oracle_aeb], [p3.baseline.contacts_not_at_fault.oracle_aeb]).
```

`/root/e1_implement/e1_control_5`:

```markdown
- **Intervention duration was the strongest collision predictor:** Shapley dropout **−0.104** (`p3.shapley.intervention_duration_s-values-dropout`).
- **Oracle AEB baseline:** **39 collisions** and **1,095 not-at-fault contacts** (`p3.baseline.collisions.oracle_aeb`; `p3.baseline.contacts_not_at_fault.oracle_aeb`).
```

All five rounded values and omitted HTML claim markers. Four combined or
compared unlike Shapley estimands; the fifth invented a cross-metric “strongest
collision predictor” interpretation. This stable control distribution is the
behavior the result-line recipe addresses.

## Review repair RED

Recorded `2026-09-07T00:14:09Z` (`2026-09-07T08:14:09+08:00`) after the
independent review reproduced five semantic gaps against the actual registry.
The repair contract ran in the pinned container before production changes and
failed 10 tests while 25 existing tests passed (exit 1):

```text
......FFFF.FF.FFFF.................                                      [100%]
10 failed, 25 passed in 2.91s
```

The failures cover metric/claim mismatch, percent conversion, a cohort value
used as a Shapley value, swapped Oracle counts, cohort-only discovery,
Markdown-table discovery, marker-derived unlike estimands, a cited but unstated
Oracle contact count, valid counts beside an explicitly unavailable distance
rate, and Traditional Chinese prose using the stable binding keys. Raw output
and exit status are preserved in ignored
`artifacts/e1-fix1-red.log` and `artifacts/e1-fix1-red.exit`.

A later defensive case proved that an exact bound result could still carry an
unbound extra number. Before its repair, the suite failed 1 test while 35
passed (exit 1); the distinct raw files are
`artifacts/e1-fix1-red-extra.log` and `.exit`. No original baseline or cold
output above was changed.
