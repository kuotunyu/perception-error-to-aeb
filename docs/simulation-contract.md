# Simulation contract

The study holds the scenario, initial state, route, controller, step rate,
requested maximum horizon and termination rules fixed across configurations.
The declared observation pipeline and error configuration may differ. Actual
executed duration can differ when a collision or route end terminates a run;
rates use each configuration's measured exposure. This document states these
settings and where each is enforced.

## The wiring

| Decision | Value | Why it is not an option |
| --- | --- | --- |
| Agents | non-reactive, replaying the log | A reactive background would respond to the ego's braking, so a perception error would change the other vehicles' behaviour as well as the ego's, and the attribution could not separate the two causal paths. |
| Ego | closed loop, driven by this project's controller | A replayed ego would give every configuration the same trajectory, and the AEB would command brakes that never happened. |
| Expert longitudinal braking | not available to the controller | The logged driver already avoided most of these collisions. A controller with access to that braking would inherit an avoidance the perception pipeline never earned, and every configuration would look competent. |
| Step rate | 10 Hz | Fixed by the protocol. |
| Requested maximum horizon | 15 s for the formal nuPlan study | The horizon and termination rules are fixed; collision or route end can shorten actual exposure, which is recorded separately for each configuration. |

`build_simulation_wiring` refuses any other value rather than accepting it, and
the wiring identifier it returns is cited in every run record beside the planner
identity. The cost of non-reactive agents is that the scenarios are slightly
unrealistic; the benefit is that the experiment has one independent variable.

## The scenario source

Scenarios are read from the nuPlan log databases through the devkit's raw-SQL
query layer, not through its scenario builder. The builder imports the map stack
and the observation types, which need rasterio and OpenCV; this container has
neither, so the boundary this project claims — world state, database and maps,
no sensor replay — is enforced by the environment rather than by discipline. An
edit that reaches for the sensor API cannot import.

`aebrisk.nuplan_adapter.query_scenario` is the whole of that bridge. It answers
the four members `oracle_world_frame` is allowed to read — the token, the
iteration count, the ego state at an iteration and the tracked objects at an
iteration — and obtains each by querying the log directly.

**The iteration grid is the protocol's rate, not the log's.** nuPlan records
lidar at about 20 Hz; the study steps at 10, so every second frame is taken. The
stride is computed from the log's *measured* interval rather than assumed: a log
recorded at another rate would otherwise be simulated at that rate, and every
duration this study reports — brake-onset delay, intervention duration — would
be scaled by a factor nobody chose, with nothing downstream looking wrong. A
rate the protocol's does not divide evenly is refused rather than resampled onto
instants the recording does not have.

**A recording too short for the full duration is refused.** Truncating a
scenario is not the same experiment as running it to the end, and a cohort
holding both would compare configurations over different amounts of time. Having
enough frames is a property of the recording, so it is an eligibility rule and is
recorded for every token, accepted or rejected.

## What the operator supplies

`NUPLAN_DATA_ROOT` names a mounted nuPlan installation; the split is a command
option. `simulate --scenario-source nuplan` refuses with the path it looked for
when nothing is mounted, in a second, rather than meeting a driver error three
layers down. `NUPLAN_SENSOR_ROOT` being set at all is a refusal: a configured
sensor root means this study's boundary has moved, and that should be deliberate
rather than inherited from a shell.

`--scenario-source synthetic` exercises the whole pipeline with no licensed data,
which is what lets the command line be checked in CI and on any machine.

## Missed and false interventions

Neither is a property of a run. Both are defined by comparing one run's braking
with the ORACLE run's braking on the same token, which no single simulation can
see. So a simulator returns what it measured together with its command trace,
and `run_common_scenario` — the only place that holds all of a token's runs —
assembles every result record once the last configuration has finished.

The reference is the single configuration whose observation is the oracle's and
whose AEB is enabled. A matrix without exactly one of those is refused before
anything is simulated: with none, the two headline metrics have nothing to be
measured against; with two, which one was chosen would decide every missed
intervention in the study. The oracle passes through no error channel, so its
replicates must produce the identical trace, and replicates that disagree are
refused for the same reason.

A configuration with no AEB is scored as zero missed and zero false rather than
as having missed everything the oracle braked for. It has nothing to brake with,
and counting the oracle's interventions against it would make the baseline that
exists to show the scenario was dangerous read as the study's worst perception
failure.

The matched delay is SIGNED: this run's onset minus the oracle's. A negative
delay is an intervention that arrived early, which is neither missed nor false,
and folding the sign away would report it as late.

A token whose run fails partway is returned as that failure alone. The runs that
had already finished were measured correctly, but the comparison they would be
scored by never happened, and a record carrying zeros for those two fields would
be a fabricated result rather than a partial one.

## A contact is not the same thing as a collision the ego caused

The agents replay the recording and never react. An ego that brakes — correctly,
for a pedestrian — is therefore driven into by the vehicle that was following it
in the log, which had a moving ego ahead of it and has one standing still.

This is not a hypothesis. The first smoke over real nuPlan scenarios, on
2026-09-06, put `oracle_aeb` at twelve collisions against `no_aeb`'s three. Every
one of the twelve happened at an ego speed of 0.00 m/s, and the striking body in
the case examined was 4.97 m BEHIND the ego's centre, 0.01 m off its axis, moving
at 6.22 m/s. Counting all contacts changes the reported comparison. The study
therefore reports counted and excluded contacts under the explicit rule below;
these categories alone do not establish a causal safety benefit or harm.

This study applies its declared simulation contact classification in
`ego_at_fault`, in `simulation/step_loop.py`:

- Contacts while the ego is at or below the stopped-speed threshold are excluded.
- Contacts with a body behind the ego whose speed magnitude exceeds the ego's
  speed are excluded. This compares speed magnitudes, not relative longitudinal
  closing velocity.
- All remaining contacts are counted in the collision metrics.

This is the study's operational definition of "at fault", not a determination
of legal responsibility or a demonstrated equivalence to every upstream nuPlan
collision condition. Both counted collisions and `contacts_not_at_fault` must
be reported: zero counted collisions does not mean contact-free operation,
better perception or demonstrated real-world safety. The frozen results retain
this rule; changing it requires a new protocol version.

Two consequences are deliberate. A contact excluded by this rule is
**counted, in `contacts_not_at_fault`, rather than dropped** — a run that the
recording rear-ended is a fact about the simulation and a reader has to see how
often it happens. And it **does not end the run**: ending it would give the
braking configurations less exposure than the others and bias the comparison the
same way again, one level down. Each body is counted once however long the
overlap lasts.

An at-fault collision does end the run, because integrating a vehicle through a
body it has hit is not a simulation of anything.

## Known controller and modelling choices

A review after the release found the behaviour below while the released
braking numbers were being explained. It is part of the frozen AEB policy v1 and of the
simulation that every released configuration ran through, oracle AEB included,
and it is recorded here so that the braking numbers can be read correctly.
Changing any of it is a new policy or protocol version with new evidence; no
released number is revised by this section.
`tests/unit/aeb/test_policy_v1_known_limitations.py` pins the target-selection
cases and `tests/unit/simulation/test_step_loop.py` pins the error-key pairing.

**Target selection is not path-gated.** Each step, `assess_threat` scores every
visible track and `select_highest_required_deceleration` keeps the one with the
highest required deceleration (`aeb/threat.py`). Required deceleration is
`v^2 / (2 d)` on the closing speed along the ego's heading and the absolute
longitudinal gap between the two bodies. It has no lateral term and does not
check that the body is ahead of the ego. The full and warning stages fire on
required deceleration alone, whether or not the constant-velocity rollout
predicts an overlap; only the time-to-collision conditions need one
(`aeb/state_machine.py`). Two consequences follow:

- Phantom braking. A stationary vehicle beside the path, or a slower vehicle
  behind the ego, can be selected and demand full braking although no overlap
  is predicted.
- Masking. An off-path body with a higher required deceleration can be selected
  in place of an in-path body whose time to collision qualifies for partial
  braking. The command then drops to a warning, or to no stage at all when the
  off-path body's required deceleration is at or below the warning threshold;
  neither makes a braking demand.

**The nominal controller does not slow for other road users.** This is the
study's control by design: it reads only the route and the map, and its target
speed is the smallest of the logged initial speed, the map limit and 13.9 m/s
(`simulation/route_follower.py`). The AEB is therefore the only part of the ego
that slows for anything. Once an intervention releases after its clear steps,
the nominal controller accelerates back toward its target speed, so the same
situation can start another intervention later in the run.

**Tracked velocity is a finite difference of observed positions.** Outside the
oracle, a track's velocity is estimated from its successive observed positions
divided by the elapsed time (`errors/fragmentation.py`,
`observation/tracking.py`), at zero severity as well; a frame with no earlier
position to difference against, such as a track's first observation or its
first after reacquisition, carries the oracle value instead. Position noise
is amplified by the step rate, and the estimate feeds the closing speed, the
rollout and therefore both braking conditions. Together with target selection
that is not path-gated, noise on any nearby track can raise the required
deceleration the controller acts on.

**One error key seeds every random channel.** The step loop builds a single
`ErrorKey` that names the dropout channel and carries the configuration's
dropout severity (`simulation/step_loop.py`), and the dropout, localization/shape
and track-instability (fragmentation) draws are all derived from it; latency
draws nothing. Two configurations that differ only in dropout severity therefore
draw different localization/shape and track-instability noise for the same
token and replicate. Common random numbers
hold across configurations that share a dropout severity, not across a change in
it, so a difference between such configurations, including the dropout
contributions in the Shapley decomposition, combines the dropout effect with a
new noise realisation of the other channels.

**The ego counts as stopped at or below 0.01 m/s.** `STOPPED_SPEED_MPS` in
`simulation/step_loop.py` decides both the recorded stop distance (the first
step at or below it) and the contact rule above: a contact while the ego is
stopped is excluded from counted collisions and recorded in
`contacts_not_at_fault`. Because logged actors do not react, a configuration
that brings the ego to rest more often can move contacts from counted
collisions into `contacts_not_at_fault`.

The experiment card's
[Reading the braking numbers](experiment-card.md#reading-the-braking-numbers)
explains how these choices show up in the released results.

## Study-only options

The pre-registered policy v2 study
([analysis plan](studies/aeb-policy-v2/analysis-plan.md)) runs this simulation
with three factors the release does not use. Each is a field of
`ExperimentConfiguration` (`simulation/common_cohort.py`) whose default is the
released behaviour: `aeb_policy="v1"`, `rng_scheme="dropout-keyed"` and
`velocity_estimator="finite-difference"`. No configuration of the formal matrix
sets any of them, so `aeb-risk simulate` and every released record are
unchanged, and the study's arm A, which sets none of them, must reproduce the
released records byte for byte. Only `aeb-risk study simulate` sets them, from
the arm's entry in `configs/experiments/aeb_policy_v2_study.yaml`, and each
arm's `run_context.json` names the three values it ran. Everything in the
section above holds under every option, except where this section says
otherwise.

**Policy v2 selects only bodies on a collision course.**
`configs/aeb/policy_v2.yaml` has the schema version `aeb-policy/v2`, copies
every other value of `policy_v1.yaml` and adds a `target_selection` block. The
policy validator refuses any other `target_selection` block, and refuses one in
a v1 policy (`aeb/state_machine.py`). Each step, every visible track is still
assessed by the unchanged `assess_threat`, and `min_ttc_s` is still taken over
all of them.
`collision_course_candidates` (`aeb/threat.py`) then keeps a track as a
candidate only when both of these hold:

- the assessment predicts an overlap: the ego is rolled out along its current
  heading and the track at its observed velocity, at constant velocity over
  4.0 s in 0.1 s samples with a 0.5 m margin, the assessment's own defaults;
- the body is not behind: the signed separation of the two centres along the
  ego's heading is at least zero, zero included.

The AEB selects among the candidates only, with the selection rule, thresholds,
stages, warning dwell and release rule of v1. With no candidate it sees no
threat, and its command names no selected track; no record carries that field.
`tests/unit/aeb/test_collision_course_gate.py` pins the gate on the cases of
the target-selection limitation above, and
`tests/unit/aeb/test_policy_v2_behaviour.py` pins what follows in the closed
loop: v2 does not brake for a parked car beside the path or for a slower car
behind, and an off-path body no longer masks an in-path one.

The gate leaks through the velocity. The rollout uses the tracked velocity,
which outside the oracle is the finite difference above, so under localization
error the noise on a parked car's velocity can predict an overlap on some
steps. A car parked 20 m ahead of an ego at 15 m/s and 3.5 m to its side, but
observed moving toward the path at 2.0 m/s, is a candidate with a time to
collision of 1.0 s, below the full-braking threshold; the gate's test file pins
this case. Section 4.2 of the analysis plan estimates how often such steps
occur, and explains why one leaking step in several can hold the AEB in
braking.

**Channel-independent keying.** `error_key_for` (`errors/pipeline.py`) builds
the root error key of a run, and both the step loop and `aeb-risk replay` build
theirs with it. Under `dropout-keyed`, the released scheme above, the key
carries the configuration's dropout severity. Under `channel-independent` it
carries severity zero in every configuration, so each channel's draws depend
only on the token, the replicate, the track, the step and the channel's own
field labels, and never on any severity. Three consequences follow. In every
configuration whose dropout severity is zero, the two schemes build the same key
and draw the same noise. The tracks dropout hides become nested across its
severities. And the full coalition shares its localization/shape and
track-instability draws with the single-channel configurations of those
channels. `tests/unit/errors/test_error_key_schemes.py` pins the key equality,
the independence from every severity and the nesting.

**Constant-velocity Kalman velocity.** Under `cv-kalman`, `CVKalmanVelocity`
(`observation/tracking.py`) replaces the finite difference with one
constant-velocity Kalman filter per source track and per axis. Its process
noise is 3.0 m/s², its measurement noise 0.5 m and its initial velocity
standard deviation 1.0 m/s (`CV_KALMAN_PARAMETERS`); the study file records the
same values, and each arm's run context names them. The fragmentation channel
calls it under the released memory rules:

- on a track's first observation, and on its first after reacquisition, the
  filter starts from the observed position and the reported velocity, and emits
  the reported velocity, as the finite difference does;
- every later detection outside a fragmentation outage is fused, including one
  that dropout hid from the AEB; nothing is fused during an outage;
- a detection whose source time is not later than that of the last fused one
  is not fused, and its reported velocity is emitted;
- the elapsed time is measured from the last fused detection, where the finite
  difference measures from the last detection seen, so the two differ in this
  only after an out-of-order timestamp.

Only the velocity changes: the position, heading and size passed to the AEB
stay the observed ones. Each run owns its filters, so no state passes from one
run to another. The filter trails a decelerating body, a cost section 4.4 of
the analysis plan states. `aeb-risk replay` rebuilds released configurations
only, and all of them use the finite difference.

## Measured simulation exposure

New runs emit `aeb-scenario-result/v2`. Its required `simulated_duration_s` is
the number of executed steps (`len(outcome.states)`) divided by the setup's
frequency in Hz. It includes the final collision or route-end step. Valid
results require a finite, strictly positive duration. An infrastructure failure
with no recoverable step count explicitly records `null` and remains excluded
from every configuration's common valid cohort.

The requested horizon and braking duration are different measurements. For
example, the synthetic stationary-lead diagnostic has a 9 s horizon, but its
no-AEB run collides after 56 steps at 10 Hz and records 5.6 s. This is synthetic
verification only, not evidence about nuPlan or a real AEB.

`metrics.safety.measured_simulated_seconds(results, cohort=...)` sums each
included replicate's actual duration for one configuration, after selection of
the common valid cohort. Formal per-hour rates must use this measured sum.
`summarize_configuration` continues to accept an explicitly supplied measured
denominator for historical callers. Historical `aeb-scenario-result/v1` records
remain readable and retain their original fields, but the measured-exposure
helper and formal resume refuse valid v1 records: missing elapsed time is never
filled from the horizon, intervention duration, or zero. Token JSON preserves
the nested version discriminator and v2 duration through serialization.

## Invalidity

Infrastructure-invalid scenarios are removed from **all** configurations before
any outcome is aggregated, and the invalid record names the token, the common
failure reason, the phase, the exception type and a stack-trace hash. A scenario
may never be removed from only the configuration in which it performed badly:
that would make the cohort a function of the outcome, which is the one thing a
common-cohort study cannot allow.

## Mutation-equivalence invariants

The release mutation audit counts every generated mutant in its denominator and
does not award credit for equivalence. The surviving mutations below are listed
because each has a concrete reason it cannot change an accepted result in the
pinned release environment. Suffixes are the exact mutmut 3.3.1 identifiers at
the audited commit; they are not patterns for future versions.

| Source / function | Exact suffixes | Invariant |
| --- | --- | --- |
| `aeb/controller.py::limit_acceleration` | 48, 49 | Only explanatory refusal prose changes; condition, exception class, and numeric behavior are unchanged. |
| `aeb/state_machine.py::load_policy` | 5, 7 | On the measured release container `encoding=None` resolves to UTF-8 and `UTF-8` is the same codec alias for the committed ASCII-compatible YAML. |
| `aeb/state_machine.py::validate_policy` | 19, 20, 21, 32, 33 | Only explanatory refusal prose changes. |
| `aeb/threat.py::_axes`, `_point_segment_distances` | axes 9; distances 10, 21, 33 | Reversing closed-polygon edge traversal preserves the unordered edge/normal set and the public per-point minimum; upper-case einsum subscripts only rename dummy indices. |
| `aeb/threat.py::_required_deceleration` | 14 | At zero closing speed both branches return exactly zero. |
| `aeb/threat.py::assess_threat` | 20, 21 | Only explanatory invalid-step prose changes. |
| `aeb/threat.py::assess_threat` | 161, 163, 169, 171, 183, 185, 198, 200 | `steps` is integer; accepted centres and velocities enter float64 multiplication/addition, so omitting the explicit array dtype preserves values and dtype. |
| `aeb/threat.py::first_overlap_step` | 20, 22, 31, 33 | Integer `arange` is multiplied by float `step_s`, and accepted velocity is combined with float64 axes; the projection is float64 without the redundant explicit dtype. |
| `aeb/threat.py::oriented_box_polygon` | 48, 50, 60, 62, 66, 68 | Division and trigonometry produce floats before every affected array operation, preserving float64 geometry when the explicit dtype is omitted. |
| `attribution/factorial.py::coalition_configurations` | 6 | `combinations(CHANNELS, len(CHANNELS)+1)` is empty, so extending the range emits no extra configuration. |
| `attribution/factorial.py::formal_configurations` | 71 | Coalition identifiers are unique and visited once; replacing the post-append membership insertion with `None` cannot suppress a later distinct coalition. |
| `attribution/factorial.py::load_experiment_matrix` | 5, 7 | Same measured UTF-8 runtime invariant as policy loading. |
| `attribution/shapley.py::_validated_game` | 5 | Combinations above the channel count are empty. |
| `attribution/shapley.py::_validated_game` | 12, 14, 16, 17, 20, 21, 24, 26, 28, 29, 32, 33, 42, 43, 44 | Only refusal labels or explanatory prose change; membership, finiteness, exception class, and valid-game output do not. |
| `attribution/shapley.py::shapley_by_metric` | 8, 9 | Only explanatory unknown-metric prose changes. |
| `cohort/filters.py::prefilter_refusal` | 8, 9 | Capitalization/decorators change in the same refusal category and branch; artifact identity does not use the message text. |
| `cohort/splits.py::freeze_family_cohort` | 12, 13, 21, 22, 23, 24 | Only explanatory refusal prose changes; accepted membership and ordering do not. |
| `errors/channels.py::ScenarioChannels.__init__` | 3, 4, 5, 6, 7, 8 | Only explanatory imported-configuration refusal prose changes. |
| `errors/dropout.py::apply_dropout` | 27, 28 | Only explanatory backward-step refusal prose changes. |
| `errors/latency.py::apply_latency`, `select_latency_frame` | apply 1; select 1 | `frequency_hz` is validated positive, but timestamp-based selection does not use its magnitude; both defaults select the same observation. |
| `errors/latency.py::invalid_reason` | 1 | Only explanatory invalid-selection prose changes. |
| `errors/latency.py::select_latency_frame` | 40, 41 | Only explanatory non-monotonic-history prose changes. |
| `errors/latency.py::select_latency_frame` | 55 | Index `-1` repeats the already-tested current frame after the descending scan; zero latency returns earlier, and positive latency cannot select that duplicate. |
| `errors/pipeline.py::_canonical_key`, `track_field_generator` | canonical 23; generator 22 | `utf-8` and `UTF-8` encode identical seed bytes. |
| `errors/pipeline.py::load_error_config` | 5, 7 | Same measured UTF-8 runtime invariant as the other YAML loaders. |
| `metrics/bootstrap.py::_strata`, `_validated` | strata 8, 9; validated 17, 18, 19, 20, 27, 28, 29 | Only explanatory validation prose changes. |
| `metrics/bootstrap.py::paired_scenario_bootstrap` | 30, 32, 38, 40 | Inputs are finite Python numbers; means are stored in float64 output and all percentile/estimate arithmetic is floating, so omitted explicit dtypes preserve output. |
| `metrics/bootstrap.py::paired_scenario_bootstrap` | 49 | NumPy `Generator.choice` defaults to `replace=True`, identical to the omitted explicit argument. |
| `metrics/events.py::extract_interventions` | 11 | `severest` is overwritten on the first braking state before an event can be emitted. |
| `metrics/events.py::match_interventions` | 19, 20 | Only explanatory invalid-threshold prose changes. |
| `metrics/safety.py::measured_simulated_seconds` | 12 | Only explanatory invalid-result prose changes. |
| `simulation/route_follower.py::_validated_route` | 3, 5 | The accepted public route type is `Float64Array`; omitting the redundant cast dtype preserves it. |
| `simulation/route_follower.py::_validated_route` | 17 | Only a dimension quoted in a refusal changes; exception class and rejected input do not. |
| `simulation/route_follower.py::build_nominal_plan` | 24 | The frozen tracking time constant is exactly `1.0`, so multiplication and division are identical. |
| `simulation/route_follower.py::pose_at_distance` | 25, 26 | Only explanatory zero-length-route refusal prose changes. |
| `simulation/step_loop.py::_category_column` | 2, 3, 4 | Only explanatory unknown-category refusal prose changes. |
| `simulation/step_loop.py::run_steps` | 4, 5 | `ScenarioChannels` unconditionally rejects zero/nonfinite `dt_s` before simulation with the same exception category; no valid zero-duration run exists. |
| `simulation/step_loop.py::run_steps` | 64, 71 | Every first transition resets release memory to zero: demanded branches set it directly, and initial MONITOR/no-demand does too. |
| `simulation/step_loop.py::run_steps` | 65, 72 | `previous_acceleration_mps2` is carried but never read here; applied acceleration and jerk limiting use the separate `applied` variable. |
| `simulation/step_loop.py::run_steps` | 97, 98, 99, 100 | Only explanatory backward-clock refusal prose changes. |
| `simulation/step_loop.py::run_steps` | 186 | `commands` has one entry per step exactly when AEB is enabled, making `commands and enabled` equal to `commands or enabled` at this point. |
| `simulation/step_loop.py::run_steps` | 261, 262 | These request extra exact geometry only when the sound lower bound cannot improve the known minimum; outputs are unchanged. Mutant 260 is excluded and has an exact-corner test. |
| `simulation/step_loop.py::run_steps` | 298 | Collision counters start at zero and the first counted collision immediately terminates the run, so assignment to one equals increment by one. |

The measured release runtime reports preferred encoding `UTF-8`, filesystem and
default encodings `utf-8`, and `sys.flags.utf8_mode == 0`; the YAML claims above
therefore depend on the actual locale, not an assumed UTF-8 mode.

One floating boundary is deliberately recorded as a limitation rather than an
equivalence: the production `wrap_to_pi(nextafter(-pi, -inf))` can round to
`+pi`, the same circular angle but outside a literal half-open representation.
The audit test proves only that `nextafter(+pi, -inf)` remains inside the
documented interval in production while the previously considered candidate
mutation did not. That mutation was killed in the final audit. No statement
here claims exact half-open representation for every finite floating-point
angle.

### Study tooling audit

The mutation audit of the policy v2 study tooling mutates eleven
`paths_to_mutate` entries, the release's eight plus `src/aebrisk/study`,
`src/aebrisk/observation/tracking.py` and
`src/aebrisk/simulation/common_cohort.py`, and scores them the same way: every
generated mutant stays in the denominator. Each survivor in the table has a
concrete reason it cannot change an accepted result in the pinned environment.
Suffixes are the exact mutmut 3.3.1 identifiers at the audited commit
`59e823f` ([mutation audit](verification/mutation-audit.md#study-tooling-audit)).
Rows for functions unchanged since the v1.0.0 audit carry its reasons; rows for changed functions were checked again on the current code. The
audit image measures the release runtime, Python 3.9.19 with preferred encoding
`UTF-8`, filesystem encoding `utf-8` and `sys.flags.utf8_mode == 0`, and pins
NumPy 1.23.4 and pydantic 2.13.5. The rows on encodings, dtypes and dump modes
rest on those measurements.

| Source / function | Exact suffixes | Invariant |
| --- | --- | --- |
| `aeb/state_machine.py::load_policy` | 13 | `UTF-8` and `utf-8` name the same codec (`codecs.lookup` returns one codec object for both), so a policy file named by path decodes to the same text. |
| `aeb/threat.py::_axes` | 9 | Reversing closed-polygon edge traversal negates and reorders the normals, and every separating-axis test treats an axis and its exact negation alike. Unchanged function; also in the v1.0.0 table. |
| `aeb/threat.py::_point_segment_distances` | 21, 33 | Upper-case einsum subscripts only rename dummy indices. Unchanged function; as in the v1.0.0 table, whose 10 is now killed (see below). |
| `aeb/threat.py::_required_deceleration` | 14 | At zero closing speed both branches return exactly zero. Unchanged function; as in the v1.0.0 table. |
| `aeb/threat.py::assess_threat` | 20, 21; 161, 163, 169, 171, 183, 185, 198, 200 | 20 and 21 change only explanatory invalid-step prose. In the others `steps` is integer, and accepted centres and velocities enter float64 multiplication/addition, so omitting the explicit array dtype preserves values and dtype. Unchanged function; as in the v1.0.0 table. |
| `aeb/threat.py::first_overlap_step` | 20, 22, 31, 33 | Integer `arange` is multiplied by float `step_s`, and accepted velocity is combined with float64 axes; the projection is float64 without the redundant explicit dtype. Unchanged function; as in the v1.0.0 table. |
| `aeb/threat.py::oriented_box_polygon` | 48, 50, 60, 62, 66, 68 | Division and trigonometry produce floats before every affected array operation, preserving float64 geometry when the explicit dtype is omitted. Unchanged function; as in the v1.0.0 table. |
| `attribution/factorial.py::coalition_configurations` | 6 | `combinations(CHANNELS, len(CHANNELS)+1)` is empty, so extending the range emits no extra configuration. Unchanged function; as in the v1.0.0 table. |
| `attribution/factorial.py::formal_configurations` | 71 | Coalition identifiers are unique and visited once; replacing the post-append membership insertion with `None` cannot suppress a later distinct coalition. Unchanged function; as in the v1.0.0 table. |
| `attribution/factorial.py::load_experiment_matrix` | 11, 13 | Only a matrix named by path is read with this argument. On the measured runtime `encoding=None` (11) resolves to UTF-8, and `UTF-8` (13) is the same codec as `utf-8`, so the decoded text and the parsed matrix are identical. |
| `attribution/shapley.py::_validated_game` | 5; 12, 14, 16, 17, 20, 21, 24, 26, 28, 29, 32, 33, 42, 43, 44 | 5: combinations above the channel count are empty. The others change only coalition labels inside the unknown/missing lists or explanatory prose; membership, finiteness, exception class and valid-game output do not. Unchanged function; as in the v1.0.0 table. |
| `attribution/shapley.py::shapley_by_metric` | 8, 9 | Only explanatory unknown-metric prose changes. Unchanged function; as in the v1.0.0 table. |
| `cohort/splits.py::freeze_family_cohort` | 12, 13, 21, 22, 23, 24 | Only explanatory refusal prose changes; accepted membership and ordering do not. Unchanged function; as in the v1.0.0 table. |
| `errors/dropout.py::apply_dropout` | 27, 28 | Only explanatory backward-step refusal prose changes. Unchanged function; as in the v1.0.0 table. |
| `errors/latency.py::apply_latency` | 1 | `frequency_hz` is validated positive, but timestamp-based selection does not use its magnitude; both defaults select the same observation. Unchanged function; as in the v1.0.0 table. |
| `errors/latency.py::invalid_reason` | 1 | Only explanatory invalid-selection prose changes. Unchanged function; as in the v1.0.0 table. |
| `errors/latency.py::select_latency_frame` | 1; 40, 41; 55 | 1: `frequency_hz` is validated positive, but timestamp-based selection does not use its magnitude. 40 and 41 change only explanatory non-monotonic-history prose. 55: index `-1` repeats the already-tested current frame after the descending scan; zero latency returns earlier, and positive latency cannot select that duplicate. Unchanged function; as in the v1.0.0 table. |
| `errors/pipeline.py::_canonical_key` | 23 | `utf-8` and `UTF-8` encode identical seed bytes. Unchanged function; as in the v1.0.0 table. |
| `errors/pipeline.py::load_error_config` | 11, 13 | Only a configuration named by path is read with this argument. On the measured runtime `encoding=None` (11) resolves to UTF-8, and `UTF-8` (13) is the same codec as `utf-8`, so the decoded text is identical. |
| `errors/pipeline.py::track_field_generator` | 22 | `utf-8` and `UTF-8` encode identical seed bytes. Unchanged function; as in the v1.0.0 table. |
| `metrics/bootstrap.py::_strata` | 8, 9 | Only explanatory validation prose changes. Unchanged function; as in the v1.0.0 table. |
| `metrics/bootstrap.py::_validated` | 17, 18, 19, 20, 27, 28, 29 | Only explanatory validation prose changes. Unchanged function; as in the v1.0.0 table. |
| `metrics/bootstrap.py::cluster_bootstrap_weights` | 64 | NumPy `Generator.choice` defaults to `replace=True`, identical to the omitted explicit argument, so the draws are the same. |
| `metrics/bootstrap.py::paired_scenario_bootstrap` | 38, 40; 49 | 38 and 40 drop the dtype only from the `np.empty` that holds the means, whose default dtype is float64. 49: NumPy `Generator.choice` defaults to `replace=True`, identical to the omitted explicit argument. Unchanged function; the v1.0.0 row for 30 and 32 does not carry over (see below). |
| `metrics/events.py::extract_interventions` | 11 | `severest` is overwritten on the first braking state before an event can be emitted. Unchanged function; as in the v1.0.0 table. |
| `metrics/events.py::match_interventions` | 19, 20 | Only explanatory invalid-threshold prose changes. Unchanged function; as in the v1.0.0 table. |
| `metrics/inference.py::classify` | 25; 36 | 25: an estimate of zero, `-0.0` included, returns `not_established` on the line before, and a non-finite one is refused earlier, so `estimate >= 0` and `estimate > 0` agree on every estimate that reaches the comparison. 36: `predicted_sign` is refused unless it is -1, 0 or 1, and 0 returns on the line before; for -1 and 1, `>= 0` and `> 0` agree. |
| `metrics/inference.py::sign_flip_p_value` | 33, 35; 40; 51; 72 | 33 and 35 omit `dtype=np.int64` from the differences array. Inputs whose absolute differences sum above `2**63 - 1` are refused first, so every value is a Python `int` within int64 and NumPy infers int64; an empty list gives an empty array, which returns p = 1 before use. 40 returns early for one difference instead of none: with none, the enumeration gives 1 / 1, and with one, every pattern reaches the observed absolute sum, so p = 1 on both paths. 51 starts the enumeration from two zeros: every pattern appears twice, the count and `totals.size` both double, and Python's correctly rounded integer division gives the same float. 72 omits the lower bound of `Generator.integers`, which then draws from [0, 2) with the same dtype and consumes the same PCG64 stream. |
| `metrics/safety.py::measured_simulated_seconds` | 12 | Only explanatory invalid-result prose changes. Unchanged function; as in the v1.0.0 table. |
| `simulation/route_follower.py::_validated_route` | 17 | Only a dimension quoted in a refusal changes; exception class and rejected input do not. Unchanged function; as in the v1.0.0 table, whose 3 and 5 are now killed (see below). |
| `simulation/route_follower.py::build_nominal_plan` | 24 | The frozen tracking time constant is exactly `1.0`, so multiplication and division are identical. Unchanged function; as in the v1.0.0 table. |
| `simulation/route_follower.py::pose_at_distance` | 25, 26 | Only explanatory zero-length-route refusal prose changes. Unchanged function; as in the v1.0.0 table. |
| `simulation/step_loop.py::_category_column` | 2, 3, 4 | Only explanatory unknown-category refusal prose changes. Unchanged function; as in the v1.0.0 table. |
| `simulation/step_loop.py::run_steps` | 78, 85; 79, 86; 216; 328 | The v1.0.0 reasons for 64/71, 65/72, 186 and 298, checked again on the changed function under both policies. 78 and 85: the first `update_aeb` from the initial MONITOR memory sets the release count to zero before reading it (a demanded stage sets it directly, and MONITOR without a demand does too), and the initial memory is never returned. 79 and 86: `previous_acceleration_mps2` is copied forward but read nowhere; applied acceleration and jerk limiting use the local `applied`. 216: `commands` gains one entry per step exactly when AEB is enabled, so `commands and enabled` equals `commands or enabled` at this point. 328: collision counters start at zero and the first counted collision ends the run, so assignment of one equals increment by one. |
| `study/addendum.py::_configuration_contrasts` | 10 | The point-estimate weights gain a second all-ones row. `weighted_mean` and `weighted_ratio` compute each row from its own weights alone, and only row 0 is read, so every estimate and the positive-denominator check are unchanged. |
| `study/addendum.py::_distribution` | 14, 16 | Every caller passes Python floats (pydantic `float` fields, which return floats for integer input, and `math.sqrt` results), so `np.asarray` infers float64 without the explicit dtype. |
| `study/addendum.py::_read_evidence` | 3; 6 | On the measured runtime `encoding=None` (3) resolves to UTF-8, and `UTF-8` (6) is the same codec as `utf-8`. |
| `study/addendum.py::analyse_addendum` | 33, 34 | Dropping `resamples=DEFAULT_RESAMPLES` or `seed=DEFAULT_SEED` leaves `cluster_bootstrap_weights` its own defaults, which are the same `metrics/bootstrap.py` constants, so the draws are identical. |
| `study/analysis.py::_combination` | 3, 5 | `np.zeros` defaults to float64, so the contrast draws keep their values and dtype. |
| `study/analysis.py::_distribution` | 6, 8 | Its only caller passes Python floats (pydantic `float` fields, which return floats for integer input, and `math.sqrt` results), so `np.asarray` infers float64 without the explicit dtype. |
| `study/analysis.py::_full_sample` | 9 | The full-sample weights gain a second all-ones row. Each draw's value depends only on its own row (elementwise products, sums along the token axis, elementwise division), and `contrast` reads row 0 only, so the estimate is unchanged. |
| `study/analysis.py::_level_draws` | 43, 45 | Without the explicit dtype a column of exposure seconds stays float64 and a column of `early_ends` counts becomes int64, whose weighted sums are exact integers far below `2**53`; `_combination`, the only caller, adds them to a float64 array, so the draws keep their values and dtype. |
| `study/analysis.py::_term_documents` | 9 | Every sign that reaches it is +1 or -1 (`_side` emits a sign and its negation, and the keying ratio writes literal terms), so `sign >= 0` and `sign > 0` agree. |
| `study/analysis.py::_unit_differences` | 13 | Every unit's sum is negated exactly. The only consumer, the two-sided `sign_flip_p_value`, depends on absolute sums alone, and each drawn or enumerated sign pattern gives the negated sum, so p is unchanged. |
| `study/analysis.py::analyse_study` | 74, 76, 77, 78; 155, 160 | 74, 76, 77 and 78 replace the single stratum `all` with `None`, `XXallXX` or `ALL`; it is only the first element of every (family, cluster) key of the whole-log draws, and one constant value gives the same groups, sort order and draws. 155 and 160: `decide` reads `primary` only as a condition, where `None` and `False` are both false. |
| `study/analysis.py::reproduce_study_cells` | 44, 49, 52, 57 | On the measured runtime `encoding=None` (44, 52) resolves to UTF-8, and `UTF-8` (49, 57) is the same codec as `utf-8`. |
| `study/definition.py::load_study` | 4, 6; 14 | 4 and 6: on the measured runtime `encoding=None` resolves to UTF-8, and `UTF-8` is the same codec as `utf-8`. 14: `text.encode("UTF-8")` gives the same bytes as `utf-8`, so the SHA-256 compared with the recorded hash is unchanged. |
| `study/definition.py::token_log_map` | 5, 7 | On the measured runtime `encoding=None` (5) resolves to UTF-8, and `UTF-8` (7) is the same codec as `utf-8`. |
| `study/evidence.py::_copied` | 4, 5; 7, 9, 11, 12 | 4 and 5: the dump now also carries the source's `schema_version`, and the explicit `"schema_version"` entry after it in the same dict display replaces it. 7, 9, 11 and 12: the pinned pydantic dumps in python mode for `None`, an omitted mode, `XXjsonXX` and `JSON`; for these models that differs from the JSON dump only by tuples in place of lists, which the non-strict tuple fields accept, so `model_validate` builds the same evidence. |
| `study/evidence.py::_earlier_attempts` | 37, 39 | A repeated attempt number is refused before the sort, so every (number, gate file) tuple has a distinct integer first element, and sorting the tuples orders them by that number without comparing the gate files. |
| `study/evidence.py::_run_times` | 4, 6; 17, 25 | 4 and 6: on the measured runtime `encoding=None` resolves to UTF-8, and `UTF-8` is the same codec as `utf-8`. 17 and 25: `times` holds exactly two matches, so index `+1` is index `-1`. |
| `study/evidence.py::_stable_bytes` | 4, 5; 9, 13; 19 | 4 and 5: `json` tests `allow_nan` and `ensure_ascii` by truth, so `None` acts as `False`. 9 and 13: `allow_nan` matters only for a non-finite float, and every payload is a dump of a validated study document whose float fields refuse NaN and infinities (gate files and `reproduction.json` hold no float). 19: `UTF-8` and `utf-8` encode identical bytes. |
| `study/evidence.py::is_ancestor` | 5, 9; 25, 28 | 5 and 9: `subprocess.run` raises only for a truthy `check`; `None` and the omitted default `False` both leave the return code to the explicit check that follows. 25 and 28: `bytes.decode` defaults to UTF-8 whatever the locale, and `UTF-8` is the same codec, so the refusal's detail is unchanged. |
| `study/evidence.py::write_addendum_evidence` | 23, 24, 25, 26, 27, 28 | The pinned pydantic dumps in python mode for `None`, `XXjsonXX` and `JSON`, which for these documents differs from the JSON dump only by tuples in place of lists. `refuse_token_strings` walks lists and tuples alike, and `json.dumps` writes both as the same array, so every refusal and the written bytes are unchanged. |
| `study/evidence.py::write_study_evidence` | 204, 205, 206, 208, 209, 210, 216, 217, 218 | The pinned pydantic dumps in python mode for `None`, `XXjsonXX` and `JSON`, which for these documents differs from the JSON dump only by tuples in place of lists. `refuse_token_strings` walks lists and tuples alike, and `json.dumps` writes both as the same array, so every refusal and the written bytes are unchanged. |
| `study/gates.py::_file_sha256` | 12, 13 | The lower- and upper-case mutations of the empty sentinel `b""` reproduce it, so the generated function is the original character for character. |
| `study/gates.py::committed_cohort_tokens` | 13, 15 | On the measured runtime `encoding=None` (13) resolves to UTF-8, and `UTF-8` (15) is the same codec as `utf-8`. |
| `study/gates.py::write_gates` | 8, 11, 16; 9, 12; 20, 26, 33; 21; 30, 31, 32 | 8, 11 and 16: on the measured runtime `encoding=None` and an omitted encoding resolve to UTF-8, and `UTF-8` is the same codec as `utf-8`. 9 and 12: on the Linux runtime a text file opened with `newline=None` or without the argument writes `\n` untranslated. 20, 26 and 33: a validated `StudyGatesV1` holds no float, so `allow_nan` is never consulted. 21: `json` tests `ensure_ascii` by truth, so `None` acts as `False`. 30, 31 and 32: the pinned pydantic dumps in python mode for `None`, `XXjsonXX` and `JSON`, which for a `StudyGatesV1` differs only by tuples in place of lists, and `json.dump` writes both as the same array. |

Three mutants time out and stay in the denominator:
`errors/latency.py::select_latency_frame` 82, the timeout of the v1.0.0 audit,
and `study/gates.py::_file_sha256` 6 and 11, whose read loop never meets its
changed sentinel (`None` and `b"XXXX"`) because a file at its end returns `b""`
on every read.

`simulation/step_loop.py::run_steps` 291 and 292 are neither killed nor
equivalent: they expose a defect in the released contact pre-check. The loop
measures a body's exact clearance only when `separation_at_least` returns zero
or less, or less than the smallest clearance so far, on the premise that this
bound never exceeds the true clearance. In float64 it can. A 4 m by 2 m ego at
the origin with yaw 0 and a 6 m by 8 m body centred at (6.47213595499958,
3.23606797749979) with yaw -0.4636476090008061 touch corner to corner:
`polygon_clearance` returns 0.0, but `separation_at_least` returns
8.881784197001252e-16, which is `2**-50`. In a one-step oracle run with the ego
stopped and AEB off, an overlapping body listed first makes the loop report one
not-at-fault contact where measuring every body exactly gives two; a body
exactly `2**-50` m ahead listed first makes it report none and a minimum
clearance of 8.881784197001252e-16 m, where exact measurement gives one contact
and 0.0. Mutant 291 (`at_least <= 1.0`) gives the exact results in both runs,
and 292 (`at_least <= min_clearance`) in the second. Each differs from the
source only by measuring more bodies exactly, so no test can kill it without
asserting the defect, and no invariant holds. The released path is kept
unchanged, because the study's arm A must reproduce the released records byte
for byte, which its gate G2 checks; both mutants stay survivors in the
denominator.

Four reasons in the v1.0.0 table do not hold for every accepted input. Three
belong to mutants that tests kill in this audit:
`aeb/threat.py::_point_segment_distances` 10, because measuring each edge from
its other end changes the last bit of some clearances;
`metrics/bootstrap.py::paired_scenario_bootstrap` 30 and 32, because
`_validated` accepts Python integers of `2**64` and more, which without the
float64 dtype are averaged exactly as objects; and
`simulation/route_follower.py::_validated_route` 3 and 5, because a route of
integers would keep its integer dtype in the plan. The fourth is the reason for
`simulation/step_loop.py::run_steps` 261 and 262, the two pre-check mutations
above.
