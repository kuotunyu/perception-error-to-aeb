"""The document that says exactly which scenarios a result was measured on.

This is what a reader checks a published number against. It names the protocol
the cohort was cut under, which split it is, and every scenario token in every
family, and it carries one hash over that membership so two freezes can be
compared without reading four hundred tokens.

Tokens are sorted on construction and the hash is taken over the sorted form, so
the document describes a membership rather than the order somebody happened to
list it in.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aebrisk.cohort.filters import SCENARIO_FAMILIES


class CohortManifestV1(BaseModel):
    """One frozen split of the scenario cohort."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aeb-cohort-manifest/v1"]
    split: Literal["development", "evaluation", "smoke"]
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    families: dict[str, tuple[str, ...]]
    log_names: tuple[str, ...]

    @field_validator("families")
    @classmethod
    def validate_families(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        """Require exactly the four strata, and sort each one's tokens."""

        unknown = sorted(set(value) - set(SCENARIO_FAMILIES))
        if unknown:
            raise ValueError(f"unknown scenario families: {unknown}")
        missing = sorted(set(SCENARIO_FAMILIES) - set(value))
        if missing:
            raise ValueError(f"missing scenario families: {missing}")
        return {family: tuple(sorted(tokens)) for family, tokens in value.items()}

    @field_validator("log_names")
    @classmethod
    def validate_log_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Sort the logs too, for the same reason the tokens are sorted."""

        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_membership(self) -> CohortManifestV1:
        """No token may appear twice, in one family or across two, and not all empty."""

        tokens = [token for family in SCENARIO_FAMILIES for token in self.families[family]]
        if len(set(tokens)) != len(tokens):
            raise ValueError("duplicate scenario token in the cohort")
        if not tokens:
            raise ValueError("the cohort is empty; a freeze that captured nothing is not a cohort")
        return self


def membership_sha256(manifest: CohortManifestV1) -> str:
    """Hash the split and its membership, and nothing else.

    Deliberately narrower than a hash of the whole document: it answers "is this
    the same set of scenarios?", which is the question a reader comparing two
    results actually has.
    """

    payload = {
        "split": manifest.split,
        "families": {family: list(manifest.families[family]) for family in SCENARIO_FAMILIES},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def save_manifest(manifest: CohortManifestV1, path: Path) -> None:
    """Write a frozen cohort, refusing to replace one that already exists.

    Overwriting silently would invalidate every result that cited the previous
    freeze while leaving those results looking current.
    """

    if path.exists():
        raise FileExistsError(f"refusing to overwrite an existing frozen cohort: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    document = manifest.model_dump(mode="json")
    # `Path.write_text` only grew a `newline` argument in 3.10, and this project
    # is 3.9 for as long as nuPlan at the pinned commit is. Opening the file is
    # the portable way to stop the platform choosing the line ending: a manifest
    # written on Windows and rebuilt in CI must hash the same.
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(document, indent=2, sort_keys=True) + "\n")


def load_manifest(path: Path) -> CohortManifestV1:
    """Read a frozen cohort back, validating it as strictly as when it was written."""

    return CohortManifestV1.model_validate(json.loads(path.read_text(encoding="utf-8")))
