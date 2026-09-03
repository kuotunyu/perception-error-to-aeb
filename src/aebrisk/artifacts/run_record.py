"""How one simulation run was produced, recorded so it can be rebuilt.

A number without a run record is an anecdote. This record names the commit, the
configuration, the protocol, the cohort, the locked environment, the hardware,
the seed, the wall-clock window and the hash of everything the run wrote.

The three measured fields — commit, lock hash and hardware — arrive through the
environment rather than as arguments, because they describe the machine that is
running and must be measured there. The first formal run in the sibling project
was launched with a hardware string typed from memory on a different machine,
and nothing in the record could have revealed it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROVENANCE_ENV_VAR = "AEBRISK_RUN_PROVENANCE"


def _validate_utc(value: str, name: str) -> str:
    if not value.endswith("Z"):
        raise ValueError(f"{name} must end in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{name} must be a valid ISO 8601 timestamp") from error
    return value


class RunProvenance(BaseModel):
    """The three facts about the running machine that must never be typed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hardware: dict[str, str]


class RunRecordV1(BaseModel):
    """One simulation run's identity, inputs, window, outcome and outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aeb-run/v1"]
    run_id: str = Field(min_length=1)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cohort_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hardware: dict[str, str]
    seed: int
    started_at_utc: str
    finished_at_utc: str
    status: Literal["succeeded", "failed", "aborted"]
    artifacts: dict[str, str]

    @field_validator("started_at_utc", "finished_at_utc")
    @classmethod
    def validate_timestamps(cls, value: str) -> str:
        """Require the canonical trailing-Z form so records sort as strings."""

        return _validate_utc(value, "timestamp")

    @field_validator("artifacts")
    @classmethod
    def validate_artifact_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        """Every artifact is named by its SHA-256, or it cannot be verified."""

        for name, digest in value.items():
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"artifact {name!r} must be recorded by its SHA-256")
        return value

    @model_validator(mode="after")
    def validate_window_and_outputs(self) -> RunRecordV1:
        """A run cannot finish before it starts, and success must have produced something.

        The second rule is the useful one: a run that reports success and wrote
        nothing is indistinguishable from a run that never executed, and it
        would still be counted in the denominator of every rate.
        """

        if self.finished_at_utc < self.started_at_utc:
            raise ValueError("finished_at_utc must not precede started_at_utc")
        if self.status == "succeeded" and not self.artifacts:
            raise ValueError("a succeeded run must record the artifacts it produced")
        return self


def load_run_provenance(environment: Optional[Mapping[str, str]] = None) -> RunProvenance:
    """Read the measured provenance, failing closed rather than guessing it."""

    source = os.environ if environment is None else environment
    raw_value = source.get(PROVENANCE_ENV_VAR)
    if not raw_value:
        raise ValueError(
            f"{PROVENANCE_ENV_VAR} is not set; run provenance is measured on the "
            "machine that runs the job and is never inferred"
        )
    try:
        document: Any = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{PROVENANCE_ENV_VAR} is not valid JSON") from error

    try:
        return RunProvenance.model_validate(document)
    except Exception as error:
        raise ValueError(f"{PROVENANCE_ENV_VAR} is incomplete or malformed: {error}") from error
