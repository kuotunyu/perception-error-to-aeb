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
