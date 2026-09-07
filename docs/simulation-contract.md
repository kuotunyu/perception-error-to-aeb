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
