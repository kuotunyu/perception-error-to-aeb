"""Generate and audit exact public claims against committed AEB evidence."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ALLOWED_EVIDENCE_TYPES = ("observed", "derived", "synthetic", "illustrative")
CLAIM_REQUIRED_FIELDS = (
    "claim_id",
    "text",
    "evidence_type",
    "protocol_hash",
    "cohort_manifest_hash",
    "artifact_path",
    "metric_path",
    "status",
)
ALLOWED_STATUSES = ("draft", "verified", "rejected", "superseded")

PUBLISHED_DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("evaluation.json", "aeb-evaluation/v1"),
    ("intervals.json", "aeb-intervals/v1"),
    ("shapley.json", "aeb-shapley/v1"),
    ("exclusions.json", "aeb-exclusions/v1"),
    ("family-interventions.json", "aeb-family-interventions/v1"),
)

_NUMBER_PATTERN = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


class _JsonNumber(str):
    """A number token retained exactly as it appeared in the JSON document."""


class ClaimV1(BaseModel):
    """One immutable public statement linked to one exact artifact number."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    text: str = Field(min_length=1)
    evidence_type: Literal["observed", "derived", "synthetic", "illustrative"]
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cohort_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_path: str = Field(min_length=1)
    metric_path: str = Field(pattern=r"^/")
    status: Literal["draft", "verified", "rejected", "superseded"]


class ClaimsRegistryV1(BaseModel):
    """This repository's strict copy of the portfolio claim vocabulary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_evidence_types: tuple[str, ...]
    claim_required_fields: tuple[str, ...]
    allowed_statuses: tuple[str, ...]
    claims: tuple[ClaimV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_shared_vocabulary(self) -> ClaimsRegistryV1:
        if self.allowed_evidence_types != ALLOWED_EVIDENCE_TYPES:
            raise ValueError("allowed_evidence_types must match the approved vocabulary")
        if self.claim_required_fields != CLAIM_REQUIRED_FIELDS:
            raise ValueError("claim_required_fields must match the approved vocabulary")
        if self.allowed_statuses != ALLOWED_STATUSES:
            raise ValueError("allowed_statuses must match the approved vocabulary")
        return self


def load_registry(claims_path: Path) -> ClaimsRegistryV1:
    """Load and strictly validate one claims registry document."""

    raw_value = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    return ClaimsRegistryV1.model_validate(raw_value)


def verified_claims(claims_path: Path) -> tuple[ClaimV1, ...]:
    """Return only claims permitted to reach a public README or report."""

    return tuple(claim for claim in load_registry(claims_path).claims if claim.status == "verified")


def _resolve_json_pointer(document: object, pointer: str) -> object:
    current = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and (
            token == "0" or (not token.startswith("0") and token.isdecimal())
        ):
            index = int(token)
            if index >= len(current):
                raise LookupError(pointer)
            current = current[index]
        else:
            raise LookupError(pointer)
    return current


def _text_numbers(text: str) -> tuple[Decimal, ...]:
    return tuple(
        Decimal(match.group().removesuffix("%")) for match in _NUMBER_PATTERN.finditer(text)
    )


def _json_numbers(value: object) -> set[Decimal]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float, Decimal)):
        return {Decimal(str(value))}
    numbers: set[Decimal] = set()
    if isinstance(value, dict):
        for child in value.values():
            numbers.update(_json_numbers(child))
    elif isinstance(value, list):
        for child in value:
            numbers.update(_json_numbers(child))
    return numbers


def audit_claims(claims_path: Path, repository_root: Path) -> tuple[str, ...]:
    """Report every claim whose exact evidence cannot be reproduced locally."""

    try:
        registry = load_registry(claims_path)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValidationError) as exc:
        return (f"claims registry is invalid: {exc}",)

    root = repository_root.resolve()
    violations: list[str] = []
    for claim in registry.claims:
        artifact_path = (root / claim.artifact_path).resolve()
        if not artifact_path.is_relative_to(root):
            violations.append(
                f"{claim.claim_id}: artifact path escapes repository: {claim.artifact_path}"
            )
            continue
        if not artifact_path.is_file():
            violations.append(f"{claim.claim_id}: artifact does not exist: {claim.artifact_path}")
            continue

        try:
            artifact: Any = json.loads(
                artifact_path.read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (OSError, UnicodeDecodeError, ValueError):
            violations.append(f"{claim.claim_id}: artifact is not valid UTF-8 JSON")
            continue

        if not isinstance(artifact, dict):
            violations.append(f"{claim.claim_id}: artifact root must be an object")
            continue
        if artifact.get("protocol_sha256") != claim.protocol_hash:
            violations.append(f"{claim.claim_id}: protocol hash mismatch")
        if artifact.get("cohort_manifest_sha256") != claim.cohort_manifest_hash:
            violations.append(f"{claim.claim_id}: cohort manifest hash mismatch")

        try:
            metric = _resolve_json_pointer(artifact, claim.metric_path)
        except LookupError:
            violations.append(
                f"{claim.claim_id}: metric JSON pointer does not exist: {claim.metric_path}"
            )
            continue

        metric_numbers = _json_numbers(metric)
        for number in _text_numbers(claim.text):
            if number not in metric_numbers:
                violations.append(f"{claim.claim_id}: claim number is absent from metric: {number}")

    return tuple(violations)


def _read_preserving_numbers(path: Path) -> dict[str, Any]:
    try:
        raw: Any = json.loads(
            path.read_text(encoding="utf-8"),
            parse_int=_JsonNumber,
            parse_float=_JsonNumber,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid published evidence {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"published evidence root must be an object: {path}")
    return raw


def _read_bytes_preserving_numbers(payload: bytes, source: str) -> dict[str, Any]:
    del source  # The strict report model already validated these exact bytes.
    return cast(
        dict[str, Any],
        json.loads(
            payload.decode("utf-8"),
            parse_int=_JsonNumber,
            parse_float=_JsonNumber,
            parse_constant=_reject_json_constant,
        ),
    )


def evidence_provenance(evidence_dir: Path) -> tuple[str, str]:
    """Read the two exact provenance keys the generation command must bind."""

    path = evidence_dir / "evaluation.json"
    if not path.is_file():
        raise ValueError(f"missing published evidence: {path}")
    document = _read_preserving_numbers(path)
    protocol = document.get("protocol_sha256")
    cohort = document.get("cohort_manifest_sha256")
    if not isinstance(protocol, str) or not isinstance(cohort, str):
        raise ValueError("evaluation evidence must name protocol_sha256 and cohort_manifest_sha256")
    return protocol, cohort


def _repository_root(evidence_dir: Path) -> Path:
    resolved = evidence_dir.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise ValueError(f"cannot find repository root above evidence directory: {evidence_dir}")


def _pointer(tokens: tuple[str, ...]) -> str:
    escaped = (token.replace("~", "~0").replace("/", "~1") for token in tokens)
    return "/" + "/".join(escaped)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")


def _claim_identity(
    filename: str, document: dict[str, Any], tokens: tuple[str, ...]
) -> tuple[str, str]:
    stem = Path(filename).stem
    if stem == "evaluation" and len(tokens) == 3 and tokens[0] == "configurations":
        row = document["configurations"][int(tokens[1])]
        configuration = str(row["configuration_id"])
        group = str(row["group"])
        metric = tokens[2]
        return f"p3.{_slug(group)}.{_slug(metric)}.{_slug(configuration)}", (
            f"{configuration} {metric}"
        )
    if stem == "intervals" and len(tokens) == 4 and tokens[0] == "intervals":
        configuration, metric, field = tokens[1:]
        return f"p3.intervals.{_slug(metric)}-{_slug(field)}.{_slug(configuration)}", (
            f"{configuration} {metric} bootstrap {field}"
        )
    if stem == "shapley" and len(tokens) >= 3 and tokens[0] == "metrics":
        metric = tokens[1]
        detail = "-".join(tokens[2:])
        return f"p3.shapley.{_slug(metric)}-{_slug(detail)}", f"Shapley {metric} {detail}"
    if stem == "family-interventions" and len(tokens) == 3 and tokens[0] == "rows":
        row = document["rows"][int(tokens[1])]
        family = str(row["family"])
        configuration = str(row["configuration_id"])
        metric = tokens[2]
        return (
            f"p3.family-interventions.{_slug(family)}.{_slug(configuration)}.{_slug(metric)}",
            f"{family} {configuration} {metric}",
        )
    metric = "-".join(tokens)
    return f"p3.{_slug(stem)}.{_slug(metric)}", f"{stem.title()} {' '.join(tokens)}"


def _numeric_leaves(
    value: object, tokens: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], str]]:
    if isinstance(value, _JsonNumber):
        return [(tokens, str(value))]
    leaves: list[tuple[tuple[str, ...], str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            leaves.extend(_numeric_leaves(child, (*tokens, str(key))))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            leaves.extend(_numeric_leaves(child, (*tokens, str(index))))
    return leaves


def _claims_for_document(
    filename: str,
    document: dict[str, Any],
    artifact_path: str,
    protocol_sha256: str,
    cohort_manifest_sha256: str,
) -> tuple[ClaimV1, ...]:
    claims: list[ClaimV1] = []
    for tokens, number in _numeric_leaves(document):
        claim_id, label = _claim_identity(filename, document, tokens)
        claims.append(
            ClaimV1(
                claim_id=claim_id,
                text=f"{label} is {number}.",
                evidence_type="observed",
                protocol_hash=protocol_sha256,
                cohort_manifest_hash=cohort_manifest_sha256,
                artifact_path=artifact_path,
                metric_path=_pointer(tokens),
                status="verified",
            )
        )
    return tuple(claims)


def validate_supplied_evidence_claims(
    claims_path: Path,
    filename: str,
    payload: bytes,
) -> None:
    """Bind one exact supplied report document to its complete registry claims."""

    document = _read_bytes_preserving_numbers(payload, filename)
    protocol = cast(str, document["protocol_sha256"])
    cohort = cast(str, document["cohort_manifest_sha256"])

    expected = _claims_for_document(filename, document, filename, protocol, cohort)
    registry = load_registry(claims_path)
    actual = tuple(claim for claim in registry.claims if Path(claim.artifact_path).name == filename)
    actual_paths = {claim.artifact_path for claim in actual}
    if len(actual_paths) > 1:
        raise ValueError(
            f"{filename} disagrees with the claim registry: its basename is ambiguous across "
            f"{sorted(actual_paths)}"
        )

    expected_by_id = {claim.claim_id: claim for claim in expected}
    actual_by_id = {claim.claim_id: claim for claim in actual}
    if len(actual_by_id) != len(actual):
        raise ValueError(
            f"{filename} disagrees with the claim registry: duplicate numeric claim id"
        )
    missing = sorted(set(expected_by_id) - set(actual_by_id))
    extra = sorted(set(actual_by_id) - set(expected_by_id))
    if missing or extra:
        raise ValueError(
            f"{filename} disagrees with the claim registry: missing claims {missing}, "
            f"extra claims {extra}"
        )

    for claim_id, expected_claim in expected_by_id.items():
        expected_fields = expected_claim.model_dump(exclude={"artifact_path"})
        actual_fields = actual_by_id[claim_id].model_dump(exclude={"artifact_path"})
        if actual_fields != expected_fields:
            raise ValueError(f"{filename} disagrees with the claim registry at claim {claim_id!r}")


def generate_claims(
    evidence_dir: Path, protocol_sha256: str, cohort_manifest_sha256: str
) -> ClaimsRegistryV1:
    """Generate one observed claim for every numeric leaf in the analysis documents."""

    root = _repository_root(evidence_dir)
    claims: list[ClaimV1] = []
    for filename, schema_version in PUBLISHED_DOCUMENTS:
        path = evidence_dir / filename
        if not path.is_file():
            raise ValueError(f"missing published evidence: {path}")
        document = _read_preserving_numbers(path)
        if document.get("schema_version") != schema_version:
            raise ValueError(f"{filename} does not declare {schema_version}")
        if document.get("protocol_sha256") != protocol_sha256:
            raise ValueError(f"{filename}: protocol hash mismatch")
        if document.get("cohort_manifest_sha256") != cohort_manifest_sha256:
            raise ValueError(f"{filename}: cohort manifest hash mismatch")
        artifact_path = path.resolve().relative_to(root).as_posix()
        claims.extend(
            _claims_for_document(
                filename,
                document,
                artifact_path,
                protocol_sha256,
                cohort_manifest_sha256,
            )
        )
    if not claims:
        raise ValueError("published evidence produced no numeric claims")
    claim_ids = [claim.claim_id for claim in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("published evidence produced duplicate claim ids")
    return ClaimsRegistryV1(
        allowed_evidence_types=ALLOWED_EVIDENCE_TYPES,
        claim_required_fields=CLAIM_REQUIRED_FIELDS,
        allowed_statuses=ALLOWED_STATUSES,
        claims=tuple(claims),
    )
