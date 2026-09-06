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
