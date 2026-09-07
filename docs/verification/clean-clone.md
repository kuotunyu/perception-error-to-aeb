# Clean Linux clone verification

This record proves that the release candidate works from committed bytes in a
new Linux filesystem, independently of the Windows checkout and its ignored
files. No remote was contacted, configured, pushed, or published.

## Source route

The first candidate route placed a clone in the `Ubuntu-bench` WSL filesystem.
The clone itself had the expected HEAD, LF bytes, and clean state, but Windows
Docker could not bind its raw UNC path because the distribution integration
socket was absent. That attempt is retained as a failed integration probe and
is not claimed as a container verification.

The verified route uses Docker's Linux named volume
`e3-p3-cleanclone-04437cb`. A root-only seed container ran `git clone
--no-local` from the source repository mounted read-only, verified the commit
and clean tree, then changed ownership of the new clone to UID/GID 1001. A
second container running as that owner removed the automatically created local
`origin`, verified zero tracked and ignored dirt, and exported the image context
before any Python process ran.

| Property | Observed value |
| --- | --- |
| Commit | `04437cbba556c0d30e87534f48ba04f35831f7bf` |
| Commit epoch | `1788786541` |
| Commit tree | `ba67b309f36e8c41fd693f2864c4af891045bacd` |
| Checkout | `README.md` and `pyproject.toml`: index LF, worktree LF |
| Owner | UID 1001, GID 1001 |
| Remotes after preparation | 0 |
| Initial tracked/ignored dirt | none |
| Volume mount and workdir | `e3-p3-cleanclone-04437cb:/clean`; `/clean/repo` |
| Binary image context | `e3-cleanclone-04437cb-context.tar` |
| Context SHA-256 | `27b712af5a6cb5e3f59d2e41ffdc223d6ec70717bcf0d3680f2fac37f07747bd` |
| Image | `perception-error-to-aeb-dev:e3-clean-04437cb-uid1001` |
| Clone import | `/clean/repo/src/aebrisk/__init__.py` |
| Image import | `/work/src/aebrisk/__init__.py` |

The 16 files changed by the preceding checkpoint plus `pyproject.toml`,
`uv.lock`, and `mutmut_compat.py` were compared byte-for-byte between Git
objects and the exported context: 19 of 19 matched. Clone and image imports are
recorded separately because an import from the image does not prove that a
mounted clone is being exercised. An early staged-volume probe found exactly
that error (`/work/src` fallback); the accepted probes set an explicit clone
path and fail if the resolved module is outside `/clean/repo/src`.

## Locked runtime and owner-aligned writes

The image was built from the binary tar context with build arguments
`AEB_UID=1001` and `AEB_GID=1001`. Runtime inspection recorded:

- CPython `3.9.19`;
- installed `perception-error-to-aeb==1.0.0`;
- `mutmut==3.3.1`;
- nonroot effective UID 1001;
- `uv lock --check` resolving 73 locked packages;
- `mutmut_compat.py` SHA-256
  `3e50377c898e892a951bb0422f77f1198cc233e93a6538e55abf371cc4011ce6`;
- `pyproject.toml` SHA-256
  `8451cfac1dba22228f4834f8be3d506ca2bf72e84476433219d716d3787e4b3f`;
- `uv.lock` SHA-256
  `6a9d8e34a0f503f6c5b295cd0705ed8f6d92cffa179de6fe41bd0b993e503cfa`.

The UID correction was tested separately. An owner-1001 Linux fixture rejected
writes by the former UID-1000 image. The rebuilt UID-1001 image then performed
Git inspection, lock checking, package import, and output writes as nonroot.
The three workflows obtain `id -u` and `id -g` from their runner and pass them
through Compose build arguments; local defaults remain 1000.

A Windows bind check later completed both test passes but failed when
`pytest-cov` tried to replace an old owner-mismatched `coverage.json`; its
overall exit was 3. It is retained as a failed environment probe. The Linux
clone gate below wrote its coverage report successfully without that warning.

## Eight-stage verification

Container `e3-cleanclone-04437cb-full-gate1` used only the named clone volume,
worked at `/clean/repo`, and set `PYTHONPATH=/clean/repo/src`. It exited 0 with
all eight markers:

```text
1666 passed, 5 skipped
TOTAL 4444 statements, 1356 branches, 100%
[verify] private_guard
[verify] format_check
[verify] lint
[verify] typecheck
[verify] unit_and_integration_tests
[verify] branch_coverage_100
[verify] schema_contracts
[verify] docs_links
```

The five skips are the explicit data-free behavior of the real nuPlan mini
adapter tests when no licensed mini split is mounted. The raw gate log SHA-256
is `a29c5e7de380293c46b7145b81f4a1b2e6940ca9d3bea9f091013c98746a0204`.

## Mutation source binding

The mutation preflight used the same clone, UID, image, and workdir. It recorded
the eight configured target entries and `tests` as the pytest root. The full
audit clears `PYTHONPATH` and invokes the compatibility adapter by source-file
path so importing its parent package cannot pin child imports to the original
tree. Generated-module import and trampoline evidence are recorded in
[`mutation-audit.md`](mutation-audit.md), separately from the clone import
above.

Fresh stats from the running committed-source audit contain 98 function maps,
all non-empty, 1,671 test durations, and the cache-cleared documented-seed test
under `_canonical_key`. Their SHA-256 is
`048fd79bbb518ef9b7d9e24cbe3eed77c0bd19643e6b1aa7c5f6f86178238104`.

## Rebuilt release artifacts

The following same-clone checks are still pending at this documentation
checkpoint and must be filled with actual exit codes and SHA-256 values before
E3 acceptance:

- rebuild the two SVG figures and static report from committed evidence;
- compare every committed/rebuilt replay asset;
- run both skill contract suites and the attribution validator;
- regenerate schemas and prove no tracked byte changes;
- build wheel and normalized sdist twice in fresh directories at this commit's
  epoch, compare hashes, and install a wheel rebuilt from the normalized sdist.

Nothing in this document is evidence that GitHub Pages or a public release has
run. Those are E4 operations and remain blocked on the explicit release
authorization and the pending human replay gate.
