# perception-error-to-aeb

**On the same nuPlan scenarios under one fixed AEB policy, how do dropout, localization and shape error, latency, and track instability propagate into collisions and braking behavior?**

[繁體中文](README.md) · [Evidence guide](docs/README.md)

This study trains no model. It holds the scenarios, initialization, route, nominal controller, simulation rate, and termination rules fixed while changing only the observations delivered to AEB. Version 1.0.0 contains the completed formal simulation, derived evidence, and data-free reproduction tools.

## Observed results

The common-valid cohort has `common_valid_tokens` = 344. <!-- claim: p3.evaluation.common_valid_tokens --> Every configuration uses those same tokens; scenario-replicates in the table are repeated runs, not independent samples.

| Configuration | Scenario-replicates | Counted collisions | `contacts_not_at_fault` | Measured exposure (s) |
| --- | ---: | ---: | ---: | ---: |
| no AEB | `scenarios` = 1032 <!-- claim: p3.baseline.scenarios.no_aeb --> | `collisions` = 204 <!-- claim: p3.baseline.collisions.no_aeb --> | `contacts_not_at_fault` = 261 <!-- claim: p3.baseline.contacts_not_at_fault.no_aeb --> | `simulated_seconds` = 10945.5 <!-- claim: p3.baseline.simulated_seconds.no_aeb --> |
| oracle AEB | `scenarios` = 1032 <!-- claim: p3.baseline.scenarios.oracle_aeb --> | `collisions` = 39 <!-- claim: p3.baseline.collisions.oracle_aeb --> | `contacts_not_at_fault` = 1095 <!-- claim: p3.baseline.contacts_not_at_fault.oracle_aeb --> | `simulated_seconds` = 14901.900000000001 <!-- claim: p3.baseline.simulated_seconds.oracle_aeb --> |
| full medium coalition | `scenarios` = 1032 <!-- claim: p3.coalition.scenarios.coalition-dropout-localization_shape-latency-track_instability --> | `collisions` = 0 <!-- claim: p3.coalition.collisions.coalition-dropout-localization_shape-latency-track_instability --> | `contacts_not_at_fault` = 1150 <!-- claim: p3.coalition.contacts_not_at_fault.coalition-dropout-localization_shape-latency-track_instability --> | `simulated_seconds` = 15395.8 <!-- claim: p3.coalition.simulated_seconds.coalition-dropout-localization_shape-latency-track_instability --> |

In the Shapley collision game, localization and shape has an observed mean contribution of `collision_indicator` = -0.027374031007751935. <!-- claim: p3.shapley.collision_indicator-values-localization_shape -->

In the Shapley intervention-duration game, the same channel has an observed mean contribution of `intervention_duration_s` = 4.0670219638242875. <!-- claim: p3.shapley.intervention_duration_s-values-localization_shape -->

These values have different units and scales. They are observed mean decompositions of full minus empty `coalition-none`; they cannot be added, ranked against each other, or read as significance. The zero-severity tracker still estimates velocity from position differences, so `coalition-none` is not the oracle. The evidence contains no uncertainty interval for Shapley values or configuration differences and supports no channel ranking.

For `bicycle_or_vru`, `valid_tokens` = 44 <!-- claim: p3.family-interventions.bicycle_or_vru.oracle_aeb.valid_tokens -->, below the protocol's `evaluation_per_family` = 100. <!-- claim: p3.family-interventions.evaluation_per_family --> These counts describe the sample shortfall rather than efficacy, so this landing page makes no formal claim about that family's intervention rates.

## Figures and replays

- [Observed mean Shapley contributions](docs/figures/shapley-contributions.svg) separate collision indicator and intervention duration into panels with their own units.
- [Intervention event rates by family](docs/figures/intervention-rates-by-family.svg) use scenario-replicates as the denominator and do not borrow whole-cohort bootstrap error bars.
- The predeclared median-nearest examples each have oracle, empty-coalition, and full-coalition replicate-zero views:

| Family | Oracle | Empty coalition | Full medium coalition |
| --- | --- | --- | --- |
| lead/stopping | [Open](docs/evidence/nuplan_aeb_v2/replays/lead_or_stopping--oracle_aeb.html) | [Open](docs/evidence/nuplan_aeb_v2/replays/lead_or_stopping--coalition-none.html) | [Open](docs/evidence/nuplan_aeb_v2/replays/lead_or_stopping--coalition-dropout+localization_shape+latency+track_instability.html) |
| cut-in/crossing | [Open](docs/evidence/nuplan_aeb_v2/replays/cut_in_or_crossing--oracle_aeb.html) | [Open](docs/evidence/nuplan_aeb_v2/replays/cut_in_or_crossing--coalition-none.html) | [Open](docs/evidence/nuplan_aeb_v2/replays/cut_in_or_crossing--coalition-dropout+localization_shape+latency+track_instability.html) |
| pedestrian/crosswalk | [Open](docs/evidence/nuplan_aeb_v2/replays/pedestrian_or_crosswalk--oracle_aeb.html) | [Open](docs/evidence/nuplan_aeb_v2/replays/pedestrian_or_crosswalk--coalition-none.html) | [Open](docs/evidence/nuplan_aeb_v2/replays/pedestrian_or_crosswalk--coalition-dropout+localization_shape+latency+track_instability.html) |
| bicycle/VRU | [Open](docs/evidence/nuplan_aeb_v2/replays/bicycle_or_vru--oracle_aeb.html) | [Open](docs/evidence/nuplan_aeb_v2/replays/bicycle_or_vru--coalition-none.html) | [Open](docs/evidence/nuplan_aeb_v2/replays/bicycle_or_vru--coalition-dropout+localization_shape+latency+track_instability.html) |

The replays are derived timelines rebuilt from the selected replicate. They use a scenario-local origin and anonymous actor IDs; they are not raw trajectory exports, map views, or nuBoard logs. Each rerun is checked against every collision, contact, exposure, and intervention measurement shared with its frozen formal record. The formal schema does not store final pose or speed, so those are instead checked against the same fresh simulator outcome.

## Interpretation boundary

The collision classifier excludes a contact when the ego is stopped, or when an actor is behind the ego and has the greater speed magnitude. This is an operational study rule. It is not a longitudinal closing-velocity test, proof of full nuPlan equivalence, or a legal assignment of responsibility. Zero counted collisions does not mean zero contacts, better perception, or safety in a real vehicle.

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

Within the same portfolio, [P1 driving-risk-metrics](https://github.com/kuotunyu/driving-risk-metrics) provides evaluation and uncertainty tooling. [P2 bev-calibration-lab](https://github.com/kuotunyu/bev-calibration-lab) studies camera/LiDAR calibration faults and is not yet published.
