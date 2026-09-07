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

The evidence-only documentation checkpoint
`8de34ac937ab816115f6798aa50e683bdbc0b932` has tree
`31bcee3615f88a281af5b8b5592d155376f08d56` and epoch `1788789972`.
Its diff from the audited checkpoint contains only this record,
[`mutation-audit.md`](mutation-audit.md),
[`release-checklist.md`](release-checklist.md), and the survivor-invariant
section of [`simulation-contract.md`](../simulation-contract.md). Source,
tests, mutation configuration, lock, Docker, Compose, and workflow bytes did
not change. This distinction preserves the actual audit provenance: the
mutation audit ran at `04437cb`, not at the later documentation SHA.

A second new volume, `e3-p3-releaseclone-8de34ac`, cloned that exact evidence
checkpoint with `git clone --no-local`, removed its local origin, and began
clean with LF bytes and UID/GID 1001. Its binary context SHA-256 is
`867ca043c049e99b844818b910a8253a6de2f3250d2d7f3e82b49feef18b21b1`;
23 of 23 key context files matched their Git blobs. Image
`perception-error-to-aeb-dev:e3-release-8de34ac-uid1001` has manifest digest
`sha256:be035fa9c4b945f994fec1368cc3c5464e250adb670b71d5004e2622fad4e937`.

The full gate inside the final clone again printed all eight markers, two
`1666 passed, 5 skipped` results, and 100% of 4,444 statements and 1,356
branches. That gate was the first phase of a longer reproduction job. The
later job phase exited 2 because it tried to compare the generated report to
an untracked `site/` directory that correctly did not exist in the clean
clone. The complete log, including the successful gate and the later setup
failure, has SHA-256
`83ef432bcc21d70d1e1a1edf5c9083637a7a3eb8f2a1ff2adda71a7e93bff9d4`.
The already successful gate was not repeated to hide that failure.

A corrected, bounded artifact/build phase then exited 0. Before the earlier
baseline-path failure, the first job had already completed the claim audit,
attribution validation, 71 skill tests, and artifact generation. The successful
phases of the two jobs together established:

- claim audit: zero violations;
- attribution validation: pass for both READMEs and the release note;
- skill contracts: 71 passed;
- two fresh figure builds: mutually and byte-identical to the two committed
  SVG files;
- two fresh report builds: mutually identical and all 15 paths, sizes, and
  SHA-256 values identical to the preserved E2 reference;
- all 12 report replay files: byte-identical to the committed replay assets;
- nine regenerated schemas: byte-identical to `schemas/`;
- report index SHA-256:
  `e5c1627901bb5278347e7d0e973626364edca5f4ac280436e6f2b2aad49da86a`;
- selected replay SHA-256:
  `04c681d7bc3604ccb361bf88f89a9f615cebeebcccc1e6334ab59136f2dba59e`.

The artifact/build log covers UTC `2026-09-07T14:22:23Z` through
`2026-09-07T14:22:42Z` and has SHA-256
`7e8601aee2de5ad0d690c9407ec872dd1e3638185d5892c916e777a8ed66a6ea`.

## Reproducible distributions

Both builds ran inside the same pinned final-clone container, used fresh
output directories and `SOURCE_DATE_EPOCH=1788789972`, and invoked the locked
backend with `python -m build --no-isolation`. Each directory contained one
correctly named wheel and one sdist. After sdist normalization, both builds
had these hashes:

| Artifact | SHA-256 |
| --- | --- |
| `perception_error_to_aeb-1.0.0-py3-none-any.whl` | `9ae57d83d78b264c7e809af59183bf0e1b2f0becae129e81c69d4cb526725506` |
| `perception_error_to_aeb-1.0.0.tar.gz` | `326b2b8bc4fb380bfa96d159c24c68b27972c6cec28963cbc4042dae3f49a3ea` |
| `SHA256SUMS` | `fd2cd8aee00df72670243ce2cd11e0162b14d2ce56147107cbd864aea78a35ba` |

The helper reported installed, wheel, and sdist version `1.0.0`, verified the
distribution name and filenames, and wrote exactly two portable checksum
lines. The direct wheel installed into one empty target and imported from that
target as 1.0.0. The normalized sdist was unpacked and built into a wheel,
which installed into a second empty target and also imported as 1.0.0. Its
wheel hash matched the direct build.

## Dependency licence metadata

This was a metadata and installed-license-file inspection, not a dedicated
licence scanner. It inspected 74 installed distribution records using
`License-Expression`, legacy `License`, classifiers, and installed licence
files; no record lacked all four forms of evidence. The pinned nuPlan package
has contradictory legacy metadata (`apache-2.0` plus a non-commercial
classifier), so its installed licence file was checked directly. That file is
the Apache License 2.0 notice from Motional and has SHA-256
`2f43f04335316ee361ed2b75e5da8e152ac7d832145a44caa111344ef4c6fa73`.

The built release-candidate wheel and sdist contain the project's MIT licence
and first-party package, and do not vendor dependency modules. On that
distribution basis the observed dependency licences are compatible with
publishing these MIT project archives. The full metadata inventory SHA-256 is
`9a6bad56711e04680330f11cd05cc80400f2150326e6cf5242417c1698f0b7c2`;
the focused nuPlan licence log SHA-256 is
`b585e2ba2156edc4eb2e6fa55f380fde2e8a2e11f5d10f0c31bd7a676c6e4ec6`.

Nothing in this document is evidence that GitHub Pages or a public release has
run. Those are E4 operations and remain blocked on the explicit release
authorization and the pending human replay gate.
