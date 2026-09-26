# Operator procedure: the policy v2 study and the v1.0.0 addendum

This procedure runs the pre-registered policy v2 study
([analysis plan](../studies/aeb-policy-v2/analysis-plan.md)) and computes the
post-hoc addendum on the v1.0.0 records
([addendum plan](../posthoc/nuplan_aeb_v2-addendum/addendum-plan.md)), from the
merged study tooling to the two results pull requests. The two plans decide
what is computed and what a failure means; this procedure gives the commands
and the records the operator keeps. Nothing here is a result.

Every command runs from the repository root, in the pinned container, on CPU
only. Paths are relative to the repository root, and the nuPlan installation is
the one `$NUPLAN_DATA_ROOT` names on the host, which `compose.yaml` mounts
read-only. The code blocks are POSIX shell. Nothing under `artifacts/` is
committed: it holds licensed per-token records and local details.

## 1. Before the first command

- The pull requests that add the two plans have merged, and so has the study
  tooling pull request. The analysis plan is frozen at the merge of its pull
  request, and that pull request's `merged_at`, as GitHub's server recorded it,
  is the time of pre-registration. Its number, merge commit and `merged_at` are
  read from the GitHub API and kept for `study evidence` (section 9):

  ```bash
  gh api repos/{owner}/{repo}/pulls/<number> --jq '.number, .merge_commit_sha, .merged_at'
  ```

- The working tree is a clean checkout of the study tooling merge commit, and
  it holds the released records at `artifacts/formal/nuplan_aeb_v2`, the
  released run's list of their SHA-256,
  `artifacts/d2-operations/d2-nuplan-aeb-v2-20260906T165808Z-a65ae17/output-hashes.json`,
  and its record of each log's size and SHA-256,
  `artifacts/d2-input-databases.json`.
- `HOST_UID` and `HOST_GID` are unset, so the image user keeps the default uid
  and gid of 1000, as in the v1.0.0 run.
- No other heavy job runs on the host while an arm runs.

## 2. Build and record the environment

```bash
git status --short
SHA=$(git rev-parse HEAD)
docker compose build
IMAGE=$(docker image inspect perception-error-to-aeb-dev:local --format '{{.Id}}')
docker compose run --rm dev uv lock --check
docker compose run --rm dev uv run --frozen python -m aebrisk.dev verify
docker compose run --rm dev python -c "import sys, numpy; print(sys.version.split()[0], numpy.__version__)"
docker compose run --rm dev sha256sum uv.lock configs/experiments/aeb_policy_v2_study.yaml
mkdir -p artifacts/formal/aeb_policy_v2
docker compose -f compose.yaml -f compose.formal-cpu.yaml config > artifacts/formal/aeb_policy_v2/compose-config.yaml
docker compose run --rm dev sha256sum artifacts/formal/aeb_policy_v2/compose-config.yaml
```

`git status` prints nothing, `uv lock --check` and `verify` exit 0, and the
version command prints the Python and numpy versions the study file records,
`3.9.19 1.23.4`, which G5 checks in every arm's run context.

Every arm runs under [`compose.formal-cpu.yaml`](../../compose.formal-cpu.yaml),
an override of `compose.yaml` with the limits of the v1.0.0 run: 8 CPUs, 12 GB of
memory and no swap beyond it, unbuffered output, and single-threaded OpenBLAS,
OpenMP, MKL and numexpr. `compose-config.yaml` is the resolved configuration; its
SHA-256 fixes those limits, which the gates cannot see.

From here until the gate file of section 7 is written, the working tree stays
at `$SHA`: no switch, pull, merge, commit or edit, and no `docker compose build`.
The project is installed in editable mode and the tree is mounted into the
container, so an arm runs whatever the tree holds, whatever `AEBRISK_COMMIT`
says. Before each launch, `git rev-parse HEAD` must print `$SHA`,
`git status --porcelain` must print nothing, and the image ID must still be
`$IMAGE`.

## 3. The operator log

The operator log is `artifacts/formal/aeb_policy_v2/operator.log`. It is UTF-8,
with one line per command or note, each line beginning with its UTC time:

```bash
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)" "<command or note>" >> artifacts/formal/aeb_policy_v2/operator.log
```

It records every command of this procedure, and in particular every command run
against the arms' documents, with each command's outcome. From section 2 it
records `$SHA`, `$IMAGE`, the dataset root, that `HOST_UID` and `HOST_GID` are
unset, and the SHA-256 of `uv.lock`, of the study file and of
`compose-config.yaml`. Every stop, every decision of the repository owner and
every failed attempt's cause is a note in it.

The log is published as `evidence/operator-log.txt` in the study's directory,
`docs/studies/aeb-policy-v2/`. The published copy writes the dataset root as
`$NUPLAN_DATA_ROOT`, holds no other local absolute path, and holds no token
identifier. Together with the run times in `reproduction.json`, it is the record
a reader can check.

## 4. Where things go

| Path under `artifacts/formal/aeb_policy_v2/` | Contents |
| --- | --- |
| `operator.log` | every command and note, with its UTC time |
| `compose-config.yaml` | the resolved compose configuration (section 2) |
| `preflight.json` | the preflight result (section 5) |
| `g0.json` | the operator's G0 record (section 6) |
| `attempt-<n>/<arm>/` | the arm directories of attempt n; `--arms-root` is `attempt-<n>` |
| `attempt-<n>.gates-A.json`, `attempt-<n>.gates.json` | the gate files of attempt n (sections 7 and 8) |
| `attempt-<n>.<container>.inspect.json`, `attempt-<n>.<container>.docker.log` | each container's records (section 7) |
| `attempt-<n>.sha256` | the SHA-256 of every file under `attempt-<n>/` (section 8) |

A pilot writes `artifacts/pilot/aeb_policy_v2/pilot-<k>/<arm>/`, with its gate
file beside it. The study's summary and G4 record go to
`artifacts/studies/aeb_policy_v2/`, and the addendum's summary to
`artifacts/posthoc/nuplan_aeb_v2-addendum/`. Gate files stay under `artifacts/`:
their `artifacts_only_detail` may name paths and tokens, and only
`study evidence` publishes a gate file, without that field.

An attempt is one execution of all five arms under one tooling commit, in its
own directory. Every attempt starts in a new `attempt-<n>/`. An arm stopped by a
host restart or a container stop is resumed with `--resume` into the same
directory, which revalidates every document already written; that is the same
attempt. An arm is never run again into an unfinished directory without
`--resume`: its `run.log` would keep the first start time, and `reproduction.json`
reads each arm's start and end from the first and last lines of that log.

## 5. Preflight

```bash
docker compose run --rm dev uv run --frozen aeb-risk study preflight \
  --study configs/experiments/aeb_policy_v2_study.yaml \
  --protocol configs/protocols/nuplan_aeb_v2.yaml \
  --released-root artifacts/formal/nuplan_aeb_v2 \
  --released-hashes artifacts/d2-operations/d2-nuplan-aeb-v2-20260906T165808Z-a65ae17/output-hashes.json \
  --input-databases artifacts/d2-input-databases.json \
  --manifest docs/evidence/nuplan_aeb_v2/cohort/evaluation.json \
  --split val \
  --output artifacts/formal/aeb_policy_v2/preflight.json
```

The preflight holds the study's inputs, the protocol and the cohort manifest to
the hashes the study file records. It refuses a hash list or an input-databases
record whose SHA-256 is not the one the study file pins. It holds the released
records to the hash list, and each log the cohort references, under
`nuplan-v1.1/splits/val` of the mounted dataset, to its size and SHA-256 in the
input-databases record; each log's modification time is recorded, not compared.
It prints the study file's SHA-256, which must equal the one recorded in
section 2. Any difference stops the procedure; a preflight failure is fixed and
the preflight repeated, and it uses up no attempt.

## 6. Pilot and G0

Each arm runs on the six-token smoke manifest, one arm at a time, into
`artifacts/pilot/aeb_policy_v2/pilot-<k>/`, with k = 1 for the first pilot. For
arm A:

```bash
docker compose -f compose.yaml -f compose.formal-cpu.yaml run --rm --no-deps \
  --env AEBRISK_COMMIT=$SHA --env AEBRISK_IMAGE_DIGEST=$IMAGE \
  dev uv run --frozen aeb-risk study simulate --pilot \
  --study configs/experiments/aeb_policy_v2_study.yaml \
  --arm A-v1-replication \
  --protocol configs/protocols/nuplan_aeb_v2.yaml \
  --manifest docs/evidence/nuplan_aeb_v2/cohort/smoke.json \
  --split mini --workers 6 \
  --output-dir artifacts/pilot/aeb_policy_v2/pilot-1/A-v1-replication
```

The same command runs `B-v2-gated`, `C-v1-kalman`, `D-v2-kalman` and
`E-v2-channel-rng`, each into its own directory. Then:

```bash
docker compose run --rm dev uv run --frozen aeb-risk study verify --pilot \
  --study configs/experiments/aeb_policy_v2_study.yaml \
  --arms-root artifacts/pilot/aeb_policy_v2/pilot-1 \
  --manifest docs/evidence/nuplan_aeb_v2/cohort/smoke.json \
  --output artifacts/pilot/aeb_policy_v2/pilot-1.verify.json
```

The pilot checks only that every token is valid and how long a token takes,
read from each arm's `run.log`. Its per-token documents are never analysed or
reported. An invalid pilot token stops the procedure; the tooling is fixed in a
separate pull request, with a failing test first, and every pilot arm runs again
into the next `pilot-<k>`. A pilot failure uses up no attempt.

The operator then writes `artifacts/formal/aeb_policy_v2/g0.json`, an
`aeb-study-g0/v1` document: `tooling_commit`, the tooling merge commit `$SHA`;
`ci_run_id`, the CI run of that commit; `pilot_run_contexts`, the five pilot
arms' `run_context.json` by arm id; and `pilot_gates`, the gate file
`study verify --pilot` wrote. `study evidence` validates it, refuses one whose
tooling commit differs from any arm's, and copies it into `reproduction.json`.

## 7. The formal arms

The arms run one after another, never two at once. For arm A of attempt 1:

```bash
docker compose -f compose.yaml -f compose.formal-cpu.yaml run --detach --no-deps \
  --name aeb-policy-v2-A \
  --env AEBRISK_COMMIT=$SHA --env AEBRISK_IMAGE_DIGEST=$IMAGE \
  dev uv run --frozen aeb-risk study simulate \
  --study configs/experiments/aeb_policy_v2_study.yaml \
  --arm A-v1-replication \
  --protocol configs/protocols/nuplan_aeb_v2.yaml \
  --manifest docs/evidence/nuplan_aeb_v2/cohort/evaluation.json \
  --split val --workers 8 \
  --output-dir artifacts/formal/aeb_policy_v2/attempt-1/A-v1-replication
docker wait aeb-policy-v2-A
docker inspect -f '{{.State.OOMKilled}} {{.State.Error}}' aeb-policy-v2-A
docker inspect aeb-policy-v2-A > artifacts/formal/aeb_policy_v2/attempt-1.aeb-policy-v2-A.inspect.json
docker logs --timestamps aeb-policy-v2-A > artifacts/formal/aeb_policy_v2/attempt-1.aeb-policy-v2-A.docker.log 2>&1
docker rm aeb-policy-v2-A
```

The exit code and the out-of-memory flag go into the operator log. An exit of
0 without an out-of-memory kill goes on. A container stopped by a host restart
or `docker stop` is recorded, removed, and relaunched with `--resume` added and
the name `aeb-policy-v2-A-resume-<r>`. Any other exit, or an out-of-memory
kill, stops the procedure for investigation.

Until the gate file of section 8 shows the required gates passed, the arm
directories are read and written only by `study simulate` and `study verify`.
Reading `run.log` or the container log for progress is allowed; no per-token
document is opened.

G2 is checked as soon as arm A finishes, before arms B to E:

```bash
docker compose run --rm dev uv run --frozen aeb-risk study verify \
  --study configs/experiments/aeb_policy_v2_study.yaml \
  --arms-root artifacts/formal/aeb_policy_v2/attempt-1 \
  --released-root artifacts/formal/nuplan_aeb_v2 \
  --manifest docs/evidence/nuplan_aeb_v2/cohort/evaluation.json \
  --arm A-v1-replication \
  --output artifacts/formal/aeb_policy_v2/attempt-1.gates-A.json
```

It runs G1, G2 and G5 on arm A, and exits 1 if one fails.

**If G2 fails** (analysis plan, section 9), a code cause is looked for first; a
tooling fix then goes through its own pull request, with a failing test first,
and a new attempt starts. If no code cause is found, the number of differing
documents per cell goes into the operator log, and the repository owner decides,
before any command for arm B, C, D or E, whether to continue with arm A as the
v1 reference. That decision is a numbered deviation in section 13 of the
analysis plan, merged before the first formal command for arm B, C, D or E. In
arm-A reference mode, `study verify` and `study analyse` take
`--reference arm-a`. The flag stamps `reference: arm-a` and `exploratory: true`
into the gate file, the summary and `reproduction.json`, and every claim's text
begins with "Exploratory:".

Arms `B-v2-gated`, `C-v1-kalman`, `D-v2-kalman` and `E-v2-channel-rng` then
run the same way, as containers `aeb-policy-v2-B` to `aeb-policy-v2-E`, each
into its own directory under `attempt-1/`. An invalid token in any arm ends the
attempt as failed.

## 8. Gates and the hash list of the attempt

```bash
docker compose run --rm dev uv run --frozen aeb-risk study verify \
  --study configs/experiments/aeb_policy_v2_study.yaml \
  --arms-root artifacts/formal/aeb_policy_v2/attempt-1 \
  --released-root artifacts/formal/nuplan_aeb_v2 \
  --manifest docs/evidence/nuplan_aeb_v2/cohort/evaluation.json \
  --output artifacts/formal/aeb_policy_v2/attempt-1.gates.json
```

In arm-A reference mode, the command takes `--reference arm-a`. The gate file
holds G1, G2, G3 and G5 over all five arms, and the command exits 1 unless every
gate the mode requires passed: all four, or, in arm-A reference mode, G1, G3
(internal invariance) and G5. A gate failure ends the attempt as failed.

Only when the gates passed is the hash list of the attempt written:

```bash
docker compose run --rm dev sh -c "cd artifacts/formal/aeb_policy_v2/attempt-1 && find . -type f -print0 | sort -z | xargs -0 sha256sum > ../attempt-1.sha256"
```

It covers `attempt-1/` only; the operator log, the gate files and the container
records beside it change later. From here the working tree may move again.

## 9. Analysis and evidence

The analysis runs on the same image, in a working tree whose `artifacts/`
holds the released records and the study's artifacts root at the same
repository-relative paths, mounted read-only when they come from another tree.
`study evidence` and `study claims` run from the repository root.

### The addendum

The addendum does not depend on the arms; it runs any time after the study
tooling merges, but not while an arm runs.

```bash
docker compose run --rm dev uv run --frozen aeb-risk study addendum \
  --released-root artifacts/formal/nuplan_aeb_v2 \
  --released-hashes artifacts/d2-operations/d2-nuplan-aeb-v2-20260906T165808Z-a65ae17/output-hashes.json \
  --evidence-dir docs/evidence/nuplan_aeb_v2 \
  --manifest docs/evidence/nuplan_aeb_v2/cohort/evaluation.json \
  --eligibility docs/evidence/nuplan_aeb_v2/cohort/evaluation-eligibility.json \
  --output artifacts/posthoc/nuplan_aeb_v2-addendum/addendum-summary.json
docker compose run --rm dev uv run --frozen aeb-risk study evidence --part addendum \
  --addendum artifacts/posthoc/nuplan_aeb_v2-addendum/addendum-summary.json \
  --output-dir docs/posthoc/nuplan_aeb_v2-addendum/evidence
docker compose run --rm dev uv run --frozen aeb-risk study claims --part addendum \
  --evidence-dir docs/posthoc/nuplan_aeb_v2-addendum/evidence \
  --output docs/posthoc/nuplan_aeb_v2-addendum/claims.yaml
```

`study addendum` first runs the addendum's reproduction gate. It refuses a hash
list whose SHA-256 is not the one section 7 of the addendum plan states, and it
computes nothing unless the released functions give back the released
`intervals.json`, `shapley.json` and the `evaluation.json` fields the addendum
uses. The summary records the gate's outcome and the hash list's SHA-256.
`study evidence --part addendum` writes `addendum-summary.json` and
`attribution-addendum-evidence.json`.

### The policy v2 study

With the attempt whose gates passed, here `attempt-1`:

```bash
docker compose run --rm dev uv run --frozen aeb-risk study analyse \
  --study configs/experiments/aeb_policy_v2_study.yaml \
  --arms-root artifacts/formal/aeb_policy_v2/attempt-1 \
  --released-root artifacts/formal/nuplan_aeb_v2 \
  --evidence-dir docs/evidence/nuplan_aeb_v2 \
  --manifest docs/evidence/nuplan_aeb_v2/cohort/evaluation.json \
  --gates artifacts/formal/aeb_policy_v2/attempt-1.gates.json \
  --output artifacts/studies/aeb_policy_v2/summary.json
docker compose run --rm dev uv run --frozen aeb-risk study evidence --part study \
  --summary artifacts/studies/aeb_policy_v2/summary.json \
  --gates artifacts/formal/aeb_policy_v2/attempt-1.gates.json \
  --arms-root artifacts/formal/aeb_policy_v2/attempt-1 \
  --g0 artifacts/formal/aeb_policy_v2/g0.json \
  --preregistration-pr <number> \
  --preregistration-commit <merge commit> \
  --preregistration-merged-at <merged_at> \
  --output-dir docs/studies/aeb-policy-v2/evidence
docker compose run --rm dev uv run --frozen aeb-risk study claims --part study \
  --evidence-dir docs/studies/aeb-policy-v2/evidence \
  --output docs/studies/aeb-policy-v2/claims.yaml
```

In arm-A reference mode, `study analyse` takes `--reference arm-a`.

- `study analyse` refuses unless the gate file holds G1, G2, G3 and G5 over all
  five arms and every one passed; with `--reference arm-a`, it accepts only a
  gate file written with that flag, in which G1, G3 and G5 passed. It then runs
  the study's G4 on the released evidence given as `--evidence-dir`, writes its
  result to `g4.json` beside the summary whether it passes or fails, and computes
  nothing if it fails.
- `study evidence --part study` writes `summary.json`, `gates.json` (the gate
  file without `artifacts_only_detail`), `policy-v2-evidence.json` and
  `reproduction.json`. `reproduction.json` records the pre-registration pull
  request's number, merge commit and `merged_at`, each arm's tooling commit,
  image and UTC start and end, and G0 to G5: G0 from `g0.json`, G1 to G3 and G5
  from the gate file, and G4 from the summary. With a summary, a `--g4` file is
  not read.
- If an earlier attempt failed, `study evidence` takes
  `--earlier-gates artifacts/formal/aeb_policy_v2/attempt-<m>.gates.json` (or
  `attempt-<m>.gates-A.json`) once for each earlier attempt m. Each is published
  as `gates-attempt-<m>.json`, so that every number the results page gives has a
  claim.
- If the Q3 implementation check rejects a contrast, the results wait until the
  audit of the keying code that section 7.5 of the analysis plan requires is
  recorded, with its commands and outcome, in the operator log.
- The operator log is copied to `docs/studies/aeb-policy-v2/evidence/operator-log.txt`
  as section 3 describes.

## 10. A study that is not completed

After two failed attempts, the study is reported as not completed (analysis
plan, section 9). There is no summary. With the last failed attempt n and its
gate file, `attempt-<n>.gates.json`, or `attempt-<n>.gates-A.json` if the
attempt stopped after arm A:

```bash
docker compose run --rm dev uv run --frozen aeb-risk study evidence --part study --not-completed \
  --gates artifacts/formal/aeb_policy_v2/attempt-<n>.gates.json \
  --arms-root artifacts/formal/aeb_policy_v2/attempt-<n> \
  --g0 artifacts/formal/aeb_policy_v2/g0.json \
  --preregistration-pr <number> \
  --preregistration-commit <merge commit> \
  --preregistration-merged-at <merged_at> \
  --earlier-gates artifacts/formal/aeb_policy_v2/attempt-<m>.gates.json \
  --output-dir docs/studies/aeb-policy-v2/evidence
docker compose run --rm dev uv run --frozen aeb-risk study claims --part study \
  --evidence-dir docs/studies/aeb-policy-v2/evidence \
  --output docs/studies/aeb-policy-v2/claims.yaml
```

`study evidence --part study --not-completed` writes `gates.json` and
`reproduction.json`, and a `gates-attempt-<m>.json` for each earlier attempt,
with no summary. `reproduction.json` covers the arms present under the attempt
directory, and records G4 as not run unless `study analyse` ran and G4 failed,
in which case `--g4 artifacts/studies/aeb_policy_v2/g4.json` records the
failure. `study claims --part study` then builds a gate-only registry: the gate
outcomes and the per-cell counts of differing documents, and no claim about the
policy. The operator log is copied as in section 9.

The study's results pull request carries these files with `results.md`, which
gives the gate diagnostics and the cause of each failed attempt, binds every
number to that registry, and makes no claim about the policy. It still merges,
and the release is tagged only after both results pull requests, the
addendum's and the study's, have merged.

## 11. Stops

The procedure stops, with a note in the operator log, on any of these:

- a preflight difference;
- an invalid pilot token;
- a G2 failure after arm A, which then follows section 7;
- a G1, G3 or G5 failure;
- a failure of G4 or of the addendum's reproduction gate, or a hash list that
  `study addendum` refuses;
- a container exit that is neither 0 nor an interruption, or an out-of-memory
  kill.

A tooling defect is fixed in its own pull request, with a failing test first.
After a failed attempt, the fix is also logged in section 13 of the analysis
plan as a numbered incident that changes no plan content. Once it merges,
sections 2 and 5 are repeated at the new commit, the pilot runs again into the
next `pilot-<k>` with a new `g0.json`, and every arm of the next attempt runs
from scratch into `attempt-<n>/` on that one build. Once the first formal
command for arm B, C, D or E has started, the analysis plan's content cannot
change.
