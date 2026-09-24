"""Executable contracts for the GitHub release workflows."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MATCH_RUNNER_IDS = (
    'echo "HOST_UID=$(id -u)" >> "$GITHUB_ENV"\necho "HOST_GID=$(id -g)" >> "$GITHUB_ENV"\n'
)
REPLAY_STATUS_STEP = "Check the replay status script against a freshly generated contract"
FIXTURE_COMMAND = (
    "docker compose run --rm dev uv run --frozen python -m pytest "
    "tests/unit/report/test_replay.py::test_replay_status_contract_fixture "
    "--no-cov -p no:cacheprovider --basetemp=/work/artifacts/replay-status-contract"
)
NODE_COMMAND = "node tests/js/check-replay-status.cjs artifacts/replay-status-contract"
FRAME_FAILURE = "AssertionError [ERR_ASSERTION]: play frame 0.1"


def workflow(name: str) -> dict[object, object]:
    with (REPO_ROOT / ".github" / "workflows" / name).open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    assert isinstance(document, dict)
    return document


def runs(document: dict[object, object], job: str) -> tuple[str, ...]:
    jobs = document["jobs"]
    assert isinstance(jobs, dict)
    selected = jobs[job]
    assert isinstance(selected, dict)
    steps = selected["steps"]
    assert isinstance(steps, list)
    return tuple(str(step["run"]) for step in steps if isinstance(step, dict) and "run" in step)


def step(document: dict[object, object], job: str, name: str) -> dict[str, object]:
    jobs = document["jobs"]
    assert isinstance(jobs, dict)
    steps = jobs[job]["steps"]
    matches = [item for item in steps if isinstance(item, dict) and item.get("name") == name]
    assert len(matches) == 1, name
    selected: dict[str, object] = matches[0]
    return selected


def test_workflows_build_a_nonroot_user_matching_the_runner_checkout() -> None:
    """The container must write runner-owned outputs without broadening permissions."""

    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert "ARG AEB_UID=1000" in dockerfile
    assert "ARG AEB_GID=1000" in dockerfile
    assert 'groupadd --gid "$AEB_GID" aeb' in dockerfile
    assert 'useradd --uid "$AEB_UID" --gid aeb' in dockerfile
    assert compose["services"]["dev"]["build"]["args"] == {
        "AEB_UID": "${HOST_UID:-1000}",
        "AEB_GID": "${HOST_GID:-1000}",
    }
    for name, job in (("ci.yml", "verify"), ("pages.yml", "build"), ("release.yml", "release")):
        assert runs(workflow(name), job)[0] == MATCH_RUNNER_IDS


def test_ci_builds_the_locked_container_and_runs_the_shared_gate() -> None:
    """CI must exercise the same embedded environment and gate as local verification."""

    document = workflow("ci.yml")
    commands = runs(document, "verify")

    assert commands[:4] == (
        MATCH_RUNNER_IDS,
        "docker compose build",
        "docker compose run --rm dev uv lock --check",
        "docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify",
    )
    # The last step checks the replay status script; the tests below execute it.
    assert commands[4:] == (str(step(document, "verify", REPLAY_STATUS_STEP)["run"]),)


def test_ci_cancels_superseded_pull_request_runs_but_verifies_every_push() -> None:
    """A newer push to a pull request makes its running check moot.

    Each push to main gets a group of its own, so no main commit's gate or
    identity check is cancelled or left pending behind a later push.
    """

    assert workflow("ci.yml")["concurrency"] == {
        "group": (
            "${{ github.workflow }}-${{ github.event_name }}-"
            "${{ github.event.pull_request.number || github.sha }}"
        ),
        "cancel-in-progress": True,
    }


def run_replay_status_step(
    tmp_path: Path,
    *,
    fixture: int = 0,
    normal: int = 0,
    mutations: Optional[dict[str, tuple[int, str]]] = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the CI step's script with only docker and node replaced by recorders.

    GitHub runs a `shell: bash` step as `bash --noprofile --norc -eo pipefail`.
    """

    selected = step(workflow("ci.yml"), "verify", REPLAY_STATUS_STEP)
    assert selected["shell"] == "bash"
    outcomes = {"missing-event": (1, FRAME_FAILURE), "stale": (1, FRAME_FAILURE)}
    outcomes.update(mutations or {})
    doubles = [
        f'docker() {{ echo "docker $*" >> calls.log; return {fixture}; }}',
        "node() {",
        '  echo "node $*" >> calls.log',
        '  case "${3:-}" in',
    ]
    for mutation, (code, message) in outcomes.items():
        doubles.append(f"    {mutation}) echo '{message}' >&2; return {code} ;;")
    doubles += [f"    *) return {normal} ;;", "  esac", "}", ""]
    result = subprocess.run(
        [
            str(shutil.which("bash")),
            "--noprofile",
            "--norc",
            "-eo",
            "pipefail",
            "-c",
            "\n".join(doubles) + str(selected["run"]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    log = tmp_path / "calls.log"
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


def test_ci_runs_the_replay_status_contract_and_both_mutations(tmp_path: Path) -> None:
    """The generated status adapter is JavaScript, so Python coverage cannot see it."""

    result, calls = run_replay_status_step(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [
        FIXTURE_COMMAND,
        NODE_COMMAND,
        f"{NODE_COMMAND} missing-event",
        f"{NODE_COMMAND} stale",
    ]


@pytest.mark.parametrize(
    ("fixture", "normal", "expected_calls"),
    [(1, 0, [FIXTURE_COMMAND]), (0, 1, [FIXTURE_COMMAND, NODE_COMMAND])],
    ids=["fixture-fails", "contract-fails"],
)
def test_ci_replay_status_step_stops_when_the_fixture_or_contract_fails(
    tmp_path: Path, fixture: int, normal: int, expected_calls: list[str]
) -> None:
    result, calls = run_replay_status_step(tmp_path, fixture=fixture, normal=normal)

    assert result.returncode == 1
    assert calls == expected_calls


@pytest.mark.parametrize("mutation", ["missing-event", "stale"])
def test_ci_replay_status_step_fails_when_a_mutation_survives(
    tmp_path: Path, mutation: str
) -> None:
    result, _ = run_replay_status_step(tmp_path, mutations={mutation: (0, "")})

    assert result.returncode == 1
    assert f"::error::the {mutation} mutation passed the replay status contract" in result.stdout


@pytest.mark.parametrize("mutation", ["missing-event", "stale"])
def test_ci_replay_status_step_requires_a_frame_status_failure(
    tmp_path: Path, mutation: str
) -> None:
    """A mutation that fails for another reason, such as a missing fixture, proves nothing."""

    message = "AssertionError [ERR_ASSERTION]: Mutation must affect the real event update"
    result, _ = run_replay_status_step(tmp_path, mutations={mutation: (1, message)})

    assert result.returncode == 1
    assert message in result.stdout
    assert f"::error::the {mutation} mutation failed without a frame-status assertion" in (
        result.stdout
    )


def test_pages_audits_actual_public_text_and_skills_before_building() -> None:
    """Pages must refuse stale claims or skill contracts before it uploads a site."""

    document = workflow("pages.yml")
    commands = runs(document, "build")

    assert commands[:5] == (
        MATCH_RUNNER_IDS,
        "docker compose build",
        "docker compose run --rm dev uv lock --check",
        "docker compose run --rm dev uv run --frozen aeb-risk audit-claims --claims docs/claims.yaml",
        "docker compose run --rm dev uv run --frozen python .agents/skills/auditing-aeb-error-attribution/scripts/validate_attribution.py --claims docs/claims.yaml --repo-root . --document README.md --document README.en.md --document docs/release-notes/v1.0.0.md",
    )
    assert commands[5:] == (
        "docker compose run --rm dev uv run --frozen python -m pytest tests/contract/skills",
        "docker compose run --rm dev uv run --frozen aeb-risk figures --evidence-dir docs/evidence/nuplan_aeb_v2 --output-dir docs/figures",
        "docker compose run --rm dev uv run --frozen aeb-risk report --claims docs/claims.yaml --artifacts-dir docs/evidence/nuplan_aeb_v2 --output-dir site",
    )


def job(document: dict[object, object], name: str) -> dict[str, object]:
    jobs = document["jobs"]
    assert isinstance(jobs, dict)
    selected: dict[str, object] = jobs[name]
    return selected


def test_pages_publishes_only_a_main_commit_that_passed_ci() -> None:
    """A push to main must not reach the site before the verification gate passes."""

    document = workflow("pages.yml")

    # PyYAML reads the bare `on` key as the boolean True.
    assert document[True] == {
        "workflow_run": {"workflows": ["CI"], "types": ["completed"], "branches": ["main"]},
        "workflow_dispatch": None,
    }
    assert job(document, "build")["if"] == (
        "github.event_name == 'workflow_dispatch' || "
        "(github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.event == 'push')"
    )
    # A workflow_run event carries the default branch's latest SHA, so the
    # verified commit has to be named explicitly.
    assert step(document, "build", "Check out repository")["with"] == {
        "ref": "${{ github.event.workflow_run.head_sha || github.sha }}"
    }


def test_only_the_pages_deploy_job_holds_deploy_credentials() -> None:
    """The build job runs repository code in Docker, so it must not hold the Pages token."""

    document = workflow("pages.yml")

    assert document["permissions"] == {"contents": "read"}
    assert "permissions" not in job(document, "build")
    deploy = job(document, "deploy")
    assert deploy["needs"] == "build"
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}


def test_release_verifies_tag_version_and_writes_checksums_from_two_artifacts() -> None:
    """A tag must never publish archives whose internal version or hashes are stale."""

    document = workflow("release.yml")
    commands = runs(document, "release")

    assert commands == (
        MATCH_RUNNER_IDS,
        "docker compose build",
        "docker compose run --rm dev uv lock --check",
        "docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify",
        'export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"\ndocker compose run --rm -e SOURCE_DATE_EPOCH dev uv run --frozen python -m build --no-isolation --outdir dist\ndocker compose run --rm dev uv run --frozen python -m aebrisk.release normalize-sdist --epoch "$SOURCE_DATE_EPOCH" --dist-dir dist\n',
        'docker compose run --rm dev uv run --frozen python -m aebrisk.release verify-version --tag "$GITHUB_REF_NAME" --dist-dir dist',
        "docker compose run --rm dev uv run --frozen python -m aebrisk.release write-checksums --dist-dir dist",
        'gh release create "$GITHUB_REF_NAME" dist/*.whl dist/*.tar.gz dist/SHA256SUMS --verify-tag --title "perception-error-to-aeb $GITHUB_REF_NAME" --notes-file "docs/release-notes/$GITHUB_REF_NAME.md"',
    )
