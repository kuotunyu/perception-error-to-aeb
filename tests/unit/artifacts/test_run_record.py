"""Contracts for the record that says how one simulation run was produced.

A number without a run record is an anecdote. The record names the commit, the
configuration, the protocol, the cohort, the locked environment, the hardware,
the seed, the wall-clock window and the hash of everything the run wrote, so a
reader can rebuild the conditions rather than trust the result.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError


def load_run_record_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.artifacts import run_record
    except ImportError:
        pytest.fail("aebrisk.artifacts.run_record is missing", pytrace=False)
    return run_record


def valid_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "schema_version": "aeb-run/v1",
        "run_id": "dropout-medium-replicate-0",
        "commit": "a" * 40,
        "config_sha256": "b" * 64,
        "protocol_sha256": "c" * 64,
        "cohort_manifest_sha256": "d" * 64,
        "lock_sha256": "e" * 64,
        "hardware": {"cpu": "x86_64", "runtime": "container"},
        "seed": 20260903,
        "started_at_utc": "2026-09-03T00:00:00Z",
        "finished_at_utc": "2026-09-03T01:00:00Z",
        "status": "succeeded",
        "artifacts": {"results": "f" * 64},
    }
    values.update(overrides)
    return values


def test_a_complete_record_validates() -> None:
    """The success path must pass or no run could record how it was produced."""

    run_record = load_run_record_module()

    record = run_record.RunRecordV1.model_validate(valid_values())

    assert record.run_id == "dropout-medium-replicate-0"
    assert record.status == "succeeded"


@pytest.mark.parametrize("status", ["succeeded", "failed", "aborted"])
def test_every_declared_status_is_accepted(status: str) -> None:
    """A run that ended badly must still be recordable, or it just disappears."""

    run_record = load_run_record_module()

    record = run_record.RunRecordV1.model_validate(valid_values(status=status))

    assert record.status == status


def test_an_unknown_status_is_refused() -> None:
    """A status nobody defined cannot be filtered on, so it hides failures."""

    run_record = load_run_record_module()

    with pytest.raises(ValidationError):
        run_record.RunRecordV1.model_validate(valid_values(status="probably fine"))


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("schema_version", "aeb-run/v2"),
        ("commit", "a" * 39),
        ("commit", "A" * 40),
        ("config_sha256", "b" * 63),
        ("protocol_sha256", "not-a-hash"),
        ("cohort_manifest_sha256", ""),
        ("lock_sha256", "e" * 65),
        ("run_id", ""),
        ("started_at_utc", "2026-09-03T00:00:00+00:00"),
        ("finished_at_utc", "yesterday"),
        ("finished_at_utc", "definitely-not-a-dateZ"),
    ],
)
def test_a_malformed_field_is_refused(field: str, invalid_value: str) -> None:
    """Provenance that cannot be parsed cannot be checked by anybody later."""

    run_record = load_run_record_module()

    with pytest.raises(ValidationError, match=rf"\n{field}\n"):
        run_record.RunRecordV1.model_validate(valid_values(**{field: invalid_value}))


def test_a_run_that_finished_before_it_started_is_refused() -> None:
    """Reversed timestamps mean the clock or the record is wrong; either invalidates it."""

    run_record = load_run_record_module()

    with pytest.raises(
        ValidationError,
        match=r"^1 validation error for RunRecordV1\n  Value error, finished_at_utc must not precede started_at_utc",
    ):
        run_record.RunRecordV1.model_validate(
            valid_values(
                started_at_utc="2026-09-03T01:00:00Z",
                finished_at_utc="2026-09-03T00:00:00Z",
            )
        )


def test_a_run_that_started_and_finished_in_the_same_second_is_accepted() -> None:
    """Equal timestamps are legitimate at one-second resolution."""

    run_record = load_run_record_module()

    record = run_record.RunRecordV1.model_validate(
        valid_values(
            started_at_utc="2026-09-03T00:00:00Z",
            finished_at_utc="2026-09-03T00:00:00Z",
        )
    )

    assert record.started_at_utc == record.finished_at_utc


def test_an_artifact_hash_must_be_a_sha256() -> None:
    """An artifact map with a bad hash cannot verify the file it points at."""

    run_record = load_run_record_module()

    with pytest.raises(ValidationError):
        run_record.RunRecordV1.model_validate(valid_values(artifacts={"results": "short"}))


def test_a_succeeded_run_must_have_produced_something() -> None:
    """A run that succeeded and wrote nothing is a run that did not happen."""

    run_record = load_run_record_module()

    with pytest.raises(
        ValidationError,
        match=r"^1 validation error for RunRecordV1\n  Value error, a succeeded run must record the artifacts it produced",
    ):
        run_record.RunRecordV1.model_validate(valid_values(status="succeeded", artifacts={}))


def test_a_failed_run_may_have_produced_nothing() -> None:
    """The whole point of recording a failure is that it has no outputs."""

    run_record = load_run_record_module()

    record = run_record.RunRecordV1.model_validate(valid_values(status="failed", artifacts={}))

    assert record.artifacts == {}


def test_an_extra_field_is_refused() -> None:
    """An unversioned provenance field would change what the record promises."""

    run_record = load_run_record_module()

    with pytest.raises(ValidationError):
        run_record.RunRecordV1.model_validate(valid_values(operator="me"))


def test_the_model_is_frozen() -> None:
    """A record that can be edited after the fact is not provenance."""

    run_record = load_run_record_module()
    record = run_record.RunRecordV1.model_validate(valid_values())

    with pytest.raises(
        ValidationError, match=r"^1 validation error for RunRecordV1\nstatus\n  Instance is frozen"
    ):
        record.status = "failed"  # type: ignore[misc]


def test_provenance_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The commit, lock hash and hardware are measured by the runner, never typed."""

    import json

    run_record = load_run_record_module()
    monkeypatch.setenv(
        run_record.PROVENANCE_ENV_VAR,
        json.dumps(
            {
                "commit": "a" * 40,
                "lock_sha256": "b" * 64,
                "hardware": {"cpu": "x86_64", "runtime": "container"},
            }
        ),
    )

    provenance = run_record.load_run_provenance()

    assert provenance.commit == "a" * 40
    assert provenance.hardware["runtime"] == "container"


def test_a_missing_provenance_variable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guessing the environment is how a result ends up describing another machine."""

    run_record = load_run_record_module()
    monkeypatch.delenv(run_record.PROVENANCE_ENV_VAR, raising=False)

    with pytest.raises(ValueError, match=run_record.PROVENANCE_ENV_VAR):
        run_record.load_run_provenance()


def test_malformed_provenance_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Half-set provenance is worse than none: it looks measured and is not."""

    run_record = load_run_record_module()
    monkeypatch.setenv(run_record.PROVENANCE_ENV_VAR, "{not json")

    with pytest.raises(ValueError, match=r"^AEBRISK_RUN_PROVENANCE is not valid JSON$"):
        run_record.load_run_provenance()


def test_provenance_requires_every_measured_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """A record missing its hardware cannot say where the number came from."""

    import json

    run_record = load_run_record_module()
    monkeypatch.setenv(
        run_record.PROVENANCE_ENV_VAR,
        json.dumps({"commit": "a" * 40, "lock_sha256": "b" * 64}),
    )

    with pytest.raises(ValueError, match=r"^AEBRISK_RUN_PROVENANCE is incomplete or malformed: "):
        run_record.load_run_provenance()


def test_the_environment_variable_is_namespaced() -> None:
    """A generic name would collide with the other two repositories in one shell."""

    run_record = load_run_record_module()

    assert run_record.PROVENANCE_ENV_VAR == "AEBRISK_RUN_PROVENANCE"
    assert True
