"""Narrow runtime corrections for the pinned mutmut 3.3.1 audit.

This module is developer tooling.  It keeps the selected mutmut release and its
mutation semantics intact while correcting two scheduler measurements that can
turn ordinary workers into timeouts under parallel execution.
"""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from importlib import metadata
from pathlib import Path
from time import sleep
from types import ModuleType
from typing import Any, Optional

SUPPORTED_MUTMUT_VERSION = "3.3.1"
SUPPORTED_MAIN_SHA256 = "8255f5edaa2fe2d32596f73faa99e9a35fe407fad152d2d9af1ea64ff6ffa082"
WALL_TIMEOUT_MULTIPLIER = 15.0


def _make_stats_collector(state: Any) -> Any:
    """Measure all pytest phases, resetting any loaded value per execution."""

    class StatsCollector:
        @staticmethod
        def pytest_runtest_teardown(item: Any, nextitem: Any) -> None:
            del nextitem
            nodeid = item._nodeid
            prefix = "mutants/"
            if nodeid.startswith(prefix):
                nodeid = nodeid[len(prefix) :]
            for function in state._stats:
                state.tests_by_mangled_function_name[function].add(nodeid)
            state._stats.clear()

        @staticmethod
        def pytest_runtest_makereport(item: Any, call: Any) -> None:
            if call.when == "setup":
                state.duration_by_test[item.nodeid] = 0.0
            state.duration_by_test[item.nodeid] = (
                state.duration_by_test.get(item.nodeid, 0.0) + call.duration
            )

    return StatsCollector()


def _expired_pids(mutation_files: Iterable[Any], *, now: datetime) -> set[int]:
    """Return workers beyond their own persisted wall-clock allowance."""

    expired: set[int] = set()
    seen: set[int] = set()
    for mutation_file in mutation_files:
        identity = id(mutation_file)
        if identity in seen:
            continue
        seen.add(identity)
        for pid, start_time in dict(mutation_file.start_time_by_pid).items():
            estimate = mutation_file.estimated_time_of_tests_by_pid.get(pid)
            if estimate is None:
                continue
            elapsed = (now - start_time).total_seconds()
            if elapsed > (estimate + 1.0) * WALL_TIMEOUT_MULTIPLIER:
                expired.add(pid)
    return expired


def _fixed_run_stats(self: Any, *, tests: Any) -> int:
    """Mirror mutmut 3.3.1's collector with full per-test phase timing."""

    import mutmut
    import mutmut.__main__ as mutmut_main

    pytest_args = ["-x", "-q"]
    if tests:
        pytest_args += list(tests)
    else:
        tests_dir = mutmut.config.tests_dir
        if tests_dir:
            pytest_args += tests_dir
    with mutmut_main.change_cwd("mutants"):
        return int(self.execute_pytest(pytest_args, plugins=[_make_stats_collector(mutmut)]))


def _fixed_timeout_checker(mutants: Any) -> Any:
    """Build mutmut's watcher using the estimate registered for each PID."""

    import mutmut.__main__ as mutmut_main

    mutation_files: list[Any] = [mutation_file for mutation_file, _, _ in mutants]

    def inner_timeout_checker() -> None:
        while True:
            sleep(1)
            with mutmut_main.START_TIMES_BY_PID_LOCK:
                expired = _expired_pids(mutation_files, now=datetime.now())
            for pid in expired:
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGXCPU)

    return inner_timeout_checker


def _fixed_register_pid(self: Any, *, pid: int, key: str, estimated_time_of_tests: float) -> None:
    """Publish a worker's identity, start time, and estimate atomically."""

    import mutmut.__main__ as mutmut_main

    del estimated_time_of_tests
    own_estimate = self.estimated_time_of_tests_by_mutant[key]
    with mutmut_main.START_TIMES_BY_PID_LOCK:
        self.key_by_pid[pid] = key
        self.start_time_by_pid[pid] = datetime.now()
        self.estimated_time_of_tests_by_pid[pid] = own_estimate


@dataclass
class _InstalledPatch:
    module: ModuleType
    original_run_stats: Any
    original_timeout_checker: Any
    original_register_pid: Any

    def restore(self) -> None:
        """Restore the process-local upstream callbacks after the CLI returns."""

        self.module.PytestRunner.run_stats = self.original_run_stats
        self.module.timeout_checker = self.original_timeout_checker  # type: ignore[attr-defined]
        self.module.SourceFileMutationData.register_pid = self.original_register_pid


def install_mutmut_331_fixes() -> _InstalledPatch:
    """Install the two verified fixes, refusing any unreviewed mutmut version."""

    version = metadata.version("mutmut")
    if version != SUPPORTED_MUTMUT_VERSION:
        raise RuntimeError(
            f"mutmut compatibility adapter supports {SUPPORTED_MUTMUT_VERSION}, got {version}"
        )

    import mutmut.__main__ as mutmut_main

    source_path = Path(mutmut_main.__file__)
    source_digest = sha256(source_path.read_bytes()).hexdigest()
    if source_digest != SUPPORTED_MAIN_SHA256:
        raise RuntimeError(
            f"mutmut 3.3.1 source does not match the reviewed implementation: {source_digest}"
        )

    patch = _InstalledPatch(
        module=mutmut_main,
        original_run_stats=mutmut_main.PytestRunner.run_stats,
        original_timeout_checker=mutmut_main.timeout_checker,  # type: ignore[attr-defined]
        original_register_pid=mutmut_main.SourceFileMutationData.register_pid,
    )
    mutmut_main.PytestRunner.run_stats = _fixed_run_stats
    mutmut_main.timeout_checker = _fixed_timeout_checker  # type: ignore[attr-defined]
    mutmut_main.SourceFileMutationData.register_pid = _fixed_register_pid
    return patch


def main(args: Optional[list[str]] = None) -> None:
    """Run the pinned mutmut CLI after installing the measured corrections."""

    patch = install_mutmut_331_fixes()
    try:
        patch.module.cli.main(args=sys.argv[1:] if args is None else args, prog_name="mutmut")
    finally:
        patch.restore()


if __name__ == "__main__":
    main()
