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

The [bilingual results](../README.md) bind every value to the
[claims registry](claims.yaml), either exactly or with a declared rounding that
the attribution audit recomputes. Configuration intervals are not intervals for
differences between configurations. Shapley collision and duration games use
different units; their observed mean contributions cannot be added, compared as
effect sizes, or interpreted as statistical significance. No channel ranking is
established by the current uncertainty evidence.

## Reading the braking numbers

Counted collisions and braking are coupled in this study through the fixed
controller, not only through perception quality. The policy brakes for the
tracked object with the highest required deceleration whether or not it is in
the ego's path, the nominal controller does not slow for other road users, and
outside the oracle every tracked velocity is a finite difference of observed
positions. In the released evaluation, every configuration that includes
localization/shape error spends a larger share of its measured exposure braking,
and records fewer counted collisions, than every AEB configuration without it,
oracle AEB included. Read those low counts as a braking result: set them beside
false and missed events, intervention duration and `contacts_not_at_fault`, and
do not read them as better perception or as real-world safety. The
[collisions-vs-braking figure](figures/collisions-vs-braking.svg) plots counted
collisions per 1,000 scenario-replicates against the share of measured exposure
spent braking for every configuration; it is descriptive, derived from
`evaluation.json`, and carries no intervals. A review after the release
found these mechanisms; the
[simulation contract](simulation-contract.md#known-controller-and-modelling-choices)
lists them, and characterization tests pin them.

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

## Relation to standards and prior work

This section places the study's measurements beside the vocabulary of
established standards and research so that a reader can locate them. It is not
a compliance claim: the study did not follow, test against or assess
conformance with any standard named here, and its simulated measurements are
not the quantities those documents specify.

- **ISO 21448 (SOTIF).** SOTIF concerns hazards that arise from functional
  insufficiencies of an intended function, including performance limitations of
  perception, and from the triggering conditions that expose them. The four
  error channels act as controlled perception insufficiencies. A false
  intervention is a braking event with no oracle-AEB counterpart within the
  matching tolerance, and a missed intervention is an oracle-AEB braking event
  that the error configuration either does not reproduce or starts more than
  0.3 s later. Because oracle AEB runs the same policy, including target
  selection that is not path-gated, both counts measure disagreement with
  oracle AEB rather than with an ideal braking need. They are the study's
  closest analogue to unintended and insufficient braking, which is why both
  are reported beside counted collisions.
- **ANSI/UL 4600.** UL 4600 asks for safety performance indicators that are
  monitored over operation. Event counts, intervention duration and excluded
  contacts are the kind of measurement such an indicator could be built on; the
  study defines no indicator and no threshold.
- **Euro NCAP AEB Car-to-Car and VRU protocols; UN Regulation No. 152.** These
  assess an AEB on defined test scenarios with physical or surrogate targets,
  scoring collision avoidance and impact-speed reduction, and UN R152 also sets
  requirements against false reactions. This study replays logged nuPlan
  scenarios with non-reactive actors, records collision energy rather than a
  test-track impact speed, and its results are not comparable to those ratings
  or approval tests.
- **ISO 26262.** Functional safety of electrical and electronic malfunctions is
  out of scope. The channels model perception performance limits, not hardware
  or software faults.
- **nuPlan closed-loop modes.** nuPlan distinguishes closed-loop simulation in
  which other agents replay the log (non-reactive, scored as CLS-NR) from one in
  which they follow an intelligent driver model (reactive, CLS-R). This study
  runs its own closed loop in the non-reactive setting, which is why a braking
  ego can be struck from behind, and it does not use the nuPlan planner scores.

Related research:

- Philion, Kar and Fidler, "Learning to Evaluate Perception Models Using
  Planner-Centric Metrics", CVPR 2020, evaluate detections by their effect on
  a planner rather than by detection metrics alone. This study asks a similar
  question with a fixed AEB policy in place of a learned planner.
- Piazzoni, Cherian, Slavik and Dauwels, "Modeling Perception Errors towards
  Robust Decision Making in Autonomous Vehicles", IJCAI 2020, perturb
  ground-truth objects with perception error models in simulation, the same
  kind of approach as this study's error channels.
- Weng, Wang, Held and Kitani, "3D Multi-Object Tracking: A Baseline and New
  Evaluation Metrics" (AB3DMOT), IROS 2020, is a Kalman-filter tracking
  baseline. This study's tracker differences observed positions instead, one of
  the choices listed in the simulation contract.
- Grabisch and Roubens, "An axiomatic approach to the concept of interaction
  among players in cooperative games", International Journal of Game Theory,
  1999, extend Shapley values to interaction indices over pairs and larger
  coalitions. This study reports first-order Shapley values only and does not
  decompose interactions between channels.

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
