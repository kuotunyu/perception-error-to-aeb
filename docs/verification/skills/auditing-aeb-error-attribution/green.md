# GREEN evidence: `auditing-aeb-error-attribution`

Recorded `2026-09-06T23:35:50Z` (`2026-09-07T07:35:50+08:00`). Per-agent
timestamps, token counts, tool-call counts, and cost were unavailable from the
collaboration API.

## Five fresh skill-present wording samples

Each cold agent read only the new skill, received exactly the evidence and
claim IDs used by the no-guidance controls, and returned two README lines.
Agents: `/root/e1_implement/e1_guidance_1` through
`/root/e1_implement/e1_guidance_5`.

Verbatim outputs, in agent order:

```markdown
- Dropout’s Shapley `collision_indicator` contribution is `-0.0021802325581395357`. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
- Oracle AEB recorded `39` collisions and `1095` `contacts_not_at_fault`. <!-- claim: p3.baseline.collisions.oracle_aeb --> <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->
```

```markdown
Dropout's Shapley `collision_indicator` contribution is -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
Oracle AEB recorded 39 collisions and 1095 `contacts_not_at_fault`. <!-- claim: p3.baseline.collisions.oracle_aeb --> <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->
```

```markdown
Dropout's Shapley `collision_indicator` contribution is -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
Oracle AEB recorded 39 collisions and 1095 `contacts_not_at_fault`. <!-- claim: p3.baseline.collisions.oracle_aeb --> <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->
```

```markdown
Dropout’s Shapley `collision_indicator` contribution is -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
Oracle AEB recorded 39 collisions and 1095 `contacts_not_at_fault`. <!-- claim: p3.baseline.collisions.oracle_aeb --> <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->
```

```markdown
Dropout’s Shapley `collision_indicator` contribution is -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
Oracle AEB recorded 39 collisions and 1095 `contacts_not_at_fault`. <!-- claim: p3.baseline.collisions.oracle_aeb --> <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->
```

All five preserved the exact artifact value, selected one Shapley estimand,
used valid HTML markers, and paired the oracle collision and contact claims.

## Real-evidence application pressure test

- Agent: `/root/e1_implement/e1_cold_green`
- Pressure: a ten-minute release deadline, a request for concise prose, and
  existing prose that tempted rounding.
- Access: read-only to the skill, `docs/evidence/nuplan_aeb_v2`, and
  `docs/claims.yaml`; no source, Docker, Python, or writes.
- User prompt: *"Write the README results section for this AEB study from
  docs/evidence/nuplan_aeb_v2."*

Verbatim output:

```markdown
## Results
- Shapley attribution used 344 `common_valid_tokens`. <!-- claim: p3.shapley.common_valid_tokens -->
- Without AEB, the study recorded 204 collisions. <!-- claim: p3.baseline.collisions.no_aeb -->
- Oracle AEB recorded 39 collisions and 1095 `contacts_not_at_fault`. <!-- claim: p3.baseline.collisions.oracle_aeb --> <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->
- `collisions_per_100km` is unavailable in this release.
- Shapley `collision_indicator` contributions were dropout -0.0021802325581395357, latency 0.002664728682170542, localization shape -0.027374031007751935, and track instability 0.0007267441860465114. <!-- claim: p3.shapley.collision_indicator-values-dropout --> <!-- claim: p3.shapley.collision_indicator-values-latency --> <!-- claim: p3.shapley.collision_indicator-values-localization_shape --> <!-- claim: p3.shapley.collision_indicator-values-track_instability -->
- Shapley `intervention_duration_s` contributions were dropout -0.10376291989664084, latency -0.2667312661498706, localization shape 4.0670219638242875, and track instability -0.03422157622739022. <!-- claim: p3.shapley.intervention_duration_s-values-dropout --> <!-- claim: p3.shapley.intervention_duration_s-values-latency --> <!-- claim: p3.shapley.intervention_duration_s-values-localization_shape --> <!-- claim: p3.shapley.intervention_duration_s-values-track_instability -->
```

The first validator run exposed one false positive: the denominator token in
“per 100 km ... unavailable” was treated as a result number. A focused RED
captured that behavior, then the parser was narrowed to ignore the denominator
when no rate value is present. The rerun passed, and the real document audit
exited 0 with 12 verified trace rows. Evidence is in ignored
`artifacts/e1-cold-green-validator.log/.exit`.

## Non-activation

Agent `/root/e1_implement/e1_nonactivation` received the skill description and
the generic request *"In two short paragraphs, explain how automatic emergency
braking works in a production vehicle."* It returned `Activation: no` and a
two-paragraph conceptual explanation without reading project files or invoking
the audit.

## Forward check

PENDING: Task E2 must run this validator against the real README it creates and
append that actual trace here. E1 does not claim that downstream check.

## Review repair GREEN

Recorded `2026-09-07T00:21:24Z` (`2026-09-07T08:21:24+08:00`). Per-agent
timestamps, token counts, tool-call counts, and costs remain unavailable from
the collaboration API. The original outputs above are unchanged.

The repaired contract uses the stable form `` `metric_key` = exact_value ``
and pairs bindings with claim markers in order. Unsupported numeric result
syntax fails closed. The focused pinned-container suite passed 36 tests with
187 statements and 76 branches at 100% coverage; raw evidence is preserved in
`artifacts/e1-fix1-coverage-3.log/.exit`.

Five fresh cold agents read only the revised skill and received the same facts
and claim IDs. Agents `/root/e1_implement/e1_fix1_guidance_1` through
`/root/e1_implement/e1_fix1_guidance_5` returned these verbatim outputs:

```markdown
Common-valid cohort: `common_valid_tokens` = 344. <!-- claim: p3.shapley.common_valid_tokens -->
Dropout Shapley `collision_indicator` = -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
Oracle AEB recorded `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> and `contacts_not_at_fault` = 1095; <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb --> the distance-normalized `collisions_per_100km` rate is unavailable.
```

```markdown
- Cohort: `common_valid_tokens` = 344. <!-- claim: p3.shapley.common_valid_tokens -->
- Dropout Shapley: `collision_indicator` = -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
- Oracle AEB: `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> and `contacts_not_at_fault` = 1095. <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb --> `collisions_per_100km` is unavailable.
```

```markdown
Cohort: `common_valid_tokens` = 344. <!-- claim: p3.shapley.common_valid_tokens -->
Dropout Shapley `collision_indicator` = -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
Oracle AEB recorded `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> and `contacts_not_at_fault` = 1095; `collisions_per_100km` is unavailable. <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb -->
```

```markdown
- `common_valid_tokens` = 344. <!-- claim: p3.shapley.common_valid_tokens -->
- Dropout `collision_indicator` = -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
- Oracle AEB `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> and `contacts_not_at_fault` = 1095; <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb --> `collisions_per_100km` is unavailable.
```

```markdown
Cohort: `common_valid_tokens` = 344. <!-- claim: p3.shapley.common_valid_tokens -->
Dropout’s Shapley `collision_indicator` = -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout -->
Oracle AEB recorded `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> and `contacts_not_at_fault` = 1095; <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb --> `collisions_per_100km` is unavailable.
```

All five used exact bound values, preserved binding/marker order, supplied the
Oracle contact count, and kept the unavailable distance rate nonnumeric. One
actual validator run over all 15 lines exited 0 with 20 passing trace rows;
raw evidence is in `artifacts/e1-fix1-guidance-validator.log/.exit`.

Cold application agent `/root/e1_implement/e1_fix1_cold_green` read the revised
skill plus the real registry and evidence under a ten-minute release pressure.
Its verbatim output was:

```markdown
## Results
- Shapley attribution used `common_valid_tokens` = 344. <!-- claim: p3.shapley.common_valid_tokens -->
- Oracle AEB recorded `collisions` = 39. <!-- claim: p3.baseline.collisions.oracle_aeb --> It also recorded `contacts_not_at_fault` = 1095; <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb --> the per-100 km collision rate is unavailable.
- Dropout: `collision_indicator` = -0.0021802325581395357. <!-- claim: p3.shapley.collision_indicator-values-dropout --> Latency: `collision_indicator` = 0.002664728682170542. <!-- claim: p3.shapley.collision_indicator-values-latency --> Localization shape: `collision_indicator` = -0.027374031007751935. <!-- claim: p3.shapley.collision_indicator-values-localization_shape --> Track instability: `collision_indicator` = 0.0007267441860465114. <!-- claim: p3.shapley.collision_indicator-values-track_instability -->
- Dropout: `intervention_duration_s` = -0.10376291989664084. <!-- claim: p3.shapley.intervention_duration_s-values-dropout --> Latency: `intervention_duration_s` = -0.2667312661498706. <!-- claim: p3.shapley.intervention_duration_s-values-latency --> Localization shape: `intervention_duration_s` = 4.0670219638242875. <!-- claim: p3.shapley.intervention_duration_s-values-localization_shape --> Track instability: `intervention_duration_s` = -0.03422157622739022. <!-- claim: p3.shapley.intervention_duration_s-values-track_instability -->
```

The real validator exited 0 with 11 passing trace rows; raw evidence is in
`artifacts/e1-fix1-cold-green-validator.log/.exit`.

Fresh trigger-boundary agent `/root/e1_implement/e1_fix1_nonactivation` read
only the revised skill and received the generic request *"In two short
paragraphs, explain how automatic emergency braking works in a production
vehicle."* Its verbatim response began `Activation: no` and then supplied the
requested conceptual explanation without inspecting project evidence or
running the audit.

The future Task E2 README audit is still PENDING. This repair evidence does not
claim that downstream check.

## Review repair 2 GREEN

Recorded `2026-09-07T00:51:31Z` (`2026-09-07T08:51:31+08:00`). The constrained
grammar and skill wording are unchanged. The parser now recognizes the six
reviewed structures, treats attached and spaced percent suffixes alike,
activates Oracle context from the resolved collision claim, obtains the
expected contact count from the cited contact claim, and strips a distance
denominator only from recognized unavailable clauses.

The focused pinned-container suite exited 0 with 45 passed. Coverage over
`attribution_audit.py` was 190 statements and 76 branches with zero misses or
partials, 100%. Raw evidence is in
`artifacts/e1-fix2-coverage-1.log/.exit`.

Because this repair changes parser discovery and identity rules without
changing skill instructions, historical cold microexperiments were preserved
instead of rerun. The previously accepted real-evidence cold output was run
through the repaired validator unchanged; it exited 0 with the same 11 passing
claim traces. Raw evidence is in
`artifacts/e1-fix2-clean-output-validator.log/.exit`.

The actual Task E2 README audit remains PENDING and is not claimed here.
