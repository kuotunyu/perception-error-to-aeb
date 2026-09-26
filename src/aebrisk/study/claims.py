"""The claims registries of the policy v2 study and of the post-hoc addendum.

Each part publishes a registry of its own beside its results page, separate
from `docs/claims.yaml`: the study's claim ids begin `p3.study.policy-v2.` and
the addendum's `p3.posthoc.v1-addendum.`. A registry is built from the part's
published evidence as the released registry is built from the released evidence
(`aebrisk.analysis.claims`): one observed, verified claim per number, under the
protocol and cohort hashes the claims audit holds it to, in the released
vocabulary.

- THE STUDY (`build_study_claims`) binds every number of
  `policy-v2-evidence.json`, and each gate's outcome and counts in
  `gates.json`. A study reported not completed publishes no summary, and its
  registry holds only those gate diagnostics, with no claim about the policy.
  Either way, each earlier failed attempt's `gates-attempt-<n>.json` is bound
  under `p3.study.policy-v2.attempt-<n>.`. When the gate file carries
  `exploratory: true` (arm-A reference mode), every claim's text begins
  "Exploratory:".
- THE ADDENDUM (`build_addendum_claims`) binds every number of
  `attribution-addendum-evidence.json`, and the released `oracle_aeb`
  collisions and not-at-fault contacts in `docs/evidence/nuplan_aeb_v2/evaluation.json`,
  which a line stating `oracle_aeb` collisions must bind together.

A claim id is the part's prefix, the document's section, the row the number
belongs to (a hypothesis, a contrast's outcome and signed terms, an arm's cell,
a configuration, a game's channel) and the field, in lower case, with every
character an id cannot hold, such as the `+` of a cell name, written `-`. So an
`oracle_aeb` claim keeps `oracle_aeb` in its id. A claim's text names the same
row and states the number exactly as the evidence spells it.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from aebrisk.analysis.attribution_audit import ADDENDUM_CLAIM_PREFIX, STUDY_CLAIM_PREFIX
from aebrisk.analysis.claims import (
    ALLOWED_EVIDENCE_TYPES,
    ALLOWED_STATUSES,
    CLAIM_REQUIRED_FIELDS,
    ClaimsRegistryV1,
    ClaimV1,
    _claim_identity,
    _numeric_leaves,
    _pointer,
    _read_preserving_numbers,
    _slug,
)
from aebrisk.artifacts.study_documents import (
    AttributionAddendumEvidenceV1,
    PolicyV2EvidenceV1,
    StudyContrastV1,
    StudyGatesV1,
    StudyLevelV1,
    StudyReportedContrastV1,
)
from aebrisk.study.definition import ORACLE_CELL
from aebrisk.study.evidence import (
    ADDENDUM_EVIDENCE_FILE,
    GATES_FILE,
    POLICY_V2_EVIDENCE_FILE,
    RELEASED_EVIDENCE_DIRECTORY,
    SUMMARY_FILE,
)
from aebrisk.study.gates import load_gates

#: What every claim of a study in arm-A reference mode begins with.
EXPLORATORY = "Exploratory: "

#: The gate file of an earlier failed attempt, as `study evidence` publishes it.
EARLIER_ATTEMPT = re.compile(r"gates-attempt-([1-9][0-9]*)\.json")

#: The released counts an `oracle_aeb` collision line binds, from the released evaluation.
RELEASED_ORACLE_METRICS: tuple[str, ...] = ("collisions", "contacts_not_at_fault")

#: How a claim id names a number after the part's prefix, and how its text names it.
_Identity = tuple[str, str]

_SIGNS = {1: "plus", -1: "minus"}


# --------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------


def _artifact_path(path: Path, repository_root: Path) -> str:
    """The path the claims audit reads, from the repository root."""

    resolved, root = path.resolve(), repository_root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{path} is not in the repository at {repository_root}")
    return resolved.relative_to(root).as_posix()


def _refuse_other_provenance(
    path: Path, hashes: tuple[object, object], provenance: tuple[str, str]
) -> None:
    """Refuse a document of another protocol or cohort than the part's evidence."""

    if hashes != provenance:
        raise ValueError(
            f"{path.name} comes from another protocol or cohort: it names {hashes}, and the "
            f"evidence it is bound with names {provenance}"
        )


def _claim(
    claim_id: str, text: str, artifact_path: str, tokens: Sequence[str], provenance: tuple[str, str]
) -> ClaimV1:
    protocol, cohort = provenance
    return ClaimV1(
        claim_id=claim_id,
        text=text,
        evidence_type="observed",
        protocol_hash=protocol,
        cohort_manifest_hash=cohort,
        artifact_path=artifact_path,
        metric_path=_pointer(tuple(tokens)),
        status="verified",
    )


def _number_claims(
    path: Path,
    repository_root: Path,
    provenance: tuple[str, str],
    identity: Callable[[tuple[str, ...]], _Identity],
    prefix: str,
    text_prefix: str = "",
) -> list[ClaimV1]:
    """One claim for every number of a document, named by `identity`."""

    artifact = _artifact_path(path, repository_root)
    claims: list[ClaimV1] = []
    for tokens, number in _numeric_leaves(_read_preserving_numbers(path)):
        key, words = identity(tokens)
        claims.append(
            _claim(prefix + key, f"{text_prefix}{words} is {number}.", artifact, tokens, provenance)
        )
    return claims


def _registry(claims: Sequence[ClaimV1]) -> ClaimsRegistryV1:
    """The registry of `claims`, refused if two numbers share an id."""

    repeated = sorted(
        claim_id
        for claim_id, count in Counter(claim.claim_id for claim in claims).items()
        if count > 1
    )
    if repeated:
        raise ValueError(f"the evidence gives more than one number the claim id {repeated[0]}")
    return ClaimsRegistryV1(
        allowed_evidence_types=ALLOWED_EVIDENCE_TYPES,
        claim_required_fields=CLAIM_REQUIRED_FIELDS,
        allowed_statuses=ALLOWED_STATUSES,
        claims=tuple(claims),
    )


def _field(identity: _Identity, rest: Sequence[str]) -> _Identity:
    """A row's identity followed by the field a number lies in; a list index names no word."""

    key, words = identity
    return (
        f"{key}.{_slug('-'.join(rest))}",
        f"{words}: {' '.join(token for token in rest if not token.isdecimal())}",
    )


def _identity(rows: Mapping[str, Sequence[_Identity]], tokens: tuple[str, ...]) -> _Identity:
    """Name a number by its section and, in a section of rows, by its row's identity."""

    section, rest = tokens[0], tokens[1:]
    if section in rows:
        key, words = rows[section][int(rest[0])]
        return _field((f"{_slug(section)}.{key}", words), rest[1:])
    if rest:
        return _field((_slug(section), section), rest)
    return _slug(section), section


# --------------------------------------------------------------------------
# The study
# --------------------------------------------------------------------------


def _contrast(contrast: StudyContrastV1) -> _Identity:
    """A contrast's outcome, signed terms and family."""

    key = ".".join(
        [
            _slug(contrast.outcome),
            *(
                f"{_SIGNS[term.sign]}-{_slug(term.arm)}-{_slug(term.cell)}"
                for term in contrast.terms
            ),
        ]
    )
    terms = " ".join(f"{_SIGNS[term.sign]} {term.arm} in {term.cell}" for term in contrast.terms)
    words = f"{contrast.outcome} of {terms.removeprefix('plus ')}"
    if contrast.family is not None:
        key, words = f"{key}.{_slug(contrast.family)}", f"{words} in family {contrast.family}"
    return key, words


def _named(names: Sequence[str], contrast: StudyContrastV1) -> _Identity:
    """A contrast after the names of its row, such as its hypothesis and its analysis."""

    key, words = _contrast(contrast)
    return ".".join([*(_slug(name) for name in names), key]), f"{' '.join(names)}, {words}"


def _reported(row: StudyReportedContrastV1) -> _Identity:
    names: list[str] = [row.analysis]
    if row.hypothesis is not None:
        names.append(row.hypothesis)
    return _named(names, row.contrast)


def _level(row: StudyLevelV1) -> _Identity:
    key, words = f"{_slug(row.arm)}.{_slug(row.cell)}", f"{row.arm} in {row.cell}"
    if row.family is not None:
        key, words = f"{key}.{_slug(row.family)}", f"{words} in family {row.family}"
    return key, words


def _policy_rows(policy: PolicyV2EvidenceV1) -> dict[str, list[_Identity]]:
    """The identity of every row of each section of rows in the policy evidence."""

    return {
        "hypotheses": [_named([row.id], row.contrast) for row in policy.hypotheses],
        "sensitivity": [
            _named([row.hypothesis, row.analysis], row.contrast) for row in policy.sensitivity
        ],
        "secondary": [_reported(row) for row in policy.secondary],
        "descriptive_contrasts": [_reported(row) for row in policy.descriptive_contrasts],
        "levels": [_level(row) for row in policy.levels],
    }


def _gate_claims(
    path: Path,
    gates: StudyGatesV1,
    section: str,
    words: str,
    repository_root: Path,
    provenance: tuple[str, str],
    text_prefix: str,
) -> list[ClaimV1]:
    """Each gate's outcome and each of its counts."""

    artifact = _artifact_path(path, repository_root)
    claims: list[ClaimV1] = []
    for index, gate in enumerate(gates.gates):
        key = f"{STUDY_CLAIM_PREFIX}{section}.{_slug(gate.gate)}"
        outcome = "passed" if gate.passed else "failed"
        tokens = ("gates", str(index))
        claims.append(
            _claim(
                f"{key}.passed",
                f"{text_prefix}{words}{gate.gate} {outcome}.",
                artifact,
                (*tokens, "passed"),
                provenance,
            )
        )
        claims.extend(
            _claim(
                f"{key}.{_slug(name)}",
                f"{text_prefix}{words}{gate.gate} counted {count} for {name}.",
                artifact,
                (*tokens, "counts", name),
                provenance,
            )
            for name, count in gate.counts.items()
        )
    return claims


def _earlier_attempts(evidence: Path) -> list[tuple[int, Path]]:
    """Each earlier attempt's gate file in the evidence directory, by attempt number."""

    found = [(EARLIER_ATTEMPT.fullmatch(path.name), path) for path in evidence.iterdir()]
    return sorted((int(match.group(1)), path) for match, path in found if match is not None)


def build_study_claims(evidence_dir: Path, repository_root: Path) -> ClaimsRegistryV1:
    """The policy v2 study's registry, from the evidence `study evidence` wrote.

    A relative `evidence_dir` is read from `repository_root`, from which every
    artifact path is written. Without `summary.json` the registry is gate-only.
    """

    evidence = repository_root / evidence_dir
    gates_path = evidence / GATES_FILE
    gates = load_gates(gates_path)
    provenance = (gates.protocol_sha256, gates.cohort_manifest_sha256)
    text_prefix = EXPLORATORY if gates.exploratory else ""

    claims: list[ClaimV1] = []
    if (evidence / SUMMARY_FILE).is_file():
        path = evidence / POLICY_V2_EVIDENCE_FILE
        policy = PolicyV2EvidenceV1.model_validate_json(path.read_bytes())
        _refuse_other_provenance(
            path, (policy.protocol_sha256, policy.cohort_manifest_sha256), provenance
        )
        rows = _policy_rows(policy)
        claims.extend(
            _number_claims(
                path,
                repository_root,
                provenance,
                lambda tokens: _identity(rows, tokens),
                STUDY_CLAIM_PREFIX,
                text_prefix,
            )
        )
    claims.extend(
        _gate_claims(gates_path, gates, "gates", "", repository_root, provenance, text_prefix)
    )
    for number, path in _earlier_attempts(evidence):
        attempt = load_gates(path)
        _refuse_other_provenance(
            path, (attempt.protocol_sha256, attempt.cohort_manifest_sha256), provenance
        )
        claims.extend(
            _gate_claims(
                path,
                attempt,
                f"attempt-{number}",
                "In an earlier attempt, ",
                repository_root,
                provenance,
                text_prefix,
            )
        )
    return _registry(claims)


# --------------------------------------------------------------------------
# The addendum
# --------------------------------------------------------------------------


def _addendum_rows(addendum: AttributionAddendumEvidenceV1) -> dict[str, list[_Identity]]:
    """The identity of every row of each section of rows in the addendum evidence."""

    rows = {
        "configuration_contrasts": [
            (
                f"{_slug(row.metric)}.{_slug(row.plus)}-minus-{_slug(row.minus)}",
                f"{row.metric} of {row.plus} minus {row.minus}",
            )
            for row in addendum.configuration_contrasts
        ]
    }
    for section in (
        "brake_activations",
        "zero_event_configurations",
        "matched_onset_delays",
        "stops_and_collision_speeds",
    ):
        rows[section] = [
            (_slug(row.configuration_id), f"{section} of {row.configuration_id}")
            for row in getattr(addendum, section)
        ]
    return rows


def _addendum_identity(
    addendum: AttributionAddendumEvidenceV1,
    rows: Mapping[str, Sequence[_Identity]],
    tokens: tuple[str, ...],
) -> _Identity:
    """Name a Shapley value by its game and channel, and a difference by its two channels."""

    if tokens[0] != "games":
        return _identity(rows, tokens)
    game = addendum.games[int(tokens[1])]
    if tokens[2] == "shapley_values":
        channel = tokens[3]
        identity = (
            f"shapley.{_slug(game.game)}.{_slug(channel)}",
            f"Shapley value of {channel} in the {game.game} game",
        )
    else:
        difference = getattr(game, tokens[2])[int(tokens[3])]
        identity = (
            f"difference.{_slug(game.game)}.{_slug(difference.plus)}-minus-"
            f"{_slug(difference.minus)}",
            f"Shapley value of {difference.plus} minus that of {difference.minus} in the "
            f"{game.game} game",
        )
    return _field(identity, tokens[4:])


def _released_oracle_claims(repository_root: Path, provenance: tuple[str, str]) -> list[ClaimV1]:
    """The released `oracle_aeb` counts that a line stating its collisions binds together."""

    path = repository_root.joinpath(*RELEASED_EVIDENCE_DIRECTORY, "evaluation.json")
    document: dict[str, Any] = _read_preserving_numbers(path)
    _refuse_other_provenance(
        path,
        (document.get("protocol_sha256"), document.get("cohort_manifest_sha256")),
        provenance,
    )
    rows = document["configurations"]
    index = [row["configuration_id"] for row in rows].index(ORACLE_CELL)
    artifact = _artifact_path(path, repository_root)
    claims: list[ClaimV1] = []
    for metric in RELEASED_ORACLE_METRICS:
        tokens = ("configurations", str(index), metric)
        released_id, words = _claim_identity("evaluation.json", document, tokens)
        claims.append(
            _claim(
                ADDENDUM_CLAIM_PREFIX + released_id.removeprefix("p3."),
                f"{words} is {rows[index][metric]}.",
                artifact,
                tokens,
                provenance,
            )
        )
    return claims


def build_addendum_claims(evidence_dir: Path, repository_root: Path) -> ClaimsRegistryV1:
    """The post-hoc addendum's registry, from the evidence `study evidence` wrote.

    A relative `evidence_dir` is read from `repository_root`, from which every
    artifact path is written.
    """

    path = repository_root / evidence_dir / ADDENDUM_EVIDENCE_FILE
    addendum = AttributionAddendumEvidenceV1.model_validate_json(path.read_bytes())
    provenance = (addendum.protocol_sha256, addendum.cohort_manifest_sha256)
    rows = _addendum_rows(addendum)
    claims = _number_claims(
        path,
        repository_root,
        provenance,
        lambda tokens: _addendum_identity(addendum, rows, tokens),
        ADDENDUM_CLAIM_PREFIX,
    )
    claims.extend(_released_oracle_claims(repository_root, provenance))
    return _registry(claims)
