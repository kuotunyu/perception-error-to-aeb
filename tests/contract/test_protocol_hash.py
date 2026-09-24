"""A checkout must reproduce the protocol bytes every published hash names.

Every evidence document, cohort manifest and registered claim cites one
`protocol_sha256`, and `simulate` and `summarize-families` refuse to run when
the protocol file on disk hashes to anything else. That hash was taken over the
raw bytes the formal run read, which had CRLF line endings. Git stores the file
with LF endings like every other text file, so a checkout produces the
published bytes only because `.gitattributes` asks for CRLF on this one path.
Without that rule a clean clone hashes the file to a different value and
cannot rerun anything the evidence describes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = "configs/protocols/nuplan_aeb_v2.yaml"
EVIDENCE = ROOT / "docs" / "evidence" / "nuplan_aeb_v2"
PUBLISHED = (
    "cohort/development.json",
    "cohort/evaluation.json",
    "cohort/smoke.json",
    "evaluation.json",
    "exclusions.json",
    "family-interventions.json",
    "intervals.json",
    "shapley.json",
)


def checked_out_protocol_sha256() -> str:
    return hashlib.sha256((ROOT / PROTOCOL).read_bytes()).hexdigest()


def test_git_writes_the_protocol_with_crlf_line_endings_on_every_system() -> None:
    """The attribute, not the machine's settings, decides the bytes on disk."""

    result = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", PROTOCOL],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        f"{PROTOCOL}: text: set",
        f"{PROTOCOL}: eol: crlf",
    ]


@pytest.mark.parametrize("document", PUBLISHED)
def test_the_checked_out_protocol_hashes_to_the_published_value(document: str) -> None:
    """A mismatch here is the refusal a clean clone would meet at `simulate`."""

    recorded = json.loads((EVIDENCE / document).read_text(encoding="utf-8"))["protocol_sha256"]

    assert checked_out_protocol_sha256() == recorded


def test_every_published_document_that_cites_a_protocol_is_checked() -> None:
    """An evidence file that cites the protocol and is not listed would go unchecked."""

    citing = sorted(
        path.relative_to(EVIDENCE).as_posix()
        for path in EVIDENCE.glob("**/*.json")
        if "protocol_sha256" in json.loads(path.read_text(encoding="utf-8"))
    )

    assert citing == list(PUBLISHED)


def test_every_registered_claim_cites_the_checked_out_protocol() -> None:
    registry = yaml.safe_load((ROOT / "docs" / "claims.yaml").read_text(encoding="utf-8"))

    assert {claim["protocol_hash"] for claim in registry["claims"]} == {
        checked_out_protocol_sha256()
    }
