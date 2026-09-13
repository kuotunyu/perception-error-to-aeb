# Experiment card

## Question and design

This study asks how controlled observation errors change the behavior of a fixed
automatic emergency braking policy in closed-loop ego simulation. It trains no
learned model. Logged surrounding actors follow their recorded trajectories and
do not react to ego braking.

The independent variables are the declared dropout, localization/shape, latency,
and track-instability channels. The scenarios, initial ego state, route, nominal
controller, requested horizon, step rate, and termination rules are fixed.
Actual executed duration can differ across configurations; exposure denominators
must therefore come from the configuration being reported.

The frozen [protocol](../configs/protocols/nuplan_aeb_v2.yaml) defines cohort
selection, scenario families and AEB thresholds. The
[simulation contract](simulation-contract.md) describes the observation pipeline,
controller, termination, collision classification and event matching. Changing a
threshold or severity mapping requires a new protocol, rather than revising the
completed study to improve its result.

## Cohort and comparisons

Development and evaluation are log-disjoint. All configurations are evaluated on
the same common-valid scenario set after the declared eligibility checks.
Repeated runs are scenario-replicates, not additional independent scenarios.
The frozen study evaluates 26 configurations with three replicates per scenario
and configuration; the [reproduction record](verification/analysis-reproduction.md)
documents this inventory and the within-token replicate averaging.
The [dataset card](dataset-card.md) records the source and the scenario-family
shortfall; the bicycle/VRU family should not be presented as a sufficiently
supported efficacy estimate.

Comparisons include no AEB, oracle AEB, single-channel severity changes, and the
medium-severity coalitions used for Shapley attribution. The empty coalition
still passes observations through the tracker. Its position-difference velocity
estimate means it is not interchangeable with the oracle baseline.

## Outcomes that must be read together

- Counted collisions and excluded `contacts_not_at_fault` measure different
  operational categories. Zero counted collisions does not mean zero contact.
- Intervention duration describes the braking exposure and its cost. Read it
  beside collision and contact outcomes rather than interpreting fewer counted
  collisions as unqualified improvement.
- False and missed interventions are matched to the oracle's events using the
  fixed matching rules. Their counts are events, not unique scenarios, and may
  exceed the scenario-replicate count. For no AEB these fields are defined as
  zero because there is no intervention mechanism; that convention does not mean
  that a disabled brake detected all threats.
- Rates per hour use measured exposure. Rates per scenario-replicate use the
  reported replicate count. A per-distance collision rate is unavailable.

The [bilingual results](../README.md) bind exact values to the
[claims registry](claims.yaml). Configuration intervals are not intervals for
differences between configurations. Shapley collision and duration games use
different units; their observed mean contributions cannot be added, compared as
effect sizes, or interpreted as statistical significance. No channel ranking is
established by the current uncertainty evidence.

## Reproduction and inspection

The [analysis reproduction record](verification/analysis-reproduction.md) binds
the formal producer and derived artifacts. The
[evidence index](evidence/README.md) locates the frozen JSON, figures and bounded
set of derived replay timelines. Existing replays show preselected examples;
they are not a representative estimate of a configuration's efficacy.

Every Python command runs in the pinned Linux container. The package's tests and
report reproduction use data-independent fixtures or approved derived evidence.
Reproducing the formal simulation additionally requires the licensed nuPlan input
and the original frozen study inputs; it is not part of ordinary installation.

## Limits and intended use

This is an experiment about this observation pipeline and fixed policy, not a
validation of a deployed perception model, a reactive multi-agent simulator, or
real-vehicle safety. Background actors cannot avoid a braking ego. Collision
exclusions are study rules, not legal responsibility findings. The study reads
world-state records, not sensor pixels; it does not establish sensor-level
robustness or causal efficacy of a calibration model from another project.

Data and replay disclosure remain subject to the
[derived-evidence notice](evidence/nuplan_aeb_v2-NOTICE.md). No raw databases,
sensor logs, maps or unrestricted trajectory exports are shipped.
