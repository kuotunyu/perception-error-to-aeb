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
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
