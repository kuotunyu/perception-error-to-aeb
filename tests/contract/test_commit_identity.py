"""The CI commit-identity job, executed against throwaway repositories.

Every commit that reaches the default branch is authored by the maintainer's
GitHub noreply address, with the maintainer or GitHub as committer, and carries
no co-author trailer. The job's shell script is read from the workflow and run
here as written; only the repository it inspects is a fixture.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAINTAINER_EMAIL = "61350295+kuotunyu@users.noreply.github.com"
STEP_NAME = "New commits use the maintainer noreply identity"


def identity_job() -> dict[str, Any]:
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job: dict[str, Any] = yaml.safe_load(text)["jobs"]["commit-identity"]
    return job


def identity_script() -> str:
    step = next(step for step in identity_job()["steps"] if step.get("name") == STEP_NAME)
    assert step["shell"] == "bash"
    assert step["env"] == {
        "RANGE_BASE": "${{ github.event.pull_request.base.sha || github.event.before }}",
        "RANGE_HEAD": "${{ github.event.pull_request.head.sha || github.sha }}",
    }
    script: str = step["run"]
    return script


def bash_executable() -> str:
    bash = shutil.which("bash")
    assert bash
    return bash


def test_identity_job_reads_full_history_without_persisted_credentials() -> None:
    job = identity_job()

    assert job["timeout-minutes"] == 5
    assert "permissions" not in job
    checkout = next(step for step in job["steps"] if "actions/checkout@" in step.get("uses", ""))
    assert checkout["uses"] == "actions/checkout@v6"
    assert checkout["with"] == {
        "ref": "${{ github.event.pull_request.head.sha || github.sha }}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }


def test_identity_script_checks_author_committer_and_trailers() -> None:
    script = identity_script()

    assert MAINTAINER_EMAIL in script
    assert "noreply@github.com" in script
    assert "grep -qiE '^[[:space:]]*co-authored-by:'" in script
    # Workflow expressions stay in env so the shell never interpolates event data.
    assert "${{" not in script


class IdentityRepo:
    """A throwaway repository driven by the system git with no inherited git settings."""

    def __init__(self, root: Path) -> None:
        self.path = root / "repo"
        self.path.mkdir()
        self.hooks = root / "no-hooks"
        self.hooks.mkdir()
        global_config = root / "gitconfig"
        global_config.write_text("", encoding="utf-8")
        self.env = {
            key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
        }
        self.env["GIT_CONFIG_GLOBAL"] = str(global_config)
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.git("init", "--quiet")

    def git(self, *args: str, env: Optional[dict[str, str]] = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=env or self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return result.stdout.strip()

    def commit(
        self,
        *messages: str,
        author_name: str = "kuotunyu",
        author_email: str = MAINTAINER_EMAIL,
        committer: Optional[tuple[str, str]] = None,
    ) -> str:
        env = dict(self.env)
        if committer is not None:
            env["GIT_COMMITTER_NAME"], env["GIT_COMMITTER_EMAIL"] = committer
        arguments = [
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={self.hooks.as_posix()}",
            "commit",
            "--quiet",
            "--no-verify",
            "--allow-empty",
        ]
        for message in messages:
            arguments += ["-m", message]
        self.git(*arguments, env=env)
        return self.git("rev-parse", "HEAD")

    def check(self, base: str, head: str) -> subprocess.CompletedProcess[str]:
        # GitHub runs a `shell: bash` step as `bash --noprofile --norc -eo pipefail`.
        return subprocess.run(
            [
                bash_executable(),
                "--noprofile",
                "--norc",
                "-eo",
                "pipefail",
                "-c",
                identity_script(),
            ],
            cwd=self.path,
            env={**self.env, "RANGE_BASE": base, "RANGE_HEAD": head},
            capture_output=True,
            text=True,
            timeout=30,
        )


@pytest.fixture
def identity_repo(tmp_path: Path) -> IdentityRepo:
    return IdentityRepo(tmp_path)


def test_identity_check_accepts_maintainer_and_github_merge_commits(
    identity_repo: IdentityRepo,
) -> None:
    base = identity_repo.commit("root")
    identity_repo.commit("Maintainer change", "A body without trailers.")
    # GitHub records itself as the committer of the merge commits it creates.
    head = identity_repo.commit("Merge pull request #1", committer=("GitHub", "noreply@github.com"))

    result = identity_repo.check(base, head)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::error::" not in result.stdout
    assert "checked 2 commit(s)" in result.stdout


@pytest.mark.parametrize(
    "trailer",
    ["Co-authored-by: X <x@y>", "CO-AUTHORED-BY: X <x@y>", "  co-authored-by: X <x@y>"],
)
def test_identity_check_rejects_a_co_author_trailer(
    identity_repo: IdentityRepo, trailer: str
) -> None:
    base = identity_repo.commit("root")
    flagged = identity_repo.commit("Change", trailer)
    head = identity_repo.commit("Later clean change")

    result = identity_repo.check(base, head)

    # A loop that ran in a subshell would lose the failure and exit 0 here.
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"::error::{flagged} message carries a co-author trailer" in result.stdout
    assert result.stdout.count("::error::") == 1


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ({"author_email": "someone@example.com"}, "author email someone@example.com"),
        ({"author_name": "dependabot[bot]"}, "is a bot account"),
        ({"committer": ("Someone", "someone@example.com")}, "committer email someone@example.com"),
    ],
)
def test_identity_check_rejects_foreign_authors_bots_and_committers(
    identity_repo: IdentityRepo, identity: dict[str, Any], expected: str
) -> None:
    base = identity_repo.commit("root")
    head = identity_repo.commit("Change", **identity)

    result = identity_repo.check(base, head)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"::error::{head} " in result.stdout
    assert expected in result.stdout


@pytest.mark.parametrize("base", ["", "0" * 40, "1234567890abcdef1234567890abcdef12345678"])
def test_identity_check_falls_back_to_the_head_commit_without_a_usable_base(
    identity_repo: IdentityRepo, base: str
) -> None:
    identity_repo.commit("Foreign root", author_email="someone@example.com")
    head = identity_repo.commit("Clean head")

    result = identity_repo.check(base, head)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"checked 1 commit(s) in {head} -1" in result.stdout


def test_identity_check_fallback_still_rejects_a_bad_head(identity_repo: IdentityRepo) -> None:
    identity_repo.commit("root")
    head = identity_repo.commit("Foreign head", author_email="someone@example.com")

    result = identity_repo.check("0" * 40, head)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"::error::{head} author email" in result.stdout


def test_identity_check_fails_closed_on_an_unreadable_head(identity_repo: IdentityRepo) -> None:
    base = identity_repo.commit("root")

    result = identity_repo.check(base, "0123456789abcdef0123456789abcdef01234567")

    assert result.returncode != 0
    assert "checked" not in result.stdout
