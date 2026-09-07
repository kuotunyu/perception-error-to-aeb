# Release checklist for `v1.0.0`

This checklist turns one verified `main` commit into the first public release.
Every unchecked box is a stop condition. Local preparation may complete before
publication, but creating a repository, adding a remote, pushing, configuring
Pages, tagging, or creating a release requires the explicit E4 authorization
described below.

## 0. Release identity and open human gate

- [ ] The E2 browser replay is accepted by a human against SHA-256
      `04c681d7bc3604ccb361bf88f89a9f615cebeebcccc1e6334ab59136f2dba59e`.
      This remains pending; a local render or automated test cannot check it off.
- [ ] `main` is clean at the release commit and the private handoff records that
      exact full commit. Before E4 authorization, the repository has zero remotes.
- [ ] Package metadata, installed metadata, wheel, sdist, release note
      [`v1.0.0.md`](../release-notes/v1.0.0.md), and the proposed tag all identify
      `perception-error-to-aeb` version `1.0.0`.
- [ ] No formal evidence, protocol, cohort, model, or published claim changed
      after its frozen validation. If one changed, repeat the applicable science
      task rather than treating it as release preparation.

## 1. Eight-stage gate on the exact candidate

Run only in the pinned Linux container, with the container UID/GID matched to
the checkout owner:

```bash
export HOST_UID="$(id -u)"
export HOST_GID="$(id -g)"
docker compose build
docker compose run --rm dev uv lock --check
docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify
```

- [ ] `verify` exits 0 and prints all eight markers: private guard, format,
      lint, typecheck, unit/integration tests, 100% statement and branch
      coverage, schema contracts, and document links.
- [ ] The recorded test result distinguishes passes from the five expected
      data-free skips in `test_nuplan_mini_adapter.py`.
- [ ] RED and GREEN evidence for release behavior and every later correction is
      retained. A setup failure is labelled as such rather than as behavioral RED.

## 2. Mutation audit

Mutation testing rewrites source and therefore runs only in a dedicated Linux
clean clone. The denominator contains every generated mutant in all declared
targets; timeout, unchecked, no-test, and equivalent statuses never count as
kills.

```bash
PYTHONPATH= uv run --frozen python \
  src/aebrisk/mutmut_compat.py run --max-children 4
```

Run this command inside the pinned clean-clone container. Clearing
`PYTHONPATH` prevents an image-installed package from shadowing the generated
mutant package.

- [ ] [`mutation-audit.md`](mutation-audit.md) records the exact commit,
      Python/mutmut versions, configuration hashes, command, UTC interval,
      generated total, every status, and killed-only arithmetic.
- [ ] Killed / all generated is at least 90% over `aeb`, `errors`, `metrics`,
      `attribution`, the two cohort modules, `step_loop.py`, and
      `route_follower.py`.
- [ ] Every final survivor has either a meaningful behavior test or a concrete
      equivalence argument tied to an invariant in
      [`simulation-contract.md`](../simulation-contract.md). The argument does
      not add equivalence credit to the score.
- [ ] Generated source, stats JSON, raw log, exit, full status inventory, and
      the exact timeout identities are preserved with SHA-256 hashes.

## 3. Claims, skills, report, figures, replay, and schemas

```bash
docker compose run --rm dev uv run --frozen aeb-risk audit-claims --claims docs/claims.yaml
docker compose run --rm dev uv run --frozen python \
  .agents/skills/auditing-aeb-error-attribution/scripts/validate_attribution.py \
  --claims docs/claims.yaml --repo-root . \
  --document README.md --document README.en.md \
  --document docs/release-notes/v1.0.0.md
docker compose run --rm dev uv run --frozen python -m pytest tests/contract/skills
docker compose run --rm dev uv run --frozen aeb-risk figures \
  --evidence-dir docs/evidence/nuplan_aeb_v2 --output-dir docs/figures
docker compose run --rm dev uv run --frozen aeb-risk report \
  --claims docs/claims.yaml --artifacts-dir docs/evidence/nuplan_aeb_v2 \
  --output-dir site
```

- [ ] Claims and the actual Traditional Chinese/English release text pass the
      attribution validator. The two metrics remain separate estimands and the
      common-valid cohort remains the only result denominator.
- [ ] Both skill contract suites pass without licensed nuPlan data.
- [ ] Rebuilt report, two SVG figures, selected replay assets, and generated
      schemas are byte-identical to their committed references. Record hashes
      and commands in [`clean-clone.md`](clean-clone.md).
- [ ] The report contains only committed derived assets; no sensor data, native
      token, raw trajectory export, database, map cache, or credential enters
      the site.

## 4. Repository hygiene

```bash
uv run --frozen python -m aebrisk.private_guard
git grep -nE "PRIVATE HANDOFF - DO NOT COMMI[T]|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|hf_[A-Za-z0-9]{34}|-----BEGIN" -- . ':!docs/verification/release-checklist.md'
git ls-files '*.ipynb'
git diff --check
```

- [ ] The private guard reports zero violations and the secret grep finds
      nothing outside this checklist. The bracketed final letter lets the grep
      detect the private marker without placing that marker verbatim here.
- [ ] No notebook, licensed dataset, database, map cache, checkpoint, raw
      prediction, private handoff, mutation tree, or build cache is tracked.
- [ ] Schema regeneration changes no tracked byte; document links pass.
- [ ] Dependency licences are reviewed from the locked environment and are
      compatible with distributing this MIT project. The evidence states
      whether this was metadata inspection or a dedicated scanner.

## 5. Clean Linux clone and reproducible distributions

The accepted local route is a new Docker Linux named volume populated by
`git clone --no-local` from the read-only source repository. It must begin with
no untracked or ignored files. The image context is exported from that clone,
not from the Windows checkout.

```bash
export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
uv lock --check
uv run --frozen python -m build --no-isolation --outdir build-one
uv run --frozen python -m aebrisk.release normalize-sdist \
  --epoch "$SOURCE_DATE_EPOCH" --dist-dir build-one
uv run --frozen python -m build --no-isolation --outdir build-two
uv run --frozen python -m aebrisk.release normalize-sdist \
  --epoch "$SOURCE_DATE_EPOCH" --dist-dir build-two
```

Run both builds inside the pinned container mounted on that same Linux clone.
The two output directories, interpreter, backend, source commit, and epoch must
be identical.

- [ ] The clone records its volume, mount, workdir, HEAD, LF checkout, clean
      state, zero remotes, UID/GID, import path, image tag/digest, and binary
      context SHA-256 in [`clean-clone.md`](clean-clone.md).
- [ ] The clone passes section 1 and all section 3 rebuilds. Output writes are
      performed as the same nonroot UID/GID as the clone owner.
- [ ] Two fresh output directories built from the same commit and epoch contain
      exactly one correctly named wheel and one sdist each. Normalized wheel and
      sdist SHA-256 hashes match across the builds.
- [ ] The helper verifies filenames, distribution name, version `1.0.0`, tag
      identity, installed metadata, wheel metadata, and canonical sdist
      metadata. A wheel built from the normalized sdist installs and imports.
- [ ] `SHA256SUMS` contains only portable relative names for the intended wheel
      and sdist. The release asset allowlist is exactly `*.whl`, `*.tar.gz`, and
      `SHA256SUMS`; no blanket `dist/*` is used.

## 6. Public repository, CI, and Pages `[E4: explicit user authorization]`

- [ ] The user receives a concrete briefing naming the public repository,
      description, contributor identity, exact main commit/count, tag,
      settings, expected duration, and success criteria, then replies with the
      single word `推`.
- [ ] Only after that reply, create `kuotunyu/perception-error-to-aeb`, add the
      remote, and push `main`. Confirm remote CI for the exact main SHA.
- [ ] Enable GitHub Pages, explicitly dispatch `pages.yml`, identify the new run
      by `workflow_dispatch` event and current main SHA, and wait for success.
      Do not treat an earlier push-triggered run as this dispatch.
- [ ] Verify the actual published index, both SVG figures, and one selected
      replay over HTTPS before tagging.

## 7. Existing tag and release

- [ ] Create and push annotated tag `v1.0.0` only after section 6 succeeds.
      The workflow itself must not create a missing tag; `gh release create`
      uses `--verify-tag`.
- [ ] The tag points at the exact commit whose CI, Pages dispatch, clean clone,
      mutation audit, and two-build proof passed.
- [ ] If a later documentation-only evidence commit follows the audited source
      commit, record byte-for-byte identity of every mutation input and run the
      full release gate and distribution proof at the final commit. Do not
      describe the earlier mutation run as having executed at the later SHA.
- [ ] The Release workflow uploads only the verified wheel, sdist, and
      `SHA256SUMS`, using [`v1.0.0.md`](../release-notes/v1.0.0.md).
- [ ] Download each public asset and verify it against `SHA256SUMS`; a public
      clone of the tag installs the wheel and passes the release gate.
