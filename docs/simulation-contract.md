# Simulation contract

Every configuration in this study must differ from every other in exactly one
way: the perception error applied to it. Anything else that differs — the
scenario, the initial state, the route, the controller, the step rate, the
duration, how long the recording is watched for — makes two numbers
incomparable while both still look like results. This document states the
settings that hold constant, and where each is enforced.

## The wiring

| Decision | Value | Why it is not an option |
| --- | --- | --- |
| Agents | non-reactive, replaying the log | A reactive background would respond to the ego's braking, so a perception error would change the other vehicles' behaviour as well as the ego's, and the attribution could not separate the two causal paths. |
| Ego | closed loop, driven by this project's controller | A replayed ego would give every configuration the same trajectory, and the AEB would command brakes that never happened. |
| Expert longitudinal braking | not available to the controller | The logged driver already avoided most of these collisions. A controller with access to that braking would inherit an avoidance the perception pipeline never earned, and every configuration would look competent. |
| Step rate | 10 Hz | Fixed by the protocol. |
| Scenario duration | 15 s, every scenario | Two scenarios simulated for different lengths cannot be compared on any rate, and no configuration may change how long it is measured over. |

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
at 6.22 m/s. **Counting those would have reported the AEB as harmful, and the
study's headline would have been backwards.**

So a contact is attributed the way nuPlan's own `ego_at_fault_collisions`
attributes it, and `ego_at_fault` in `simulation/step_loop.py` is the whole rule:

- **A stopped ego is not at fault.** There is nothing left for perception or
  braking to have done differently.
- **An ego struck from behind by something faster is not at fault.** The body is
  behind the ego's centre and closing on it.
- **Everything else is.** The ego drove into a body, and whether it should have
  braked sooner is exactly what this study measures.

Two consequences are deliberate. A contact that is not the ego's fault is
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
