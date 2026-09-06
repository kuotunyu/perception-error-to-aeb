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

## Invalidity

Infrastructure-invalid scenarios are removed from **all** configurations before
any outcome is aggregated, and the invalid record names the token, the common
failure reason, the phase, the exception type and a stack-trace hash. A scenario
may never be removed from only the configuration in which it performed badly:
that would make the cohort a function of the outcome, which is the one thing a
common-cohort study cannot allow.
