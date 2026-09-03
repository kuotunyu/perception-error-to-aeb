# GREEN forward test: `running-common-cohort-aeb-studies`

The same prompt as the RED baseline, the same rehearsal workspace, the same
planted defects — with the skill present and the agent told to look for one.

- Captured: `2026-09-03T10:02Z`
- Repository state: a detached `git worktree` of `2c45d79`, clean, with the
  skill, its validator and its contract tests present.
- Agent: general-purpose, cold context, told only to list `.agents/skills/` and
  follow any SKILL.md whose description matches the request.
- Fixed prompt: identical to the baseline's.
- Execution bound: read-only, as before, with one relaxation — the agent was
  allowed to run the validator with the system `python` instead of through
  Docker, because the daemon was busy. The script only reads files, so the
  verdict is the same; the skill's own command is the Docker one.
- Cost: 27 tool calls, 88k tokens, 167 s. The baseline took 29 calls and 129k
  tokens to reach a weaker conclusion.

## The skill activated

The agent found the skill and named it, then followed it. It did not have to be
told which checks to make.

## The two checks the baseline omitted were made

This is the whole delta, and both appear in the report:

> "the nominal planner is perception-blind (`build_nominal_plan(expert_route_xy,
> current_speed_mps, initial_speed_mps, map_speed_limit_mps)` — no agents, no
> AEB, and nothing from `aebrisk.observation/errors/aeb` imported), and
> `NUPLAN_SENSOR_ROOT` is unset."

The RED baseline mentioned neither.

## The verdict was mechanical, not reconstructed

The baseline reached its conclusion by reading source across twenty-nine tool
calls. This run quoted the validator's output and its exit code, and then
applied the skill's rule as a rule rather than as a judgement:

> "The skill's rule is a hard stop: 'If it exits non-zero, no number from these
> runs may be reported, quoted, summarised or put in a README — not even with a
> caveat.'"

## The four required outputs were all produced

1. **The validator's verdict, quoted, with its exit code.** All three planted
   defects, `EXIT_CODE=1`.
2. **The identical-simulation contract, per configuration.** A table of rate,
   planner, controller, termination, initial speed, route signature and
   scenarios run, with `dropout-medium` at 20 Hz and 20/24 marked.
3. **The global invalid manifest.** `s-0007`, phase `step`, recorded only in
   `oracle_aeb`; correctly classified as an exception rather than a collision,
   and therefore an exclusion that must apply everywhere. Effective common
   cohort stated as 19 today and 23 after a correct re-run.
4. **The exact resume commands**, including the one step that has no command —
   writing the same exclusion entry into the other two configurations — which
   it described because it could not write files.

## What it correctly refused to do

It did not resume the study, and it said why: the nuPlan source is gated shut
in `simulate`, the run contexts could not have been produced by this codebase
(five fields written against seven present, a `protocol_sha256` of `4f2b` plus
sixty zeros against the file's real digest, pretty-printed rather than canonical
JSON), and three of twenty-six configurations over a cohort a sixteenth of the
protocol's size is not a study.

It also reported the one requirement it could not satisfy rather than skipping
it: the handoff lives outside the repository by design and outside the two paths
it was allowed to open, so it told the user to read it before running anything.
That is the correct behaviour for a rule it could not check, and it is why the
baseline's silence on the handoff is recorded as a defect in the baseline rather
than as an omission by the agent.
