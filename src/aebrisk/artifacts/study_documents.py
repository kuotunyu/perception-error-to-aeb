"""The documents of the policy v2 study and of the post-hoc addendum.

These models describe what the study writes and what its evidence publishes.
They import nothing from `aebrisk.study`, so the published-document registry can
list them beside the released documents without an import cycle.

`StudyGatesV1` is a gate file: the outcome of the study's preflight, or of the
gates `study verify` runs over the arms of one attempt. Each gate publishes its
name, whether it passed, and what it counted. What a gate found behind a count,
file by file, is kept in `artifacts_only_detail`. That field may name paths and
tokens, so it stays under `artifacts/`; a published gate file leaves it out and
is read by this same model.

`PolicyV2SummaryV1` is the study's summary: every number `study analyse`
computes from the arms, and the reproduction gate's outcome. It holds aggregates
only, never a token.

`PolicyV2EvidenceV1`, `StudyReproductionV1` and `AttributionAddendumEvidenceV1`
are the evidence `study evidence` publishes beside the summaries. `StudyG0V1`
is the operator's record of G0, which reaches the evidence only inside
`reproduction.json`.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aebrisk.artifacts.results import ScenarioFamily

STUDY_GATES_SCHEMA_VERSION: Literal["aeb-study-gates/v1"] = "aeb-study-gates/v1"

#: The released records are the reference unless the repository owner chose the
#: rebuilt arm A instead, after G2 failed with no code cause.
Reference = Literal["released", "arm-a"]

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, strict=True)]
Name = Annotated[str, Field(min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StudyGateV1(_Strict):
    """One gate's published outcome."""

    gate: Name
    passed: Annotated[bool, Field(strict=True)]
    counts: dict[str, Count]


class StudyGatesV1(_Strict):
    """The gates of one check, and the study, cohort and protocol they were checked against.

    `protocol_sha256` and `cohort_manifest_sha256` are the study file's
    `seed_namespace_protocol_sha256` and cohort membership hash, under the names
    the claims audit reads.
    """

    schema_version: Literal["aeb-study-gates/v1"]
    study_sha256: Sha256
    arms_checked: tuple[Name, ...]
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    reference: Reference
    exploratory: Annotated[bool, Field(strict=True)]
    gates: tuple[StudyGateV1, ...]
    artifacts_only_detail: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_gates(self) -> StudyGatesV1:
        """Tie the exploratory label to the reference, and each detail to one gate."""

        if self.exploratory != (self.reference == "arm-a"):
            raise ValueError(
                "exploratory must be true exactly when the reference is arm-a, got "
                f"reference {self.reference!r} and exploratory {self.exploratory}"
            )
        names = [gate.gate for gate in self.gates]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"gates appear more than once: {repeated}")
        unknown = sorted(set(self.artifacts_only_detail) - set(names))
        if unknown:
            raise ValueError(f"artifacts_only_detail names gates the file does not hold: {unknown}")
        return self


# --------------------------------------------------------------------------
# The policy v2 study's summary
#
# `PolicyV2SummaryV1` is what `study analyse` computes from the five arms, in
# the order of sections 7.1 to 7.6 of the analysis plan. Every contrast is
# written as its terms, signed (arm, cell) levels, so a reader can see which
# arms and cells it compares without knowing how it was labelled.
# --------------------------------------------------------------------------

POLICY_V2_SUMMARY_SCHEMA_VERSION: Literal["aeb-policy-v2-summary/v1"] = "aeb-policy-v2-summary/v1"

#: The outcomes a contrast can compare, as section 5 of the analysis plan
#: defines them. The braking share and the rates are ratios of sums, the
#: simulated seconds and early ends are sums, and every other outcome is a mean
#: of per-token values.
StudyOutcome = Literal[
    "collision_indicator",
    "braking_share",
    "not_at_fault_contact_rate",
    "missed_interventions",
    "false_interventions",
    "any_contact",
    "avoided",
    "induced",
    "activation_rate",
    "braking_seconds_per_run",
    "not_at_fault_contacts_per_run",
    "simulated_seconds",
    "early_ends",
]
StudyTest = Literal["bootstrap", "sign_flip"]
StudyCluster = Literal["family-log", "family-drive", "log"]
StudyClassification = Literal[
    "supported", "contradicted", "not_established", "increase", "decrease"
]
StudySensitivity = Literal[
    "braking_seconds_per_run",
    "not_at_fault_contacts_per_run",
    "family_drive_clusters",
    "drive_sign_flip",
    "whole_log_bootstrap",
]
StudyReportedAnalysis = Literal[
    "other_cells",
    "difference_in_differences",
    "combined_remedy",
    "benefit_and_harm",
    "any_contact",
    "activation_rate",
    "exposure",
    "velocity_by_cell",
    "velocity_interaction",
    "primary_by_family",
]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
Confidence = Annotated[float, Field(gt=0.0, lt=1.0, allow_inf_nan=False)]
PositiveCount = Annotated[int, Field(ge=1, strict=True)]
StrictBool = Annotated[bool, Field(strict=True)]


class StudyIntervalV1(_Strict):
    """A two-sided percentile interval of the cluster-bootstrap draws."""

    confidence: Confidence
    low: FiniteFloat
    high: FiniteFloat

    @model_validator(mode="after")
    def validate_order(self) -> StudyIntervalV1:
        """Refuse an inverted interval, which would still print."""

        if self.low > self.high:
            raise ValueError(f"low {self.low} exceeds high {self.high}")
        return self


class StudyProportionV1(_Strict):
    """An exact Clopper-Pearson interval for the share of tokens with an event.

    It counts tokens, which are correlated within logs, so it is descriptive and
    too narrow.
    """

    event: Literal["collision", "avoided", "induced"]
    arm: Name
    cell: Name
    family: Optional[ScenarioFamily] = None
    successes: Count
    trials: PositiveCount
    confidence: Confidence
    low: Probability
    high: Probability


class StudyTermV1(_Strict):
    """One signed level of a contrast: an arm's value in one cell."""

    sign: Literal[-1, 1]
    arm: Name
    cell: Name


class StudyContrastV1(_Strict):
    """A contrast: the sum of its signed terms, over the cohort or over one family.

    The intervals are percentile intervals of the draws of `cluster`; the
    simultaneous and Holm-step intervals belong to the primary hypotheses.
    `p_value` comes from `test`, flipping `test_unit` for a sign flip. A value a
    row does not report is left out.
    """

    outcome: StudyOutcome
    terms: tuple[StudyTermV1, ...] = Field(min_length=2)
    family: Optional[ScenarioFamily] = None
    estimate: FiniteFloat
    cluster: Optional[StudyCluster] = None
    interval: Optional[StudyIntervalV1] = None
    simultaneous_interval: Optional[StudyIntervalV1] = None
    holm_step_interval: Optional[StudyIntervalV1] = None
    test: Optional[StudyTest] = None
    test_unit: Optional[Literal["log", "drive"]] = None
    p_value: Optional[Probability] = None
    clopper_pearson: tuple[StudyProportionV1, ...] = ()


class StudyHypothesisResultV1(_Strict):
    """One pre-registered hypothesis, decided by Holm within its family."""

    id: Name
    family: Name
    predicted_sign: Literal[-1, 0, 1]
    contrast: StudyContrastV1
    holm_step: PositiveCount
    rejected: StrictBool
    classification: StudyClassification


class StudySensitivityV1(_Strict):
    """One sensitivity analysis of a primary hypothesis. It enters no decision."""

    hypothesis: Name
    analysis: StudySensitivity
    contrast: StudyContrastV1


class StudyReportedContrastV1(_Strict):
    """A secondary or descriptive contrast, reported at 95%."""

    analysis: StudyReportedAnalysis
    hypothesis: Optional[Name] = None
    contrast: StudyContrastV1


class StudyKeyingRatioV1(_Strict):
    """The answer to Q3: how much the channel-independent keying tightens a contrast.

    `sd_b` and `sd_e` are the cluster-bootstrap standard deviations of the
    braking-share contrast of the full coalition minus
    `localization_shape-medium`, under B's keying and under E's. The ratio is
    absent when E's contrast does not vary.
    """

    outcome: Literal["braking_share"]
    b_terms: tuple[StudyTermV1, ...]
    e_terms: tuple[StudyTermV1, ...]
    sd_b: NonNegativeFloat
    sd_e: NonNegativeFloat
    ratio: Optional[NonNegativeFloat]


class StudyStatementsV1(_Strict):
    """The fixed sentences that section 7.2 of the analysis plan chooses for H3, H4 and H5."""

    h3: Name
    h4: Name
    h5: Name


class StudyDistributionV1(_Strict):
    """The count, quartiles and maximum of some values; each is left out when there are none."""

    count: Count
    median: Optional[FiniteFloat] = None
    lower_quartile: Optional[FiniteFloat] = None
    upper_quartile: Optional[FiniteFloat] = None
    maximum: Optional[FiniteFloat] = None


class StudyLevelV1(_Strict):
    """One arm's outcomes in one cell, over the cohort or over one family.

    `collisions` and `contacts_not_at_fault` count records, under their released
    names. Missed and false interventions and matched delays are measured
    against the arm's own oracle, so they describe the arm and are never
    compared across policies.
    """

    arm: Name
    cell: Name
    family: Optional[ScenarioFamily] = None
    tokens: PositiveCount
    records: PositiveCount
    collisions: Count
    collision_tokens: Count
    collision_indicator: Probability
    collision_clopper_pearson: StudyProportionV1
    contacts_not_at_fault: Count
    any_contact: Probability
    simulated_seconds: NonNegativeFloat
    braking_seconds: NonNegativeFloat
    braking_share: NonNegativeFloat
    braking_seconds_per_run: NonNegativeFloat
    not_at_fault_contact_rate: NonNegativeFloat
    brake_activations: Count
    activation_rate: NonNegativeFloat
    early_ends: Count
    max_deceleration_mps2: NonNegativeFloat
    missed_interventions: Count
    false_interventions: Count
    matched_delays_s: StudyDistributionV1
    stop_distance_m: StudyDistributionV1
    runs_never_stopped: Count
    collision_speed_mps: StudyDistributionV1


class PolicyV2SummaryV1(_Strict):
    """Everything `study analyse` computed, in the order of the analysis plan.

    `protocol_sha256` and `cohort_manifest_sha256` carry the names the claims
    audit reads. `g4` is the reproduction gate's outcome and counts, without its
    local detail.
    """

    schema_version: Literal["aeb-policy-v2-summary/v1"]
    study_sha256: Sha256
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    common_valid_tokens: PositiveCount
    reference: Reference
    exploratory: StrictBool
    g4: StudyGateV1
    hypotheses: tuple[StudyHypothesisResultV1, ...]
    q1_label: Literal["support", "partial_support", "no_support"]
    h5_qualifier: StrictBool
    statements: StudyStatementsV1
    sensitivity: tuple[StudySensitivityV1, ...]
    q3_ratio: StudyKeyingRatioV1
    secondary: tuple[StudyReportedContrastV1, ...]
    descriptive_contrasts: tuple[StudyReportedContrastV1, ...]
    levels: tuple[StudyLevelV1, ...]

    @model_validator(mode="after")
    def validate_label(self) -> PolicyV2SummaryV1:
        """Tie the exploratory label to the reference, as the gate file does."""

        if self.exploratory != (self.reference == "arm-a"):
            raise ValueError(
                "exploratory must be true exactly when the reference is arm-a, got "
                f"reference {self.reference!r} and exploratory {self.exploratory}"
            )
        return self


# --------------------------------------------------------------------------
# The post-hoc addendum
#
# `docs/posthoc/nuplan_aeb_v2-addendum/addendum-plan.md` fixes what the addendum
# summary holds: the outcome of its reproduction gate, intervals from a
# family-stratified log-cluster bootstrap for the released Shapley values and
# configuration contrasts, and a descriptive set. It estimates and describes; it
# has no field for a p-value, a test decision or a ranking.
# --------------------------------------------------------------------------

ATTRIBUTION_ADDENDUM_SCHEMA_VERSION: Literal["aeb-attribution-addendum/v1"] = (
    "aeb-attribution-addendum/v1"
)

#: The two released games, in the order the addendum prints them.
AddendumGame = Literal["intervention_duration_s", "collision_indicator"]
ADDENDUM_GAMES: tuple[AddendumGame, ...] = ("intervention_duration_s", "collision_indicator")

#: The sentence the addendum plan fixes for the collision game, word for word.
COLLISION_GAME_SENTENCE = (
    "Under v1, the lower collision indicator of the configurations with localization error "
    "coincides with braking for most of their measured exposure; it should not be read as a "
    "safety benefit."
)

#: The outcomes a configuration contrast is taken on.
ContrastMetric = Literal["collision_indicator", "braking_share", "not_at_fault_contact_rate"]

Level = Annotated[float, Field(gt=0.0, lt=1.0)]


class AddendumIntervalV1(_Strict):
    """A two-sided percentile interval of the bootstrap draws, at one level."""

    confidence: Level
    low: FiniteFloat
    high: FiniteFloat

    @model_validator(mode="after")
    def validate_order(self) -> AddendumIntervalV1:
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        return self


class AddendumEstimateV1(_Strict):
    """A post-hoc estimate and its intervals.

    `computed_before_plan` is true for a quantity that the scratch analyses
    listed in section 1 of the addendum plan computed before the plan was
    written.
    """

    estimate: FiniteFloat
    intervals: tuple[AddendumIntervalV1, ...] = Field(min_length=1)
    computed_before_plan: StrictBool


class AddendumDifferenceV1(AddendumEstimateV1):
    """The `plus` value minus the `minus` value, resampled token by token."""

    plus: Name
    minus: Name


class AddendumContrastV1(AddendumDifferenceV1):
    """One configuration minus another on one outcome."""

    metric: ContrastMetric


class AddendumGameV1(_Strict):
    """One released game: its Shapley values and their pairwise differences.

    The six localization-and-shape differences carry a simultaneous interval
    and a 95% one; every other interval is 95%. The collision game carries the
    fixed sentence in `caution`, and the duration game none.
    """

    game: AddendumGame
    shapley_values: dict[str, AddendumEstimateV1]
    localization_shape_differences: tuple[AddendumDifferenceV1, ...]
    other_differences: tuple[AddendumDifferenceV1, ...]
    caution: Optional[str]

    @model_validator(mode="after")
    def validate_caution(self) -> AddendumGameV1:
        expected = COLLISION_GAME_SENTENCE if self.game == "collision_indicator" else None
        if self.caution != expected:
            raise ValueError(
                f"the {self.game} game must carry the caution {expected!r}, got {self.caution!r}"
            )
        return self


class AddendumProportionV1(_Strict):
    """A count of tokens out of the tokens, with its exact Clopper-Pearson interval.

    It is descriptive: the tokens of one log are correlated, so the interval is
    too narrow.
    """

    events: Count
    tokens: PositiveCount
    confidence: Level
    low: FiniteFloat
    high: FiniteFloat

    @model_validator(mode="after")
    def validate_events(self) -> AddendumProportionV1:
        if self.events > self.tokens:
            raise ValueError(f"events cannot exceed tokens, got {self.events} of {self.tokens}")
        return self


class AddendumOracleCollisionsV1(_Strict):
    """The released oracle's avoided and induced collisions, beside its not-at-fault contacts."""

    avoided: AddendumProportionV1
    induced: AddendumProportionV1
    oracle_aeb_contacts_not_at_fault: Count
    computed_before_plan: StrictBool


class AddendumActivationRateV1(_Strict):
    """Brake activations of one configuration per simulated hour, with both of its terms."""

    configuration_id: Name
    brake_activations: Count
    simulated_seconds: Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
    per_hour: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    computed_before_plan: StrictBool


class AddendumZeroEventV1(_Strict):
    """A configuration without a counted collision, overall and in each family."""

    configuration_id: Name
    overall: AddendumProportionV1
    by_family: dict[str, AddendumProportionV1]
    computed_before_plan: StrictBool


class AddendumDistributionV1(_Strict):
    """The count, median and quartiles of some values, with numpy's linear interpolation."""

    count: Count
    median: Optional[FiniteFloat]
    lower_quartile: Optional[FiniteFloat]
    upper_quartile: Optional[FiniteFloat]

    @model_validator(mode="after")
    def validate_values(self) -> AddendumDistributionV1:
        values = (self.lower_quartile, self.median, self.upper_quartile)
        if any((value is None) != (self.count == 0) for value in values):
            raise ValueError(
                "a distribution has a median and quartiles exactly when it has values, got "
                f"count {self.count} and {values}"
            )
        return self


class AddendumOnsetDelaysV1(_Strict):
    """The matched onset delays of one configuration, against the oracle."""

    configuration_id: Name
    matched_onset_delays_s: AddendumDistributionV1
    computed_before_plan: StrictBool


class AddendumStopsAndSpeedsV1(_Strict):
    """First stop distances where the ego stopped, and its own speed at counted collisions."""

    configuration_id: Name
    first_stop_distance_m: AddendumDistributionV1
    ego_speed_at_collision_mps: AddendumDistributionV1
    computed_before_plan: StrictBool


class AddendumBootstrapV1(_Strict):
    """The resampling every addendum interval is read from; one draw serves them all."""

    cluster: Literal["family-log"]
    clusters: PositiveCount
    resamples: PositiveCount
    seed: Count


class AttributionAddendumV1(_Strict):
    """The addendum summary, and the outcome of the reproduction gate that preceded it.

    `protocol_sha256` and `cohort_manifest_sha256` are the released records'
    protocol and cohort membership hashes, under the names the claims audit
    reads. `released_output_hashes_sha256` is the SHA-256 of the list the
    released records were held to.
    """

    schema_version: Literal["aeb-attribution-addendum/v1"]
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    cohort_size: Count
    common_valid_tokens: Count
    released_output_hashes_sha256: Sha256
    reproduction_gate: StudyGateV1
    bootstrap: AddendumBootstrapV1
    games: tuple[AddendumGameV1, ...]
    configuration_contrasts: tuple[AddendumContrastV1, ...]
    oracle_collisions: AddendumOracleCollisionsV1
    brake_activations: tuple[AddendumActivationRateV1, ...]
    zero_event_configurations: tuple[AddendumZeroEventV1, ...]
    matched_onset_delays: tuple[AddendumOnsetDelaysV1, ...]
    stops_and_collision_speeds: tuple[AddendumStopsAndSpeedsV1, ...]

    @model_validator(mode="after")
    def validate_games(self) -> AttributionAddendumV1:
        """The collision game follows the duration game, so its caution follows that line."""

        _refuse_games_out_of_order(self.games)
        return self


def _refuse_games_out_of_order(games: tuple[AddendumGameV1, ...]) -> None:
    order = tuple(game.game for game in games)
    if order != ADDENDUM_GAMES:
        raise ValueError(
            "games must be the duration game and then the collision game, "
            f"{list(ADDENDUM_GAMES)}, got {list(order)}"
        )


# --------------------------------------------------------------------------
# The published evidence
#
# `study evidence` writes what a results pull request commits. It copies
# numbers and recomputes none. The derived evidence of each part holds the
# numbers of its summary, field for field under the same names, beside the
# protocol and cohort hashes the claims audit reads; what a summary holds beside
# its numbers (its fixed sentences and labels, and the outcome of the gate that
# preceded the analysis) stays in the summary. `reproduction.json` records how
# the study's numbers came about: the pre-registration, each arm's run, the
# outcome of every gate, and the gate file of every attempt.
# --------------------------------------------------------------------------

POLICY_V2_EVIDENCE_SCHEMA_VERSION: Literal["aeb-policy-v2-evidence/v1"] = (
    "aeb-policy-v2-evidence/v1"
)
STUDY_REPRODUCTION_SCHEMA_VERSION: Literal["aeb-study-reproduction/v1"] = (
    "aeb-study-reproduction/v1"
)
ATTRIBUTION_ADDENDUM_EVIDENCE_SCHEMA_VERSION: Literal["aeb-attribution-addendum-evidence/v1"] = (
    "aeb-attribution-addendum-evidence/v1"
)
STUDY_G0_SCHEMA_VERSION: Literal["aeb-study-g0/v1"] = "aeb-study-g0/v1"

#: The gates of the analysis plan, in the order `reproduction.json` records them.
StudyGateName = Literal["G0", "G1", "G2", "G3", "G4", "G5"]
STUDY_GATES: tuple[StudyGateName, ...] = ("G0", "G1", "G2", "G3", "G4", "G5")

#: Every arm of the study runs in the pilot that G0 records.
PILOT_ARMS = 5

Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
#: A time as each line of an arm's run log begins with it.
RunLogTime = Annotated[
    str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$")
]
#: A time as the GitHub API writes a pull request's `merged_at`.
GitHubTime = Annotated[
    str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
]


class PolicyV2EvidenceV1(_Strict):
    """The numbers of the policy v2 summary, each under the name it has there."""

    schema_version: Literal["aeb-policy-v2-evidence/v1"]
    study_sha256: Sha256
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    common_valid_tokens: PositiveCount
    reference: Reference
    exploratory: StrictBool
    hypotheses: tuple[StudyHypothesisResultV1, ...]
    sensitivity: tuple[StudySensitivityV1, ...]
    q3_ratio: StudyKeyingRatioV1
    secondary: tuple[StudyReportedContrastV1, ...]
    descriptive_contrasts: tuple[StudyReportedContrastV1, ...]
    levels: tuple[StudyLevelV1, ...]

    @model_validator(mode="after")
    def validate_label(self) -> PolicyV2EvidenceV1:
        """Tie the exploratory label to the reference, as the summary does."""

        if self.exploratory != (self.reference == "arm-a"):
            raise ValueError(
                "exploratory must be true exactly when the reference is arm-a, got "
                f"reference {self.reference!r} and exploratory {self.exploratory}"
            )
        return self


class AttributionAddendumEvidenceV1(_Strict):
    """The numbers of the addendum summary, each under the name it has there.

    Every Shapley value of the addendum and its intervals are here, in `games`.
    """

    schema_version: Literal["aeb-attribution-addendum-evidence/v1"]
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    cohort_size: Count
    common_valid_tokens: Count
    bootstrap: AddendumBootstrapV1
    games: tuple[AddendumGameV1, ...]
    configuration_contrasts: tuple[AddendumContrastV1, ...]
    oracle_collisions: AddendumOracleCollisionsV1
    brake_activations: tuple[AddendumActivationRateV1, ...]
    zero_event_configurations: tuple[AddendumZeroEventV1, ...]
    matched_onset_delays: tuple[AddendumOnsetDelaysV1, ...]
    stops_and_collision_speeds: tuple[AddendumStopsAndSpeedsV1, ...]

    @model_validator(mode="after")
    def validate_games(self) -> AttributionAddendumEvidenceV1:
        """The collision game follows the duration game, as in the summary."""

        _refuse_games_out_of_order(self.games)
        return self


class StudyRunContextV1(_Strict):
    """An arm's `run_context.json`, with the fields of the study's run context in its order."""

    configuration_id: str
    protocol_sha256: str
    cohort_sha256: str
    container_digest: str
    commit: str
    study_sha256: str
    arm_id: str
    aeb_policy: str
    policy_sha256: str
    rng_scheme: str
    velocity_estimator: str
    velocity_parameters: str
    error_config_sha256: str
    python_version: str
    numpy_version: str


class StudyG0V1(_Strict):
    """What the operator records for G0, in `g0.json` under the study's artifacts root.

    It is read by `study evidence` and nested in `reproduction.json`; it is
    never committed on its own. `pilot_gates` is the gate file that
    `study verify --pilot` wrote.
    """

    schema_version: Literal["aeb-study-g0/v1"]
    tooling_commit: Commit
    ci_run_id: PositiveCount
    pilot_run_contexts: dict[str, StudyRunContextV1] = Field(
        min_length=PILOT_ARMS, max_length=PILOT_ARMS
    )
    pilot_gates: StudyGatesV1

    @model_validator(mode="after")
    def validate_arms(self) -> StudyG0V1:
        """File each pilot run context under the arm it names."""

        misfiled = sorted(
            arm for arm, context in self.pilot_run_contexts.items() if context.arm_id != arm
        )
        if misfiled:
            raise ValueError(f"pilot run contexts filed under another arm's id: {misfiled}")
        return self


class StudyArmRunV1(_Strict):
    """One arm's run: its tooling commit and image, and the first and last times of its run log."""

    arm_id: Name
    tooling_commit: Commit
    image_id: Name
    started_at_utc: RunLogTime
    finished_at_utc: RunLogTime
    python_version: Name
    numpy_version: Name


class StudyGateRecordV1(_Strict):
    """One gate's outcome as `reproduction.json` records it; a gate that did not run counts nothing."""

    gate: StudyGateName
    outcome: Literal["passed", "failed", "not run"]
    counts: dict[str, Count] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_counts(self) -> StudyGateRecordV1:
        if self.outcome == "not run" and self.counts:
            raise ValueError(f"{self.gate} did not run, so it counts nothing, got {self.counts}")
        return self


class StudyGateFileV1(_Strict):
    """A published gate file: `gates.json`, or the gate file of an earlier failed attempt."""

    file: Annotated[str, Field(pattern=r"^gates(-attempt-[1-9][0-9]*)?\.json$")]
    attempt: Optional[PositiveCount]
    passed: StrictBool


class StudyReproductionV1(_Strict):
    """How the study's numbers came about, for a reader to check against the freeze point.

    The pre-registration pull request's number, merge commit and `merged_at`
    are read from the GitHub API. Each arm's tooling commit, image and versions
    come from its run context, and its start and end from the first and last
    lines of its run log. G0 comes from `g0.json`, G1 to G3 and G5 from the gate
    file, and G4 from the summary, or from `g4.json` when there is no summary.
    """

    schema_version: Literal["aeb-study-reproduction/v1"]
    study_sha256: Sha256
    protocol_sha256: Sha256
    cohort_manifest_sha256: Sha256
    reference: Reference
    exploratory: StrictBool
    preregistration_pull_request: PositiveCount
    preregistration_commit: Commit
    preregistration_merged_at: GitHubTime
    arms: tuple[StudyArmRunV1, ...]
    gates: tuple[StudyGateRecordV1, ...]
    gate_files: tuple[StudyGateFileV1, ...]
    g0: StudyG0V1

    @model_validator(mode="after")
    def validate_record(self) -> StudyReproductionV1:
        """Refuse a record that contradicts itself or keeps a local detail."""

        if self.exploratory != (self.reference == "arm-a"):
            raise ValueError(
                "exploratory must be true exactly when the reference is arm-a, got "
                f"reference {self.reference!r} and exploratory {self.exploratory}"
            )
        gates = tuple(record.gate for record in self.gates)
        if gates != STUDY_GATES:
            raise ValueError(f"gates must be {list(STUDY_GATES)} in that order, got {list(gates)}")
        for label, names in (
            ("arms", [arm.arm_id for arm in self.arms]),
            ("gate files", [gate_file.file for gate_file in self.gate_files]),
        ):
            if len(set(names)) != len(names):
                raise ValueError(f"{label} appear more than once: {names}")
        if self.g0.pilot_gates.artifacts_only_detail:
            raise ValueError("the pilot's gate file is recorded without its artifacts_only_detail")
        return self
