#!/usr/bin/env python3
"""Refuse a set of AEB runs that cannot be compared with each other.

The study's entire claim is that the CONTROLLER, the ROUTE, the INITIAL STATE,
the RATE and the TERMINATION were identical across configurations and only the
perception differed. Every check here is one way that claim can be false while
every individual run still looks finished, which is why it is a script rather
than a paragraph in a document: a reviewer can run it, and so can the agent
that is about to write a number into a README.

Three failures it exists to catch, all of which produce plausible numbers:

  A DIFFERING SETUP. One configuration run at 20 Hz produces a different
  trajectory for reasons that have nothing to do with perception, and its
  results sit beside the others looking comparable.

  A PARTIAL MATRIX. A configuration that ran fewer scenarios than the others
  is averaged over a different set of roads, and the difference is reported as
  an effect of perception error.

  A LOCAL-ONLY EXCLUSION. A scenario excluded in one configuration and kept in
  the others breaks the same comparison more quietly, because each run's own
  denominator is internally consistent.

Exit codes: 0 when the runs are comparable, 1 when they are not, 2 when the
inputs are not there to check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: The fields that must agree across every configuration of one study.
IDENTICAL_SETUP_FIELDS: tuple[str, ...] = (
    "frequency_hz",
    "planner_id",
    "controller_id",
    "termination_s",
    "initial_speed_mps",
    "route_signature",
)

#: The protocol fixes the rate. Another rate is a different experiment.
PROTOCOL_FREQUENCY_HZ = 10.0


def load_runs(runs_dir: Path) -> dict[str, dict[str, Any]]:
    """Read every run context under a directory, keyed by configuration."""

    runs: dict[str, dict[str, Any]] = {}
    for path in sorted(runs_dir.glob("*/run_context.json")):
        runs[path.parent.name] = json.loads(path.read_text(encoding="utf-8"))
    return runs


def load_exclusions(runs_dir: Path, names: tuple[str, ...]) -> dict[str, set[str]]:
    """Read each configuration's excluded tokens, defaulting to none.

    Keyed by the configurations that actually have a run context, so a
    half-written directory cannot appear here as a configuration that kept a
    token it never ran.
    """

    exclusions: dict[str, set[str]] = {}
    for name in names:
        path = runs_dir / name / "exclusions.json"
        tokens: set[str] = set()
        if path.is_file():
            document = json.loads(path.read_text(encoding="utf-8"))
            tokens = {entry["scenario_token"] for entry in document.get("excluded", [])}
        exclusions[name] = tokens
    return exclusions


def check_directories(runs_dir: Path, runs: dict[str, Any]) -> list[str]:
    """A run directory with no context is a broken run, not an absent one.

    Skipping it would be a false negative in a gate whose whole job is to stop
    a plausible number: the remaining configurations would agree and pass.
    """

    return [
        f"{directory.name} has no run_context.json. A directory under runs/ with no "
        "context is a run that did not finish, and the configurations that did "
        "would otherwise agree with each other and pass."
        for directory in sorted(p for p in runs_dir.iterdir() if p.is_dir())
        if directory.name not in runs
    ]


def check_setups(runs: dict[str, dict[str, Any]]) -> list[str]:
    """Every configuration must have been given the same simulation setup."""

    problems: list[str] = []
    for field in IDENTICAL_SETUP_FIELDS:
        values = {name: run.get("setup", {}).get(field) for name, run in runs.items()}
        distinct = set(values.values())
        if len(distinct) > 1:
            problems.append(
                f"{field} differs across configurations: {values}. The study claims the "
                "controller and the scenario were identical and only the perception "
                "changed; a difference here means that claim is false."
            )

    for name, run in runs.items():
        rate = run.get("setup", {}).get("frequency_hz")
        if rate != PROTOCOL_FREQUENCY_HZ:
            problems.append(
                f"{name} ran at {rate} Hz, not the protocol's {PROTOCOL_FREQUENCY_HZ} Hz. "
                "Every latency and counter in the committed policy is a duration at that "
                "rate, so this run used a different controller."
            )
    return problems


def check_matrix(runs: dict[str, dict[str, Any]], expected: set[str]) -> list[str]:
    """Every configuration must have run every scenario in the cohort."""

    problems: list[str] = []
    for name, run in sorted(runs.items()):
        ran = set(run.get("scenario_tokens", []))
        missing = sorted(expected - ran)
        extra = sorted(ran - expected)
        if missing:
            problems.append(
                f"{name} did not run {len(missing)} scenario(s) the cohort names, "
                f"starting with {missing[:3]}. Its mean is taken over a different set "
                "of roads than the other configurations."
            )
        if extra:
            problems.append(
                f"{name} ran {len(extra)} scenario(s) the cohort does not name, "
                f"starting with {extra[:3]}."
            )
    return problems


def check_exclusions(exclusions: dict[str, set[str]]) -> list[str]:
    """An exclusion in one configuration must be an exclusion in all of them."""

    everything = set().union(*exclusions.values()) if exclusions else set()
    problems: list[str] = []
    for token in sorted(everything):
        holders = sorted(name for name, tokens in exclusions.items() if token in tokens)
        missing = sorted(name for name in exclusions if token not in exclusions[name])
        if missing:
            problems.append(
                f"{token} was excluded in {holders} but kept in {missing}. A scenario "
                "that failed anywhere must be used nowhere, or the configurations are "
                "averaged over different cohorts."
            )
    return problems


def resume_commands(runs: dict[str, dict[str, Any]], expected: set[str]) -> list[str]:
    """The exact commands that would finish the remaining work."""

    commands: list[str] = []
    for name, run in sorted(runs.items()):
        if set(run.get("scenario_tokens", [])) != expected:
            commands.append(
                "docker compose run --rm dev uv run --frozen aeb-risk simulate "
                f"--protocol configs/protocols/nuplan_aeb_v2.yaml "
                f"--manifest artifacts/manifests/nuplan_aeb_v2/evaluation.json "
                f"--config-id {name} --output-dir artifacts/runs/{name}"
            )
    return commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args(argv)

    if not arguments.runs_dir.is_dir():
        print(f"no runs directory at {arguments.runs_dir}", file=sys.stderr)
        return 2
    if not arguments.manifest.is_file():
        print(f"no cohort manifest at {arguments.manifest}", file=sys.stderr)
        return 2

    manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    expected = set(manifest.get("scenario_tokens", []))
    runs = load_runs(arguments.runs_dir)
    if not runs:
        print(f"no run contexts under {arguments.runs_dir}", file=sys.stderr)
        return 2

    problems = (
        check_directories(arguments.runs_dir, runs)
        + check_setups(runs)
        + check_matrix(runs, expected)
        + check_exclusions(load_exclusions(arguments.runs_dir, tuple(runs)))
    )

    if not problems:
        print(f"{len(runs)} configurations are comparable over {len(expected)} scenarios")
        return 0

    print("these runs cannot be compared with each other:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)

    commands = resume_commands(runs, expected)
    if commands:
        print("\nto finish the remaining work:", file=sys.stderr)
        for command in commands:
            print(f"  {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
