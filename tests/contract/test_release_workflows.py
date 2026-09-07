"""Executable contracts for the GitHub release workflows."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MATCH_RUNNER_IDS = (
    'echo "HOST_UID=$(id -u)" >> "$GITHUB_ENV"\necho "HOST_GID=$(id -g)" >> "$GITHUB_ENV"\n'
)


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

    assert commands == (
        MATCH_RUNNER_IDS,
        "docker compose build",
        "docker compose run --rm dev uv lock --check",
        "docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify",
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
