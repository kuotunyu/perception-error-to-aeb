"""Audit AEB attribution prose against exact published evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from aebrisk.analysis.claims import (
    ClaimV1,
    _json_numbers,
    _resolve_json_pointer,
    audit_claims,
    load_registry,
)

MARKER = re.compile(r"<!--\s*claim:\s*([a-z0-9][a-z0-9._-]*)\s*-->")
NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?")
BINDING = re.compile(
    r"`(?P<metric>[a-z][a-z0-9_]*)`\s*=\s*"
    r"(?P<value>[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"(?P<percent>\s*%)?"
)
RESULT_TERM = re.compile(
    r"(?:\b(?:shapley|collision(?:s|_indicator)?|contacts_not_at_fault|"
    r"intervention(?:s|_duration_s)?|false_interventions|missed_interventions|"
    r"common_valid_tokens|common[-_ ]valid|cohort)\b|\u5171\u540c\u6709\u6548\u6a23\u672c)",
    re.IGNORECASE,
)
UNAVAILABLE_PER_100_KM = re.compile(
    r"(?:(?:collisions?\s+)?(?:per[-_ ]?100\s*km|/\s*100\s*km)"
    r"(?:\s+(?:collision\s+)?rate)?\s+(?:is|was|were)\s+unavailable"
    r"|\u6bcf\s*100\s*km\s*\u78b0\u649e\u7387(?:\u70ba)?\u672a\u63d0\u4f9b)",
    re.IGNORECASE,
)
NUMERIC_PER_100_KM = re.compile(
    rf"(?:{NUMBER.pattern}\s+(?:collisions?\s+)?"
    rf"(?:per[-_ ]?100\s*km|/\s*100\s*km|\u6bcf\s*100\s*km)"
    rf"|`collisions_per_100km`\s*=\s*{NUMBER.pattern})",
    re.IGNORECASE,
)
COHORT_SIZE = re.compile(
    r"(?:common[-_ ]valid(?:\s+(?:cohort|tokens?))?|cohort(?:\s+of)?|"
    r"`?common_valid_tokens`?|\u5171\u540c\u6709\u6548\u6a23\u672c)\D{0,20}(\d+)",
    re.IGNORECASE,
)
FENCE_PREFIXES = ("```", "~~~")


def _text_numbers(text: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(match.group().removesuffix("%")) for match in NUMBER.finditer(text))


def _json_document(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _claim_numbers(claim: ClaimV1, repository_root: Path) -> set[Decimal]:
    artifact = repository_root / claim.artifact_path
    return _json_numbers(_resolve_json_pointer(_json_document(artifact), claim.metric_path))


def _claim_metric(claim: ClaimV1) -> str:
    """Return the stable metric key required in a constrained result binding."""

    tokens = [token for token in claim.metric_path.split("/") if token]
    if Path(claim.artifact_path).name == "shapley.json" and tokens[:1] == ["metrics"]:
        return tokens[1]
    return tokens[-1]


def _result_numbers(text: str) -> tuple[Decimal, ...]:
    """Ignore a literal distance denominator when no numeric rate is stated."""

    return _text_numbers(UNAVAILABLE_PER_100_KM.sub("", text))


def _common_valid_tokens(registry: dict[str, ClaimV1], repository_root: Path) -> int:
    relative = next(
        claim.artifact_path
        for claim in registry.values()
        if Path(claim.artifact_path).name == "shapley.json"
    )
    document = _json_document(repository_root / relative)
    return int(document["common_valid_tokens"])  # type: ignore[index]


def _structural_violations(
    source: str,
    text: str,
    claims: tuple[ClaimV1, ...],
    common_valid_tokens: int,
    repository_root: Path,
) -> list[str]:
    lower = text.lower()
    violations: list[str] = []
    if NUMERIC_PER_100_KM.search(text):
        violations.append(
            f"{source}: per-100 km rate is unpublished in this release; evaluation.json holds null"
        )

    for match in COHORT_SIZE.finditer(text):
        stated = int(match.group(1))
        if stated != common_valid_tokens:
            violations.append(
                f"{source}: stated cohort {stated}, but the common-valid cohort is "
                f"{common_valid_tokens}"
            )

    claim_metrics = {_claim_metric(claim) for claim in claims}
    if {"collision_indicator", "intervention_duration_s"} <= claim_metrics:
        violations.append(
            f"{source}: collision_indicator and intervention_duration_s are separate estimands; "
            "state them on separate result lines and never sum, compare or rank them"
        )

    attribution = "shapley" in lower or "attribut" in lower
    if attribution and any(Path(claim.artifact_path).name != "shapley.json" for claim in claims):
        violations.append(f"{source}: every Shapley number must trace to shapley.json")

    oracle_collision_claims = [
        claim
        for claim in claims
        if _claim_metric(claim) == "collisions" and "oracle_aeb" in claim.claim_id
    ]
    oracle_collision = (
        re.search(r"oracle(?:_|\s|-)?aeb", lower) and re.search(r"\bcollisions?\b", lower)
    ) or oracle_collision_claims
    if oracle_collision:
        contact_claims = [
            claim
            for claim in claims
            if claim.metric_path.endswith("/contacts_not_at_fault")
            and "oracle_aeb" in claim.claim_id
        ]
        expected_contacts = {
            number for claim in contact_claims for number in _claim_numbers(claim, repository_root)
        }
        contact_bindings = [
            match
            for match in BINDING.finditer(text)
            if match.group("metric") == "contacts_not_at_fault"
            and match.group("percent") is None
            and Decimal(match.group("value")) in expected_contacts
        ]
        if "contacts_not_at_fault" not in lower or not contact_claims:
            violations.append(
                f"{source}: an oracle_aeb collision result must also state "
                "contacts_not_at_fault and cite its claim"
            )
        elif not contact_bindings:
            expected = ", ".join(str(number) for number in sorted(expected_contacts))
            violations.append(
                f"{source}: an oracle_aeb collision result must state "
                f"contacts_not_at_fault = {expected}"
            )
    return violations


def _check_statement(
    source: str,
    text: str,
    claim_ids: tuple[str, ...],
    registry: dict[str, ClaimV1],
    repository_root: Path,
    common_valid_tokens: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    violations: list[str] = []
    claims: list[ClaimV1] = []
    traces: list[dict[str, Any]] = []
    claim_numbers: dict[str, set[Decimal]] = {}
    for claim_id in claim_ids:
        claim = registry.get(claim_id)
        if claim is None:
            violations.append(f"{source} {claim_id}: no registry claim backs this result")
            continue
        claims.append(claim)
        numbers = _claim_numbers(claim, repository_root)
        claim_numbers[claim_id] = numbers
        traces.append(
            {
                "source": source,
                "claim_id": claim_id,
                "artifact_path": claim.artifact_path,
                "metric_path": claim.metric_path,
                "numbers": [str(number) for number in sorted(numbers)],
                "verdict": "pass",
            }
        )

    violations.extend(
        _structural_violations(source, text, tuple(claims), common_valid_tokens, repository_root)
    )
    bindings = tuple(BINDING.finditer(text))
    if len(bindings) != len(claim_ids):
        violations.append(
            f"{source}: unsupported result syntax; write each value as "
            "`metric_key` = exact_value and pair one claim marker in the same order"
        )
    stated_numbers = Counter(_result_numbers(text))
    bound_numbers = Counter(Decimal(binding.group("value")) for binding in bindings)
    if stated_numbers != bound_numbers:
        violations.append(
            f"{source}: every numeric result needs a metric binding; use `metric_key` = exact_value"
        )

    for binding, claim_id in zip(bindings, claim_ids):
        claim = registry.get(claim_id)
        if claim is None:
            continue
        numbers = claim_numbers[claim_id]
        metric = binding.group("metric")
        expected_metric = _claim_metric(claim)
        if metric != expected_metric:
            violations.append(
                f"{source}: binding `{metric}` binds {metric} to {expected_metric} claim"
            )
            continue
        raw_value = binding.group("value")
        if binding.group("percent") is not None:
            violations.append(
                f"{source}: `{metric}` uses a percent conversion; "
                "percent conversion is not registered"
            )
            continue
        value = Decimal(raw_value)
        if value not in numbers:
            held = ", ".join(str(number) for number in sorted(numbers)) or "no number"
            if metric == "common_valid_tokens":
                violations.append(
                    f"{source}: stated cohort {value}, but the common-valid cohort is "
                    f"{common_valid_tokens}"
                )
            else:
                violations.append(
                    f"{source}: `{metric}` says {value}, but the {expected_metric} "
                    f"claim holds {held}"
                )
    if violations:
        for trace in traces:
            trace["verdict"] = "fail"
    return violations, traces


def _proposal_statements(path: Path) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = document.get("proposals") if isinstance(document, dict) else document
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected a list under 'proposals'")
    statements: list[tuple[str, str, tuple[str, ...]]] = []
    for position, entry in enumerate(entries):
        source = f"proposal[{position}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            statements.append((source, "", ()))
            continue
        raw_ids = entry.get("claim_ids")
        if (
            not isinstance(raw_ids, list)
            or not raw_ids
            or not all(isinstance(value, str) for value in raw_ids)
        ):
            statements.append((source, entry["text"], ()))
            continue
        statements.append((source, entry["text"], tuple(raw_ids)))
    return tuple(statements)


def _document_statements(path: Path) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    statements: list[tuple[str, str, tuple[str, ...]]] = []
    in_fence = False
    in_result_table = False
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if raw.strip().startswith(FENCE_PREFIXES):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        is_table_row = "|" in raw
        if is_table_row and RESULT_TERM.search(raw):
            in_result_table = True
        elif not is_table_row:
            in_result_table = False
        claim_ids = tuple(MARKER.findall(raw))
        text = MARKER.sub("", raw).strip()
        result_numbers = _result_numbers(text)
        if claim_ids or ((RESULT_TERM.search(text) or in_result_table) and result_numbers):
            statements.append((f"{path.name}:{line_number}", text, claim_ids))
    return tuple(statements)


def validate_attribution(
    claims_path: Path,
    repository_root: Path,
    proposal_path: Path | None,
    document_paths: list[Path],
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Return every publication violation and a machine-readable evidence trace."""

    registry_model = load_registry(claims_path)
    registry = {claim.claim_id: claim for claim in registry_model.claims}
    violations = [
        f"registry: {violation}" for violation in audit_claims(claims_path, repository_root)
    ]
    common_valid_tokens = _common_valid_tokens(registry, repository_root)
    statements: list[tuple[str, str, tuple[str, ...]]] = []
    if proposal_path is not None:
        statements.extend(_proposal_statements(proposal_path))
    for document_path in document_paths:
        statements.extend(_document_statements(document_path))

    traces: list[dict[str, Any]] = []
    for source, text, claim_ids in statements:
        if not text or not claim_ids:
            violations.append(
                f"{source}: result needs text and claim_ids; no <!-- claim: ... --> marker"
            )
            violations.extend(
                _structural_violations(source, text, (), common_valid_tokens, repository_root)
            )
            continue
        found, traced = _check_statement(
            source, text, claim_ids, registry, repository_root, common_valid_tokens
        )
        violations.extend(found)
        traces.extend(traced)

    status = {
        "validator": "validate_attribution",
        "claims_registry": str(claims_path),
        "common_valid_tokens": common_valid_tokens,
        "statements": traces,
    }
    return tuple(violations), status


def main(argv: list[str] | None = None) -> int:
    """Audit Markdown or YAML attribution prose and print every violation."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claims", required=True, type=Path)
    parser.add_argument("--repo-root", default=Path(), type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--document", action="append", default=[], type=Path)
    arguments = parser.parse_args(argv)
    if arguments.proposal is None and not arguments.document:
        print("nothing to audit: pass --proposal and/or --document", file=sys.stderr)
        return 2
    try:
        violations, status = validate_attribution(
            arguments.claims,
            arguments.repo_root,
            arguments.proposal,
            arguments.document,
        )
    except (
        KeyError,
        OSError,
        StopIteration,
        LookupError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ) as error:
        print(f"validation could not run: {error}", file=sys.stderr)
        return 2
    if violations:
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0
