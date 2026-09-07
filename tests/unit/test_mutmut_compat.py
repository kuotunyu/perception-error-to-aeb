"""Regression tests for the pinned mutmut 3.3.1 compatibility adapter."""

from __future__ import annotations

import runpy
import sys
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import mutmut
import mutmut.__main__ as mutmut_main
import pytest

from aebrisk import mutmut_compat as compat


def test_stats_collector_sums_setup_call_and_teardown() -> None:
    """Fixture time is part of the worker's required wall-clock allowance."""

    state = SimpleNamespace(
        _stats=set(),
        duration_by_test={"tests/unit/test_example.py::test_contract": 99.0},
        tests_by_mangled_function_name={},
    )
    collector = compat._make_stats_collector(state)
    item = SimpleNamespace(
        nodeid="tests/unit/test_example.py::test_contract",
        _nodeid="mutants/tests/unit/test_example.py::test_contract",
    )

    for when, duration in (("setup", 0.4), ("call", 2.5), ("teardown", 0.1)):
        collector.pytest_runtest_makereport(item, SimpleNamespace(when=when, duration=duration))

    assert state.duration_by_test == {item.nodeid: pytest.approx(3.0)}


@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [
        ("mutants/tests/test_rate.py::test_rate", "tests/test_rate.py::test_rate"),
        ("external", "external"),
    ],
)
def test_stats_collector_associates_covered_functions_with_the_test(
    nodeid: str, expected: str
) -> None:
    state = SimpleNamespace(
        _stats={"aebrisk.metrics.safety._rate"},
        duration_by_test={},
        tests_by_mangled_function_name={"aebrisk.metrics.safety._rate": set()},
    )
    collector = compat._make_stats_collector(state)
    item = SimpleNamespace(nodeid=nodeid, _nodeid=nodeid)

    collector.pytest_runtest_teardown(item, None)

    assert state.tests_by_mangled_function_name["aebrisk.metrics.safety._rate"] == {expected}
    assert state._stats == set()


def test_expired_pids_use_each_workers_own_estimate() -> None:
    """A long worker must not inherit the deadline of a short sibling mutant."""

    now = datetime(2026, 9, 7, 6, 0, 20)
    mutation_file = SimpleNamespace(
        start_time_by_pid={101: now - timedelta(seconds=20), 202: now - timedelta(seconds=20)},
        estimated_time_of_tests_by_pid={101: 0.1, 202: 30.0},
    )

    assert compat._expired_pids([mutation_file, mutation_file], now=now) == {101}


def test_expired_pids_skip_a_registration_transition() -> None:
    now = datetime(2026, 9, 7, 6, 0, 20)
    mutation_file = SimpleNamespace(
        start_time_by_pid={101: now - timedelta(seconds=20)},
        estimated_time_of_tests_by_pid={},
    )

    assert compat._expired_pids([mutation_file], now=now) == set()


def test_fixed_stats_runner_uses_requested_tests_and_corrected_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], list[Any]]] = []

    def execute(params: list[str], **kwargs: Any) -> int:
        calls.append((params, kwargs["plugins"]))
        return 0

    runner = SimpleNamespace(execute_pytest=execute)
    monkeypatch.setattr(mutmut_main, "change_cwd", lambda _path: nullcontext())

    assert compat._fixed_run_stats(runner, tests=("test_one",)) == 0
    assert calls[0][0] == ["-x", "-q", "test_one"]
    assert calls[0][1][0].__class__.__name__ == "StatsCollector"


@pytest.mark.parametrize(
    ("tests_dir", "expected"),
    [(["tests/unit"], ["-x", "-q", "tests/unit"]), (None, ["-x", "-q"])],
)
def test_fixed_stats_runner_uses_configured_test_directory_or_pytest_default(
    monkeypatch: pytest.MonkeyPatch,
    tests_dir: Optional[list[str]],
    expected: list[str],
) -> None:
    calls: list[list[str]] = []

    def execute(params: list[str], **_kwargs: Any) -> int:
        calls.append(params)
        return 0

    runner = SimpleNamespace(execute_pytest=execute)
    monkeypatch.setattr(mutmut, "config", SimpleNamespace(tests_dir=tests_dir))
    monkeypatch.setattr(mutmut_main, "change_cwd", lambda _path: nullcontext())

    assert compat._fixed_run_stats(runner, tests=()) == 0
    assert calls == [expected]


@pytest.mark.parametrize("missing_process", [False, True])
def test_fixed_timeout_checker_signals_only_expired_workers(
    monkeypatch: pytest.MonkeyPatch, missing_process: bool
) -> None:
    now = datetime(2026, 9, 7, 6, 0, 20)
    mutation_file = SimpleNamespace(
        start_time_by_pid={101: now - timedelta(seconds=20), 202: now - timedelta(seconds=20)},
        estimated_time_of_tests_by_pid={101: 0.1, 202: 30.0},
    )
    calls: list[int] = []
    sleeps = 0

    def stop_after_one_iteration(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise RuntimeError("probe complete")

    def kill(pid: int, _signal: int) -> None:
        calls.append(pid)
        if missing_process:
            raise ProcessLookupError

    monkeypatch.setattr(compat, "sleep", stop_after_one_iteration)
    monkeypatch.setattr(compat, "datetime", SimpleNamespace(now=lambda: now))
    monkeypatch.setattr(compat.os, "kill", kill)

    checker = compat._fixed_timeout_checker([(mutation_file, "short", None)])
    with pytest.raises(RuntimeError, match="probe complete"):
        checker()

    assert calls == [101]


def test_fixed_registration_publishes_all_pid_state_together() -> None:
    key = "aebrisk.metrics.safety.x__rate__mutmut_1"
    mutation_file = SimpleNamespace(
        key_by_pid={},
        start_time_by_pid={},
        estimated_time_of_tests_by_pid={},
        estimated_time_of_tests_by_mutant={key: 2.5},
    )

    compat._fixed_register_pid(
        mutation_file,
        pid=101,
        key=key,
        estimated_time_of_tests=99.0,
    )

    assert mutation_file.key_by_pid[101].endswith("mutmut_1")
    assert isinstance(mutation_file.start_time_by_pid[101], datetime)
    assert mutation_file.estimated_time_of_tests_by_pid[101] == 2.5


def test_install_and_restore_patch_the_exact_reviewed_runtime() -> None:
    originals = (
        mutmut_main.PytestRunner.run_stats,
        mutmut_main.timeout_checker,
        mutmut_main.SourceFileMutationData.register_pid,
    )

    patch = compat.install_mutmut_331_fixes()
    try:
        assert mutmut_main.PytestRunner.run_stats is compat._fixed_run_stats
        assert mutmut_main.timeout_checker is compat._fixed_timeout_checker
        assert mutmut_main.SourceFileMutationData.register_pid is compat._fixed_register_pid
    finally:
        patch.restore()

    assert (
        mutmut_main.PytestRunner.run_stats,
        mutmut_main.timeout_checker,
        mutmut_main.SourceFileMutationData.register_pid,
    ) == originals


def test_install_refuses_an_unreviewed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compat.metadata, "version", lambda _name: "3.3.2")

    with pytest.raises(RuntimeError, match=r"supports 3\.3\.1, got 3\.3\.2"):
        compat.install_mutmut_331_fixes()


def test_install_refuses_changed_upstream_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compat, "SUPPORTED_MAIN_SHA256", "0" * 64)

    with pytest.raises(RuntimeError, match="does not match the reviewed implementation"):
        compat.install_mutmut_331_fixes()


@pytest.mark.parametrize("explicit_args", [None, ["results"]])
def test_main_runs_cli_and_restores_patch(
    monkeypatch: pytest.MonkeyPatch, explicit_args: Optional[list[str]]
) -> None:
    calls: list[tuple[list[str], str]] = []
    restored: list[bool] = []
    cli = SimpleNamespace(main=lambda **kwargs: calls.append((kwargs["args"], kwargs["prog_name"])))
    patch = SimpleNamespace(module=SimpleNamespace(cli=cli), restore=lambda: restored.append(True))
    monkeypatch.setattr(compat, "install_mutmut_331_fixes", lambda: patch)
    monkeypatch.setattr(sys, "argv", ["mutmut-compat", "results"])

    compat.main(explicit_args)

    assert calls == [(["results"], "mutmut")]
    assert restored == [True]


def test_module_entrypoint_runs_the_real_help_and_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    originals = (
        mutmut_main.PytestRunner.run_stats,
        mutmut_main.timeout_checker,
        mutmut_main.SourceFileMutationData.register_pid,
    )
    monkeypatch.setattr(sys, "argv", ["mutmut-compat", "--help"])

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(Path(compat.__file__)), run_name="__main__")

    assert raised.value.code == 0
    assert (
        mutmut_main.PytestRunner.run_stats,
        mutmut_main.timeout_checker,
        mutmut_main.SourceFileMutationData.register_pid,
    ) == originals
