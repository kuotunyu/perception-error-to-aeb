# Mutation audit

Mutation testing asks whether a test notices a changed behavior, rather than
whether execution merely visited a line. This release scores every generated
mutant in the declared controller, perception-error, metric, attribution,
cohort-filter, and simulation-loop targets. The gate is killed / all generated;
timeouts, unchecked cases, no-test cases, and equivalent survivors stay in the
denominator and never receive kill credit.

## Reproduce the committed-source run

The audit ran in a new Docker Linux-volume clone of the committed source, with
no dataset mounted and no GPU. Clearing `PYTHONPATH` is part of the command: the
image's normal `/work/src` path must not provide an unmutated fallback.

```bash
docker run --name e3-mutmut-04437cb-final2 --rm \
  --cpus 8 --memory 12g --memory-swap 12g \
  -e PYTHONPATH= \
  -v e3-p3-cleanclone-04437cb:/clean \
  -w /clean/repo \
  perception-error-to-aeb-dev:e3-clean-04437cb-uid1001 \
  /opt/venv/bin/python src/aebrisk/mutmut_compat.py run --max-children 4
```

| Binding | Value |
| --- | --- |
| Source commit | `04437cbba556c0d30e87534f48ba04f35831f7bf` |
| Commit epoch | `1788786541` |
| Python | `3.9.19` |
| mutmut | `3.3.1` |
| Package | `perception-error-to-aeb==1.0.0` |
| Worker/resource limit | 4 children; 8 CPU; 12 GiB RAM; 12 GiB RAM+swap limit; no GPU |
| UTC interval | `2026-09-07T13:21:59Z` to `2026-09-07T13:46:02Z` |
| `pyproject.toml` SHA-256 | `8451cfac1dba22228f4834f8be3d506ca2bf72e84476433219d716d3787e4b3f` |
| `uv.lock` SHA-256 | `6a9d8e34a0f503f6c5b295cd0705ed8f6d92cffa179de6fe41bd0b993e503cfa` |
| compatibility adapter SHA-256 | `3e50377c898e892a951bb0422f77f1198cc233e93a6538e55abf371cc4011ce6` |

The eight `[tool.mutmut].paths_to_mutate` entries are:

- `src/aebrisk/aeb`
- `src/aebrisk/errors`
- `src/aebrisk/metrics`
- `src/aebrisk/attribution`
- `src/aebrisk/cohort/filters.py`
- `src/aebrisk/cohort/splits.py`
- `src/aebrisk/simulation/step_loop.py`
- `src/aebrisk/simulation/route_follower.py`

They expand to 22 generated `.py.meta` files. The saved stats contain 98
function maps, all 98 non-empty, 6,642 function-to-test links, 919 unique
selected tests in 36 files, and 1,671 phase-duration records. The
cache-cleared fixed-seed contract is one of the 15 tests mapped to
`_canonical_key`. The stats SHA-256 is
`048fd79bbb518ef9b7d9e24cbe3eed77c0bd19643e6b1aa7c5f6f86178238104`.

## Final result

The direct-file run exited 0 and its 22 metadata files account for 3,096 unique
case-sensitive IDs:

| Status | Count | Score treatment |
| --- | ---: | --- |
| Killed | **2,972** | numerator |
| Survived | 123 | denominator only |
| Timeout | 1 | denominator only |
| Unchecked / no result | 0 | denominator only |
| Other | 0 | denominator only |
| **Generated total** | **3,096** | denominator |

The killed-only score is
`2972 / 3096 * 100 = 95.99483204134367%`, which exceeds the 90% gate by
185 killed mutants. No equivalence adjustment is part of that number.

The only timeout is
`aebrisk.errors.latency.x_select_latency_frame__mutmut_82`. It replaces the
backward scan's `selected -= 1` with `selected = 1`. When the first two source
timestamps are equal, the loop stays at index 1. Its actual status remains
timeout (`-24`); it is neither relabelled nor credited as a kill.

Every one of the 65 previously identified behavior-changing survivors is killed
in this complete run. The 123 remaining survivors are exactly the 123 IDs in
the concrete-invariant table in
[`simulation-contract.md`](../simulation-contract.md#mutation-equivalence-invariants):
there are no omissions or extras. They stay in the denominator. The table
states the accepted-domain or pinned-runtime reason for each result-preserving
change; generic claims such as coverage, lack of failure, or a narrowed public
input domain are not used.

## Generated-source and baseline evidence

The run completed mutant generation, fresh stats, clean tests, and the complete
3,096-case execution before returning 0. A post-run probe mounted the preserved
tree read-only, disabled bytecode and pytest caches, and resolved
`aebrisk.errors.localization` to:

```text
/clean/repo/mutants/src/aebrisk/errors/localization.py
```

The module contained `x_wrap_to_pi__mutmut_orig` and the generated trampoline.
The new half-open-interval test passed with `MUTANT_UNDER_TEST=original` and
failed with the actual `x_wrap_to_pi__mutmut_3`; this separately rules out the
installed original package as the observed source. An earlier probe proved the
path/trampoline but made the whole root filesystem read-only without a `/tmp`,
so both pytest invocations failed during capture setup. That overall exit 1 is
retained as setup failure and is not behavioral evidence.

## Saved evidence

The private release record preserves these local artifacts. They are not
tracked product files and do not change the score:

| Artifact | SHA-256 |
| --- | --- |
| Full generated tree and metadata | `2f74a3defccd0197bb95fbd8fee3037d7c75cba89157dbcede0bc763d096690d` |
| Raw run log | `207bfb8b435125df0f0db3ed63a1ef830a02a719fadf4fd9412e1d4d585031be` |
| Exit record | `69771b3357ade5c807b9267633f25eabcf4a3e3c241b4e65c9ec2f5120a4eaa9` |
| Fresh stats JSON | `048fd79bbb518ef9b7d9e24cbe3eed77c0bd19643e6b1aa7c5f6f86178238104` |
| Full status JSON | `55d57a8e140d204d29b6a6f92c13516fdea0e33c3abdf73678d7528498de340d` |
| Full status TSV | `6e6783dc7e1c15b6ee24441dd1a036fdb35346e5c502447a453969146034d495` |
| Status summary JSON | `ec7b52af9de0ef9adeb7e75558287da40a42cd045ddf8bc26417a41b6daf0f12` |

## Why the compatibility adapter exists

The pinned upstream mutmut 3.3.1 run generated the same 3,096 mutants but
produced 2,718 kills, 298 timeouts, and 80 survivors (87.7907%). Focused
synthetic tests against the installed upstream implementation reproduced two
scheduler defects: teardown duration overwrote call duration, and the timeout
watcher compared a worker against a sibling mutant's estimate. A third dispatch
path passed stale estimates into PID registration.

`aebrisk.mutmut_compat` is a source-bound, process-local adapter. It verifies
the exact installed upstream file hash, accumulates setup/call/teardown time per
test execution, records the authoritative estimate for each mutant and PID
atomically, and restores all three callbacks after the CLI exits. It preserves
Python, mutmut version, operators, timeout multipliers, targets, and denominator;
it never edits `site-packages` or reclassifies a status.

An intermediate corrected run at commit `959bda1` removed the false timeouts
but scored 2,750 killed / 346 survived / 0 timeout (88.8243%). The later
`0572561` complete audit, after the first semantic-test pass, scored 2,907
killed / 188 survived / 1 timeout (93.8953%). Both are retained as historical
evidence and are not combined with the final status inventory.
