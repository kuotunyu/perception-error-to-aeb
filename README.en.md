# perception-error-to-aeb

**On the same nuPlan scenarios under one fixed AEB policy, how do dropout, localization and shape error, latency, and track instability propagate into collisions and braking behavior?**

[繁體中文](README.md) · [Live report](https://kuotunyu.github.io/perception-error-to-aeb/) · [Release v1.0.0](https://github.com/kuotunyu/perception-error-to-aeb/releases/tag/v1.0.0) · [Documentation guide](docs/README.md#english-guide)

[![CI](https://github.com/kuotunyu/perception-error-to-aeb/actions/workflows/ci.yml/badge.svg)](https://github.com/kuotunyu/perception-error-to-aeb/actions/workflows/ci.yml) [![Pages](https://github.com/kuotunyu/perception-error-to-aeb/actions/workflows/pages.yml/badge.svg)](https://github.com/kuotunyu/perception-error-to-aeb/actions/workflows/pages.yml)

## Finding

- **Observed.** Counted collisions go from `collisions` = 204 <!-- claim: p3.baseline.collisions.no_aeb --> without AEB to `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> with oracle AEB, which also records `contacts_not_at_fault` = 1095 <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb --> excluded contacts, and to `collisions` = 0 <!-- claim: p3.coalition.collisions.coalition-dropout-localization_shape-latency-track_instability --> with all four medium perception errors injected. That configuration also records `false_interventions` = 4881 <!-- claim: p3.coalition.false_interventions.coalition-dropout-localization_shape-latency-track_instability --> false and `missed_interventions` = 2685 <!-- claim: p3.coalition.missed_interventions.coalition-dropout-localization_shape-latency-track_instability --> missed braking events relative to oracle AEB, and brakes for `mean_intervention_duration_s` = 13.3 <!-- claim: p3.coalition.mean_intervention_duration_s.coalition-dropout-localization_shape-latency-track_instability; rounded: 1 --> s per scenario-replicate on average, against `mean_intervention_duration_s` = 9.2 <!-- claim: p3.baseline.mean_intervention_duration_s.oracle_aeb; rounded: 1 --> s for oracle AEB.
- **Why it matters.** Counted collisions alone reward an AEB that brakes more: here the configurations with the fewest counted collisions are the ones that spend the largest share of their measured exposure braking, not the ones with better perception. Perception faults therefore have to be judged on collisions and braking-decision errors together, the unintended-versus-missed activation trade-off that SOTIF addresses; this study makes no compliance claim. The mechanism is described in [Known controller and modelling choices](docs/simulation-contract.md#known-controller-and-modelling-choices).
- **What the repository shows.** Closed-loop fault injection on logged nuPlan scenarios, the full factorial over four error channels with exact Shapley values, a claims registry that CI checks against every README and release-note number, a digest-pinned container, full statement and branch coverage, and a recorded mutation audit.

![Counted collisions against the share of measured exposure spent braking, one point per configuration](docs/figures/collisions-vs-braking.svg)

The figure is descriptive and derived from `evaluation.json` by `aeb-risk figures`. Scenario-replicates are not independent, and the figure has no intervals and supports no channel ranking.

This study trains no model. It holds the scenarios, initialization, route, nominal controller, simulation rate, and termination rules fixed while changing only the observations delivered to AEB. Version 1.0.0 contains the completed formal simulation, derived evidence, and data-free reproduction tools.

## Observed results

The common-valid cohort has `common_valid_tokens` = 344. <!-- claim: p3.evaluation.common_valid_tokens --> Every configuration uses those same tokens; scenario-replicates in the table are repeated runs, not independent samples.

| Configuration | Scenario-replicates | Counted collisions | `contacts_not_at_fault` | Measured exposure (s) |
| --- | ---: | ---: | ---: | ---: |
| no AEB | `scenarios` = 1032 <!-- claim: p3.baseline.scenarios.no_aeb --> | `collisions` = 204 <!-- claim: p3.baseline.collisions.no_aeb --> | `contacts_not_at_fault` = 261 <!-- claim: p3.baseline.contacts_not_at_fault.no_aeb --> | `simulated_seconds` = 10945.5 <!-- claim: p3.baseline.simulated_seconds.no_aeb --> |
| oracle AEB | `scenarios` = 1032 <!-- claim: p3.baseline.scenarios.oracle_aeb --> | `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> | `contacts_not_at_fault` = 1095 <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb --> | `simulated_seconds` = 14901.9 <!-- claim: p3.baseline.simulated_seconds.oracle_aeb; rounded: 1 --> |
| empty coalition `coalition-none` | `scenarios` = 1032 <!-- claim: p3.coalition.scenarios.coalition-none --> | `collisions` = 27 <!-- claim: p3.coalition.collisions.coalition-none --> | `contacts_not_at_fault` = 1149 <!-- claim: p3.coalition.contacts_not_at_fault.coalition-none --> | `simulated_seconds` = 15043.5 <!-- claim: p3.coalition.simulated_seconds.coalition-none; rounded: 1 --> |
| full medium coalition | `scenarios` = 1032 <!-- claim: p3.coalition.scenarios.coalition-dropout-localization_shape-latency-track_instability --> | `collisions` = 0 <!-- claim: p3.coalition.collisions.coalition-dropout-localization_shape-latency-track_instability --> | `contacts_not_at_fault` = 1150 <!-- claim: p3.coalition.contacts_not_at_fault.coalition-dropout-localization_shape-latency-track_instability --> | `simulated_seconds` = 15395.8 <!-- claim: p3.coalition.simulated_seconds.coalition-dropout-localization_shape-latency-track_instability --> |

Read braking costs alongside collisions. False/missed counts below are events,
not independent scenarios, and can exceed the scenario-replicate count. The
contract defines both fields as zero for no AEB; this does not mean the disabled
brake missed no threats. The [experiment card](docs/experiment-card.md) explains
the comparisons.

| Configuration | Mean intervention duration (s) | False events | Missed events |
| --- | ---: | ---: | ---: |
| no AEB | `mean_intervention_duration_s` = 0.0 <!-- claim: p3.baseline.mean_intervention_duration_s.no_aeb --> | `false_interventions` = 0 <!-- claim: p3.baseline.false_interventions.no_aeb --> | `missed_interventions` = 0 <!-- claim: p3.baseline.missed_interventions.no_aeb --> |
| oracle AEB | `mean_intervention_duration_s` = 9.2 <!-- claim: p3.baseline.mean_intervention_duration_s.oracle_aeb; rounded: 1 --> | `false_interventions` = 0 <!-- claim: p3.baseline.false_interventions.oracle_aeb --> | `missed_interventions` = 0 <!-- claim: p3.baseline.missed_interventions.oracle_aeb --> |
| empty coalition `coalition-none` | `mean_intervention_duration_s` = 9.6 <!-- claim: p3.coalition.mean_intervention_duration_s.coalition-none; rounded: 1 --> | `false_interventions` = 2577 <!-- claim: p3.coalition.false_interventions.coalition-none --> | `missed_interventions` = 1209 <!-- claim: p3.coalition.missed_interventions.coalition-none --> |
| full medium coalition | `mean_intervention_duration_s` = 13.3 <!-- claim: p3.coalition.mean_intervention_duration_s.coalition-dropout-localization_shape-latency-track_instability; rounded: 1 --> | `false_interventions` = 4881 <!-- claim: p3.coalition.false_interventions.coalition-dropout-localization_shape-latency-track_instability --> | `missed_interventions` = 2685 <!-- claim: p3.coalition.missed_interventions.coalition-dropout-localization_shape-latency-track_instability --> |

In the Shapley collision game, localization and shape has an observed mean contribution of `collision_indicator` = -0.0274. <!-- claim: p3.shapley.collision_indicator-values-localization_shape; rounded: 4 -->

In the Shapley intervention-duration game, the same channel has an observed mean contribution of `intervention_duration_s` = 4.07 s. <!-- claim: p3.shapley.intervention_duration_s-values-localization_shape; rounded: 2 -->

<details>
<summary>Full-precision values behind the rounded entries</summary>

Seconds are shown to one decimal and Shapley contributions to three significant figures; each rounded value is recomputed from its claim by the attribution audit. The exact values are:

- oracle AEB: `simulated_seconds` = 14901.900000000001 <!-- claim: p3.baseline.simulated_seconds.oracle_aeb -->
- empty coalition: `simulated_seconds` = 15043.500000000002 <!-- claim: p3.coalition.simulated_seconds.coalition-none -->
- oracle AEB: `mean_intervention_duration_s` = 9.150872093023263 <!-- claim: p3.baseline.mean_intervention_duration_s.oracle_aeb -->
- empty coalition: `mean_intervention_duration_s` = 9.603197674418631 <!-- claim: p3.coalition.mean_intervention_duration_s.coalition-none -->
- full medium coalition: `mean_intervention_duration_s` = 13.265503875969005 <!-- claim: p3.coalition.mean_intervention_duration_s.coalition-dropout-localization_shape-latency-track_instability -->
- Shapley collision game, localization and shape: `collision_indicator` = -0.027374031007751935 <!-- claim: p3.shapley.collision_indicator-values-localization_shape -->
- Shapley intervention-duration game, localization and shape: `intervention_duration_s` = 4.0670219638242875 <!-- claim: p3.shapley.intervention_duration_s-values-localization_shape -->

</details>

These values have different units and scales. They are observed mean decompositions of full minus empty `coalition-none`; they cannot be added, ranked against each other, or read as significance. The zero-severity tracker still estimates velocity from position differences, so `coalition-none` is not the oracle. The evidence contains no uncertainty interval for Shapley values or configuration differences and supports no channel ranking.

For `bicycle_or_vru`, `valid_tokens` = 44 <!-- claim: p3.family-interventions.bicycle_or_vru.oracle_aeb.valid_tokens -->, below the protocol's `evaluation_per_family` = 100. <!-- claim: p3.family-interventions.evaluation_per_family --> These counts describe the sample shortfall rather than efficacy, so this landing page makes no formal claim about that family's intervention rates.

## Figures and replays

Start with oracle, empty coalition and full medium coalition within the same
family in the table below. Seek to the same time and compare Ego speed, AEB state,
TTC and surrounding tracks, then return to the aggregate collision, contact and
intervention tables. Unavailable TTC does not establish absence of risk.

These examples follow the predeclared median-nearest selection rule. They explain
mechanisms; a single frame cannot establish aggregate performance. Read zero
counted collisions together with contact classification and braking costs.

- [Counted collisions against braking share](docs/figures/collisions-vs-braking.svg), shown under the finding, places the no-AEB and oracle references beside every error configuration.
- [Single-channel severity sensitivity](docs/figures/error-severity-sensitivity.svg) compares the fixed levels using separate scales for collision rate, false/missed event rates and intervention duration, with each configuration's measured exposure and replicate denominator.
- [Observed mean Shapley contributions](docs/figures/shapley-contributions.svg) separate collision indicator and intervention duration into panels with their own units.
- [Intervention event rates by family](docs/figures/intervention-rates-by-family.svg) use scenario-replicates as the denominator and do not borrow whole-cohort bootstrap error bars.
- The predeclared median-nearest examples each have oracle, empty-coalition, and full-coalition replicate-zero views:

Each replay is a self-contained HTML page of 7.5 to 11.7 MB served from the live report site, so a desktop browser is recommended. The replay controls are labelled in Traditional Chinese: 播放 = Play, 暫停 = Pause.

| Family | Oracle | Empty coalition | Full medium coalition |
| --- | --- | --- | --- |
| lead/stopping | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/lead_or_stopping--oracle_aeb.html) | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/lead_or_stopping--coalition-none.html) | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/lead_or_stopping--coalition-dropout+localization_shape+latency+track_instability.html) |
| cut-in/crossing | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/cut_in_or_crossing--oracle_aeb.html) | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/cut_in_or_crossing--coalition-none.html) | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/cut_in_or_crossing--coalition-dropout+localization_shape+latency+track_instability.html) |
| pedestrian/crosswalk | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/pedestrian_or_crosswalk--oracle_aeb.html) | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/pedestrian_or_crosswalk--coalition-none.html) | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/pedestrian_or_crosswalk--coalition-dropout+localization_shape+latency+track_instability.html) |
| bicycle/VRU | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/bicycle_or_vru--oracle_aeb.html) | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/bicycle_or_vru--coalition-none.html) | [Open](https://kuotunyu.github.io/perception-error-to-aeb/replays/bicycle_or_vru--coalition-dropout+localization_shape+latency+track_instability.html) |

The replays are derived timelines rebuilt from the selected replicate. They use a scenario-local origin and anonymous actor IDs; they are not raw trajectory exports, map views, or nuBoard logs. Each rerun is checked against every collision, contact, exposure, and intervention measurement shared with its frozen formal record. The formal schema does not store final pose or speed, so those are instead checked against the same fresh simulator outcome.

## Interpretation boundary

The collision classifier excludes a contact when the ego is stopped, or when an actor is behind the ego and has the greater speed magnitude. This is an operational study rule. It is not a longitudinal closing-velocity test, proof of full nuPlan equivalence, or a legal assignment of responsibility. Zero counted collisions does not mean zero contacts, better perception, or safety in a real vehicle.

The evaluation harness surfaced properties of the fixed controller that shape these braking numbers: the AEB brakes for the tracked object with the highest required deceleration whether or not that object is in the ego's path, the nominal controller does not slow for other road users, and tracked velocity is a finite difference of noisy positions. In the released evaluation, the configurations with the fewest counted collisions are the ones that spend the largest share of their measured exposure braking. See [Known controller and modelling choices](docs/simulation-contract.md#known-controller-and-modelling-choices) and [Reading the braking numbers](docs/experiment-card.md#reading-the-braking-numbers).

How these measurements relate to SOTIF, AEB test protocols, safety performance indicators and prior research is set out in [Relation to standards and prior work](docs/experiment-card.md#relation-to-standards-and-prior-work); it is context, not a compliance claim.

The protocol requests a maximum horizon of 15 seconds.

A collision, route end, data end, or that ceiling can stop a run, so measured exposure is reported per configuration above. Logged actors do not react to the ego. This release has no sensor pixels, map view, nuBoard log, per-distance rate, or policy comparison beyond one fixed AEB controller. The collisions per 100 km rate is unavailable.

## Reproduce

All Python commands run in the pinned Linux container:

```powershell
$env:NUPLAN_DATA_ROOT='D:/datasets/nuplan'
docker compose run --rm dev uv run --frozen aeb-risk summarize-families --results-dir artifacts/formal/nuplan_aeb_v2 --manifest artifacts/manifests/nuplan_aeb_v2/evaluation.json --protocol configs/protocols/nuplan_aeb_v2.yaml --output-dir docs/evidence/nuplan_aeb_v2
docker compose run --rm dev uv run --frozen aeb-risk figures --evidence-dir docs/evidence/nuplan_aeb_v2 --output-dir docs/figures
docker compose run --rm dev uv run --frozen aeb-risk report --claims docs/claims.yaml --artifacts-dir docs/evidence/nuplan_aeb_v2 --output-dir site
docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify
```

See the [analysis reproduction record](docs/verification/analysis-reproduction.md) for full provenance, hashes, and interpretation limits. Data-derived material is governed by the nuPlan/Motional terms and [CC BY-NC-SA 4.0](docs/evidence/nuplan_aeb_v2-NOTICE.md); independently authored source code is MIT licensed.

Within the same portfolio, [P1 driving-risk-metrics](https://github.com/kuotunyu/driving-risk-metrics) provides evaluation and uncertainty tooling. [P2 bev-calibration-lab](https://github.com/kuotunyu/bev-calibration-lab) studies camera/LiDAR calibration faults and has published v1.0.0. The projects use different datasets and study settings; their research narrative and descriptive artifact interchange do not establish a validated perception-to-AEB model pipeline.
