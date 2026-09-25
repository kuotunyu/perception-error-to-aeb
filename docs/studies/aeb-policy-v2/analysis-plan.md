# Pre-registered study: collision-course target gating for the AEB (policy v2)

**Status: pre-registered analysis plan.** Drafted 2026-09-24 and revised before the freeze on 2026-09-26. Repository path: `docs/studies/aeb-policy-v2/analysis-plan.md`.

- **Freeze point.** This plan is frozen at the GitHub merge of the pull request that adds it. The time of pre-registration is that pull request's `merged_at`, the merge time recorded by GitHub's server. The merge happens before the code that runs the study exists, and before any simulation of the policy, keying or velocity variants described below.
- **Changes after the freeze.** Before the first formal command for arm B, C, D or E starts, the plan may change only through a numbered entry in §13 (Deviations). Each entry gives the date, the change, the reason, and whether any pilot or formal output of arms B–E existed when it was made. Once the first formal command for arm B, C, D or E has started, the plan's content (hypotheses, outcomes, tests, cells, parameters, gates and decision rules) cannot change. A tooling defect found after that point is fixed as §9 says, and is logged in §13 as a numbered incident that changes no plan content.
- **What counts as a result.** Any output of `aeb-risk study analyse`, and any number read from a formal per-token document of arms B–E. Until gates G1–G3 and G5 have passed (§8, with §9's exception for a G2 failure), those documents are read only by `study simulate --resume` and `study verify`. Every command run against them is recorded with its UTC time in an operator log, which is published as `evidence/operator-log.txt` in the study directory (it contains no token identifiers and no local absolute paths).
- **Data seen before the freeze.** The authors had the released v1.0.0 per-token records (arm A's cells) and computed descriptive values from them before the freeze: runs ending early, brake activations, collision tokens (including the overlap of the `no_aeb` and `oracle_aeb` collision tokens), matched onset delays in `coalition-none` and the latency configurations, whether the replicates of `no_aeb`, `oracle_aeb`, `coalition-none`, `latency-medium` and `dropout-medium` are identical within each token, and Clopper-Pearson bounds for cells with zero counted collisions. No output of any variant in §4 had been produced.
- **Departures.** Anything reported that departs from this plan is labelled exploratory.
- **Not in this document.** The post-hoc addendum on the released v1.0.0 records (paired intervals for the released Shapley values and configuration contrasts) is a separate document, `docs/posthoc/nuplan_aeb_v2-addendum/addendum-plan.md`. It is not pre-registered. It has its own gate, its own claims registry and its own publication, and nothing below depends on it.

This study is a new closed-loop simulation on the released evaluation cohort. It compares the released AEB target selection (policy v1) with one that may brake only for bodies on a predicted collision course along the ego's current heading and not behind it (policy v2). Two further factors are kept separate from the policy change: how the error channels' random draws are keyed, and how track velocity is estimated.

The study changes no published number, claim, figure, tag or release. The following stay as they are:

- the released evidence (`docs/evidence/nuplan_aeb_v2/**` and `docs/evidence/nuplan_aeb_v2-NOTICE.md`);
- the claims registry (`docs/claims.yaml`) and the v1.0.0 release notes;
- the protocol; the formal experiment matrix, `policy_v1.yaml` and the error configuration, with the packaged copies of those three under `src/aebrisk/configs/`;
- the v1.0.0 tag and release.

Results go into `docs/studies/aeb-policy-v2/`, with their own claims file.

## 1. Why

The study's original design specified that the AEB selects "the highest required-deceleration in-path threat", using a corridor built from the planned ego trajectory. The released code omitted the in-path condition. It also rolls the ego forward along its current heading rather than along a planned trajectory. Its tests pinned braking without a predicted overlap as intended behaviour (`test_a_threat_with_no_predicted_overlap_can_still_demand_braking` in `tests/unit/aeb/test_state_machine.py`). This was found after v1.0.0, while asking why the perfect-perception oracle brakes for 63% of its simulated time. Policy v2 restores an in-path condition only, in the simplest form the released geometry supports. The planned-trajectory corridor stays out of scope (§10).

In the released controller, every visible observed track is assessed, and the AEB brakes for the one with the largest required deceleration. `run_steps` in `src/aebrisk/simulation/step_loop.py` passes every assessment to `update_aeb`, which calls `select_highest_required_deceleration`. The required deceleration is v²/(2d), where v is the closing speed along the ego heading and d is the **absolute** longitudinal gap (`_required_deceleration` and `_longitudinal_gap` in `src/aebrisk/aeb/threat.py`). It has no lateral term and no ahead-or-behind test. A demand above 5.0 m/s² selects full braking whether or not the body is on a collision course.

Three consequences follow, and the [simulation contract](../../simulation-contract.md) records them:

- a stationary body beside the path can trigger full braking;
- so can a slower body behind the ego;
- a body off the path can hide one that is on it.

The released numbers are consistent with this, although they do not prove it (values and JSON pointers in Appendix A):

- the oracle configuration, which has perfect perception, brakes for 0.634 of all simulated time;
- the two cells of this study that contain localization error, `localization_shape-medium` and the all-channel coalition, have zero counted collisions and brake for 0.914 and 0.889 of simulated time;
- `coalition-none`, the zero-severity reference, has 27 counted collisions at 0.659.

Much of this braking is expected under any target selection. The nominal controller does not slow for other road users (`src/aebrisk/simulation/route_follower.py`: its target speed is min(initial, map limit, 13.9 m/s)). The AEB is therefore the ego's only response, even to a lead vehicle it should simply follow. Braking share also counts steps held in partial or full braking at standstill. Braking share is therefore not comparable to an AEB activation rate, and 0.634 bounds, rather than estimates, the braking attributable to target selection.

## 2. Questions

- **Q1 (primary).** How much of the released braking comes from the target-selection rule? Policy v2 changes one thing. The AEB may only select a body when two conditions hold. First, the unchanged constant-velocity rollout along the ego's current heading predicts that the body will overlap the ego's margin-inflated footprint within 4.0 s. Second, the body's centre is not behind the ego. Under that change, how does the share of simulated time in braking move, with perfect perception (oracle) and with all four error channels at medium? The side effects on excluded contacts and counted collisions are stated separately (H3–H5).
- **Q2 (secondary).** Does velocity taken from finite differences of noisy positions drive the braking under localization error? Answered by replacing it with a constant-velocity Kalman estimate under each policy.
- **Q3 (secondary, methodological).** The released scheme keys every channel's random draws on the dropout severity. How much does keying them independently of every severity tighten a contrast between two cells that share a channel? The E − B contrasts serve only as an implementation check (§7.5).

## 3. What is frozen and reused

- **Cohort.** The released evaluation manifest, [`cohort/evaluation.json`](../../evidence/nuplan_aeb_v2/cohort/evaluation.json):
  - file SHA-256 `76d326690b6ba0666a0db9a452bbb38307877dcf6ba730586838ab6b557762d6`;
  - membership SHA-256 `65e38df24b91786fb773b883e1cad4348c0cdc58ac976c729871c77e9478daf9`.

  It holds 344 tokens from 183 nuPlan validation logs: `lead_or_stopping` 100, `cut_in_or_crossing` 100, `pedestrian_or_crosswalk` 100 and `bicycle_or_vru` 44. No token is added, removed or re-selected, and the prefilter is not re-run.
- **Token-to-log mapping**, used for clustering (§6). Each token's log is the `log_name` of its accepted row in [`cohort/evaluation-eligibility.json`](../../evidence/nuplan_aeb_v2/cohort/evaluation-eligibility.json). Per family:

  | Family | Tokens | Logs |
  | --- | ---: | ---: |
  | `bicycle_or_vru` | 44 | 8 |
  | `cut_in_or_crossing` | 100 | 78 |
  | `lead_or_stopping` | 100 | 45 |
  | `pedestrian_or_crosswalk` | 100 | 67 |

  Thirteen logs have tokens in more than one family (11 in two families, 2 in three), which gives 198 (family, log) clusters.
- **Protocol.** `configs/protocols/nuplan_aeb_v2.yaml` is unchanged. The hash the release records, `bbf0b6d31943a0f99160afe1d4c18f9a181850367494004037bab98d8f2e59f9`, is taken over the file's CRLF materialisation. This study changes no value that the protocol fixes:
  - the thresholds in its `aeb:` block stay as they are;
  - so do the scenario duration, the frequency and the non-reactive agents.

  What the study adds is something the protocol never specified: which observed bodies may be selected, how velocity is estimated, and how draws are keyed. The protocol's header says "Everything a published number depends on is here, and changing any of it is a new protocol version rather than an edit". That does not hold for this study's results, which also depend on the study file and `policy_v2.yaml`. Each new run records their SHA-256 values separately (§4.5). The protocol hash therefore remains the seed namespace only, and every released noise draw is reproduced exactly.
- **Released records.** The per-token formal records of the v1.0.0 run: 26 configurations × 344 tokens × 3 replicates, run context commit `a65ae17`. They are kept outside Git under the nuPlan licence. Only aggregates derived from them are published.
- **Unchanged inputs**, with their SHA-256:
  - `configs/aeb/policy_v1.yaml`: `263c4eebf718c8d7b748e4d0ad2d93af6654f17c8ce43a3a4d1144f48547564e`;
  - `configs/errors/formal_v1.yaml`: `e13f26aabf749e688fa911fa300e2fa794d32be4e1805443215334398d8f0e4a`;
  - `configs/experiments/formal_v1.yaml`: `6d7935977ba4bcea6bd1428c2632ee665a30c1a865fe68ba28eadf9554b254ab`.

  Medium severity is unchanged:
  - dropout probability 0.10;
  - localization error of 0.50 m in position, 2.0° in yaw and 5% in size;
  - latency 0.20 s;
  - fragmentation 0.05/s, with a 0.50 s reacquisition delay.
- **Simulation.** These are unchanged: 10 Hz, 150 steps (15.0 s), non-reactive logged agents, the blind nominal controller, the at-fault rule, and the rule that a counted collision ends the run.
- **Environment.** Python 3.9.19 and numpy 1.23.4, from the committed lock. The base image is pinned by digest in the `Dockerfile`. The image the v1.0.0 run used is no longer on the host, so the image is rebuilt from the tooling commit. That commit's lock differs from the v1.0.0 run's lock only in development tools and the project version. Its `Dockerfile` differs from the v1.0.0 run's only in comments and in taking the user and group IDs as build arguments, which default to 1000, the values the v1.0.0 run's `Dockerfile` fixed. The `Dockerfile`'s system-package layer (`apt-get install` of `ca-certificates`, `git` and `build-essential`) is not pinned to versions, so the rebuilt image can differ from the v1.0.0 image below the lock. Gate G2 (§8) tests whether the rebuilt environment reproduces the released records.

## 4. Design

### 4.1 Cells

The study uses eight cells of the released matrix. Their identifiers are unchanged, so every simulated cell pairs with its released record token by token.

| Cell | AEB | Observation | Severities |
| --- | --- | --- | --- |
| `no_aeb` | off | oracle | all zero |
| `oracle_aeb` | on | oracle | all zero |
| `coalition-none` | on | corrupted | all zero |
| `dropout-medium` | on | corrupted | dropout medium, others zero |
| `latency-medium` | on | corrupted | latency medium, others zero |
| `localization_shape-medium` | on | corrupted | localization and shape medium, others zero |
| `track_instability-medium` | on | corrupted | track instability medium, others zero |
| `coalition-dropout+localization_shape+latency+track_instability` (the "full coalition") | on | corrupted | all four medium |

### 4.2 Factor 1: target selection (policy)

- **v1**, as released (`policy_v1.yaml`): every visible observed track is a candidate.
- **v2** (`configs/aeb/policy_v2.yaml`, new): a visible observed track is a candidate only if **both** conditions hold.
  1. **Collision course along the current heading (predicted overlap).** `assess_threat` is unchanged and keeps its released parameters: a 4.0 s horizon, 0.1 s samples and a 0.5 m margin. It must report `predicted_overlap = true`. Both footprints are translated at constant velocity: the ego along its current heading at its current speed, the track at its observed velocity. There is no yaw rate and no route. The condition holds when, at some sample 0 ≤ t ≤ 4.0 s, the ego footprint overlaps the track's footprint, with the ego footprint inflated by 0.5 m on every side (0.5 m added to both its half-length and its half-width).

     For an aligned target, overlap therefore needs two things at once. The target's footprint must come within the ego's half-width plus 0.5 m of the ego's centreline: about 1.65 m for the nuPlan ego's 2.297 m width. It must also overlap longitudinally with the ego box extended 0.5 m to the front and rear.
  2. **Not behind.** The signed separation of centres along the ego heading is computed on the observed track:

     s = (x_track − x_ego)·cos ψ + (y_track − y_ego)·sin ψ

     It must satisfy s ≥ 0.

  Everything else stays as in v1:
  - the thresholds and the required-deceleration formula;
  - the selection rule (largest required deceleration, ties broken by track id), now applied to the candidates only;
  - the stage demands, the warning dwell and the release rule;
  - the acceleration bounds and the jerk limit;
  - the nominal controller;
  - the `min_ttc_s` metric, which is still taken over all visible tracks.

  With no candidate, the AEB sees no threat, as v1 does when no track is visible.

**What this gate does, and where it leaks:**

- **With the oracle's reported velocity,** it removes each documented v1 failure mode:
  - a body beside the path has no predicted overlap;
  - a body behind has s < 0;
  - an off-path body is removed before selection, so it can no longer mask an in-path one.

  In cells without localization error the velocity is a finite difference of the logged box centres. That is not the reported velocity: the released `coalition-none` and `oracle_aeb` differ (27 against 39 counted collisions). The same holds there only as far as the finite difference is accurate.
- **Under localization error, the rollout uses finite-difference velocity.** That velocity carries about 7.07 m/s of noise per axis (§4.4), so bodies beside the path, and bodies alongside whose centre is ahead, can still be predicted to overlap on many steps.
  - **Worked example (arithmetic on the rollout, not a result).** Take the parked-car case of the policy v1 characterisation tests: an ego 4.8 × 2.0 m at 15 m/s, and a car 4.5 × 1.9 m centred 20 m ahead and 3.5 m to the side. Any observed lateral velocity toward the path between about 0.66 and 5.95 m/s produces a predicted overlap, first reached at 1.0–1.6 s. That is mostly below the 1.5 s full-braking time to collision. Under N(0, 7.07²) about 26% of steps fall in that range. For the Kalman filter of §4.4, a stationary car's velocity estimate has a realised steady-state standard deviation of about 0.36 m/s per axis (its own covariance, which also allows for σ_a, reports 0.69 m/s), so about 3% of steps fall in that range.
  - **Why a leak holds braking.** The braking stages need no dwell, and release needs five consecutive clear steps (`policy_v1.yaml`). A leak on about one step in four can therefore hold the AEB in braking.
  - **Bodies alongside go straight to full braking.** When an admitted body is alongside the ego with s ≥ 0, its longitudinal gap is clamped to 0.05 m (`MINIMUM_LONGITUDINAL_GAP_M`). Any closing speed above 0.71 m/s then demands more than 5.0 m/s², which is full braking.

  This is why Q2 (the Kalman arms) and the combined contrast D − A (§7.3.3) are part of the design.
- **It introduces no new geometry.** Predicted overlap is the unchanged `assess_threat` output. The s ≥ 0 boundary uses the same projection as the frozen at-fault rule, and puts zero on the same side (`ego_at_fault` treats s < 0 as behind).

  Condition 1 is also the cohort prefilter's corridor predicate (`src/aebrisk/cohort/prefilter.py`). There it was applied to oracle tracks against the logged ego, over the first 4 s. Condition 2 (s ≥ 0) is new and has no counterpart in eligibility. The coupling between the cohort and condition 1 is a limitation, not a strength (§12).
- **On a straight road with accurate velocity, it drops no demand that brakes** (for aligned bodies at constant velocity):
  - A body ahead within the corridor's width, closing at v, demands more than 5.0 m/s² only if its gap is below v²/10. Its time to overlap is then below v/10, which is inside the 4.0 s horizon for any v below 40 m/s.
  - The two time-to-collision stages need a predicted overlap by definition.
  - What v2 can drop is a warning demanded by required deceleration alone, with no predicted overlap. A warning does not brake.
- **Limitations kept on purpose, so that the gate is the only difference:**
  - The rollout is straight-line and constant-velocity along the current heading, not along the route. On a curve it can admit an oncoming body and miss a lead vehicle around the bend.
  - Among candidates, selection is still by required deceleration. A candidate with a larger required deceleration but a later time to collision can still be chosen over a more urgent one.
  - Release still reads only the selected candidate.

### 4.3 Factor 2: keying of the error draws (RNG scheme)

- **dropout-keyed**, as released. Each run has one root key, `ErrorKey(token, "dropout", <the cell's dropout severity>, replicate, protocol hash)`. Every draw of every channel is derived from the root key plus the source track id, the loop step and a field label. As a result, the draws of **every** channel change with the dropout severity.
- **channel-independent** (new). Every cell uses the root key `ErrorKey(token, "dropout", "zero", replicate, protocol hash)`, and the per-draw derivation is unchanged. Each channel has its own field labels:
  - dropout: `dropout`;
  - localization and shape: `position_x`, `position_y`, `yaw`, `size_length`, `size_width`;
  - track instability: `fragmentation`.

  Each channel's draws therefore depend only on the token, replicate, track, step and field, and never on any channel's severity. Consequences:
  1. It is byte-identical to the released scheme in every cell whose dropout severity is zero. Here that is every cell except `dropout-medium` and the full coalition.
  2. The tracks that dropout hides become nested across severities.
  3. The full coalition shares its localization and fragmentation draws with `localization_shape-medium` and `track_instability-medium`.
- **Rejected alternative:** a key per channel that carries that channel's own severity. It would give each severity of a channel different draws, removing the sharing the released localization and track-instability sweeps already have. It would also redraw every cell that has an active random channel.

### 4.4 Factor 3: velocity estimate

- **finite-difference**, as released:
  - In the tracking stage, velocity is the change in the observed centre divided by the elapsed source time.
  - The reported (true) velocity is used instead on first sighting, on reacquisition after a fragmentation outage, and when the elapsed time is not positive.
  - Detections hidden by dropout still update the memory.
  - With independent position noise σ per axis, the velocity noise is √2·σ/Δt per axis: 7.07 m/s at the medium σ = 0.50 m and Δt = 0.1 s.
- **cv-kalman** (new): one constant-velocity Kalman filter per source track and per axis.
  - **State:** [p, v].
  - **Transition:** F = [[1, Δt], [0, 1]], with Δt the elapsed source time since the last fused measurement.
  - **Process noise:** Q = σ_a²·[[Δt⁴/4, Δt³/2], [Δt³/2, Δt²]], with σ_a = 3.0 m/s².
  - **Measurement:** z is the observed centre coordinate after latency, dropout and localization, with H = [1, 0] and R = σ_R², σ_R = 0.50 m in every cell.
  - **Initialisation**, on first sighting and on reacquisition: p = z, v = the reported velocity (the same value the released estimator emits there), and P₀ = diag(σ_R², σ_v0²) with σ_v0 = 1.0 m/s.
  - **Fusion** follows the released memory rules:
    - every detection that reaches the tracking stage outside a fragmentation outage is fused, whether visible or hidden by dropout;
    - a detection whose elapsed time is not positive is not fused, and the reported velocity is emitted, as released.
  - **Output:** only the velocity changes. The position, yaw and size passed to the AEB stay the observed ones.
  - **Why these values.** σ_R is the medium localization position standard deviation in the frozen error configuration, the severity of both cells here that have localization error. It is fixed rather than told each cell's severity, because a deployed tracker has one sensor model. σ_a is the magnitude of the policy's partial-braking target, a bound on ordinary urban manoeuvring. The values were set from the committed configuration files before any run. They are not tuned, and no other values will be run or reported. For these values, the filter equations imply a steady-state velocity standard deviation of about 0.69 m/s per axis (the filter's posterior covariance; for a target moving at constant velocity the realised error from σ_R alone is about 0.36 m/s per axis), with steady-state gain [0.292, 0.505]. That is a property of the filter, not a result.
  - **Parity with the released estimator.** Initialisation with the reported velocity, and fusion of hidden detections, mirror the released estimator, so the comparison isolates the steady-state estimate. Both estimators share two oracle elements:
    - initialisation with the reported velocity;
    - use of detections that dropout hid from the AEB, which a deployed tracker would never receive.

    Both elements are disclosed and not removed here. As a result, dropout degrades neither velocity estimate.
  - **Cost.** A constant-velocity filter trails a decelerating target. For these parameters, the velocity estimate lags a lead braking at 6 m/s² by about 2.4 m/s after 0.5 s and 3.3 m/s after 1.0 s. At 3 m/s² the lags are about 1.2 and 1.7 m/s. The finite difference lags by a·Δt/2, which is 0.3 m/s at 6 m/s². (This is filter arithmetic, not a result.) In cells without localization error (`coalition-none`, `dropout-medium`, `latency-medium` and `track_instability-medium`), the filter can only smooth whatever jitter the logged boxes carry, and it adds this lag. Which effect is larger is not known in advance; §7.4 pre-declares what lag alone would look like.

### 4.5 Arms

Each arm is one simulation run with its own oracle, because the released runner measures missed and false interventions against the oracle of the same run.

| Arm | Policy | RNG scheme | Velocity | Cells | Role |
| --- | --- | --- | --- | --- | --- |
| A `A-v1-replication` | v1 | dropout-keyed | finite-difference | all 8 | replication gate G2; the v1 arm of every contrast |
| B `B-v2-gated` | v2 | dropout-keyed | finite-difference | all 8 | primary |
| C `C-v1-kalman` | v1 | dropout-keyed | cv-kalman | all 8 | Q2 |
| D `D-v2-kalman` | v2 | dropout-keyed | cv-kalman | all 8 | Q2, policy × velocity, combined remedy |
| E `E-v2-channel-rng` | v2 | channel-independent | finite-difference | `no_aeb`, `oracle_aeb`, `dropout-medium`, full coalition | Q3 |

- The primary contrast, B against A, changes one factor and keeps the released draws.
- C and D complete a 2 × 2 of policy × velocity estimate under the released keying.
- E changes only the keying, under v2. It runs only the two cells whose draws the keying changes, plus the oracle every run needs, and `no_aeb` as a control. E's other cells are, by construction, the same as B's. A unit test proves the key equality, and G3 checks the shared oracle.

Each arm's run context records:

- the SHA-256 of the study file `configs/experiments/aeb_policy_v2_study.yaml` (committed with the tooling; it names this plan, the arms and their settings) and the arm identifier;
- the policy file and its SHA-256;
- the RNG scheme, and the velocity estimator with its parameters;
- the error configuration's SHA-256;
- the tooling commit and the container image identifier;
- the Python and numpy versions inside the container (G5, §8).

The arm's `run.log` stamps every line in UTC, so it gives the run's start and end.

Per-token documents keep the released schema, so that G2 can compare bytes.

### 4.6 Pairing and the unit of resampling

The error channels never read the ego's state or the AEB's command. So within a token and replicate, every arm sees the same recording, and under the same keying the same noise draws at every step. That is common random numbers.

- **The unit of pairing is the token.** Replicates are averaged within a token. In cells where no random draw can change the outcome (`no_aeb`, `oracle_aeb`, `coalition-none`, `latency-medium`), the replicates are identical. Counting scenario-replicates as independent would therefore triple-count their events. No statement in this study uses 1,032 as a sample size.
- **The unit of resampling is the nuPlan log within a family** (§6). Tokens from one log share vehicle, place and time. The 344 tokens come from 183 logs:
  - 113 logs contribute one token and 44 contribute two;
  - the largest five contribute 8, 10, 10, 11 and 14;
  - the size-weighted mean number of tokens per log is Σm²/Σm = 1,300/344 = 3.8 (3.4 over the 198 (family, log) clusters: 1,186/344).

## 5. Outcomes per token

Notation: token t, arm a and cell c, over replicates r = 0, 1, 2. In replicate r:

- C_tr = 1 if the run had a counted (at-fault) collision;
- D_tr = `simulated_duration_s`;
- I_tr = `intervention_duration_s`;
- N_tr = `contacts_not_at_fault`.

**Exposure depends on the policy.** A run ends at 15.0 s, at the end of the logged route, or at a counted collision. The simulated time is therefore affected by the policy: less braking can mean reaching the end of the route sooner. In the released evidence, `no_aeb` simulated 10,945.5 s and `oracle_aeb` 14,901.9 s, over 1,032 runs each (Appendix A). Rates below are normalised by simulated time; per-token means are not.

- **Collision indicator** Y_t = mean over r of C_tr. This is the released definition.
- **Any-collision binary** B_t = max over r of C_tr. It is used only for Clopper-Pearson bounds.
- **Braking share** (H1, H2): Σ_t Σ_r I_tr ÷ Σ_t Σ_r D_tr. This is the share of simulated time in partial or full braking, **including time held in braking at standstill**. It is the same definition as the released values in Appendix A. It is not an activation rate.
- **Braking seconds per scenario-replicate** (sensitivity for H1 and H2): Σ_t Σ_r I_tr ÷ (3 × 344).
- **Not-at-fault contact rate** (H3): Σ_t Σ_r N_tr ÷ Σ_t Σ_r D_tr × 1,000, in contacts per 1,000 simulated seconds.
  - `contacts_not_at_fault` counts the contacts excluded by the frozen rule: any contact while the ego is at or below 0.01 m/s, from any direction, and any contact with a body behind the ego whose speed magnitude exceeds the ego's.
  - The per-token mean of N is descriptive.
- **Any contact** A_t = mean over r of 1[C_tr = 1 or N_tr > 0]. It does not depend on the at-fault classification.
- **Avoided and induced collisions.** Both are defined against the same token's `no_aeb` run in the same arm. `no_aeb` is identical in every arm under G3, and its replicates are identical.
  - avoided_t = mean over r of 1[`no_aeb` has a counted collision and the cell has none];
  - induced_t = mean over r of 1[`no_aeb` has none and the cell has one].
- **Brake activations** K_t = Σ_r (number of entries in `matched_delay_s` + `false_interventions`). Matching against the arm's oracle is one to one, so this counts every contiguous episode of partial or full braking.
  - **Activation rate:** Σ_t K_t ÷ Σ_t Σ_r D_tr × 3,600, in activations per simulated hour.
  - Unlike missed and false interventions, it does not depend on which oracle an arm used.
- **Exposure outcomes:**
  - Σ_t Σ_r D_tr;
  - the count of runs that end early without a counted collision (`simulated_duration_s` < 15.0 and no counted collision).
- **Descriptive only:**
  - maximum deceleration;
  - first stop distance, conditional on stopping, with the count of runs that never stop;
  - matched brake-onset delay, and missed and false interventions, within an arm or between arms that share an oracle;
  - ego speed at the counted collision, √(2E/1500) from `collision_energy` (the ego's own speed, not the relative impact speed).

Level estimands are cohort means over the 344 tokens, with every token weighted equally. Rates are the ratios of sums defined above.

## 6. Statistics common to every interval and test

- **Resampling: a family-stratified log-cluster bootstrap.**
  - A cluster is a (family, log) pair: all tokens of one nuPlan log in one family (198 clusters, §3).
  - Each draw resamples, within each family, that family's clusters with replacement, as many clusters as the family has. It carries all tokens of each drawn cluster.
  - One draw of clusters is shared by every arm and cell; that sharing is the pairing.
  - Thirteen logs have tokens in more than one family (11 in two, 2 in three). Their tokens form a separate cluster in each family's stratum. So neither this bootstrap nor the (family, drive) intervals of §7.1 carry within-log correlation across families. The sign-flip test below does, because it flips whole logs. So do the drive-level sign flip and the whole-log bootstrap of §7.1.
  - 5,000 draws from `numpy.random.Generator(PCG64(20260831))`.
  - The released token-level `paired_scenario_bootstrap` is used only to reproduce the released intervals (gate G4).
- **Contrasts.** For a contrast Δ = θ(arm₂, cell₂) − θ(arm₁, cell₁), both terms are computed from the same draw.
  - θ is the token-weighted mean for per-token outcomes, and the ratio of sums over the drawn tokens for rates.
  - The point estimate is the full-sample value.
- **Intervals.** Two-sided percentile intervals, using numpy's linear interpolation as in the released code.
  - For each primary hypothesis (H1–H5), three intervals are reported:
    - the simultaneous Bonferroni 99.0% interval (1 − 0.05/5);
    - the percentile interval at 1 − 0.05/(K − i + 1) for its Holm step i;
    - the 95% interval.

    None of these intervals is the basis of a decision. Decisions come from the Holm procedure below.
  - Everything else: 95%.
- **p-values.**
  - **Collision-indicator contrasts use an exact paired sign-flip randomization test over logs.**
    - For each log g, D_g = Σ over the log's tokens of (Y_t(arm₂, cell₂) − Y_t(arm₁, cell₁)). D_g is computed in integer thirds, so ties are exact.
    - Let m be the number of logs with D_g ≠ 0. The two-sided p is the share of sign patterns s with |Σ s_g·D_g| ≥ |Σ D_g|.
    - If m ≤ 20, all 2^m patterns are enumerated.
    - If m > 20, 100,000 patterns are drawn from `PCG64(20260831)`, and p = (1 + count) ÷ (1 + 100,000).
    - If m = 0, then p = 1.
    - Flips are over logs rather than (family, log) clusters. A sign flip needs no stratification, and the log is the coarser unit.
  - **Every other contrast uses the cluster-bootstrap p:** p = min(1, 2·min(#{Δ* ≤ 0} + 1, #{Δ* ≥ 0} + 1) ÷ (5,000 + 1)). The smallest attainable value is 2/5,001 ≈ 0.0004.
  - **Why collision contrasts differ.** For sparse paired binary data, the bootstrap p is anti-conservative. When one arm has no collisions, every bootstrap Δ* has the same sign, so p reduces to twice the chance that no collision token is drawn. With four such tokens in different strata, that is about 2 × 0.366⁴ ≈ 0.036, where the exact sign test gives 2 × 0.5⁴ = 0.125.
- **Holm.** Within a family of K hypotheses at family-wise α = 0.05, sort the p-values in ascending order. Reject the i-th smallest while it is at most 0.05/(K − i + 1), and stop at the first that is not rejected.
- **Classification.**
  - A directional hypothesis is:
    - **supported** when Holm rejects it and the point estimate has the predicted sign;
    - **contradicted** when Holm rejects it and the sign is the opposite;
    - **not established** otherwise.
  - The two-sided hypothesis (H5) is classed as **increase**, **decrease** or **not established**.
  - "Not established" is not evidence of no effect or of equivalence.
- **Zero-event cells.** When no token of a cell has a counted collision in any replicate:
  - Report the exact two-sided 95% Clopper-Pearson interval for the token-level probability (B_t). It is given overall with n = 344, and per family with n = 100, 100, 100 and 44. For zero events the interval is [0, 1 − 0.025^(1/n)], which is [0, 0.0107] at n = 344.
  - The Clopper-Pearson bounds are descriptive. Their n counts tokens, which are correlated within logs, so the bounds are too narrow.
  - Clopper-Pearson intervals are also reported, descriptively, for cells with events.
  - When both terms of a collision contrast have zero events, its p-value is 1 (m = 0). It stays in its Holm family, K is unchanged, and it is classified not established. Both Clopper-Pearson intervals are reported.
  - A percentile interval that collapses to [0, 0] is not interpreted.

## 7. Pre-registered analysis

### 7.1 Primary family (K = 5, Holm)

Every primary contrast is **B − A**: policy v2 minus policy v1, with the released keying and finite-difference velocity.

| | Cell | Outcome | p-value | Predicted | Reason |
| --- | --- | --- | --- | --- | --- |
| H1 | `oracle_aeb` | braking share | cluster bootstrap | Δ < 0 | With perfect perception the observed velocity is exact, so gating removes braking for bodies beside or behind the path. |
| H2 | full coalition | braking share | cluster bootstrap | Δ < 0 | Gating removes rear bodies in every cell. Under localization error, it removes off-path bodies only on steps where their noisy velocity predicts no overlap (§4.2). H2 tests whether gating alone lowers braking despite that. |
| H3 | `oracle_aeb` | not-at-fault contact rate per 1,000 simulated seconds | cluster bootstrap | Δ < 0 | Logged agents do not react. Braking that is not needed leaves the ego stopped or slow where logged traffic drives into it from any side, or in front of faster followers. |
| H4 | full coalition | collision indicator | sign flip | Δ > 0 | The released zero counted collisions in this cell coincide with braking for most of the exposure. A contact while the ego is stopped is excluded, so part of the released zero may be reclassification rather than avoidance. |
| H5 | `oracle_aeb` | collision indicator | sign flip | two-sided | Safety check. Gating may remove braking that caused collisions after brake–release–re-accelerate cycles (Δ < 0). It may also remove braking for real threats the straight rollout misses (Δ > 0). |

**Sensitivity analyses.** They are reported in the same table and enter no decision:

- braking seconds per scenario-replicate, for H1 and H2;
- the per-token mean of not-at-fault contacts, for H3;
- the H1–H5 intervals recomputed with (family, drive) clusters. A drive is the log name without its trailing segment range: the 183 logs are segments of 101 drives;
- the H4 and H5 sign-flip p recomputed with flips over the 101 drives; reported only, it enters no decision;
- H1–H3 recomputed with an unstratified bootstrap over the 183 whole logs, in which all tokens of a log form one cluster whatever their family.

### 7.2 Decision rules and fixed wording

**The Q1 label comes from H1 and H2 only:**

- **Support.** H1 and H2 are both supported, each with a point estimate at or below −0.10 (at least ten percentage points of simulated time).
- **Partial support.** At least one of H1 and H2 is supported and neither is contradicted, but Support is not met.
- **No support.** Otherwise.

**H3 and H4** are each classified by §6 and stated on their own. They do not enter the Q1 label.

**H5 qualifier.** If H5's unadjusted sign-flip p is below 0.05 and its estimate is above zero, the label is printed as "<label> (with more oracle collisions)". The unadjusted level is deliberate: for a harm check, a missed signal costs more than a false alarm. H5's Holm classification is printed as well.

**Fixed wording in `results.md`:**

- The Q1 verdict says where the released braking came from. It is not a claim that v2 is safer, and `results.md` does not use "safer" or "fixes" about v2.
- The verdict paragraph always states the following in the same paragraph as the label:
  - H5: estimate, sign-flip p, 95% interval and Holm classification;
  - H4;
  - the induced and avoided split for `oracle_aeb` and the full coalition (§7.3.4).
- The H5 sentence is exactly one of:
  - if the unadjusted p < 0.05 and Δ > 0: "Under oracle perception, gating increased counted collisions.";
  - if H5 is classified "decrease" by Holm: "Under oracle perception, gating reduced counted collisions.";
  - otherwise: "This study does not detect a change in counted collisions under oracle perception."

  A decrease with an unadjusted p < 0.05 that Holm does not reject falls under the last sentence; its estimate and p are printed beside it.
- The H4 sentence is one of:
  - supported: "Under collision-course gating, the all-channel configuration no longer has zero counted collisions.";
  - otherwise: "This study does not show that the zero depends on the released target selection."

  It is followed by the Clopper-Pearson intervals of both arms and by the exposure contrasts of §7.3.7.
- The H3 statement always carries this sentence: "Excluded contacts and counted collisions are coupled by the stopped-ego rule, so less braking can move contacts from the excluded class into counted collisions."

### 7.3 Secondary analyses (95%, not a family of tests)

1. **Other cells.** B − A for `coalition-none` and the four single-channel medium cells, on the collision indicator, braking share and the not-at-fault contact rate.
2. **How much of the error pattern survives gating.** The difference in differences (full − `coalition-none`) under B minus the same under A. The same for `localization_shape-medium` − `coalition-none`. Both on the collision indicator and braking share.
3. **Combined remedy.** D − A (v2 with cv-kalman, minus the released controller) for `localization_shape-medium` and the full coalition, on the collision indicator, braking share and the not-at-fault contact rate.
4. **Benefit and harm, separately.** B − A on induced and on avoided collisions, for `oracle_aeb` and the full coalition, with Clopper-Pearson intervals for zero cells.
5. **Any contact.** B − A on A_t, for `oracle_aeb` and the full coalition. This outcome does not depend on the at-fault classification.
6. **Activation rate.** B − A, C − A and D − B, for `oracle_aeb` and the full coalition.
7. **Exposure.** B − A on total simulated time and on the count of runs that end early without a counted collision, for `oracle_aeb` and the full coalition. These are reported beside H4 and H5.

### 7.4 Q2: velocity estimate

- **Family Q2 (K = 4, Holm, cluster-bootstrap p).** Outcome: braking share, predicted Δ < 0 for each of:
  - C − A in `localization_shape-medium`;
  - C − A in the full coalition;
  - D − B in `localization_shape-medium`;
  - D − B in the full coalition.

  Each is classified by §6. Q2's answer is these four classifications, with no composite label.
- **Descriptive (95%):**
  - C − A and D − B for every corrupted cell, on the collision indicator, braking share and the not-at-fault contact rate;
  - the interaction (D − B) − (C − A).

  C shares A's oracle, and D shares B's. Missed and false interventions and onset delays are therefore compared only between arms that share an oracle: C − A, D − B (here) and E − B (§7.5).
- **Pre-declared cost check (descriptive, 95%).** In `coalition-none` and in the `dropout-medium`, `latency-medium` and `track_instability-medium` cells, the filter smooths only the logged boxes' own jitter and adds lag (§4.4). There, C − A on the collision indicator is expected to be at or above zero, and matched onset delay is expected to be later under C than under A. A and C share an oracle, so their onset delays are comparable.

### 7.5 Q3: keying

- **Answer.** The ratio SD_B ÷ SD_E of cluster-bootstrap standard deviations of the braking-share contrast full − `localization_shape-medium`. SD_B is taken under B; SD_E under E, where the two cells share their localization draws. E's `localization_shape-medium` is B's, as §4.5 explains. The ratio is reported as a descriptive number.
- **Implementation check.** E − B for `dropout-medium` and the full coalition, on five outcomes:
  - the collision indicator (sign flip);
  - braking share, the not-at-fault contact rate, and missed and false interventions per token (cluster bootstrap).

  That gives 10 contrasts, with Holm at 0.05 over the 10. Both keyings are valid draws from the same distributions, so the expected difference is zero. Replicate noise is already inside the token-level variance. A Holm rejection therefore triggers a byte-level audit of the keying code, and is not interpreted as an effect. The audit re-derives, for the tokens behind each rejected contrast, the keys and draws of arms B and E and checks them against §4.5. Its commands and outcome are recorded in the operator log, and `results.md` reports it. The study's results are not published before the audit is recorded. If it finds a defect, §9's tooling-defect path applies; otherwise the rejection is reported as it came out, as an implementation-check result.

### 7.6 Descriptive (no inference)

1. For every arm × cell, overall and per family:
   - counted collisions, in records and in tokens;
   - braking share and braking seconds per scenario-replicate;
   - the not-at-fault contact rate;
   - any contact;
   - the activation rate;
   - maximum deceleration;
   - Clopper-Pearson intervals.
2. Within each arm, missed and false interventions against that arm's oracle, and the distribution of matched onset delays (count, median, quartiles). Policies use different oracles, so these are **never** compared across policies.
3. First stop distance conditional on stopping, with the count of runs that never stop.
4. Ego speed at counted collisions: count, median and maximum.
5. The H1–H5 contrasts per family, 95%, descriptive. `bicycle_or_vru` has 44 tokens in 8 logs.

## 8. Gates before any result is read

Gates G1–G3 and G5 must pass before any contrast is computed, except as §9 provides for a G2 failure, and G4 must pass before any number is computed. A failure stops the analysis for investigation. After arm A finishes, `study verify --arm A-v1-replication` runs G1, G2 and G5 on arm A alone, so that a replication failure stops the run before arms B–E.

- **G0: before the formal run.**
  - The study tooling pull request (branch `study/aeb-policy-v2-tooling`) is merged with 100% branch coverage.
  - Unit tests prove three things:
    1. the v1 path through the new code equals the released path on every existing step-loop and golden-sequence test;
    2. the channel-independent key equals the released key whenever the dropout severity is zero;
    3. the finite-difference path is unchanged.
  - A pilot runs every arm with `--pilot` on the six-token smoke manifest (nuPlan mini split; none of its tokens is in the cohort), into `artifacts/pilot/`. It checks only that every token is valid, and how long a token takes. Its per-token outputs are not analysed and never reported. The development cohort is not used.
  - The operator records G0 in `g0.json`: the tooling merge commit, the CI run id for that commit, and the pilot's run contexts and `study verify --pilot` output. `study evidence` validates it, copies it into `reproduction.json`, and refuses to record G0 or G5 without its input.
- **G1: integrity, for each arm.**
  - `run_complete.json` lists exactly the 344 manifest tokens.
  - There is one document per token per listed cell, and no other files except `run_context.json`, `run_complete.json` and `run.log`.
  - Every document is valid, with replicates 0, 1 and 2, and no token is invalid.
  - The run context records:
    - protocol `bbf0b6d3…59f9` and cohort `65e38df2…daf9`;
    - the committed study file's hash;
    - the arm's policy, keying and estimator;
    - the tooling commit;
    - the container image identifier recorded before the run (and, when all five arms are verified, the same identifier in every arm).
- **G2: v1 replication.** Every one of arm A's 2,752 per-token documents (8 cells × 344 tokens) is byte-identical to the released document for the same cell and token.
- **G3: invariance.**
  - `no_aeb` is byte-identical to the released documents in all five arms.
  - `oracle_aeb` is byte-identical to the released documents in A and C.
  - `oracle_aeb` is byte-identical across B, D and E.
  - In arm-A reference mode (§9), the comparisons with the released documents are reported but not required, and §9 gives what replaces them.
- **G4: analysis reproduction.** The study's analysis code is run on the released records, using the released `paired_scenario_bootstrap` exactly as at v1.0.0 (index-based means), not a reimplementation. For the eight study cells, it must reproduce with float equality:
  - every estimate, bound, confidence, resample count and seed in [`intervals.json`](../../evidence/nuplan_aeb_v2/intervals.json);
  - `collisions`, `contacts_not_at_fault`, `simulated_seconds` and `mean_intervention_duration_s` in [`evaluation.json`](../../evidence/nuplan_aeb_v2/evaluation.json).

  This checks the loaders and metrics. The addendum has its own reproduction gate over all 26 configurations.
- **G5: environment.** Inside the container: Python 3.9.19 and numpy 1.23.4. `study simulate` records both in every arm's run context, and `study verify` checks them there and writes the result into the gate file with G1–G3. Both versions are recorded in `evidence/reproduction.json`, together with every gate's outcome.

## 9. Failed runs, and what is reported anyway

- **A failed run.** An attempt is one execution of all arms under one tooling commit, in its own attempt directory. Any gate failure, or any invalid token in any arm, ends the attempt as failed, except a G2 failure that continues in arm-A reference mode (below). (The released run had no invalid token.)
  - No contrast is computed or read after a failure.
  - A defect found in the tooling is fixed in a separate pull request, with a failing test first. Every arm is then rerun from scratch into a new attempt directory. The plan is not changed. The fix is logged in §13 as a numbered incident that changes no plan content; it is not a deviation. A rerun after a failure is reported in `results.md`.
  - An interrupted run (a host restart or a container stop) may be resumed with `--resume`, which revalidates every existing document. That is the same attempt, not a failure.
  - A failure before the formal run, in the `study preflight` check or in G0 (including an invalid pilot token), is fixed and the step repeated. That check covers the inputs, the released records, and the size and SHA-256 of each referenced log. Such a failure does not use up an attempt.
  - After two failed attempts, the study is reported as **not completed**. The results page then gives the gate diagnostics and the cause of each failed attempt, with no contrast and no claim about the policy. Each of its numbers (gate outcomes and per-cell counts of differing documents) is registered as a claim in a claims registry that holds only these diagnostics. `study evidence` writes the gate report and `reproduction.json`, with no summary. That page is still published, in the same new release as the post-hoc addendum.
- **If G2 fails,** a code cause is looked for first: a tooling fix as above, then a new attempt. If no code cause is found, it is an environment difference. The number of differing documents per cell is recorded and published. The repository owner then decides whether to continue with the rebuilt arm A as the v1 reference (arm-A reference mode). The decision is taken before the first formal command for arm B, C, D or E starts, and is recorded as a numbered deviation in §13, with "No" under "Formal B–E output existed?". In arm-A reference mode:
  - G2 is reported as failed, with the per-cell count of differing documents;
  - G3's comparisons with the released documents are reported but not required. G3 instead requires `no_aeb` to be byte-identical across all five arms, and `oracle_aeb` to be byte-identical between A and C and across B, D and E;
  - G1, G4 and G5 are unchanged;
  - `study verify` and `study analyse` run with `--reference arm-a`. The flag stamps `reference: arm-a` and `exploratory: true` into the gate file, `reproduction.json` and the summary, and every claim's text begins with "Exploratory:". Without the flag, `analyse` refuses unless every gate passed;
  - every output, claim and results page of this study is labelled exploratory.
- **Reported whatever the outcome:**
  - every primary and secondary result above, in the direction it came out;
  - every failed attempt and its cause;
  - the gate report.

## 10. What will not be done

- **No change to released material:** no edit to the released evidence, claims, release notes, tag or release, the protocol, the formal matrix, `policy_v1.yaml`, the error configuration, or their packaged copies.
- **No choice after the fact:** no threshold, margin, horizon, filter parameter, cell or arm is chosen after any result.
- **No other gate:** no alternative gate is run in this study. That rules out a planned-trajectory or route-following corridor, a lateral-only corridor, stage-priority selection, and release on every candidate. Any later gate is a new pre-registration.
- **No other model changes:** no reactive agents, and no change to the nominal controller, the at-fault rule or the collision end rule.
- **No v2 attribution:** no 26-cell v2 matrix, and no v2 Shapley values, which would need all 16 coalitions.
- **No cross-policy comparisons** of missed or false interventions or of onset delays, because they are measured against different oracles. The activation rate is not such a comparison; it does not use the oracle.
- **No use of the development split**, and no pilot output in any result.
- **No per-token publication:** no per-token table, token identifier, scenario list or nuPlan replay from this study.
- **No external claims:** nothing about real-world AEB performance, and no regulatory or standards-compliance claim.

## 11. Publication and licence

- **Licence.** nuPlan-derived material is shared under CC BY-NC-SA 4.0 and the Motional dataset terms. Only aggregate numbers are published. The per-token records stay in the Git-ignored `artifacts/` directory.
- **Where results go.** They are published in `docs/studies/aeb-policy-v2/`, which holds:
  - this plan;
  - `results.md`;
  - `claims.yaml`, a registry separate from `docs/claims.yaml`, with claim identifiers beginning `p3.study.policy-v2.`;
  - `evidence/`, with the summary, the derived evidence, the gate report (`gates.json`, and `gates-attempt-<n>.json` for each earlier failed attempt, so that every number the page reports has a claim), `reproduction.json` and the operator log `operator-log.txt` (no token identifiers, no local absolute paths);
  - a licence notice.
- **Provenance a reader can check.** `reproduction.json` records the pre-registration pull request's number, its merge commit and its `merged_at` time, read from the GitHub API. For each arm it also records the tooling commit SHA, the image ID, and the UTC start and end read from that arm's `run.log`. A contract test checks that the pre-registration merge commit is an ancestor of every arm's tooling commit.
- **"What this does not show".** This section of `results.md` includes:
  - the selection-coupling paragraph of §12;
  - the velocity leak of §4.2;
  - the sentence "This is a simulation of non-reactive logged traffic; it says nothing about real-world AEB performance."
- **Release.** Results are released as a new version, and v1.0.0 stays as it is.
- **README.** The interpretation-boundary text stays. The pull request that publishes this study's results adds a pointer to this directory beside that section's text in both READMEs (`## 解釋邊界` in `README.md`, `## Interpretation boundary` in `README.en.md`), with no numbers.

## 12. Risks and limitations

- **Few collision events.** H4 and H5 are decided by the sign-flip test over logs. When every log with a nonzero difference points the same way, p = 2^(1−m) for m such logs. So H4 needs at least 6 logs with a v2-only collision to reach 0.05, the threshold of the last Holm step, and 8 to reach 0.01, the threshold of the first. The released oracle collision indicator is 0.0378 (39 records, Appendix A), so collision contrasts can detect only large differences.
- **The gate inherits the observed velocity.** See §4.2. Under localization error it can admit bodies beside the path on many steps (about a quarter in the worked example of §4.2).
- **Selection coupling.** Every cohort token was admitted because at least one oracle body passed this same predicate against the logged ego within the first 4 s (`src/aebrisk/cohort/prefilter.py`). The cohort therefore under-represents threats that a straight-line, current-heading rollout does not predict. H5 cannot estimate how often the gate misses such threats outside this cohort. The protocol comment describes the prefilter corridor as swept along the expert route. The code, which this plan follows, uses the straight-line rollout along the current heading.
- **The straight rollout.** Curve scenarios in `cut_in_or_crossing` can be judged wrongly by v1 and v2 alike. H5 watches for harm.
- **Residual masking, and release on the selected candidate,** remain in v2. The v2 numbers therefore still describe an imperfect controller.
- **Braking share is near its ceiling** in localization cells, which limits how far the tracker factor can move it.
- **The Kalman variant:**
  - It has one fixed parameter set. Its result says what this filter does, not what the best tracker would do.
  - It lags decelerating targets (§4.4).
  - It shares two oracle elements with the released estimator: initialisation with the reported velocity, and fusion of detections hidden by dropout.
- **Clustering beyond the log.** The 183 logs are segments of 101 drives, and segments of one drive also share vehicle and day. The plan resamples logs. The drive-level sensitivities of §7.1 show how much wider the intervals become and how the H4/H5 p-values move.
- **The v1 image is no longer on the host.** G2 is the only proof that the rebuilt environment matches.
- **Reading before the gates.** Nothing technically stops someone opening an arm's documents before the gates run. The published operator log (`evidence/operator-log.txt`) is the control, and `reproduction.json` gives the run times a reader can compare with the freeze point.

## 13. Deviations

None at the freeze. Each later entry is one of two kinds:

- a **deviation** changes the plan's content. It is allowed only before the first formal command for arm B, C, D or E starts, and must have "No" under "Formal B–E output existed?";
- an **incident** records a tooling defect fixed as §9 says. It changes no plan content, may have "Yes" under "Formal B–E output existed?", and states that no plan content changed.

Each entry takes this form:

| No. | Date | Change | Reason | Pilot output existed? | Formal B–E output existed? |
| --- | --- | --- | --- | --- | --- |

## Appendix A. Released values quoted in this plan

Every number below is read from committed evidence under [`docs/evidence/nuplan_aeb_v2/`](../../evidence/nuplan_aeb_v2/). E = `evaluation.json` and I = `intervals.json`.

Braking share is derived from `evaluation.json`. It is shown, unregistered, in the descriptive figure `docs/figures/collisions-vs-braking.svg`. The not-at-fault rate is derived and not published. Both are computed as follows:

- braking share = `mean_intervention_duration_s` × `scenarios` ÷ `simulated_seconds`;
- not-at-fault rate = `contacts_not_at_fault` ÷ `simulated_seconds` × 1,000.

Both use the pointers given below; `scenarios` = 1032 in every row.

| Cell | E index | Counted collisions (records) | Not-at-fault contacts (records) | Simulated seconds | Braking share (derived) | Not-at-fault per 1,000 s (derived) | Collision indicator (I) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `no_aeb` | `/configurations/0` | 204 | 261 | 10,945.5 | 0.000 | 23.8 | 0.1977 |
| `oracle_aeb` | `/configurations/1` | 39 | 1095 | 14,901.9 | 0.634 | 73.5 | 0.0378 |
| `dropout-medium` | `/configurations/3` | 19 | 1150 | 15,078.2 | 0.652 | 76.3 | 0.0184 |
| `localization_shape-medium` | `/configurations/6` | 0 | 1168 | 15,480.0 | 0.914 | 75.5 | 0.0000 |
| `latency-medium` | `/configurations/9` | 30 | 1113 | 14,891.7 | 0.648 | 74.7 | 0.0291 |
| `track_instability-medium` | `/configurations/12` | 27 | 1140 | 15,044.2 | 0.657 | 75.8 | 0.0262 |
| `coalition-none` | `/configurations/14` | 27 | 1149 | 15,043.5 | 0.659 | 76.4 | 0.0262 |
| full coalition | `/configurations/25` | 0 | 1150 | 15,395.8 | 0.889 | 74.7 | 0.0000 |

- **Column sources.** The E columns are `…/collisions`, `…/contacts_not_at_fault` and `…/simulated_seconds`, with `…/mean_intervention_duration_s` and `…/scenarios`, under each E index. The collision indicator is `/intervals/<cell>/collision_indicator/estimate` in I.
- **Example.** The oracle's braking share is 9.150872093023263 × 1032 ÷ 14,901.9 = 0.634.
- **Cohort.** E `/cohort_size` = 344 and `/common_valid_tokens` = 344.
