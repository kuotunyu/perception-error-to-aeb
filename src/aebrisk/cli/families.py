"""Generate the observed per-family intervention summary from formal results."""

# Typer on the pinned Python 3.9 runtime needs evaluated Annotated metadata.

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from aebrisk.analysis.aggregate import load_formal_results
from aebrisk.analysis.family_interventions import family_interventions
from aebrisk.artifacts.documents import write_document
from aebrisk.cohort.manifest import load_manifest


def summarize_families(
    results_dir: Annotated[Path, typer.Option("--results-dir")],
    manifest: Annotated[Path, typer.Option("--manifest")],
    protocol: Annotated[Path, typer.Option("--protocol")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
) -> None:
    """Write one strict summary without running any simulation."""

    try:
        protocol_bytes = protocol.read_bytes()
        protocol_document: Any = yaml.safe_load(protocol_bytes.decode("utf-8"))
        if not isinstance(protocol_document, dict):
            raise ValueError("protocol root must be a mapping")
        cohort_manifest = load_manifest(manifest)
        if hashlib.sha256(protocol_bytes).hexdigest() != cohort_manifest.protocol_sha256:
            raise ValueError("protocol hash does not match the frozen cohort manifest")
        summary = family_interventions(
            load_formal_results(results_dir), cohort_manifest, protocol_document
        )
        path = output_dir / "family-interventions.json"
        write_document(summary, path)
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError, json.JSONDecodeError) as error:
        typer.echo(f"summarize-families failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"summarize-families wrote {path}")
